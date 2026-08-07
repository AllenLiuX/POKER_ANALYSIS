"""把纹理/成手/胜率/建议串起来的门面函数，供 API 判分调用。

equity 用蒙特卡洛估算：种子由 hero+board 决定，保证同一手牌多次判分结果一致
（无状态、可复现）。
"""
from __future__ import annotations

import zlib
from typing import Dict, List, Optional, Tuple

from app.poker.equity import equity_hand_vs_range
from app.poker.postflop.handstrength import classify_hand
from app.poker.postflop.heuristics import recommend_cbet, recommend_defense
from app.poker.postflop.texture import classify_board

TRIALS = 4000


def _stable_seed(hero: List[str], board: List[str]) -> int:
    return zlib.crc32(("".join(hero) + "".join(board)).encode()) & 0xFFFFFFFF


def analyze_spot(
    *,
    role: str,
    hero: List[str],
    board: List[str],
    villain_range: str,
    pot_bb: float,
    bet_bb: Optional[float],
    trials: int = TRIALS,
) -> Tuple[Dict, Dict, float, Dict]:
    texture = classify_board(board)
    hand = classify_hand(hero, board)
    eq = equity_hand_vs_range(
        hero, villain_range, board=board, trials=trials, seed=_stable_seed(hero, board)
    )
    equity = eq["equity"]
    if role == "pfr":
        rec = recommend_cbet(texture, hand, equity)
    elif role == "caller":
        if bet_bb is None:
            raise ValueError("防守场景需要 bet_bb")
        rec = recommend_defense(texture, hand, equity, pot_bb, bet_bb)
    else:
        raise ValueError("role 只能是 pfr / caller")
    return texture, hand, equity, rec
