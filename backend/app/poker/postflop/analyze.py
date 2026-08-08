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
from app.poker.postflop.range_advantage import range_vs_range
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
    hero_range: Optional[str] = None,
    trials: int = TRIALS,
    ra_trials: int = 500,
) -> Tuple[Dict, Dict, float, Dict]:
    """翻后门面：纹理 + 成手 + 英雄胜率 + 决策建议。

    当同时给出 hero_range（英雄整段范围）时，额外计算"范围 vs 范围优势"并接入建议，
    让 c-bet 频率/尺度更接地；不给则退化为纯手牌力启发式（向后兼容）。
    """
    texture = classify_board(board)
    hand = classify_hand(hero, board)
    eq = equity_hand_vs_range(
        hero, villain_range, board=board, trials=trials, seed=_stable_seed(hero, board)
    )
    equity = eq["equity"]

    # 范围优势（两边范围齐全才算；PFR=加注方范围，caller=跟注方范围）
    ra: Optional[Dict] = None
    if hero_range:
        if role == "pfr":
            ra = range_vs_range(hero_range, villain_range, board, trials=ra_trials)
        elif role == "caller":
            ra = range_vs_range(villain_range, hero_range, board, trials=ra_trials)

    if role == "pfr":
        rec = recommend_cbet(texture, hand, equity, ra)
    elif role == "caller":
        if bet_bb is None:
            raise ValueError("防守场景需要 bet_bb")
        rec = recommend_defense(texture, hand, equity, pot_bb, bet_bb, ra)
    else:
        raise ValueError("role 只能是 pfr / caller")
    return texture, hand, equity, rec
