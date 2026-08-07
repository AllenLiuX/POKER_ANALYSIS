"""翻后确定性中文反馈。以引擎事实（纹理/成手/听牌/胜率/MDF/赔率）为准，
明确标注"启发式近似"。深度讲解仍可另接 LLM（复用现有 provider）。"""
from __future__ import annotations

from typing import Dict

ACTION_LABELS = {"fold": "弃牌", "call": "跟注", "raise": "加注", "check": "过牌", "bet": "下注"}

HEADLINE = {"optimal": "正解", "acceptable": "可接受", "mistake": "偏离"}

APPROX_NOTE = "（启发式近似，非精确 GTO 求解）"


def build_feedback(texture: Dict, hand: Dict, recommendation: Dict, score: Dict) -> Dict[str, str]:
    grade = score["grade"]
    chosen = ACTION_LABELS.get(score["chosen"], score["chosen"])
    rec = ACTION_LABELS.get(score["recommended"], score["recommended"])

    headline = HEADLINE.get(grade, grade)

    hand_desc = hand["made_label"]
    if hand.get("draw_label"):
        hand_desc += f"+{hand['draw_label']}"

    lead = f"{texture['descriptor']}，你的牌：{hand_desc}（{_tier_cn(hand['tier'])}）。"
    reasons = "；".join(recommendation.get("reasons", []))

    if grade == "optimal":
        explanation = f"{lead}建议{rec}——{reasons}。{APPROX_NOTE}"
    elif grade == "acceptable":
        explanation = (
            f"{lead}你选择{chosen}属可接受的混合区；首选{rec}——{reasons}。{APPROX_NOTE}"
        )
    else:
        explanation = f"{lead}更好的做法是{rec}——{reasons}。你选了{chosen}，偏离建议。{APPROX_NOTE}"

    tip = _tip(recommendation)
    return {"headline": headline, "explanation": explanation, "tip": tip}


def _tier_cn(tier: str) -> str:
    return {
        "value": "价值牌",
        "medium": "边缘成手",
        "draw": "强听牌",
        "weak": "弱听牌",
        "air": "空气",
    }.get(tier, tier)


def _tip(rec: Dict) -> str:
    if rec.get("spot") == "defense":
        return (
            f"底池赔率需 {rec['required_equity']:.0%} 胜率，你的估算胜率 {rec['equity']:.0%}；"
            f"面对该下注 MDF≈{rec['mdf']:.0%}。"
        )
    return f"面对该面湿度 {rec['wetness']:.0%}，建议尺度：{rec.get('size_advice','')}。"
