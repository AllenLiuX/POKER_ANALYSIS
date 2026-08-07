"""翻后打分：把用户动作与启发式建议对比，容忍合理区间。

分级：
  optimal    —— 命中首选建议
  acceptable —— 命中"可接受集合"（混合区/合理替代）
  mistake    —— 明显偏离
"""
from __future__ import annotations

from typing import Dict


def score_postflop(recommendation: Dict, chosen: str) -> Dict[str, object]:
    rec = recommendation["recommended"]
    accept = set(recommendation.get("accept", [rec]))
    if chosen == rec:
        grade, correct = "optimal", True
    elif chosen in accept:
        grade, correct = "acceptable", True
    else:
        grade, correct = "mistake", False
    return {
        "correct": correct,
        "grade": grade,
        "chosen": chosen,
        "recommended": rec,
        "accept": sorted(accept),
        "mix": bool(recommendation.get("mix", False)),
    }
