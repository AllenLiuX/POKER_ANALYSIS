"""牌的解析与校验（标准 2 字符表示，如 'As' 'Td' '7h'）。"""
from __future__ import annotations

from typing import List

RANKS = "23456789TJQKA"
SUITS = "cdhs"

_RANK_ALIASES = {"10": "T"}


def normalize_card(card: str) -> str:
    """把单张牌统一成 2 字符形式（rank 大写、suit 小写）。非法则抛 ValueError。"""
    if not isinstance(card, str):
        raise ValueError(f"card 必须是字符串，收到 {type(card).__name__}")
    c = card.strip()
    if len(c) == 3 and c[:2] in _RANK_ALIASES:  # '10s'
        c = _RANK_ALIASES[c[:2]] + c[2]
    if len(c) != 2:
        raise ValueError(f"非法牌：{card!r}（应为 2 字符，如 'As'）")
    rank, suit = c[0].upper(), c[1].lower()
    if rank not in RANKS:
        raise ValueError(f"非法点数：{card!r}")
    if suit not in SUITS:
        raise ValueError(f"非法花色：{card!r}")
    return rank + suit


def normalize_cards(cards: List[str]) -> List[str]:
    out = [normalize_card(c) for c in cards]
    if len(set(out)) != len(out):
        raise ValueError(f"存在重复牌：{cards}")
    return out


def full_deck() -> List[str]:
    return [r + s for r in RANKS for s in SUITS]


def assert_disjoint(*groups: List[str]) -> None:
    """确保多组牌之间没有重复（如 hero / board / villain 不能冲突）。"""
    seen: set = set()
    for g in groups:
        for c in g:
            if c in seen:
                raise ValueError(f"牌冲突：{c} 出现在多处")
            seen.add(c)
