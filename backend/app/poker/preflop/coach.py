"""把打分结果翻译成中文教学反馈（确定性、离线、零延迟）。

这里刻意不调用 LLM：翻前对错来自范围表，属于"事实"，用规则模板即可给出稳定、
可解释的反馈。LLM 教练是后续可选增强（成本/延迟更高），不放进每手必经路径。
"""
from __future__ import annotations

from typing import Dict

ACTION_LABELS = {"fold": "弃牌", "call": "跟注", "raise": "加注", "allin": "全下"}


def _pct(x: float) -> str:
    return f"{round(float(x) * 100)}%"


def _label(action: str) -> str:
    return ACTION_LABELS.get(action, action)


def _spot_desc(spot: str, opener_position: str | None) -> str:
    if spot == "RFI":
        return "开池（首先行动）"
    if spot == "vs_RFI":
        return f"面对 {opener_position} 开池" if opener_position else "面对开池"
    return spot


def build_feedback(
    *,
    hero_position: str,
    spot: str,
    hand_class: str,
    score: Dict[str, object],
    opener_position: str | None = None,
) -> Dict[str, object]:
    """根据 score_action 的结果生成 {grade, headline, explanation, tip}。"""
    grade = score["grade"]
    chosen = str(score["chosen"])
    chosen_freq = float(score["chosen_freq"])
    optimal = str(score["optimal_action"])
    optimal_freq = float(score["optimal_freq"])
    is_mixed = bool(score["is_mixed"])
    position = hero_position
    spot_desc = _spot_desc(spot, opener_position)

    if grade == "optimal":
        headline = "正解"
        if is_mixed:
            explanation = (
                f"{hand_class} 在 {position} {spot_desc}是混合策略，"
                f"其中{_label(optimal)}频率最高（约 {_pct(optimal_freq)}），你选中了它。"
            )
        else:
            explanation = (
                f"{hand_class} 在 {position} {spot_desc}的标准打法就是{_label(optimal)}"
                f"（约 {_pct(optimal_freq)}）。"
            )
        tip = ""
    elif grade == "acceptable":
        headline = "可接受"
        explanation = (
            f"这是混合区：{_label(chosen)}约 {_pct(chosen_freq)}，"
            f"最高频动作是{_label(optimal)}（约 {_pct(optimal_freq)}）。"
            f"两种打法长期都合理，只是频率不同。"
        )
        tip = f"若想更贴近最高频线，可优先考虑{_label(optimal)}。"
    else:  # mistake
        headline = "偏离"
        if chosen_freq <= 0.0:
            freq_phrase = f"GTO 基本不会用{hand_class}{_label(chosen)}"
        else:
            freq_phrase = f"{hand_class}{_label(chosen)}的频率很低（约 {_pct(chosen_freq)}）"
        explanation = (
            f"{freq_phrase}；在 {position} {spot_desc}下应当{_label(optimal)}"
            f"（约 {_pct(optimal_freq)}）。"
        )
        tip = f"记住：{position} 这类位置，{hand_class} 更适合{_label(optimal)}。"

    return {
        "grade": grade,
        "headline": headline,
        "explanation": explanation,
        "tip": tip,
    }
