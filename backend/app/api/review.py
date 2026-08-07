"""AI 复盘：把学员近期训练的**聚合事实**喂给 LLM，产出漏洞分析 + 训练建议。

铁律与 coach 一致：所有统计数字来自训练器判分（前端由 attempts 聚合后回传），是权威事实，
LLM 只能基于它们分析、不得编造未提供的数字或手牌。服务端再做一层"倾向分类"（过紧/过松/
太被动/太激进/线路偏差）帮助模型接地。无状态：不依赖服务端会话或数据库。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.llm import get_provider

router = APIRouter(tags=["review"])

ACTION_LABELS = {
    "fold": "弃牌",
    "call": "跟注",
    "check": "过牌",
    "raise": "加注",
    "bet": "下注",
    "allin": "全下",
}
SPOT_LABELS = {
    "RFI": "翻前开池 (RFI)",
    "vs_RFI": "翻前防守（面对开池）",
    "postflop_cbet": "翻后持续下注 (c-bet)",
    "postflop_defense": "翻后防守",
}
_AGGR = {"raise", "bet", "allin"}

LEAK_LABELS = {
    "too_tight": "过紧（该进攻/防守却弃牌）",
    "too_loose": "过松（该弃牌却入池/进攻）",
    "too_passive": "太被动（该加注/下注却只跟注或过牌）",
    "too_aggressive": "太激进（该跟注/过牌却加注/下注）",
    "line_error": "线路偏差",
}


def classify_leak(action: str, optimal: str) -> Optional[str]:
    """把一手错题归为一种倾向，供聚合与接地。action==optimal 返回 None。"""
    if not action or not optimal or action == optimal:
        return None
    o_aggr = optimal in _AGGR
    if optimal == "fold" and action != "fold":
        return "too_loose"
    if o_aggr and action == "fold":
        return "too_tight"
    if optimal in {"call", "check"} and action == "fold":
        return "too_tight"
    if o_aggr and action in {"call", "check"}:
        return "too_passive"
    if optimal in {"call", "check"} and action in _AGGR:
        return "too_aggressive"
    return "line_error"


class SpotStat(BaseModel):
    key: str
    total: int = 0
    correct: int = 0


class MistakeItem(BaseModel):
    spot: str = ""
    position: str = ""
    hero_position: Optional[str] = None
    opener: Optional[str] = None
    hand_class: str = ""
    action: str = ""
    optimal_action: str = ""


class ReviewRequest(BaseModel):
    total: int = Field(0, ge=0)
    accuracy: float = Field(0.0, ge=0.0, le=1.0)
    current_streak: int = 0
    best_streak: int = 0
    by_grade: Dict[str, int] = Field(default_factory=dict)
    by_spot: List[SpotStat] = Field(default_factory=list)
    by_position: List[SpotStat] = Field(default_factory=list)
    mistakes: List[MistakeItem] = Field(default_factory=list)


def _spot_label(spot: str) -> str:
    return SPOT_LABELS.get(spot, spot)


def _pos_label(m: MistakeItem) -> str:
    if m.opener and m.hero_position:
        return f"{m.hero_position} vs {m.opener}"
    return m.hero_position or m.position or "-"


def _act(a: str) -> str:
    return ACTION_LABELS.get(a, a)


def build_review_prompt(req: ReviewRequest) -> str:
    lines: List[str] = []
    lines.append(
        f"总体：累计 {req.total} 手，正确率 {req.accuracy:.0%}，"
        f"当前连对 {req.current_streak}，最佳连对 {req.best_streak}。"
    )
    if req.by_grade:
        g = req.by_grade
        lines.append(
            f"评级分布：最优 {g.get('optimal', 0)} / 可接受 {g.get('acceptable', 0)} / 偏离 {g.get('mistake', 0)}。"
        )

    if req.by_spot:
        lines.append("\n按训练类型正确率：")
        for s in sorted(req.by_spot, key=lambda x: x.total, reverse=True):
            acc = s.correct / s.total if s.total else 0.0
            lines.append(f"- {_spot_label(s.key)}：{s.correct}/{s.total}（{acc:.0%}）")

    if req.by_position:
        lines.append("\n按位置 / 对局正确率（样本多的在前）：")
        for s in sorted(req.by_position, key=lambda x: x.total, reverse=True)[:10]:
            acc = s.correct / s.total if s.total else 0.0
            lines.append(f"- {s.key}：{s.correct}/{s.total}（{acc:.0%}）")

    # 倾向聚合（服务端接地）
    tally: Dict[str, int] = {}
    for m in req.mistakes:
        leak = classify_leak(m.action, m.optimal_action)
        if leak:
            tally[leak] = tally.get(leak, 0) + 1
    if tally:
        parts = [f"{LEAK_LABELS[k]}×{v}" for k, v in sorted(tally.items(), key=lambda x: -x[1])]
        lines.append(f"\n错题倾向统计（共 {len(req.mistakes)} 手错题）：" + "；".join(parts) + "。")

    if req.mistakes:
        lines.append("\n近期错题样例：")
        for m in req.mistakes[:24]:
            leak = classify_leak(m.action, m.optimal_action)
            leak_txt = f"，{LEAK_LABELS[leak]}" if leak else ""
            lines.append(
                f"- {m.hand_class} @ {_spot_label(m.spot)} · {_pos_label(m)}："
                f"选了{_act(m.action)}，应{_act(m.optimal_action)}{leak_txt}"
            )

    lines.append(
        "\n请基于以上事实做复盘，用中文输出，条理清晰、务实可执行：\n"
        "1) 总体评估：1-2 句，点出最突出的倾向（如整体偏紧/偏松/线路问题）；\n"
        "2) 主要漏洞：2-4 条，按严重程度排序，每条要引用上面的具体数据/位置/手牌类型，并说明为什么是漏洞；\n"
        "3) 针对性建议：每条可直接执行（如某位置的防守范围如何调整）；\n"
        "4) 下一步训练重点：具体到 spot / 位置，给出优先级。\n"
        "只依据上面提供的数字，不要编造未出现的手牌或统计。"
    )
    return "\n".join(lines)


REVIEW_SYSTEM = (
    "你是一名职业德州扑克教练，正在根据学员的训练判分数据做复盘。"
    "下面给你的所有统计与错题都是**权威事实**（来自训练器判分），你只能基于它们分析，"
    "绝不能编造未提供的数字或手牌。用简洁中文、分点作答，务实、可执行，不要罗列免责声明。"
)


@router.post("/trainer/review")
def post_trainer_review(req: ReviewRequest) -> dict:
    if req.total <= 0:
        raise HTTPException(status_code=400, detail="暂无训练数据，先去练几手吧")

    provider = get_provider()
    if not (provider.gateway_ready or provider.openai_ready):
        raise HTTPException(status_code=503, detail="LLM 未配置（见 backend/.env.example）")

    prompt = build_review_prompt(req)
    try:
        text = provider.text(prompt, system=REVIEW_SYSTEM, max_tokens=900)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"LLM 调用失败：{exc}") from exc

    return {
        "report": text,
        "analyzed": req.total,
        "mistakes_considered": len(req.mistakes),
    }
