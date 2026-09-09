from __future__ import annotations

import hashlib
import itertools
import random
from bisect import bisect_left
from collections import Counter
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


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
    if len(cards) <= 7:
        return _best_five_to_seven_rank(cards)
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
    if len(board) < 5:
        expanded = set(ranks)
        if 14 in expanded:
            expanded.add(1)
        for start in range(1, 11):
            present = len(expanded & set(range(start, start + 5)))
            if present == 4:
                missing = list(set(range(start, start + 5)) - expanded)
                straight_draw = (
                    straight_draw
                    or missing[0] in {start, start + 4}
                )
                gutshot = (
                    gutshot
                    or missing[0] not in {start, start + 4}
                )
    return {
        "category": category,
        "category_rank": rank[0],
        "rank": rank,
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
    return equity_vs_random_multiway(
        hole_cards, board, opponents=1, trials=trials
    )


def equity_vs_random_multiway(
    hole_cards: Sequence[str],
    board: Sequence[str],
    opponents: int,
    trials: int = 800,
) -> float:
    """Monte Carlo pot share against independent uniformly random hands."""

    return showdown_stats_vs_random_multiway(
        hole_cards, board, opponents, trials
    )["pot_share"]


def showdown_stats_vs_random_multiway(
    hole_cards: Sequence[str],
    board: Sequence[str],
    opponents: int,
    trials: int = 800,
) -> Dict[str, float]:
    """Return pot share and probability of receiving a pot-win award."""

    if len(hole_cards) != 2 or len(board) > 5:
        return {"pot_share": 0.0, "award_probability": 0.0}
    opponents = max(1, min(int(opponents), 9))
    known = set(hole_cards) | set(board)
    deck = [f"{rank}{suit}" for rank in RANKS for suit in SUITS if f"{rank}{suit}" not in known]
    needed = 5 - len(board)
    cards_needed = opponents * 2 + needed
    if cards_needed > len(deck):
        return {"pot_share": 0.0, "award_probability": 0.0}
    seed_text = "|".join([*sorted(hole_cards), *board, f"opponents={opponents}"])
    seed = int(hashlib.sha256(seed_text.encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    share = 0.0
    awards = 0.0
    for _ in range(max(1, int(trials))):
        drawn = rng.sample(deck, cards_needed)
        runout = [*board, *drawn[opponents * 2 :]]
        hero_rank = best_rank([*hole_cards, *runout])
        opponent_ranks = [
            best_rank([*drawn[index * 2 : index * 2 + 2], *runout])
            for index in range(opponents)
        ]
        best_opponent = max(opponent_ranks)
        if hero_rank > best_opponent:
            share += 1.0
            awards += 1.0
        elif hero_rank == best_opponent:
            tied_opponents = sum(rank == hero_rank for rank in opponent_ranks)
            share += 1.0 / (tied_opponents + 1)
            awards += 1.0
    completed = max(1, int(trials))
    return {
        "pot_share": round(share / completed, 4),
        "award_probability": round(awards / completed, 4),
    }


def equity_vs_weighted_ranges(
    hole_cards: Sequence[str],
    board: Sequence[str],
    ranges: Sequence[Mapping[str, float]],
    trials: int = 350,
) -> float:
    """Monte Carlo pot share against blocker-aware 169-class range weights."""

    return showdown_stats_vs_weighted_ranges(
        hole_cards, board, ranges, trials
    )["pot_share"]


def showdown_stats_vs_weighted_ranges(
    hole_cards: Sequence[str],
    board: Sequence[str],
    ranges: Sequence[Mapping[str, float]],
    trials: int = 350,
) -> Dict[str, float]:
    """Return blocker-aware pot share and pot-win award probability."""

    if len(hole_cards) != 2 or len(board) > 5 or not ranges:
        return {
            "pot_share": 0.0,
            "award_probability": 0.0,
            "completed_trials": 0,
        }
    known = set(hole_cards) | set(board)
    full_deck = [f"{rank}{suit}" for rank in RANKS for suit in SUITS]
    range_fingerprints = tuple(
        tuple(
            (str(key), round(max(0.0, float(value)), 6))
            for key, value in sorted(weights_by_class.items())
            if float(value) > 0
        )
        for weights_by_class in ranges
    )
    samplers = _weighted_range_samplers(range_fingerprints)
    seed_text = "|".join(
        [
            *sorted(hole_cards),
            *board,
            *(
                ",".join(f"{key}:{value:.3f}" for key, value in fingerprint)
                for fingerprint in range_fingerprints
            ),
        ]
    )
    seed = int(hashlib.sha256(seed_text.encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    share = 0.0
    awards = 0.0
    completed = 0
    for _ in range(max(1, int(trials))):
        used = set(known)
        opponent_hands = []
        for combos, cumulative in samplers:
            hand = _draw_weighted_combo(rng, combos, cumulative, used)
            if hand is None:
                opponent_hands = []
                break
            opponent_hands.append(hand)
            used.update(hand)
        if not opponent_hands:
            continue
        remaining = [card for card in full_deck if card not in used]
        needed = 5 - len(board)
        if needed > len(remaining):
            continue
        runout = [*board, *rng.sample(remaining, needed)]
        hero_rank = best_rank([*hole_cards, *runout])
        opponent_ranks = [
            best_rank([*hand, *runout]) for hand in opponent_hands
        ]
        best_opponent = max(opponent_ranks)
        if hero_rank > best_opponent:
            share += 1.0
            awards += 1.0
        elif hero_rank == best_opponent:
            share += 1.0 / (
                1 + sum(rank == hero_rank for rank in opponent_ranks)
            )
            awards += 1.0
        completed += 1
    return {
        "pot_share": round(share / completed, 4) if completed else 0.0,
        "award_probability": (
            round(awards / completed, 4) if completed else 0.0
        ),
        "completed_trials": completed,
    }


@lru_cache(maxsize=128)
def _weighted_range_samplers(
    range_fingerprints: Tuple[Tuple[Tuple[str, float], ...], ...],
) -> Tuple[Tuple[Tuple[Tuple[str, str], ...], Tuple[float, ...]], ...]:
    full_deck = [f"{rank}{suit}" for rank in RANKS for suit in SUITS]
    combo_classes = [
        ((first, second), _starting_hand_class(first, second))
        for first, second in itertools.combinations(full_deck, 2)
    ]
    samplers = []
    for fingerprint in range_fingerprints:
        weights_by_class = dict(fingerprint)
        combos = []
        cumulative = []
        total = 0.0
        for combo, hand_class in combo_classes:
            weight = weights_by_class.get(hand_class, 0.0)
            if weight <= 0:
                continue
            total += weight
            combos.append(combo)
            cumulative.append(total)
        if not combos:
            combos = [combo for combo, _ in combo_classes]
            cumulative = list(range(1, len(combos) + 1))
        samplers.append((tuple(combos), tuple(cumulative)))
    return tuple(samplers)


def _draw_weighted_combo(
    rng: random.Random,
    combos: Sequence[Tuple[str, str]],
    cumulative: Sequence[float],
    used: set,
) -> Optional[Tuple[str, str]]:
    for _ in range(40):
        index = bisect_left(cumulative, rng.random() * cumulative[-1])
        combo = combos[min(index, len(combos) - 1)]
        if combo[0] not in used and combo[1] not in used:
            return combo
    available = [
        combo for combo in combos if combo[0] not in used and combo[1] not in used
    ]
    return rng.choice(available) if available else None


def starting_hand_class(hole_cards: Sequence[str]) -> Optional[str]:
    if len(hole_cards) != 2:
        return None
    return _starting_hand_class(str(hole_cards[0]), str(hole_cards[1]))


def starting_hand_combos_by_class(
    excluded: Sequence[str] = (),
) -> Dict[str, List[Tuple[str, str]]]:
    blocked = set(excluded)
    deck = [
        f"{rank}{suit}"
        for rank in RANKS
        for suit in SUITS
        if f"{rank}{suit}" not in blocked
    ]
    result: Dict[str, List[Tuple[str, str]]] = {}
    for first, second in itertools.combinations(deck, 2):
        result.setdefault(_starting_hand_class(first, second), []).append(
            (first, second)
        )
    return result


def _starting_hand_class(first: str, second: str) -> str:
    order = "AKQJT98765432"
    first_rank, second_rank = first[:-1].upper(), second[:-1].upper()
    if first_rank == second_rank:
        return first_rank * 2
    high, low = sorted((first_rank, second_rank), key=order.index)
    return f"{high}{low}{'s' if first[-1] == second[-1] else 'o'}"


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


def _best_five_to_seven_rank(cards: Sequence[str]) -> Tuple[int, ...]:
    """Return the best five-card rank without enumerating all combinations."""

    ranks = [_rank(card) for card in cards]
    counts = Counter(ranks)
    ranks_desc = sorted(counts, reverse=True)
    suits: Dict[str, List[int]] = {}
    for card, rank in zip(cards, ranks):
        suits.setdefault(card[-1], []).append(rank)

    for suited_ranks in suits.values():
        if len(suited_ranks) >= 5:
            straight_flush = _straight_high(suited_ranks)
            if straight_flush:
                return (8, straight_flush)

    quads = [rank for rank in ranks_desc if counts[rank] == 4]
    if quads:
        quad = quads[0]
        kicker = next(rank for rank in ranks_desc if rank != quad)
        return (7, quad, kicker)

    trips = [rank for rank in ranks_desc if counts[rank] >= 3]
    if trips:
        pair = next(
            (
                rank
                for rank in ranks_desc
                if rank != trips[0] and counts[rank] >= 2
            ),
            None,
        )
        if pair is not None:
            return (6, trips[0], pair)

    flushes = [
        sorted(suited_ranks, reverse=True)[:5]
        for suited_ranks in suits.values()
        if len(suited_ranks) >= 5
    ]
    if flushes:
        return (5, *max(flushes))

    straight = _straight_high(ranks)
    if straight:
        return (4, straight)

    if trips:
        trip = trips[0]
        kickers = [rank for rank in ranks_desc if rank != trip][:2]
        return (3, trip, *kickers)

    pairs = [rank for rank in ranks_desc if counts[rank] >= 2]
    if len(pairs) >= 2:
        high_pair, low_pair = pairs[:2]
        kicker = next(
            rank
            for rank in ranks_desc
            if rank not in {high_pair, low_pair}
        )
        return (2, high_pair, low_pair, kicker)
    if pairs:
        pair = pairs[0]
        kickers = [rank for rank in ranks_desc if rank != pair][:3]
        return (1, pair, *kickers)
    return (0, *sorted(ranks, reverse=True)[:5])


def _straight_high(ranks: Iterable[int]) -> int:
    unique = set(ranks)
    if 14 in unique:
        unique.add(1)
    for high in range(14, 4, -1):
        if all(high - offset in unique for offset in range(5)):
            return high
    return 0


def _partial_rank(cards: Sequence[str]) -> Tuple[int, ...]:
    ranks = sorted((_rank(card) for card in cards), reverse=True)
    counts = Counter(ranks)
    if not counts:
        return (0,)
    most = max(counts.values())
    pairs = sum(count == 2 for count in counts.values())
    category = (
        7
        if most == 4
        else 3
        if most == 3
        else 2
        if pairs >= 2
        else 1
        if pairs == 1
        else 0
    )
    return (category, *ranks)


def _rank(card: str) -> int:
    return RANKS.index(card[:-1].upper()) + 2
