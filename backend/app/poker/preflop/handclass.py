"""169 个起手牌类别（pair / suited / offsuit）与 13×13 网格。

约定：
- 类别字符串如 'AA'（对子）、'AKs'（同花）、'AKo'（非同花）。
- 网格 13×13：行/列均按 A,K,Q,...,2。对角线=对子，右上=同花，左下=非同花。
"""
from __future__ import annotations

from typing import Dict, List

from app.poker.cards import normalize_card

RANK_ORDER = "AKQJT98765432"  # 由高到低
_RANK_IDX = {r: i for i, r in enumerate(RANK_ORDER)}

COMBOS_PER_CLASS = {"pair": 6, "suited": 4, "offsuit": 12}


def class_kind(hand_class: str) -> str:
    if len(hand_class) == 2:
        return "pair"
    if hand_class.endswith("s"):
        return "suited"
    if hand_class.endswith("o"):
        return "offsuit"
    raise ValueError(f"非法手牌类别：{hand_class!r}")


def combos_for_class(hand_class: str) -> int:
    return COMBOS_PER_CLASS[class_kind(hand_class)]


def hand_class(card_a: str, card_b: str) -> str:
    """两张具体牌 -> 类别字符串（如 'AKs'）。"""
    a, b = normalize_card(card_a), normalize_card(card_b)
    if a == b:
        raise ValueError(f"两张牌相同：{card_a}")
    ra, sa = a[0], a[1]
    rb, sb = b[0], b[1]
    if ra == rb:
        return ra + rb  # 对子
    # 高牌在前
    if _RANK_IDX[ra] < _RANK_IDX[rb]:
        hi, lo = ra, rb
    else:
        hi, lo = rb, ra
    return hi + lo + ("s" if sa == sb else "o")


def all_classes() -> List[str]:
    """返回全部 169 个类别（按网格行优先顺序）。"""
    classes: List[str] = []
    for i, hi in enumerate(RANK_ORDER):
        for j, lo in enumerate(RANK_ORDER):
            if i == j:
                classes.append(hi + lo)
            elif i < j:
                classes.append(hi + lo + "s")
            else:
                classes.append(lo + hi + "o")  # 左下：低列高行 -> offsuit（高在前）
    return classes


def grid_cells() -> List[Dict[str, object]]:
    """13×13 网格单元（含 row/col 索引与类别），便于前端渲染。"""
    cells: List[Dict[str, object]] = []
    for i, hi in enumerate(RANK_ORDER):
        for j, lo in enumerate(RANK_ORDER):
            if i == j:
                cls = hi + lo
            elif i < j:
                cls = hi + lo + "s"
            else:
                cls = lo + hi + "o"
            cells.append({"row": i, "col": j, "hand_class": cls})
    return cells
