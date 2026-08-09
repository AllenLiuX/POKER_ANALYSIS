"""确定性发牌 + 对战常量。deal_seed 决定整手牌（英雄/对手/公共牌），可复现、可隐藏对手牌。"""
from __future__ import annotations

import random
from typing import Dict, List

from app.poker.cards import full_deck

# ---- HU 100bb 配置 ----
SB = 0.5
BB = 1.0
START_STACK = 100.0
OPEN_TO = 2.5           # 开池方 raise-to
THREEBET_TO = 9.0       # 防守方 3-bet raise-to（≈3.6×）
POSITIONS = ("BTN", "BB")

# 盲注与翻后行动顺序（首个行动=最小序号；序号越大越靠后=有位置）。与翻后训练器一致。
BLIND_POST = {"SB": 0.5, "BB": 1.0}
POSTFLOP_ORDER = {"SB": 0, "BB": 1, "UTG": 2, "MP": 3, "CO": 4, "BTN": 5}
POS_CN = {"UTG": "枪口", "MP": "中位", "CO": "劫位", "BTN": "按钮", "SB": "小盲", "BB": "大盲"}

# 候选对位（键名 = "{防守方}_vs_{开池方}"），与 data/ranges 的 vs_RFI 一致。
_CANDIDATE_MATCHUPS = [
    "BB_vs_UTG", "BB_vs_MP", "BB_vs_CO", "BB_vs_BTN", "BB_vs_SB",
    "SB_vs_UTG", "SB_vs_MP", "SB_vs_CO", "SB_vs_BTN",
    "BTN_vs_UTG", "BTN_vs_MP", "BTN_vs_CO",
]


def has_flat_range(vs_spot: str) -> bool:
    """该防守方对位是否有跟注(flat)范围。SB/BTN 多为 3bet-or-fold（无 flat），
    没有 flat 就没有单加注底池（SRP），不纳入对战/翻后训练对位。"""
    from app.poker.preflop.ranges import load_spot

    try:
        ps = load_spot("6max_100bb", "vs_RFI", vs_spot)
    except FileNotFoundError:
        return False
    return any(f.get("call", 0.0) > 0.0 for f in ps.frequencies.values())


# 仅保留有 flat 范围的对位（当前数据下为 BB 防守的 5 个：UTG/MP/CO/BTN/SB 开池）。
MATCHUPS = [m for m in _CANDIDATE_MATCHUPS if has_flat_range(m)]
DEFAULT_MATCHUP = "BB_vs_BTN"


def matchup_positions(matchup: str) -> tuple:
    """返回 (opener 开池方, defender 防守方)。"""
    if matchup not in MATCHUPS:
        raise ValueError(f"对位不存在：{matchup}")
    defender, opener = matchup.split("_vs_")
    return opener, defender


def matchup_label(matchup: str) -> str:
    opener, defender = matchup_positions(matchup)
    return f"{POS_CN.get(opener, opener)} 开池 vs {POS_CN.get(defender, defender)} 防守"


def oop_position(a_pos: str, b_pos: str) -> str:
    """翻后先行动（无位置）的一方：行动顺序序号较小者。"""
    return a_pos if POSTFLOP_ORDER[a_pos] <= POSTFLOP_ORDER[b_pos] else b_pos


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
