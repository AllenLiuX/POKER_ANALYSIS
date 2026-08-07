"""LLM 深度教练：把「引擎算出的事实」喂给 LLM，让它只做**解释**。

铁律（见 docs/ALGORITHMS.md §6）：数字与对错来自范围表/引擎，LLM 不得另造数字，
只负责讲"为什么这样打"。因此 prompt 里把 GTO 频率、判定结果作为 ground truth 传入，
并显式要求模型不得给出与之矛盾的频率。
"""
from __future__ import annotations

from typing import Dict

ACTION_LABELS = {"fold": "弃牌", "call": "跟注", "raise": "加注", "allin": "全下"}
GRADE_DESC = {
    "optimal": "最优（选中了最高频动作）",
    "acceptable": "可接受（命中混合区，但非最高频）",
    "mistake": "偏离（GTO 几乎不这么打）",
}

COACH_SYSTEM = (
    "你是一名职业德州扑克教练，擅长用简洁中文讲清 GTO 决策背后的原因。"
    "下面会给你由求解器/范围表得出的**事实**（各动作频率、判定结论）——"
    "这些数字是权威事实，你只能解释它们，绝不能给出与之矛盾或自行编造的频率。"
    "回答用中文，3-5 句话，聚焦'为什么'，不要罗列免责声明。"
)


def _fmt_freqs(freqs: Dict[str, float]) -> str:
    parts = []
    for a in ("raise", "call", "fold", "allin"):
        if a in freqs and freqs[a] > 0:
            parts.append(f"{ACTION_LABELS.get(a, a)} {round(freqs[a] * 100)}%")
    return " / ".join(parts) if parts else "全部弃牌"


def _scene_desc(spot: str, hero_position: str, opener_position: str | None) -> str:
    if spot == "RFI":
        return f"位置 {hero_position}，开池 / 首先行动（前面全部弃牌）"
    if spot == "vs_RFI":
        return (
            f"你在 {hero_position}，面对 {opener_position} 的开池加注（2.5bb），"
            "其余玩家已弃牌，轮到你防守"
        )
    return f"位置 {hero_position}（{spot}）"


def build_coach_prompt(
    *,
    hero_position: str,
    spot: str,
    hand_class: str,
    hero_glyphs: str,
    score: Dict[str, object],
    opener_position: str | None = None,
) -> str:
    freqs = score.get("frequencies", {})  # type: ignore[assignment]
    chosen = str(score["chosen"])
    optimal = str(score["optimal_action"])
    grade = str(score["grade"])
    if spot == "vs_RFI":
        why = (
            f"1) {hand_class} 在「{hero_position} 面对 {opener_position} 开池」为什么是这个打法"
            "（结合位置是否有利、牌力、翻后可玩性、3-bet 的价值/诈唬构成、阻断牌等，挑最相关的 1-2 点）；"
        )
    else:
        why = (
            f"1) {hand_class} 在 {hero_position} 为什么是这个打法（结合位置、牌力、可玩性/连张同花性、"
            "阻断牌、后续被 3-bet 的处理等，挑最相关的 1-2 点即可）；"
        )
    return (
        f"场景：6-max 100bb 深度，无前注。{_scene_desc(spot, hero_position, opener_position)}。\n"
        f"手牌：{hero_glyphs}（起手类别 {hand_class}）。\n"
        f"GTO 策略（事实，以此为准）：{_fmt_freqs(freqs)}。\n"
        f"最高频动作：{ACTION_LABELS.get(optimal, optimal)}。\n"
        f"玩家选择：{ACTION_LABELS.get(chosen, chosen)}；判定：{GRADE_DESC.get(grade, grade)}。\n\n"
        "请讲清：\n"
        f"{why}\n"
        "2) 若玩家偏离，指出主要风险或损失来自哪里；若已是最优，点出关键取胜逻辑；\n"
        "3) 给一条可迁移到同类牌的记忆要点。"
    )
