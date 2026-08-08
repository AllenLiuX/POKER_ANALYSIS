"""范围 vs 范围优势：翻后 c-bet 决策的核心接地信号。

`docs/ALGORITHMS.md` §「决策启发式」既定用 **range advantage → c-bet 频率与尺度**，
但此前引擎只看英雄单手强度。这里补上真正的范围对抗信号：

- range_equity   —— 在真实牌面上，加注方(PFR)整段范围对跟注方(caller)整段范围的平均胜率。
                    >0.5 表示 PFR 范围占优（利于高频 c-bet）。
- nut_advantage  —— PFR 范围里的"强牌(坚果向)"占比 − caller 范围里的强牌占比。
                    >0 表示 PFR 更常握有大牌（利于大注、极化下注）。

两者都用蒙特卡洛估算：种子由 board 决定，保证同一牌面结果可复现（无状态）。
计算被有意做轻（组合预筛 + 适度 trials），既能给出方向性信号，又不拖慢答题/对战。
"""
from __future__ import annotations

import random
import zlib
from typing import Dict, List, Optional, Tuple

import eval7

from app.poker.cards import full_deck, normalize_cards
from app.poker.evaluate import to_eval7

RANK_VAL = {r: i for i, r in enumerate("23456789TJQKA", start=2)}
_VALUE_TYPES = {"Straight Flush", "Quads", "Full House", "Flush", "Straight", "Trips", "Two Pair"}


def _cid(card: "eval7.Card") -> int:
    return card.rank * 4 + card.suit


def _seed(board: List[str], tag: str) -> int:
    return zlib.crc32((tag + "|" + "".join(board)).encode()) & 0xFFFFFFFF


def _range_combos(range_str: str, dead: set) -> List[List["eval7.Card"]]:
    """范围字符串 → 不与 dead(已知牌) 冲突的具体两张组合列表。"""
    hr = eval7.HandRange(range_str)
    out: List[List["eval7.Card"]] = []
    for entry in hr.hands:
        cards = list(entry[0])
        ids = [_cid(c) for c in cards]
        if ids[0] == ids[1] or any(i in dead for i in ids):
            continue
        out.append(cards)
    return out


def _is_strong(combo: List["eval7.Card"], board_cards: List["eval7.Card"], board_top: int) -> bool:
    """坚果向判断：两对及以上，或超对/顶对（对子 rank ≥ 牌面最高张）。"""
    made = eval7.handtype(eval7.evaluate(combo + board_cards))
    if made in _VALUE_TYPES:
        return True
    if made == "Pair":
        # 找出成对的 rank：combo 或 board 中出现两次的 rank
        ranks = [c.rank + 2 for c in combo + board_cards]  # eval7 rank 0..12 → 2..14
        counts: Dict[int, int] = {}
        for r in ranks:
            counts[r] = counts.get(r, 0) + 1
        paired = [r for r, n in counts.items() if n >= 2]
        pair_rank = max(paired) if paired else 0
        return pair_rank >= board_top
    return False


def range_vs_range(
    pfr_range: str,
    caller_range: str,
    board: List[str],
    *,
    trials: int = 500,
) -> Optional[Dict[str, float]]:
    """估算 PFR 范围 vs caller 范围在给定牌面上的 range_equity 与 nut_advantage。

    Returns None 表示无法计算（范围空/牌面冲突），调用方应优雅降级。
    """
    board = normalize_cards(board)
    if not 3 <= len(board) <= 5:
        return None
    board_c = to_eval7(board)
    dead = {_cid(c) for c in board_c}
    pfr = _range_combos(pfr_range, dead)
    caller = _range_combos(caller_range, dead)
    if not pfr or not caller:
        return None

    board_ranks = [RANK_VAL[c[0]] for c in board]
    board_top = max(board_ranks)

    # ---- 坚果占比（确定性，遍历组合）----
    pfr_strong = sum(1 for c in pfr if _is_strong(c, board_c, board_top)) / len(pfr)
    caller_strong = sum(1 for c in caller if _is_strong(c, board_c, board_top)) / len(caller)

    # ---- range_equity（蒙特卡洛：随机配对 + 补全公共牌）----
    rng = random.Random(_seed(board, "rvr"))
    full = [eval7.Card(c) for c in full_deck()]
    need = 5 - len(board_c)
    wins = 0.0
    count = 0
    attempts = 0
    max_attempts = trials * 12
    while count < trials and attempts < max_attempts:
        attempts += 1
        a = rng.choice(pfr)
        b = rng.choice(caller)
        ids = {_cid(x) for x in a} | {_cid(x) for x in b} | dead
        if len(ids) != 4 + len(board_c):  # a/b/board 互相冲突
            continue
        avail = [c for c in full if _cid(c) not in ids]
        drawn = rng.sample(avail, need) if need else []
        final = board_c + drawn
        sa = eval7.evaluate(a + final)
        sb = eval7.evaluate(b + final)
        if sa > sb:
            wins += 1.0
        elif sa == sb:
            wins += 0.5
        count += 1

    if count == 0:
        return None
    range_equity = wins / count
    return {
        "range_equity": round(range_equity, 4),
        "range_advantage": round(range_equity - 0.5, 4),
        "nut_advantage": round(pfr_strong - caller_strong, 4),
        "pfr_strong": round(pfr_strong, 4),
        "caller_strong": round(caller_strong, 4),
        "samples": count,
    }


def advantage_labels(ra: Dict[str, float]) -> Tuple[str, str]:
    """把数值优势翻成中文档位标签：(范围优势档, 坚果优势档)。"""
    radv = ra.get("range_advantage", 0.0)
    nadv = ra.get("nut_advantage", 0.0)
    if radv >= 0.06:
        r = "范围占优"
    elif radv <= -0.06:
        r = "范围劣势"
    else:
        r = "范围均势"
    if nadv >= 0.06:
        n = "坚果占优"
    elif nadv <= -0.06:
        n = "坚果劣势"
    else:
        n = "坚果均势"
    return r, n
