"""确定性发牌 + 对战常量。deal_seed 决定整手牌（英雄/对手/公共牌），可复现、可隐藏对手牌。"""
from __future__ import annotations

import random
from typing import Dict, List

from app.poker.cards import full_deck

# ---- HU 100bb 配置 ----
SB = 0.5
BB = 1.0
START_STACK = 100.0
OPEN_TO = 2.5           # BTN 开池 raise-to
THREEBET_TO = 9.0       # BB 3-bet raise-to（≈3.6×）
POSITIONS = ("BTN", "BB")


def deal(deal_seed: int) -> Dict[str, object]:
    """按 seed 洗牌并切出各家底牌与 5 张公共牌。"""
    deck = full_deck()
    random.Random(deal_seed).shuffle(deck)
    return {
        "hero": [deck[0], deck[1]],
        "villain": [deck[2], deck[3]],
        "flop": [deck[4], deck[5], deck[6]],
        "turn": deck[7],
        "river": deck[8],
    }


def other(pos: str) -> str:
    return "BB" if pos == "BTN" else "BTN"


def full_board(d: Dict[str, object]) -> List[str]:
    return list(d["flop"]) + [str(d["turn"]), str(d["river"])]  # type: ignore[index]
