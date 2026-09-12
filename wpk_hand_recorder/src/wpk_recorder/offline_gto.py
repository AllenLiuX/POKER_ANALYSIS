"""Transparent solver-inspired priors for offline NLHE decisions.

This module is deliberately not labelled as a solver.  It turns public solver
heuristics into deterministic priors that can later be replaced by imported
solver grids without changing the inference contract.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .poker import best_rank, hand_features


FIT_VERSION = "public-solver-fit-v6"
RANK_VALUE = {
    rank: value for value, rank in enumerate("23456789TJQKA", start=2)
}
POSTFLOP_DEFINITIONS = (
    ("strong_value", "强价值", "两对、三条及以上"),
    ("top_pair_strong", "强顶对 / 超对", "好踢脚顶对与超对"),
    ("top_pair_weak", "弱踢脚顶对", "顶对但踢脚较弱，依赖人数与牌面"),
    ("showdown", "中弱摊牌价值", "中对、底对与口袋小对"),
    ("strong_draw", "强听牌", "同花听牌、开放顺听与组合听牌"),
    ("weak_draw", "弱听牌", "卡顺与少量后门改善"),
    ("air", "空气", "高牌且缺少可靠改善"),
)

# These references document the qualitative constraints used by the fit.  No
# proprietary frequency grid is copied into this package.
PUBLIC_STRATEGY_SOURCES = (
    {
        "title": "ThinkGTO — 9-max 200bb live cash opening ranges",
        "url": "https://thinkgto.com/live",
    },
    {
        "title": "GTO Wizard — Flop Heuristics: IP C-Betting in Cash Games",
        "url": "https://blog.gtowizard.com/flop-heuristics-ip-c-betting-in-cash-games/",
    },
    {
        "title": "GTO Wizard — 10 Tips for Multiway Pots",
        "url": "https://blog.gtowizard.com/10-tips-multiway-pots-in-poker/",
    },
    {
        "title": "GTO Wizard — How to Crush Ante Cash Games",
        "url": "https://blog.gtowizard.com/how_to_crush_ante_cash_games/",
    },
    {
        "title": "GTO Wizard — Preflop Raise Sizing",
        "url": (
            "https://blog.gtowizard.com/"
            "preflop-raise-sizing-examining-2-key-factors/"
        ),
    },
    {
        "title": "Upswing Poker — Small Blind Strategy",
        "url": "https://upswingpoker.com/small-blind-poker-strategy-tips/",
    },
    {
        "title": "Upswing Poker — Playing Versus Multiple Limpers",
        "url": "https://upswingpoker.com/vs-multiple-limpers/",
    },
    {
        "title": "AHTOOOXA/poker-charts — MIT 6-max preflop charts",
        "url": "https://github.com/AHTOOOXA/poker-charts",
    },
    {
        "title": "StandUpPoker — Squid / stand-up game format notes",
        "url": "https://standuppoker.com/info",
    },
    {
        "title": "TexasSolver — open-source postflop solver",
        "url": "https://github.com/bupticybee/texassolver",
    },
)


def preflop_open_fraction(decision: Mapping[str, Any]) -> float:
    """Return a table-size/position/ante adjusted RFI target."""

    table_players = max(2, min(9, int(decision.get("table_players") or 6)))
    position = _position(decision.get("hero_position"))
    by_family = (
        {
            "BTN/SB": 0.82,
            "BTN": 0.82,
            "SB": 0.82,
            "BB": 0.75,
        }
        if table_players == 2
        else {
            "UTG": 0.20,
            "UTG+1": 0.21,
            "MP": 0.23,
            "MP+1": 0.25,
            "HJ": 0.28,
            "CO": 0.35,
            "BTN": 0.50,
            "BTN/SB": 0.48,
            "SB": 0.48,
            "BB": 0.30,
        }
        if table_players <= 4
        else {
            "UTG": 0.16,
            "UTG+1": 0.18,
            "MP": 0.20,
            "MP+1": 0.22,
            "HJ": 0.25,
            "CO": 0.32,
            "BTN": 0.45,
            "BTN/SB": 0.43,
            "SB": 0.41,
            "BB": 0.28,
        }
        if table_players <= 6
        else {
            "UTG": 0.11,
            "UTG+1": 0.13,
            "MP": 0.16,
            "MP+1": 0.18,
            "HJ": 0.22,
            "CO": 0.30,
            "BTN": 0.44,
            "BTN/SB": 0.42,
            "SB": 0.39,
            "BB": 0.26,
        }
    )
    fraction = by_family.get(position, 0.24)
    big_blind = _number(decision.get("big_blind")) or 0.0
    ante = _number(decision.get("ante")) or 0.0
    if big_blind > 0 and ante > 0:
        total_ante_bb = ante * table_players / big_blind
        fraction += min(0.08, 0.035 * total_ante_bb)
    stack_bb = _number(decision.get("hero_stack_bb"))
    if stack_bb is not None and stack_bb < 40:
        fraction -= min(0.04, (40 - stack_bb) / 500)
    return round(max(0.08, min(0.88, fraction)), 3)


def postflop_strategy(
    context: Mapping[str, Any], legal_actions: Sequence[str]
) -> Dict[str, Any]:
    """Build context-sensitive range buckets and an exact-hand bucket."""

    decision = context.get("decision") or {}
    profile = postflop_profile(context)
    hero_bucket = classify_postflop_hand(decision)
    buckets = []
    for key, label, description in POSTFLOP_DEFINITIONS:
        frequencies = _postflop_mix(key, profile, legal_actions)
        buckets.append(
            {
                "key": key,
                "label": label,
                "description": description,
                "primary_action": max(frequencies, key=frequencies.get),
                "frequencies": frequencies,
            }
        )
    return {
        "hero_bucket": hero_bucket,
        "buckets": buckets,
        "fit_profile": profile,
        "fit_version": FIT_VERSION,
        "strategy_sources": list(PUBLIC_STRATEGY_SOURCES),
    }


def postflop_profile(context: Mapping[str, Any]) -> Dict[str, Any]:
    decision = context.get("decision") or {}
    board = list(decision.get("board") or [])
    actions = list(context.get("action_history") or [])
    players = max(2, int(decision.get("players_in_hand") or 2))
    pot = max(0.0, _number(decision.get("pot")) or 0.0)
    effective_stack = _effective_stack(decision)
    texture = board_texture(board)
    position_class = _postflop_position(decision)
    preflop = [
        action
        for action in actions
        if action.get("street") == "preflop" and action.get("action")
    ]
    raises = [
        action
        for action in preflop
        if action.get("action") in {"raise", "all_in"}
    ]
    if len(raises) >= 3:
        pot_type = "four_bet"
    elif len(raises) == 2:
        pot_type = "three_bet"
    elif len(raises) == 1:
        pot_type = "single_raised"
    else:
        pot_type = "limped"
    acting_seat = decision.get("acting_seat")
    last_raiser = raises[-1] if raises else None
    hero_aggressor = bool(
        last_raiser
        and (
            last_raiser.get("seat") == acting_seat
            or _position(last_raiser.get("position"))
            == _position(decision.get("hero_position"))
        )
    )
    call = max(0.0, _number(decision.get("call_score")) or 0.0)
    required = call / (pot + call) if call > 0 and pot >= 0 else 0.0
    return {
        "players_in_hand": players,
        "opponents": players - 1,
        "table_players": int(decision.get("table_players") or players),
        "street": str(decision.get("street") or "flop"),
        "position": position_class,
        "in_position": position_class == "ip",
        "pot_type": pot_type,
        "hero_is_preflop_aggressor": hero_aggressor,
        "facing_bet": call > 0 and "call" in legal_actions_from(decision),
        "call_amount": round(call, 2),
        "call_to_pot_ratio": round(call / pot, 4) if pot > 0 else None,
        "required_equity": round(required, 4),
        "price_bucket": _price_bucket(required),
        "spr": round(effective_stack / pot, 2) if pot > 0 else None,
        "texture": texture,
        "profile_id": (
            f"{'hu' if players == 2 else 'multiway'}-{pot_type}-"
            f"{position_class}-{str(decision.get('street') or 'flop')}"
        ),
    }


def classify_postflop_hand(decision: Mapping[str, Any]) -> Optional[str]:
    hole = list(decision.get("hero_cards") or [])
    board = list(decision.get("board") or [])
    if len(hole) != 2 or len(board) < 3:
        return None
    try:
        features = hand_features(hole, board)
    except (TypeError, ValueError):
        return None
    category_rank = int(features.get("category_rank") or 0)
    if _board_dominated(hole, board, features):
        return "showdown"
    if category_rank >= 2:
        return "strong_value"
    flush_draw, open_ended, gutshot = _draws(hole, board)
    if category_rank == 1:
        pair = _pair_quality(hole, board)
        if pair in {"overpair", "top_pair_strong"}:
            return "top_pair_strong"
        if pair == "top_pair_weak":
            return "top_pair_weak"
        return "showdown"
    if flush_draw or open_ended:
        return "strong_draw"
    if gutshot:
        return "weak_draw"
    return "air"


def preferred_bet_fraction(
    context: Mapping[str, Any], hero_bucket: Optional[str] = None
) -> float:
    profile = postflop_profile(context)
    bucket = hero_bucket or classify_postflop_hand(context.get("decision") or {})
    texture = profile["texture"]
    if profile["players_in_hand"] > 2:
        return 0.33
    if bucket == "strong_value":
        return 0.75 if texture["wetness"] >= 0.35 else 0.5
    if bucket in {"top_pair_strong", "top_pair_weak", "showdown"}:
        return 0.5 if texture["wetness"] >= 0.28 else 0.33
    if bucket == "strong_draw":
        return 0.66
    return 0.33


def allow_postflop_all_in(
    context: Mapping[str, Any], hero_bucket: Optional[str] = None
) -> bool:
    profile = postflop_profile(context)
    bucket = hero_bucket or classify_postflop_hand(context.get("decision") or {})
    spr = profile.get("spr")
    if spr is None:
        return False
    if spr <= 1.5 and bucket in {"strong_value", "top_pair_strong", "strong_draw"}:
        return True
    return bool(spr <= 4 and bucket == "strong_value")


def board_texture(board: Sequence[str]) -> Dict[str, Any]:
    ranks = [_card_rank(card) for card in board]
    suits = [str(card)[-1:].lower() for card in board]
    rank_counts = Counter(ranks)
    suit_counts = Counter(suits)
    max_suit = max(suit_counts.values(), default=0)
    unique = set(ranks)
    expanded = set(unique)
    if 14 in expanded:
        expanded.add(1)
    straightiness = max(
        (
            len(expanded & set(range(start, start + 5)))
            for start in range(1, 11)
        ),
        default=0,
    )
    wetness = 0.0
    if max_suit == 2:
        wetness += 0.28
    elif max_suit == 3:
        wetness += 0.52
    elif max_suit >= 4:
        wetness += 0.72
    if straightiness >= 4:
        wetness += 0.38
    elif straightiness == 3:
        wetness += 0.24
    elif straightiness == 2 and len(unique) == len(board):
        wetness += 0.10
    paired = any(count >= 2 for count in rank_counts.values())
    if paired:
        wetness -= 0.08
    return {
        "wetness": round(max(0.0, min(1.0, wetness)), 3),
        "paired": paired,
        "monotone": max_suit >= 3,
        "two_tone": max_suit == 2,
        "straightiness": straightiness,
        "high_card": max(ranks, default=0),
    }


def _postflop_mix(
    bucket: str,
    profile: Mapping[str, Any],
    legal_actions: Sequence[str],
) -> Dict[str, int]:
    aggressive, passive, decline = _role_actions(legal_actions)
    if profile["facing_bet"]:
        raw = _facing_bet_mix(bucket, profile, aggressive, passive, decline)
    else:
        rate = _unchecked_aggression_rate(bucket, profile)
        raw = [(aggressive, rate), (passive, 100 - rate)]
    return _combine(raw)


def _unchecked_aggression_rate(
    bucket: str, profile: Mapping[str, Any]
) -> int:
    rates = {
        "strong_value": 86.0,
        "top_pair_strong": 78.0,
        "top_pair_weak": 58.0,
        "showdown": 24.0,
        "strong_draw": 55.0,
        "weak_draw": 31.0,
        "air": 27.0,
    }
    rate = rates[bucket]
    players = int(profile["players_in_hand"])
    texture = profile["texture"]
    wetness = float(texture["wetness"])
    position = str(profile["position"])
    pot_type = str(profile["pot_type"])
    street = str(profile["street"])
    aggressor = bool(profile["hero_is_preflop_aggressor"])

    if position == "ip":
        rate += 6 if bucket not in {"strong_value", "top_pair_strong"} else 3
    elif position == "oop":
        rate -= 8
        if not aggressor and bucket in {"showdown", "weak_draw", "air"}:
            rate -= 13
    if aggressor:
        if bucket in {"strong_value", "top_pair_strong", "strong_draw"}:
            rate += 4
        elif bucket == "air" and (
            texture["paired"] or (texture["high_card"] >= 11 and wetness < 0.35)
        ):
            rate += 18
    elif pot_type == "limped" and position == "ip":
        if bucket in {"top_pair_strong", "top_pair_weak", "strong_draw"}:
            rate += 4

    if bucket == "top_pair_weak":
        if players == 2:
            rate += 20 * wetness
            if texture["high_card"] >= 13 and wetness < 0.25:
                rate -= 20
            elif texture["high_card"] == 12 and wetness < 0.20:
                rate -= 10
        else:
            rate -= 8 * wetness
    elif bucket == "air" and wetness >= 0.45:
        rate -= 12
    elif bucket == "strong_draw" and wetness >= 0.35:
        rate += 4

    if players > 2:
        factors = {
            "strong_value": 0.82,
            "top_pair_strong": 0.72,
            "top_pair_weak": 0.52,
            "showdown": 0.50,
            "strong_draw": 0.68,
            "weak_draw": 0.45,
            "air": 0.30,
        }
        rate *= factors[bucket] ** min(2, players - 2)
        if position == "ip":
            rate += 3
    if pot_type in {"three_bet", "four_bet"}:
        if bucket in {"strong_value", "top_pair_strong"}:
            rate += 7
        elif bucket in {"weak_draw", "air"}:
            rate -= 5
    if street == "turn":
        if bucket in {"top_pair_weak", "showdown", "weak_draw", "air"}:
            rate -= 6
    elif street == "river":
        river_rates = {
            "strong_value": 78,
            "top_pair_strong": 50,
            "top_pair_weak": 24,
            "showdown": 10,
            "strong_draw": 16,
            "weak_draw": 12,
            "air": 10,
        }
        rate = river_rates[bucket]
        if players > 2:
            rate *= 0.72
    return int(round(max(2.0, min(96.0, rate))))


def _facing_bet_mix(
    bucket: str,
    profile: Mapping[str, Any],
    aggressive: str,
    passive: str,
    decline: str,
) -> List[Tuple[str, int]]:
    required = float(profile["required_equity"])
    multiway = int(profile["players_in_hand"]) > 2
    if bucket == "strong_value":
        raise_rate = 48 if multiway else 58
        return [(aggressive, raise_rate), (passive, 100 - raise_rate)]
    if bucket == "top_pair_strong":
        raise_rate = 4 if multiway else 9
        call = _interpolate_price_rate(
            required,
            ((0.0, 94), (0.25, 84), (0.40, 72), (0.55, 52), (0.70, 24)),
        )
        if multiway:
            call -= 16
        call = max(0, min(100 - raise_rate, call))
        return [
            (aggressive, raise_rate),
            (passive, call),
            (decline, 100 - raise_rate - call),
        ]
    if bucket == "top_pair_weak":
        call = _interpolate_price_rate(
            required,
            ((0.0, 94), (0.20, 76), (0.33, 60), (0.50, 32), (0.70, 8)),
        )
        if multiway:
            call -= 20
        return [(aggressive, 2), (passive, call), (decline, 98 - call)]
    if bucket == "showdown":
        call = _interpolate_price_rate(
            required,
            ((0.0, 82), (0.20, 56), (0.33, 38), (0.50, 16), (0.70, 3)),
        )
        if multiway:
            call -= 12
        return [(passive, max(6, call)), (decline, 100 - max(6, call))]
    if bucket == "strong_draw":
        raise_rate = 12 if multiway else 24
        call = _interpolate_price_rate(
            required,
            ((0.0, 88), (0.25, 62), (0.38, 44), (0.55, 18), (0.70, 4)),
        )
        call = max(0, min(100 - raise_rate, call))
        return [
            (aggressive, raise_rate),
            (passive, call),
            (decline, 100 - raise_rate - call),
        ]
    if bucket == "weak_draw":
        call = _interpolate_price_rate(
            required,
            ((0.0, 72), (0.18, 38), (0.28, 20), (0.45, 6), (0.70, 2)),
        )
        if multiway:
            call -= 6
        return [(passive, max(3, call)), (decline, 100 - max(3, call))]
    bluff_raise = 2 if multiway else 7
    return [(aggressive, bluff_raise), (decline, 100 - bluff_raise)]


def _interpolate_price_rate(
    required_equity: float,
    points: Sequence[Tuple[float, int]],
) -> int:
    """Interpolate continue frequency so every change in price is reflected."""

    required = max(0.0, min(1.0, required_equity))
    if required <= points[0][0]:
        return int(points[0][1])
    for (left_x, left_y), (right_x, right_y) in zip(points, points[1:]):
        if required <= right_x:
            span = max(1e-9, right_x - left_x)
            weight = (required - left_x) / span
            return int(round(left_y + weight * (right_y - left_y)))
    return int(points[-1][1])


def _price_bucket(required_equity: float) -> str:
    if required_equity <= 0:
        return "free"
    if required_equity <= 0.20:
        return "cheap"
    if required_equity <= 0.30:
        return "standard"
    if required_equity <= 0.42:
        return "expensive"
    return "very_expensive"


def _role_actions(legal: Sequence[str]) -> Tuple[str, str, str]:
    actions = list(dict.fromkeys(legal))
    aggressive = next(
        (
            action
            for action in ("raise", "all_in", "bet", "call", "check", "fold")
            if action in actions
        ),
        actions[0] if actions else "check",
    )
    passive = next(
        (
            action
            for action in ("call", "check", "fold", aggressive)
            if action in actions
        ),
        aggressive,
    )
    decline = next(
        (
            action
            for action in ("fold", "check", passive)
            if action in actions
        ),
        passive,
    )
    return aggressive, passive, decline


def _combine(items: Sequence[Tuple[str, int]]) -> Dict[str, int]:
    result: Dict[str, int] = {}
    for action, frequency in items:
        result[action] = result.get(action, 0) + max(0, int(frequency))
    total = sum(result.values())
    if total <= 0:
        return {"check": 100}
    normalized = {
        action: int(round(100 * value / total))
        for action, value in result.items()
        if value > 0
    }
    if normalized:
        largest = max(normalized, key=normalized.get)
        normalized[largest] += 100 - sum(normalized.values())
    return normalized


def _postflop_position(decision: Mapping[str, Any]) -> str:
    order = list(decision.get("action_order") or [])
    acting = decision.get("acting_seat")
    if acting in order and len(order) >= 2:
        index = order.index(acting)
        if index == len(order) - 1:
            return "ip"
        if index == 0:
            return "oop"
        return "middle"
    position = _position(decision.get("hero_position"))
    if position in {"BTN", "BTN/SB"}:
        return "ip"
    if position in {"SB", "BB"}:
        return "oop"
    return "unknown"


def _pair_quality(hole: Sequence[str], board: Sequence[str]) -> str:
    hole_ranks = [_card_rank(card) for card in hole]
    board_ranks = [_card_rank(card) for card in board]
    top = max(board_ranks)
    if hole_ranks[0] == hole_ranks[1]:
        return "overpair" if hole_ranks[0] > top else "showdown"
    matches = [rank for rank in hole_ranks if rank in board_ranks]
    if not matches:
        return "showdown"
    paired = max(matches)
    if paired != top:
        return "showdown"
    kickers = [rank for rank in hole_ranks if rank != paired]
    kicker = max(kickers, default=0)
    strong_floor = max(10, top - 2)
    return "top_pair_strong" if kicker >= strong_floor else "top_pair_weak"


def _board_dominated(
    hole: Sequence[str],
    board: Sequence[str],
    features: Mapping[str, Any],
) -> bool:
    """Return whether the made-hand core is already entirely on the board."""

    if len(board) < 4:
        return False
    category = str(features.get("category") or "")
    board_ranks = [_card_rank(card) for card in board]
    counts = Counter(board_ranks)
    if category == "two_pair":
        board_pairs = sorted(
            (rank for rank, count in counts.items() if count >= 2),
            reverse=True,
        )
        if len(board_pairs) < 2:
            return False
        hole_ranks = [_card_rank(card) for card in hole]
        if (
            hole_ranks[0] == hole_ranks[1]
            and hole_ranks[0] > board_pairs[1]
        ):
            return False
        return True
    if category == "trips" and max(counts.values(), default=0) >= 3:
        return True
    if category in {"straight", "flush", "straight_flush"}:
        try:
            return best_rank([*hole, *board]) == best_rank(board)
        except (TypeError, ValueError):
            return False
    if category in {"full_house", "quads"} and len(board) == 5:
        try:
            return best_rank([*hole, *board]) == best_rank(board)
        except (TypeError, ValueError):
            return False
    return False


def _draws(
    hole: Sequence[str], board: Sequence[str]
) -> Tuple[bool, bool, bool]:
    if len(board) >= 5:
        return False, False, False
    hole_suits = [str(card)[-1:].lower() for card in hole]
    all_suits = Counter(
        [*hole_suits, *(str(card)[-1:].lower() for card in board)]
    )
    flush_draw = any(
        count == 4 and suit in hole_suits for suit, count in all_suits.items()
    )
    all_ranks = {_card_rank(card) for card in [*hole, *board]}
    board_ranks = {_card_rank(card) for card in board}
    if 14 in all_ranks:
        all_ranks.add(1)
    if 14 in board_ranks:
        board_ranks.add(1)
    outs = 0
    for rank in range(1, 15):
        normalized = 14 if rank == 1 else rank
        if normalized in all_ranks:
            continue
        candidate = set(all_ranks)
        candidate.add(rank)
        board_candidate = set(board_ranks)
        board_candidate.add(rank)
        hero_completes = any(
            set(range(start, start + 5)) <= candidate for start in range(1, 11)
        )
        board_completes = any(
            set(range(start, start + 5)) <= board_candidate
            for start in range(1, 11)
        )
        if hero_completes and not board_completes:
            outs += 1
    return flush_draw, outs >= 2, outs == 1


def _effective_stack(decision: Mapping[str, Any]) -> float:
    opponents = [
        _number(player.get("effective_stack_to_hero"))
        for player in decision.get("players") or []
        if isinstance(player, Mapping)
        and player.get("active")
        and not player.get("is_hero")
    ]
    known = [value for value in opponents if value is not None]
    if known:
        return min(known)
    return max(0.0, _number(decision.get("hero_stack")) or 0.0)


def legal_actions_from(decision: Mapping[str, Any]) -> List[str]:
    return [
        str(action)
        for action in decision.get("legal_actions") or []
        if str(action)
    ]


def _position(value: Any) -> str:
    text = str(value or "").strip().upper().replace(" ", "_")
    return {
        "DEALER": "BTN",
        "BUTTON": "BTN",
        "SMALL_BLIND": "SB",
        "BIG_BLIND": "BB",
    }.get(text, text)


def _card_rank(card: Any) -> int:
    text = str(card or "").strip().upper()
    rank = text[:-1]
    if rank == "10":
        rank = "T"
    if rank not in RANK_VALUE:
        raise ValueError(f"invalid card rank: {card}")
    return RANK_VALUE[rank]


def _number(value: Any) -> Optional[float]:
    try:
        result = float(value) if value is not None else None
        return result if result is None or math.isfinite(result) else None
    except (TypeError, ValueError):
        return None
