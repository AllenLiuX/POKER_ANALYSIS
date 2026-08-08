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

    if made in ("Straight Flush", "Quads", "Full House", "Flush", "Straight", "Trips", "Two Pair"):
        tier = "value"
        pair_kind = None
    elif made == "Pair":
        pair_kind, tier = _classify_pair(hero_ranks, board_ranks, top_board)
    else:  # High Card
        pair_kind = None
        if "flush_draw" in draws or "oesd" in draws:
            tier = "draw"
        elif "gutshot" in draws:
            tier = "weak"
        else:
            tier = "air"

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
