"""翻前打分：处理混合策略。

分级：
    optimal    —— 选中最高频动作（或纯策略下的唯一动作）。
    acceptable —— 选中一个 GTO 有可观频率(>=min_mix)但非最高频的动作（混合区）。
    mistake    —— 选中一个 GTO 几乎不选(<min_mix)的动作。

ev_loss_proxy：用「最优动作频率 - 所选动作频率」近似（0~1，单位为频率而非真实 EV）。
真实 EV 损失需求解器；此处为教学用近似，UI 应如实标注。
"""
from __future__ import annotations

from typing import Dict

MIN_MIX = 0.10  # 视为“可接受”的最低频率
PURE = 0.90     # 视为“纯策略”的最高频率阈值


def score_action(freqs: Dict[str, float], chosen: str) -> Dict[str, object]:
    if chosen not in freqs:
        raise ValueError(f"非法动作 {chosen!r}，可选：{sorted(freqs.keys())}")

    optimal = max(freqs, key=lambda a: freqs[a])
    optimal_freq = freqs[optimal]
    chosen_freq = freqs[chosen]
    acceptable = {a for a, f in freqs.items() if f >= MIN_MIX}

    if chosen == optimal:
        grade = "optimal"
        correct = True
    elif chosen in acceptable:
        grade = "acceptable"
        correct = True
    else:
        grade = "mistake"
        correct = False

    return {
        "correct": correct,
        "grade": grade,
        "chosen": chosen,
        "chosen_freq": chosen_freq,
        "optimal_action": optimal,
        "optimal_freq": optimal_freq,
        "frequencies": freqs,
        "is_mixed": len(acceptable) > 1,
        "ev_loss_proxy": round(max(0.0, optimal_freq - chosen_freq), 6),
    }
