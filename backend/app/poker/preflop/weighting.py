"""难度加权：把出题从"纯随机"变成"偏向临界手牌"，练得更高效。

核心思路：GTO 在**决策边界**上会混合策略（同一手牌既有 raise 又有 fold 等）。
这些"临界手牌"正是最容易打错、最值得反复练的。用动作分布的**香农熵**度量一手
牌的"临界程度"（纯策略熵=0，50/50 混合熵最大），再按难度放大其被抽中的权重。

抽样权重 = combos_for_class(自然组合数) × importance(难度, 熵)
  - combos 因子保证 easy 档等价于"自然随机发牌"（对子本就比同花/非同花少）。
  - importance 在 standard/hard 档进一步抬高混合/边界手牌的出现概率。
"""
from __future__ import annotations

import math
import random
from typing import Dict, List

from app.poker.preflop.handclass import all_classes, combos_for_class
from app.poker.preflop.ranges import PreflopSpot

MIN_MIX = 0.10  # 与 scoring.MIN_MIX 对齐：某动作频率≥此值视为"可观"
DIFFICULTIES = ("easy", "standard", "hard")


def action_entropy(freqs: Dict[str, float]) -> float:
    """动作分布的香农熵（bits）。纯策略=0，二元 50/50=1，越大越"临界"。"""
    h = 0.0
    for p in freqs.values():
        if p > 0.0:
            h -= p * math.log2(p)
    return h


def is_critical(freqs: Dict[str, float]) -> bool:
    """是否为临界（混合）手牌：至少两个动作达到可观频率。"""
    return sum(1 for f in freqs.values() if f >= MIN_MIX) >= 2


def _importance(freqs: Dict[str, float], difficulty: str) -> float:
    """把熵映射成一手牌的重要性乘子（>0）。"""
    h = action_entropy(freqs)
    if difficulty == "easy":
        return 1.0  # 不加权，等价于自然随机
    if difficulty == "hard":
        return 0.02 + 4.0 * h  # 强烈聚焦于混合/边界手牌
    # standard：温和抬高临界手牌，纯手牌仍保留基础出现率
    return 0.20 + 1.4 * h


def class_weights(ps: PreflopSpot, difficulty: str = "standard") -> Dict[str, float]:
    """给定 spot 与难度，返回 169 个类别的抽样权重。"""
    if difficulty not in DIFFICULTIES:
        raise ValueError(f"未知难度 {difficulty!r}，可选：{DIFFICULTIES}")
    weights: Dict[str, float] = {}
    for cls in all_classes():
        freqs = ps.frequencies.get(cls, {"fold": 1.0})
        weights[cls] = combos_for_class(cls) * _importance(freqs, difficulty)
    return weights


def sample_class(ps: PreflopSpot, difficulty: str, rng: random.Random) -> str:
    """按难度加权抽一个手牌类别。"""
    weights = class_weights(ps, difficulty)
    classes: List[str] = list(weights.keys())
    return rng.choices(classes, weights=[weights[c] for c in classes], k=1)[0]
