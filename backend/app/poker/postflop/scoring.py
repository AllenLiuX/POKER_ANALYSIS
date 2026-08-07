"""翻后打分：把用户动作与启发式建议对比，容忍合理区间。

分级：
  optimal    —— 命中首选建议（且尺度合理）
  acceptable —— 命中"可接受集合"（混合区/合理替代），或动作对但尺度偏离
  mistake    —— 明显偏离

尺度：仅当选对了下注/加注动作时才评估尺度；尺度偏离会把 optimal 降为 acceptable，
但不会判为错误（correct 仍为 True）。
"""
from __future__ import annotations

from typing import Dict, Optional


def score_postflop(
    recommendation: Dict, chosen: str, size: Optional[str] = None
) -> Dict[str, object]:
    rec = recommendation["recommended"]
    accept = set(recommendation.get("accept", [rec]))
    if chosen == rec:
        grade, correct = "optimal", True
    elif chosen in accept:
        grade, correct = "acceptable", True
    else:
        grade, correct = "mistake", False

    # 尺度评估：只在动作正确、且是"下注/加注"这类需要尺度的动作时进行。
    recommended_size: Optional[str] = None
    accept_sizes: list = []
    size_ok: Optional[bool] = None
    if correct and chosen == "bet":
        recommended_size = recommendation.get("recommended_size")
        accept_sizes = list(
            recommendation.get("accept_sizes")
            or ([recommended_size] if recommended_size else [])
        )
    elif correct and chosen == "raise":
        recommended_size = recommendation.get("recommended_raise_size")
        accept_sizes = list(
            recommendation.get("accept_raise_sizes")
            or ([recommended_size] if recommended_size else [])
        )

    if recommended_size and size is not None:
        size_ok = size in set(accept_sizes)
        if not size_ok and grade == "optimal":
            grade = "acceptable"

    return {
        "correct": correct,
        "grade": grade,
        "chosen": chosen,
        "recommended": rec,
        "accept": sorted(accept),
        "mix": bool(recommendation.get("mix", False)),
        "size": size,
        "recommended_size": recommended_size,
        "accept_sizes": accept_sizes,
        "size_ok": size_ok,
    }
