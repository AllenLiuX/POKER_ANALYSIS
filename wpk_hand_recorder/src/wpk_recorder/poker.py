from __future__ import annotations

import hashlib
import itertools
import random
from collections import Counter
from typing import Any, Dict, Iterable, List, Sequence, Tuple


RANKS = "23456789TJQKA"
SUITS = "shcd"
CATEGORY_NAMES = (
    "high_card",
    "pair",
    "two_pair",
    "trips",
    "straight",
    "flush",
    "full_house",
    "quads",
    "straight_flush",
)


def best_rank(cards: Sequence[str]) -> Tuple[int, ...]:
    if len(cards) < 5:
        return _partial_rank(cards)
    return max(_five_card_rank(combo) for combo in itertools.combinations(cards, 5))


def hand_features(hole_cards: Sequence[str], board: Sequence[str]) -> Dict[str, Any]:
    cards = [*hole_cards, *board]
    rank = best_rank(cards)
    category = CATEGORY_NAMES[rank[0]]
    ranks = {_rank(card) for card in cards}
    suit_counts = Counter(card[-1] for card in cards)
    flush_draw = len(board) < 5 and max(suit_counts.values(), default=0) == 4
    straight_draw = False
    gutshot = False
    expanded = set(ranks)
    if 14 in expanded:
        expanded.add(1)
    for start in range(1, 11):
        present = len(expanded & set(range(start, start + 5)))
        if present == 4:
            missing = list(set(range(start, start + 5)) - expanded)
            straight_draw = straight_draw or missing[0] in {start, start + 4}
            gutshot = gutshot or missing[0] not in {start, start + 4}
    return {
        "category": category,
        "category_rank": rank[0],
        "flush_draw": flush_draw,
        "open_ended_draw": straight_draw,
        "gutshot": gutshot,
        "preflop_class": preflop_class(hole_cards),
    }


def preflop_class(hole_cards: Sequence[str]) -> str:
    if len(hole_cards) != 2:
        return "unknown"
    first, second = hole_cards
    ranks = sorted((_rank(first), _rank(second)), reverse=True)
    pair = ranks[0] == ranks[1]
    suited = first[-1] == second[-1]
    gap = ranks[0] - ranks[1]
    if pair and ranks[0] >= 11:
        return "premium_pair"
    if pair:
        return "pair"
    if ranks[0] == 14 and ranks[1] >= 10:
        return "strong_ace"
    if ranks[0] >= 11 and ranks[1] >= 10:
        return "broadway"
    if suited and gap <= 2:
        return "suited_connector"
    if ranks[0] == 14:
        return "ace_x"
    if suited:
        return "suited"
    return "other"


def equity_vs_random(
    hole_cards: Sequence[str],
    board: Sequence[str],
    trials: int = 600,
) -> float:
    if len(hole_cards) != 2 or len(board) > 5:
        return 0.0
    known = set(hole_cards) | set(board)
    deck = [f"{rank}{suit}" for rank in RANKS for suit in SUITS if f"{rank}{suit}" not in known]
    needed = 5 - len(board)
    seed_text = "|".join([*sorted(hole_cards), *board])
    seed = int(hashlib.sha256(seed_text.encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    wins = ties = 0
    for _ in range(trials):
        drawn = rng.sample(deck, 2 + needed)
        opponent = drawn[:2]
        runout = [*board, *drawn[2:]]
        hero_rank = best_rank([*hole_cards, *runout])
        opponent_rank = best_rank([*opponent, *runout])
        if hero_rank > opponent_rank:
            wins += 1
        elif hero_rank == opponent_rank:
            ties += 1
    return round((wins + ties / 2) / trials, 4)


def _five_card_rank(cards: Iterable[str]) -> Tuple[int, ...]:
    cards = list(cards)
    ranks = sorted((_rank(card) for card in cards), reverse=True)
    counts = Counter(ranks)
    groups = sorted(((count, rank) for rank, count in counts.items()), reverse=True)
    flush = len({card[-1] for card in cards}) == 1
    unique = sorted(set(ranks), reverse=True)
    if unique == [14, 5, 4, 3, 2]:
        straight_high = 5
    else:
        straight_high = unique[0] if len(unique) == 5 and unique[0] - unique[-1] == 4 else 0
    if flush and straight_high:
        return (8, straight_high)
    if groups[0][0] == 4:
        return (7, groups[0][1], groups[1][1])
    if groups[0][0] == 3 and groups[1][0] == 2:
        return (6, groups[0][1], groups[1][1])
    if flush:
        return (5, *ranks)
    if straight_high:
        return (4, straight_high)
    if groups[0][0] == 3:
        kickers = sorted((rank for rank in ranks if rank != groups[0][1]), reverse=True)
        return (3, groups[0][1], *kickers)
    pairs = sorted((rank for count, rank in groups if count == 2), reverse=True)
    if len(pairs) == 2:
        kicker = max(rank for rank in ranks if rank not in pairs)
        return (2, *pairs, kicker)
    if len(pairs) == 1:
        kickers = sorted((rank for rank in ranks if rank != pairs[0]), reverse=True)
        return (1, pairs[0], *kickers)
    return (0, *ranks)


def _partial_rank(cards: Sequence[str]) -> Tuple[int, ...]:
    ranks = sorted((_rank(card) for card in cards), reverse=True)
    counts = Counter(ranks)
    if not counts:
        return (0,)
    most = max(counts.values())
    category = 3 if most == 3 else 1 if most == 2 else 0
    return (category, *ranks)


def _rank(card: str) -> int:
    return RANKS.index(card[:-1].upper()) + 2
