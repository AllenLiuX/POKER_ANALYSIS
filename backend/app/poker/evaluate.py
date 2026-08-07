"""手牌强度评估（基于 eval7）。"""
from __future__ import annotations

from typing import List

import eval7

from app.poker.cards import normalize_cards


def to_eval7(cards: List[str]) -> List["eval7.Card"]:
    return [eval7.Card(c) for c in normalize_cards(cards)]


def evaluate(cards: List[str]) -> int:
    """给 5~7 张牌打分，分值越大越强。"""
    ec = to_eval7(cards)
    if not 5 <= len(ec) <= 7:
        raise ValueError(f"evaluate 需要 5~7 张牌，收到 {len(ec)}")
    return eval7.evaluate(ec)


def hand_type(cards: List[str]) -> str:
    """返回牌型名称（如 'Full House'）。"""
    return eval7.handtype(evaluate(cards))
