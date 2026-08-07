"""翻后 LLM 深度教练：把「引擎算出的事实」喂给 LLM，让它只做**解释**。

铁律（见 docs/ALGORITHMS.md §6）：胜率/MDF/赔率/建议动作都来自启发式引擎，LLM 不得另造数字，
只负责讲"为什么这样打"。因此 prompt 里把这些数字作为 ground truth 传入，并显式要求模型
不得给出与之矛盾的数字。
"""
from __future__ import annotations

from typing import Dict

ACTION_LABELS = {"fold": "弃牌", "call": "跟注", "raise": "加注", "check": "过牌", "bet": "下注"}
TIER_CN = {
    "value": "价值牌",
    "medium": "边缘成手",
    "draw": "强听牌",
    "weak": "弱听牌",
    "air": "空气",
}
GRADE_DESC = {
    "optimal": "最优（命中引擎建议）",
    "acceptable": "可接受（混合区，或方向对但尺度略偏）",
    "mistake": "偏离建议",
}

POSTFLOP_COACH_SYSTEM = (
    "你是一名职业德州扑克教练，擅长用简洁中文讲清翻后决策背后的原因。"
    "下面给你的是由启发式引擎算出的**事实**（牌面纹理、成手/听牌、蒙特卡洛胜率、MDF、底池赔率、"
    "建议动作与下注尺度）——这些数字是权威事实，你只能解释它们，绝不能给出与之矛盾或自行编造的数字。"
    "请结合 range 优势、极化/线性范围、阻断牌、bluff-to-value、翻后可玩性等概念，"
    "用中文 3-5 句聚焦'为什么'，不要罗列免责声明，也不要重复'启发式近似'字样。"
)


def _role_desc(role: str, hero_pos: str, villain_pos: str, pot_bb, bet_bb) -> str:
    if role == "pfr":
        return (
            f"你在 {hero_pos} 是翻前加注方（{villain_pos} 跟注），翻牌轮到你决定是否持续下注；"
            f"底池 {pot_bb}bb。"
        )
    return (
        f"你在 {hero_pos} 面对翻前加注方 {villain_pos} 的持续下注 {bet_bb}bb"
        f"（底池 {pot_bb}bb），轮到你防守。"
    )


def build_postflop_coach_prompt(
    *,
    role: str,
    hero_pos: str,
    villain_pos: str,
    board_glyphs: str,
    texture: Dict,
    hand: Dict,
    equity: float,
    rec: Dict,
    score: Dict,
) -> str:
    made = str(hand.get("made_label", ""))
    draw = hand.get("draw_label")
    hand_desc = made + (f" + {draw}" if draw else "")
    tier = TIER_CN.get(str(hand.get("tier")), str(hand.get("tier", "")))

    chosen = ACTION_LABELS.get(score["chosen"], score["chosen"])
    if score.get("size_label"):
        chosen += f"（{score['size_label']}）"
    recommended = ACTION_LABELS.get(score["recommended"], score["recommended"])
    if score.get("recommended_size_label"):
        recommended += f"（{score['recommended_size_label']}）"
    grade_desc = GRADE_DESC.get(str(score.get("grade")), str(score.get("grade")))
    reasons = "；".join(rec.get("reasons", []))

    lines = [
        f"场景：6-max 100bb 深度翻牌。{_role_desc(role, hero_pos, villain_pos, rec.get('pot_bb'), rec.get('bet_bb'))}",
        f"牌面：{board_glyphs}（{texture.get('descriptor', '')}）。",
        f"你的牌：{hand_desc or '空气'}（{tier}，估算胜率 {equity:.0%}）。",
    ]
    if rec.get("spot") == "defense":
        lines.append(
            f"防守数据（事实）：底池赔率需 {rec.get('required_equity', 0):.0%} 胜率，"
            f"面对该下注 MDF≈{rec.get('mdf', 0):.0%}。"
        )
    else:
        lines.append(
            f"牌面湿度 {rec.get('wetness', 0):.0%}；引擎尺度建议：{rec.get('size_advice', '')}。"
        )
    lines.append(f"引擎建议：{recommended}；依据：{reasons}。")
    lines.append(f"玩家选择：{chosen}；判定：{grade_desc}。")
    lines.append(
        "请讲清：1) 为什么在这个牌面、以这手牌力/听牌，引擎会这样建议"
        "（结合 range 优势、湿度、阻断牌、价值/诈唬构成或 MDF/赔率，挑最相关的 1-2 点）；"
        "2) 若玩家偏离或尺度不佳，指出主要风险 / EV 损失来自哪里；若已最优，点出关键取胜逻辑；"
        "3) 给一条可迁移到同类牌面的记忆要点。"
    )
    return "\n".join(lines)
