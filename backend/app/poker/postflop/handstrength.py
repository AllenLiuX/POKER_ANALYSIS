"""英雄成手强度分级 + 听牌识别（借助 eval7）。

分级 tier：
  value    —— 两对及以上，或强顶对/超对（可下注取价值）
  medium   —— 中等成手（次顶对、弱顶对、口袋对低于顶张），有摊牌价值
  draw     —— 无成对但有强听牌（同花听牌 / 两头顺，可半诈唬）
  weak     —— 仅卡顺等弱听牌
  air      —— 无成对无有效听牌
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, List

import eval7

from app.poker.cards import normalize_cards

RANK_VAL = {r: i for i, r in enumerate("23456789TJQKA", start=2)}
_SUITS = "cdhs"


def _board_dominated(made: str, hero_ranks: List[int], board_ranks: List[int]) -> bool:
    """英雄的成对类成手是否**完全由公共牌构成**（底牌没参与关键配对）。

    例：牌面 KK77Q，英雄 J5 —— "两对(KK77)"整段来自公共牌，英雄一张都没用上，
    实际只能与人平分、打不过任何 Kx/7x/Qx。这类必须降档，不能当价值牌下注。
    做法：取"英雄+公共牌"组合里构成该成手的关键 rank，若这些 rank 单靠公共牌就已达到所需数量，
    说明底牌没贡献。
    """
    bcount = Counter(board_ranks)
    ccount = Counter(hero_ranks + board_ranks)
    if made == "Pair":
        pr = max((r for r, c in ccount.items() if c >= 2), default=None)
        return pr is not None and bcount.get(pr, 0) >= 2
    if made == "Two Pair":
        pairs = sorted((r for r, c in ccount.items() if c >= 2), reverse=True)[:2]
        return len(pairs) == 2 and all(bcount.get(r, 0) >= 2 for r in pairs)
    if made == "Trips":
        tr = max((r for r, c in ccount.items() if c >= 3), default=None)
        return tr is not None and bcount.get(tr, 0) >= 3
    if made == "Quads":
        q = max((r for r, c in ccount.items() if c >= 4), default=None)
        return q is not None and bcount.get(q, 0) >= 4
    if made == "Full House":
        tr = max((r for r, c in ccount.items() if c >= 3), default=None)
        pair = max((r for r, c in ccount.items() if c >= 2 and r != tr), default=None)
        return (
            tr is not None and pair is not None
            and bcount.get(tr, 0) >= 3 and bcount.get(pair, 0) >= 2
        )
    return False


def _straight_outs(ranks: set[int]) -> int:
    """给定一组 rank，返回能补成 5 连的不同"补牌 rank"数量（近似顺子听牌 outs 的 rank 数）。"""
    have = set(ranks)
    if 14 in have:
        have.add(1)
    outs = 0
    for r in range(1, 15):
        if r in have:
            continue
        cand = have | {r}
        # 是否存在包含 r 的 5 连
        for low in range(max(1, r - 4), min(10, r) + 1):
            if set(range(low, low + 5)) <= cand:
                outs += 1
                break
    return outs


def classify_hand(hero: List[str], board: List[str]) -> Dict[str, object]:
    hero = normalize_cards(hero)
    board = normalize_cards(board)
    hc = [eval7.Card(c) for c in hero]
    bc = [eval7.Card(c) for c in board]
    made = eval7.handtype(eval7.evaluate(hc + bc))  # 'Pair'/'Two Pair'/...

    hero_ranks = [RANK_VAL[c[0]] for c in hero]
    board_ranks = sorted((RANK_VAL[c[0]] for c in board), reverse=True)
    hero_suits = [c[1] for c in hero]
    board_suits = [c[1] for c in board]
    top_board = board_ranks[0]

    # ---- 听牌 ----
    # 河牌（5 张公共牌）已无后续发牌，不存在听牌；仅在翻牌/转牌识别听牌。
    draws: List[str] = []
    outs = 0
    if len(board) < 5:
        # 同花听牌：某花色在 hero+board 恰好 4 张且 hero 有贡献（未成花）
        if made not in ("Flush", "Straight Flush"):
            suit_all = Counter(hero_suits + board_suits)
            for s, n in suit_all.items():
                if n == 4 and hero_suits.count(s) >= 1:
                    draws.append("flush_draw")
                    outs += 9
                    break
        # 顺子听牌
        if made not in ("Straight", "Straight Flush"):
            so = _straight_outs(set(hero_ranks) | set(board_ranks))
            # 扣掉纯靠公共牌就已存在的顺听（只在 hero 参与时才算英雄的听牌）
            so_board_only = _straight_outs(set(board_ranks))
            hero_so = max(0, so - so_board_only)
            if hero_so >= 2:
                draws.append("oesd")
                outs += 8
            elif hero_so == 1:
                draws.append("gutshot")
                outs += 4

    combo = len(draws) >= 2

    # ---- 成手分级 ----
    made_label = {
        "Straight Flush": "同花顺",
        "Quads": "四条",
        "Full House": "葫芦",
        "Flush": "同花",
        "Straight": "顺子",
        "Trips": "三条",
        "Two Pair": "两对",
        "Pair": "一对",
        "High Card": "高牌",
    }.get(made, made)

    # 关键校正：英雄的成手是否"完全由公共牌构成"（含河牌打公共牌）。
    # 是的话不能当作英雄的价值牌——最多平分，打不过任何用到公共牌配对的手。
    board_dominated = _board_dominated(made, hero_ranks, board_ranks)
    if len(board) == 5 and eval7.evaluate(hc + bc) == eval7.evaluate(bc):
        board_dominated = True  # 河牌：英雄最佳五张就是公共牌本身（打公共牌）

    value_made = made in (
        "Straight Flush", "Quads", "Full House", "Flush", "Straight", "Trips", "Two Pair"
    )
    if value_made and not board_dominated:
        tier = "value"
        pair_kind = None
    elif made == "Pair" and not board_dominated:
        pair_kind, tier = _classify_pair(hero_ranks, board_ranks, top_board)
    else:  # 高牌，或成手完全来自公共牌 → 按听牌定档（否则空气）
        pair_kind = None
        if "flush_draw" in draws or "oesd" in draws:
            tier = "draw"
        elif "gutshot" in draws:
            tier = "weak"
        else:
            tier = "air"

    if board_dominated and value_made:
        made_label = f"{made_label}（公共牌构成·仅摊牌无价值）"

    # 有强听牌可提升边缘成手的"进攻性"（半诈唬价值）
    if tier in ("medium",) and combo:
        tier = "value"

    draw_label = _draw_label(draws)
    return {
        "made": made,
        "made_label": made_label,
        "pair_kind": pair_kind,
        "tier": tier,
        "draws": draws,
        "draw_label": draw_label,
        "outs": outs,
        "combo_draw": combo,
        "board_dominated": board_dominated,
    }


def _classify_pair(hero_ranks, board_ranks, top_board):
    """区分对子类型并给分级。返回 (pair_kind, tier)。"""
    hero_pocket = hero_ranks[0] == hero_ranks[1]
    if hero_pocket:
        pr = hero_ranks[0]
        if pr > top_board:
            return "overpair", "value"  # 超对
        return "underpair", "medium"  # 低于顶张的口袋对
    # 英雄某张与公共牌配对
    matched = [r for r in hero_ranks if r in board_ranks]
    pr = max(matched) if matched else 0
    kicker = max(r for r in hero_ranks if r != pr) if any(r != pr for r in hero_ranks) else 0
    if pr == top_board:
        # 顶对：看踢脚
        if kicker >= RANK_VAL["T"]:
            return "top_pair", "value"
        return "top_pair_weak", "medium"
    second_board = board_ranks[1] if len(board_ranks) > 1 else 0
    if pr == second_board:
        return "second_pair", "medium"
    return "low_pair", "medium"


def _draw_label(draws: List[str]) -> str:
    m = {"flush_draw": "同花听牌", "oesd": "两头顺听", "gutshot": "卡顺听"}
    return "+".join(m[d] for d in draws) if draws else ""
