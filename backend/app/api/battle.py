"""HU 人机对战 API：发新手 /new、行动 /act、问题手 AI 复盘 /analyze。

无状态：/act 只需 {deal_seed, hero_pos, history, action, size}，服务端重放出全部状态。
对手底牌只在摊牌时随 result 下发。判分复用翻前范围表 + 翻后启发式引擎。
"""
from __future__ import annotations

import random
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.llm import get_provider
from app.poker.battle import engine as E

router = APIRouter(prefix="/battle", tags=["battle"])


class NewRequest(BaseModel):
    hero_pos: Optional[str] = Field(None, description="BTN / BB，缺省随机")
    seed: Optional[int] = Field(None, description="发牌种子，缺省随机（可复现用）")


@router.post("/new")
def battle_new(req: NewRequest) -> dict:
    hero_pos = req.hero_pos or random.choice(["BTN", "BB"])
    if hero_pos not in ("BTN", "BB"):
        raise HTTPException(status_code=400, detail="hero_pos 只能是 BTN / BB")
    seed = req.seed if req.seed is not None else random.getrandbits(32)
    try:
        b = E.new_hand(seed, hero_pos)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"发牌失败：{exc}") from exc
    return {"state": b.to_public()}


class ActRequest(BaseModel):
    deal_seed: int
    hero_pos: str = Field(..., pattern="^(BTN|BB)$")
    history: List[Dict] = Field(default_factory=list)
    action: str = Field(..., description="fold/check/call/bet/raise")
    size: Optional[str] = Field(None, description="下注/加注尺度 id")


@router.post("/act")
def battle_act(req: ActRequest) -> dict:
    try:
        b = E.act(req.deal_seed, req.hero_pos, req.history, req.action, req.size)
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
    hero_net: Optional[float] = None
    reason: str = ""  # fold / showdown
    winner: str = ""
    decisions: List[HandDecision] = Field(default_factory=list)


class BattleAnalyzeRequest(BaseModel):
    hands: List[ProblemHand] = Field(default_factory=list)


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
