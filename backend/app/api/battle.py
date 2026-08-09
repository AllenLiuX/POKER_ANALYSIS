"""HU 人机对战 API：发新手 /new、行动 /act、问题手 AI 复盘 /analyze。

无状态：/act 只需 {deal_seed, hero_pos, history, action, size}，服务端重放出全部状态。
对手底牌只在摊牌时随 result 下发。判分复用翻前范围表 + 翻后启发式引擎。
"""
from __future__ import annotations

import random
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.llm import get_provider
from app.poker.battle import engine as E

_STREAM_HEADERS = {"X-Accel-Buffering": "no", "Cache-Control": "no-cache"}

router = APIRouter(prefix="/battle", tags=["battle"])


@router.get("/matchups")
def battle_matchups() -> dict:
    """可选对位（不同位置对抗）。每个对位可选英雄扮演开池方或防守方。"""
    from app.poker.battle.deal import MATCHUPS, matchup_label, matchup_positions

    out = []
    for m in MATCHUPS:
        opener, defender = matchup_positions(m)
        out.append(
            {
                "matchup": m,
                "label": matchup_label(m),
                "opener": opener,
                "defender": defender,
                "positions": [opener, defender],
            }
        )
    return {"matchups": out}


class NewRequest(BaseModel):
    matchup: Optional[str] = Field(None, description="对位 vs_RFI 键，如 BB_vs_BTN；缺省随机")
    hero_pos: Optional[str] = Field(None, description="英雄位置（须属于该对位）；缺省随机取一侧")
    seed: Optional[int] = Field(None, description="发牌种子，缺省随机（可复现用）")


def _resolve_matchup_hero(matchup: Optional[str], hero_pos: Optional[str]) -> tuple:
    """解析对位 + 英雄位置。兼容旧的仅传 hero_pos=BTN/BB（默认对位 BB_vs_BTN）。"""
    from app.poker.battle.deal import MATCHUPS, matchup_positions

    if matchup is None:
        if hero_pos in ("BTN", "BB") or hero_pos is None:
            matchup = "BB_vs_BTN"  # 向后兼容默认对位
        else:
            raise HTTPException(status_code=400, detail="缺少 matchup，且 hero_pos 非 BTN/BB")
    if matchup not in MATCHUPS:
        raise HTTPException(status_code=400, detail=f"对位不存在：{matchup}")
    opener, defender = matchup_positions(matchup)
    if hero_pos is None:
        hero_pos = random.choice([opener, defender])
    if hero_pos not in (opener, defender):
        raise HTTPException(
            status_code=400, detail=f"hero_pos {hero_pos} 不属于对位 {matchup}（{opener}/{defender}）"
        )
    return matchup, hero_pos


@router.post("/new")
def battle_new(req: NewRequest) -> dict:
    matchup, hero_pos = _resolve_matchup_hero(req.matchup, req.hero_pos)
    seed = req.seed if req.seed is not None else random.getrandbits(32)
    try:
        b = E.new_hand(seed, matchup, hero_pos)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"发牌失败：{exc}") from exc
    return {"state": b.to_public()}


class ActRequest(BaseModel):
    deal_seed: int
    matchup: Optional[str] = Field(None, description="对位 vs_RFI 键；缺省按 hero_pos 兼容推断")
    hero_pos: str = Field(..., description="英雄位置")
    history: List[Dict] = Field(default_factory=list)
    action: str = Field(..., description="fold/check/call/bet/raise")
    size: Optional[str] = Field(None, description="下注/加注尺度 id")


@router.post("/act")
def battle_act(req: ActRequest) -> dict:
    matchup, hero_pos = _resolve_matchup_hero(req.matchup, req.hero_pos)
    try:
        b = E.act(req.deal_seed, matchup, hero_pos, req.history, req.action, req.size)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"对战推进失败：{exc}") from exc
    return {"state": b.to_public()}


# ---------------- 问题手 AI 复盘 ----------------
class HandDecision(BaseModel):
    street: str = ""
    spot_label: str = ""
    hand_class: str = ""
    action: str = ""
    optimal_action: Optional[str] = None
    grade: str = ""
    made_label: Optional[str] = None
    draw_label: Optional[str] = None
    tier: Optional[str] = None
    equity: Optional[float] = None
    reasons: List[str] = Field(default_factory=list)


class ProblemHand(BaseModel):
    hero_glyphs: List[str] = Field(default_factory=list)
    hero_pos: str = ""
    villain_pos: str = ""
    board_glyphs: List[str] = Field(default_factory=list)
    villain_glyphs: List[str] = Field(default_factory=list)  # 仅摊牌
    villain_class: str = ""  # 仅摊牌
    hero_net: Optional[float] = None
    reason: str = ""  # fold / showdown
    winner: str = ""
    action_line: List[str] = Field(default_factory=list)  # 分街行动线（可读）
    decisions: List[HandDecision] = Field(default_factory=list)


class BattleAnalyzeRequest(BaseModel):
    hands: List[ProblemHand] = Field(default_factory=list)


class BattleExplainRequest(BaseModel):
    hand: ProblemHand


ACTION_CN = {"fold": "弃牌", "call": "跟注", "check": "过牌", "bet": "下注", "raise": "加注"}


def _act_cn(a: Optional[str]) -> str:
    return ACTION_CN.get(a or "", a or "-")


BATTLE_REVIEW_SYSTEM = (
    "你是一名职业德州扑克教练，正在复盘学员和 AI 对战中被系统标记为「有问题」的手牌。"
    "每手牌的判分（最优动作、评级、牌力/胜率、牌面纹理与依据）都是引擎给出的**权威事实**，"
    "你只能基于它们分析，绝不能编造未提供的数字或牌。用简洁中文、分点作答，务实、可执行。"
)


def build_battle_prompt(hands: List[ProblemHand]) -> str:
    lines: List[str] = [f"共 {len(hands)} 手被标记的问题手，逐手事实如下：\n"]
    for i, h in enumerate(hands, 1):
        board = " ".join(h.board_glyphs) or "（未到翻牌/未摊牌）"
        hero = " ".join(h.hero_glyphs)
        net = f"{h.hero_net:+.1f}bb" if h.hero_net is not None else "?"
        lines.append(f"[手 {i}] {h.hero_pos} 持 {hero}，牌面 {board}，结果 {net}（{h.winner or '-'}）")
        if h.action_line:
            lines.append("   行动线：" + " ｜ ".join(h.action_line))
        for d in h.decisions:
            if d.grade not in ("mistake", "acceptable"):
                continue
            hand_desc = d.made_label or ""
            if d.draw_label:
                hand_desc += f"+{d.draw_label}"
            eq = f"，胜率 {d.equity:.0%}" if d.equity is not None else ""
            opt = f"，应{_act_cn(d.optimal_action)}" if d.optimal_action else ""
            why = ("；依据：" + "；".join(d.reasons)) if d.reasons else ""
            flag = "❌偏离" if d.grade == "mistake" else "⚠可接受但非首选"
            lines.append(
                f"   - {d.spot_label}·{d.hand_class}（{hand_desc or '—'}{eq}）："
                f"选了{_act_cn(d.action)}{opt} [{flag}]{why}"
            )
        lines.append("")
    lines.append(
        "请基于以上事实复盘，中文输出：\n"
        "1) 总体倾向：1-2 句点出最突出的漏洞（过紧/过松/太被动/太激进/尺度/线路）；\n"
        "2) 主要漏洞：2-4 条，按严重度排序，每条引用上面具体的手牌/牌面/位置并说明为何是漏洞；\n"
        "3) 修正建议：每条可直接执行；\n"
        "4) 下一步训练重点：具体到翻前/翻后与位置。\n"
        "只依据上面提供的事实，不要编造未出现的牌或数字。"
    )
    return "\n".join(lines)


@router.post("/analyze")
def battle_analyze(req: BattleAnalyzeRequest) -> dict:
    if not req.hands:
        raise HTTPException(status_code=400, detail="暂无问题手可分析，先去对战积累几手吧")

    provider = get_provider()
    if not (provider.gateway_ready or provider.openai_ready):
        raise HTTPException(status_code=503, detail="LLM 未配置（见 backend/.env.example）")

    prompt = build_battle_prompt(req.hands)
    try:
        text = provider.text(prompt, system=BATTLE_REVIEW_SYSTEM, max_tokens=900, model="gpt-4o")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"LLM 调用失败：{exc}") from exc
    if not text or not text.strip():
        raise HTTPException(status_code=502, detail="复盘生成为空，请重试")

    return {"report": text, "analyzed": len(req.hands)}


@router.post("/analyze/stream")
def battle_analyze_stream(req: BattleAnalyzeRequest) -> StreamingResponse:
    """与 /battle/analyze 相同，但纯文本流式返回，边收边渲染。"""
    if not req.hands:
        raise HTTPException(status_code=400, detail="暂无问题手可分析，先去对战积累几手吧")

    provider = get_provider()
    if not (provider.gateway_ready or provider.openai_ready):
        raise HTTPException(status_code=503, detail="LLM 未配置（见 backend/.env.example）")

    prompt = build_battle_prompt(req.hands)

    def gen():
        # 用非推理的 gpt-4o：逐字即时流出，避免推理模型"先思考后爆发"导致流式体感无提升。
        yield from provider.text_stream(prompt, system=BATTLE_REVIEW_SYSTEM, max_tokens=900, model="gpt-4o")

    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8", headers=_STREAM_HEADERS)


# ---------------- 单手复盘：这手打得对不对 ----------------
GRADE_CN = {"optimal": "最优", "acceptable": "可接受", "mistake": "偏离", "ungraded": "未判分"}


def build_explain_prompt(h: ProblemHand) -> str:
    board = " ".join(h.board_glyphs) or "（未到翻牌/未摊牌）"
    hero = " ".join(h.hero_glyphs)
    net = f"{h.hero_net:+.1f}bb" if h.hero_net is not None else "?"
    villain = " ".join(h.villain_glyphs)
    if h.reason == "showdown" and villain:
        villain_line = f"对手 {h.villain_pos} 摊牌 {villain}" + (f"（{h.villain_class}）" if h.villain_class else "")
    elif h.reason == "fold":
        villain_line = f"对手 {h.villain_pos} 未摊牌（有人弃牌成交）"
    else:
        villain_line = f"对手 {h.villain_pos}（底牌未知）"

    lines: List[str] = [
        "复盘这一手单挑（HU，100bb）的英雄决策是否正确：",
        f"英雄 {h.hero_pos} 持 {hero}；{villain_line}",
        f"牌面 {board}；本手英雄净收益 {net}（{h.winner or '-'}）",
    ]
    if h.action_line:
        lines.append("行动线：" + " ｜ ".join(h.action_line))
    lines += [
        "",
        "本手英雄逐个决策（含引擎判分，判分为权威事实）：",
    ]
    if not h.decisions:
        lines.append("（本手英雄无需决策或未记录）")
    for d in h.decisions:
        hand_desc = d.made_label or d.hand_class or ""
        if d.draw_label:
            hand_desc += f"+{d.draw_label}"
        eq = f"，胜率 {d.equity:.0%}" if d.equity is not None else ""
        opt = f"，引擎建议{_act_cn(d.optimal_action)}" if d.optimal_action else ""
        why = ("；依据：" + "；".join(d.reasons)) if d.reasons else ""
        tag = GRADE_CN.get(d.grade, d.grade)
        lines.append(
            f"   - {d.spot_label}·{d.hand_class}（{hand_desc or '—'}{eq}）："
            f"实际{_act_cn(d.action)}{opt} [{tag}]{why}"
        )
    lines.append("")
    lines.append(
        "请用简洁中文回答，务实、可执行：\n"
        "1) 结论：这手整体打得对不对（一句话，含总体评价）；\n"
        "2) 逐街点评：翻前/翻牌/转牌/河牌各自的决策是否正确、为什么（引用上面的胜率/牌力/建议）；\n"
        "3) 更优线路：若有更好的打法请具体说明尺度与理由；没有则说明为何当前已足够好。\n"
        "只依据上面提供的事实，不要编造未出现的牌或数字。若对手未摊牌，不要臆测其底牌。"
    )
    return "\n".join(lines)


EXPLAIN_SYSTEM = (
    "你是一名职业德州扑克教练，正在针对学员和 AI 单挑中的**某一手牌**做逐街复盘。"
    "每个决策的判分（最优动作、评级、牌力/胜率、依据）都是引擎给出的**权威事实**，"
    "你只能据此分析，绝不能编造未提供的数字或牌。用简洁中文、分点作答。"
)


@router.post("/explain")
def battle_explain(req: BattleExplainRequest) -> dict:
    provider = get_provider()
    if not (provider.gateway_ready or provider.openai_ready):
        raise HTTPException(status_code=503, detail="LLM 未配置（见 backend/.env.example）")
    prompt = build_explain_prompt(req.hand)
    try:
        text = provider.text(prompt, system=EXPLAIN_SYSTEM, max_tokens=700, model="gpt-4o")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"LLM 调用失败：{exc}") from exc
    if not text or not text.strip():
        raise HTTPException(status_code=502, detail="复盘生成为空，请重试")
    return {"report": text}


@router.post("/explain/stream")
def battle_explain_stream(req: BattleExplainRequest) -> StreamingResponse:
    """单手复盘，纯文本流式返回。"""
    provider = get_provider()
    if not (provider.gateway_ready or provider.openai_ready):
        raise HTTPException(status_code=503, detail="LLM 未配置（见 backend/.env.example）")
    prompt = build_explain_prompt(req.hand)

    def gen():
        yield from provider.text_stream(prompt, system=EXPLAIN_SYSTEM, max_tokens=700, model="gpt-4o")

    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8", headers=_STREAM_HEADERS)
