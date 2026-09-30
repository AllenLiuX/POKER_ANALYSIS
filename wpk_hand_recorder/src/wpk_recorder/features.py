from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

from .models import Action, HandHistory
from .poker import hand_features


AGGRESSIVE = {"bet", "raise", "all_in"}
VOLUNTARY = AGGRESSIVE | {"call"}
BLINDS = {"small_blind", "big_blind", "ante", "straddle"}
STREETS_ORDER = {"preflop": 0, "flop": 1, "turn": 2, "river": 3}
STREET_CARD_COUNT = {"preflop": 0, "flop": 3, "turn": 4, "river": 5}
TWO_PAIR_PLUS = {
    "two_pair",
    "trips",
    "straight",
    "flush",
    "full_house",
    "quads",
    "straight_flush",
}
COMPLETED_DRAW = {"straight", "flush", "straight_flush", "full_house", "quads"}


def stack_bucket(value: Any) -> str:
    """Shared effective-stack taxonomy for statistics and range features."""

    try:
        stack_bb = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if stack_bb < 20:
        return "short"
    if stack_bb < 60:
        return "medium"
    if stack_bb < 150:
        return "deep"
    return "very_deep"


@dataclass
class Opportunity:
    metric: str
    success: bool


@dataclass
class DecisionSnapshot:
    hand_id: str
    action_sequence: int
    event_sequence: Optional[int]
    user_id: Optional[str]
    alias: Optional[str]
    seat: Optional[int]
    street: str
    position: Optional[str]
    game_mode: str
    chosen_action: str
    pot_before: float
    stack_before: Optional[float]
    effective_stack: Optional[float]
    effective_stack_bb: Optional[float]
    spr: Optional[float]
    to_call: float
    facing_action: str
    facing_amount: float
    bet_fraction: Optional[float]
    raise_multiple: Optional[float]
    is_ip: Optional[bool]
    is_preflop_aggressor: bool
    players_in_hand: int
    board_texture: Dict[str, Any] = field(default_factory=dict)
    opportunities: List[Opportunity] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["opportunities"] = [asdict(item) for item in self.opportunities]
        return data


@dataclass
class ProjectedHandState:
    street: str
    reconstructed_pot: float
    street_contributions: Dict[int, float]
    total_contributions: Dict[int, float]
    stacks: Dict[int, float]
    active_seats: Set[int]
    folded_seats: Set[int]
    preflop_aggressor: Optional[int]
    side_pots: List[Dict[str, Any]]


def project_hand_state(
    hand: HandHistory,
    before_action_sequence: Optional[int] = None,
) -> ProjectedHandState:
    """Pure action replay used by both live decisions and historical snapshots."""

    stacks = {
        seat: float(player.stack_start)
        for seat, player in hand.players.items()
        if player.stack_start is not None
    }
    active = set(hand.players)
    folded: Set[int] = set()
    street_contributions: Dict[int, float] = {}
    total_contributions: Dict[int, float] = {}
    reconstructed_pot = 0.0
    current_street = ""
    preflop_aggressor = None
    for action in sorted(hand.actions, key=lambda item: item.sequence):
        if (
            before_action_sequence is not None
            and action.sequence >= before_action_sequence
        ):
            break
        if action.street != current_street:
            current_street = action.street
            street_contributions = {}
        seat = action.seat
        amount = max(0.0, float(action.amount or 0.0))
        if seat is not None:
            previous = street_contributions.get(seat, 0.0)
            street_contributions[seat] = (
                float(action.amount_to)
                if action.amount_to is not None
                else previous + amount
            )
            total_contributions[seat] = (
                total_contributions.get(seat, 0.0) + amount
            )
            if action.stack_after is not None:
                stacks[seat] = float(action.stack_after)
            elif seat in stacks:
                stacks[seat] = max(0.0, stacks[seat] - amount)
            kind = _action(action.action)
            if kind == "fold":
                active.discard(seat)
                folded.add(seat)
            if action.street == "preflop" and kind in AGGRESSIVE:
                preflop_aggressor = seat
        reconstructed_pot += amount
    street = current_street or _street_for_board(hand.board)
    if before_action_sequence is None:
        board_street = _street_for_board(hand.board)
        if STREETS_ORDER.get(board_street, 0) > STREETS_ORDER.get(street, 0):
            street = board_street
            street_contributions = {}
    return ProjectedHandState(
        street=street,
        reconstructed_pot=reconstructed_pot,
        street_contributions=street_contributions,
        total_contributions=total_contributions,
        stacks=stacks,
        active_seats=active,
        folded_seats=folded,
        preflop_aggressor=preflop_aggressor,
        side_pots=_project_side_pots(total_contributions, active),
    )


def derive_decisions(hand: HandHistory) -> List[DecisionSnapshot]:
    positions = derive_positions(hand)
    for seat, position in positions.items():
        if seat in hand.players and (
            hand.button_seat in hand.players
            or not (hand.players[seat].position or "").strip()
        ):
            hand.players[seat].position = position

    stacks = {
        seat: player.stack_start
        for seat, player in hand.players.items()
        if player.stack_start is not None
    }
    active: Set[int] = set(hand.players)
    contributions: Dict[int, float] = {}
    pot = 0.0
    current_street = ""
    street_actions: List[Action] = []
    street_raises = 0
    first_raiser: Optional[int] = None
    preflop_aggressor: Optional[int] = None
    checked: Set[int] = set()
    street_history: Dict[str, List[Action]] = {}
    snapshots: List[DecisionSnapshot] = []

    for action in sorted(hand.actions, key=lambda item: item.sequence):
        projected = project_hand_state(
            hand, before_action_sequence=action.sequence
        )
        if action.street != current_street:
            if current_street:
                street_history[current_street] = list(street_actions)
            current_street = action.street
            contributions = {}
            street_actions = []
            street_raises = 0
            checked = set()
        contributions = dict(projected.street_contributions)
        stacks = dict(projected.stacks)
        active = set(projected.active_seats)
        pot = projected.reconstructed_pot
        preflop_aggressor = projected.preflop_aggressor
        kind = _action(action.action)
        seat = action.seat
        contribution = contributions.get(seat, 0.0) if seat is not None else 0.0
        max_contribution = max(contributions.values(), default=0.0)
        to_call = max(0.0, max_contribution - contribution)
        previous = street_actions[-1] if street_actions else None
        previous_kind = _action(previous.action) if previous else "none"
        stack_before = stacks.get(seat) if seat is not None else None
        if stack_before is None and action.stack_after is not None:
            stack_before = action.stack_after + (action.amount or 0.0)
        opponent_stacks = [
            value for other, value in stacks.items() if other != seat and other in active
        ]
        effective = (
            min(stack_before, max(opponent_stacks))
            if stack_before is not None and opponent_stacks
            else stack_before
        )
        amount = float(action.amount or 0.0)
        bet_fraction = amount / pot if amount > 0 and pot > 0 else None
        raise_multiple = (
            float(action.amount_to) / max_contribution
            if kind in {"raise", "all_in"} and action.amount_to and max_contribution > 0
            else None
        )
        is_ip = _is_in_position(seat, active, hand.button_seat)
        player = hand.players.get(seat) if seat is not None else None
        hole_cards = list(player.hole_cards) if player else []
        opportunities = _opportunities(
            action=action,
            kind=kind,
            street_actions=street_actions,
            street_raises=street_raises,
            first_raiser=first_raiser,
            preflop_aggressor=preflop_aggressor,
            checked=checked,
            to_call=to_call,
            street_history=street_history,
            pot_before=pot,
            spr=effective / pot if effective is not None and pot > 0 else None,
            big_blind=hand.big_blind,
            is_ip=is_ip,
            players_in_hand=len(active),
            hole_cards=hole_cards,
            board=hand.board,
        )
        snapshots.append(
            DecisionSnapshot(
                hand_id=hand.hand_id,
                action_sequence=action.sequence,
                event_sequence=action.event_sequence,
                user_id=action.user_id,
                alias=action.player,
                seat=seat,
                street=action.street,
                position=positions.get(seat) if seat is not None else None,
                game_mode=hand.game_mode,
                chosen_action=kind,
                pot_before=pot,
                stack_before=stack_before,
                effective_stack=effective,
                effective_stack_bb=(
                    effective / hand.big_blind if effective is not None and hand.big_blind else None
                ),
                spr=effective / pot if effective is not None and pot > 0 else None,
                to_call=to_call,
                facing_action=previous_kind,
                facing_amount=max_contribution,
                bet_fraction=bet_fraction,
                raise_multiple=raise_multiple,
                is_ip=is_ip,
                is_preflop_aggressor=seat is not None and seat == preflop_aggressor,
                players_in_hand=len(active),
                board_texture=board_texture(hand.board, action.street),
                opportunities=opportunities,
            )
        )

        if seat is not None:
            contributions[seat] = (
                float(action.amount_to)
                if action.amount_to is not None
                else contribution + amount
            )
            if stack_before is not None:
                stacks[seat] = (
                    action.stack_after
                    if action.stack_after is not None
                    else max(0.0, stack_before - amount)
                )
            if kind == "fold":
                active.discard(seat)
            elif kind == "check":
                checked.add(seat)
        if action.street == "preflop" and kind in {"raise", "all_in"}:
            street_raises += 1
            preflop_aggressor = seat
            if first_raiser is None:
                first_raiser = seat
        elif action.street != "preflop" and kind in AGGRESSIVE:
            street_raises += 1
        pot += amount
        street_actions.append(action)
    _append_showdown_hand_opportunities(hand, snapshots)
    return snapshots


def derive_positions(hand: HandHistory) -> Dict[int, str]:
    seats = sorted(hand.players)
    if not seats:
        return {}
    if hand.button_seat not in seats:
        return {
            seat: hand.players[seat].position or f"Seat {seat}"
            for seat in seats
        }
    button_index = seats.index(hand.button_seat)
    ordered = seats[button_index + 1 :] + seats[: button_index + 1]
    count = len(ordered)
    if count == 2:
        return {ordered[0]: "BB", ordered[1]: "BTN/SB"}
    middle = {
        3: [],
        4: ["UTG"],
        5: ["UTG", "CO"],
        6: ["UTG", "HJ", "CO"],
        7: ["UTG", "MP", "HJ", "CO"],
        8: ["UTG", "UTG+1", "MP", "HJ", "CO"],
        9: ["UTG", "UTG+1", "MP", "MP+1", "HJ", "CO"],
    }.get(count)
    if middle is None:
        middle = [f"EP+{index}" for index in range(max(0, count - 3))]
    labels = ["SB", "BB", *middle, "BTN"]
    return {seat: labels[index] for index, seat in enumerate(ordered)}


def board_texture(board: List[str], street: str) -> Dict[str, Any]:
    count = STREET_CARD_COUNT.get(street, len(board))
    cards = board[:count]
    if not cards:
        return {}
    ranks = [_rank_value(card[:-1]) for card in cards]
    suits = [card[-1] for card in cards]
    unique_ranks = len(set(ranks))
    suit_counts = sorted(
        (suits.count(suit) for suit in set(suits)), reverse=True
    )
    sorted_unique = sorted(set(ranks))
    max_gap = max(
        (right - left for left, right in zip(sorted_unique, sorted_unique[1:])),
        default=0,
    )
    return {
        "high_card": max(ranks),
        "paired": unique_ranks < len(ranks),
        "monotone": suit_counts[0] >= 3,
        "two_tone": suit_counts[0] == 2,
        "connected": max_gap <= 2 and len(sorted_unique) >= 3,
        "broadway_count": sum(rank >= 10 for rank in ranks),
        "cards": cards,
    }


def _opportunities(
    action: Action,
    kind: str,
    street_actions: List[Action],
    street_raises: int,
    first_raiser: Optional[int],
    preflop_aggressor: Optional[int],
    checked: Set[int],
    to_call: float,
    street_history: Dict[str, List[Action]],
    pot_before: float = 0.0,
    spr: Optional[float] = None,
    big_blind: Optional[float] = None,
    is_ip: Optional[bool] = None,
    players_in_hand: int = 0,
    hole_cards: Optional[List[str]] = None,
    board: Optional[List[str]] = None,
) -> List[Opportunity]:
    result: List[Opportunity] = []
    prior_voluntary = [
        item for item in street_actions if _action(item.action) in VOLUNTARY
    ]
    aggressive = kind in AGGRESSIVE
    if action.street == "preflop" and kind not in BLINDS:
        if not any(item.seat == action.seat for item in prior_voluntary):
            result.append(Opportunity("vpip", kind in VOLUNTARY))
            result.append(Opportunity("pfr", aggressive))
        if not prior_voluntary:
            result.append(Opportunity("rfi", aggressive))
        if street_raises == 1 and action.seat != preflop_aggressor:
            result.append(Opportunity("three_bet", aggressive))
        if street_raises >= 2:
            result.append(Opportunity("four_bet", aggressive))
        if (
            first_raiser is not None
            and action.seat == first_raiser
            and street_raises >= 2
        ):
            result.append(Opportunity("fold_to_three_bet", kind == "fold"))
    elif action.street in {"flop", "turn", "river"}:
        prior_aggression = [
            item for item in street_actions if _action(item.action) in AGGRESSIVE
        ]
        previous_street = {
            "flop": "preflop",
            "turn": "flop",
            "river": "turn",
        }.get(action.street)
        previous_actions = (
            street_history.get(previous_street, [])
            if previous_street
            else []
        )
        aggressor_missed_previous = _aggressor_checked_through(
            previous_actions, preflop_aggressor
        )
        aggressor_bet_previous = _seat_was_aggressive(
            previous_actions, preflop_aggressor
        )
        aggressor_acted = any(
            item.seat == preflop_aggressor for item in street_actions
        )
        can_open_bet = not prior_aggression and to_call == 0
        if can_open_bet and action.seat == preflop_aggressor:
            result.append(Opportunity(f"{action.street}_cbet", aggressive))
            result.append(Opportunity("missed_initiative", kind == "check"))
            if action.street == "turn" and aggressor_missed_previous:
                result.append(Opportunity("turn_delayed_cbet", aggressive))
            if action.street in {"turn", "river"} and aggressor_bet_previous:
                result.append(
                    Opportunity(f"{action.street}_barrel", aggressive)
                )
            if (
                action.street == "river"
                and aggressor_bet_previous
                and _seat_was_aggressive(
                    street_history.get("flop", []),
                    preflop_aggressor,
                )
            ):
                result.append(
                    Opportunity("river_triple_barrel", aggressive)
                )
        if (
            to_call > 0
            and prior_aggression
            and prior_aggression[-1].seat == preflop_aggressor
        ):
            result.append(
                Opportunity(f"fold_to_{action.street}_cbet", kind == "fold")
            )
            if action.street == "turn" and aggressor_missed_previous:
                result.append(
                    Opportunity("fold_to_turn_delayed_cbet", kind == "fold")
                )
            if action.street in {"turn", "river"} and aggressor_bet_previous:
                result.append(
                    Opportunity(
                        f"fold_to_{action.street}_barrel",
                        kind == "fold",
                    )
                )
                result.append(Opportunity("fold_to_barrel", kind == "fold"))
        if (
            can_open_bet
            and action.seat != preflop_aggressor
            and not aggressor_acted
        ):
            if aggressor_missed_previous:
                result.append(
                    Opportunity(f"{action.street}_probe", aggressive)
                )
            elif action.street == "flop" or aggressor_bet_previous:
                result.append(
                    Opportunity(f"{action.street}_donk", aggressive)
                )
        if can_open_bet and kind in {"check", "bet", "raise", "all_in"}:
            result.append(Opportunity("bet_aversion", kind == "check"))
        if can_open_bet and _seat_called(previous_actions, action.seat):
            result.append(Opportunity("call_then_lead", aggressive))
        if (
            action.street == "river"
            and is_ip
            and can_open_bet
            and players_in_hand == 2
        ):
            result.append(Opportunity("ip_river_checkback", kind == "check"))
        if action.seat in checked and to_call > 0:
            result.append(Opportunity(f"{action.street}_check_raise", aggressive))
        if to_call > 0:
            result.append(Opportunity(f"fold_to_{action.street}_bet", kind == "fold"))
            result.append(
                Opportunity(
                    f"call_vs_{action.street}_bet",
                    kind == "call",
                )
            )
            result.append(
                Opportunity(
                    f"raise_vs_{action.street}_bet",
                    aggressive,
                )
            )
            facing_frac = _facing_pot_fraction(to_call, pot_before)
            pot_bb = (
                pot_before / big_blind
                if big_blind and big_blind > 0
                else None
            )
            if kind in {"fold", "call"}:
                if action.street in {"turn", "river"} and _is_big_pot(
                    pot_bb, spr
                ):
                    result.append(
                        Opportunity("big_pot_calldown", kind == "call")
                    )
                if (
                    action.street == "river"
                    and facing_frac is not None
                    and facing_frac >= 0.50
                ):
                    result.append(Opportunity("river_station", kind == "call"))
                prior_pot = pot_before - to_call
                if prior_pot > 0 and to_call / prior_pot > 1.0:
                    result.append(
                        Opportunity("overcall_overbet", kind == "call")
                    )
            if facing_frac is not None and facing_frac <= 0.33:
                result.append(Opportunity("overfold_small_bet", kind == "fold"))
        if to_call > 0 and street_raises >= 2:
            result.append(
                Opportunity(f"fold_to_{action.street}_raise", kind == "fold")
            )
            result.append(Opportunity("fold_to_postflop_raise", kind == "fold"))
            if kind in {"fold", "call", "raise", "all_in"}:
                result.append(Opportunity("call_vs_raise", kind == "call"))
        result.extend(
            _showdown_opportunities(
                action=action,
                kind=kind,
                aggressive=aggressive,
                can_open_bet=can_open_bet,
                to_call=to_call,
                checked=checked,
                previous_actions=previous_actions,
                street_history=street_history,
                hole_cards=hole_cards or [],
                board=board or [],
                is_ip=is_ip,
                preflop_aggressor=preflop_aggressor,
                pot_before=pot_before,
                spr=spr,
                big_blind=big_blind,
            )
        )
    return result


def _showdown_opportunities(
    action: Action,
    kind: str,
    aggressive: bool,
    can_open_bet: bool,
    to_call: float,
    checked: Set[int],
    previous_actions: List[Action],
    street_history: Dict[str, List[Action]],
    hole_cards: List[str],
    board: List[str],
    is_ip: Optional[bool] = None,
    preflop_aggressor: Optional[int] = None,
    pot_before: float = 0.0,
    spr: Optional[float] = None,
    big_blind: Optional[float] = None,
) -> List[Opportunity]:
    """Emit showdown-conditioned opportunities when two hole cards are known.

    Hero always has cards; villains only after they show. Opponent samples are
    therefore selection-biased and tagged separately in the portrait layer.
    """

    current = _shown_features(hole_cards, board, action.street)
    if current is None:
        return []
    previous_street = {"flop": "preflop", "turn": "flop", "river": "turn"}.get(
        action.street
    )
    previous = (
        _shown_features(hole_cards, board, previous_street)
        if previous_street and previous_street != "preflop"
        else None
    )
    two_pair_plus = _is_two_pair_plus(current)
    pair_kind = (
        _pair_kind(hole_cards, board[: STREET_CARD_COUNT.get(action.street, 0)])
        if current.get("category") == "pair"
        else None
    )
    strong_pair = pair_kind in {"overpair", "top_pair"}
    air = _is_air(current, action.street)
    hit = _is_hit(previous, current)
    called_previous = _seat_called(previous_actions, action.seat)
    flop_checked = _seat_checked(
        street_history.get("flop", []), action.seat
    ) and not _seat_was_aggressive(street_history.get("flop", []), action.seat)
    result: List[Opportunity] = []
    if two_pair_plus and kind in {"check", "call", "bet", "raise", "all_in"}:
        result.append(
            Opportunity("slowplay_two_pair_plus", kind in {"check", "call"})
        )
    if strong_pair and can_open_bet:
        result.append(Opportunity("checked_strong_pair", kind == "check"))
    if (
        two_pair_plus
        and action.street in {"turn", "river"}
        and flop_checked
        and can_open_bet
    ):
        result.append(Opportunity("delayed_value", aggressive))
    if two_pair_plus and action.seat in checked and to_call > 0:
        result.append(Opportunity("check_raise_nuts", aggressive))
    if can_open_bet and called_previous and hit:
        result.append(Opportunity("hit_then_lead", aggressive))
    if can_open_bet and called_previous and aggressive:
        result.append(Opportunity("shown_lead_was_hit", hit or two_pair_plus))
    if (
        called_previous
        and previous is not None
        and _has_made_draw(previous)
        and not hit
        and not two_pair_plus
        and not _has_made_draw(current)
        and (can_open_bet or to_call > 0)
        and kind in {"check", "fold", "bet", "raise", "all_in", "call"}
    ):
        result.append(Opportunity("miss_then_give_up", kind in {"check", "fold"}))
    if aggressive:
        result.append(Opportunity("shown_air_aggression", air))
        if action.street == "river":
            result.append(
                Opportunity(
                    "river_air_bluff",
                    current.get("category") == "high_card",
                )
            )
        bet_fraction = (
            float(action.amount or 0.0) / pot_before
            if pot_before > 0 and (action.amount or 0) > 0
            else None
        )
        if air and bet_fraction is not None:
            result.append(
                Opportunity("bluff_size_split", bet_fraction <= 0.40)
            )
    if (
        can_open_bet
        and is_ip is False
        and action.seat != preflop_aggressor
        and previous is not None
        and _has_made_draw(previous)
        and str(current.get("category") or "") in COMPLETED_DRAW
        and kind in {"check", "bet", "raise", "all_in"}
    ):
        result.append(Opportunity("draw_complete_donk", aggressive))
    band = _shown_band(hole_cards, board, current, action.street)
    if (
        band == "medium"
        and can_open_bet
        and action.street in {"turn", "river"}
        and kind in {"check", "bet", "raise", "all_in"}
    ):
        result.append(Opportunity("thin_value_medium", aggressive))
    pot_bb = (
        pot_before / big_blind
        if big_blind and big_blind > 0
        else None
    )
    if (
        to_call > 0
        and kind in {"fold", "call"}
        and _is_big_pot(pot_bb, spr)
        and action.street in {"turn", "river"}
    ):
        if band == "medium":
            result.append(Opportunity("medium_calls_big", kind == "call"))
        if band == "weak":
            result.append(Opportunity("weak_pays_big", kind == "call"))
    if (
        band == "weak"
        and action.street == "river"
        and to_call > 0
        and kind in {"fold", "call"}
    ):
        result.append(Opportunity("river_weak_call", kind == "call"))
    return result


def _append_showdown_hand_opportunities(
    hand: HandHistory,
    snapshots: List[DecisionSnapshot],
) -> None:
    if not snapshots:
        return
    folded = {
        action.seat
        for action in hand.actions
        if action.seat is not None and _action(action.action) == "fold"
    }
    final = project_hand_state(hand)
    pot_bb = (
        final.reconstructed_pot / hand.big_blind
        if hand.big_blind and hand.big_blind > 0
        else None
    )
    last_by_seat: Dict[int, DecisionSnapshot] = {}
    for snapshot in snapshots:
        if snapshot.seat is not None:
            last_by_seat[snapshot.seat] = snapshot
    for seat, player in hand.players.items():
        if len(player.hole_cards or []) < 2 or seat in folded:
            continue
        if player.net is None:
            continue
        stack = final.stacks.get(seat)
        spr = (
            stack / final.reconstructed_pot
            if stack is not None and final.reconstructed_pot > 0
            else None
        )
        if not _is_big_pot(pot_bb, spr):
            continue
        snapshot = last_by_seat.get(seat)
        if snapshot is None:
            continue
        snapshot.opportunities.append(
            Opportunity("low_wsd_large_pot", float(player.net) <= 0)
        )


def _shown_features(
    hole_cards: Sequence[str], board: Sequence[str], street: Optional[str]
) -> Optional[Dict[str, Any]]:
    if street is None or len(hole_cards) < 2:
        return None
    visible = list(board[: STREET_CARD_COUNT.get(street, 0)])
    if street != "preflop" and len(visible) < 3:
        return None
    if street == "preflop":
        return None
    try:
        features = hand_features(hole_cards, visible)
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    if not _category_uses_hole(hole_cards, visible, features):
        features = dict(features)
        features["category"] = "high_card"
        features["category_rank"] = 0
    return features


def _category_uses_hole(
    hole_cards: Sequence[str],
    board: Sequence[str],
    features: Mapping[str, Any],
) -> bool:
    """True only if hole cards raise made-hand quality above the board itself.

    Pair through full house / quads need a higher *category* than the board —
    an Ace kicker on board two pair is still playing the board. Straights and
    flushes compare the full rank tuple so a nut flush beats a board flush.
    """

    if len(board) < 3:
        return True
    try:
        board_only = hand_features([], board)
    except (TypeError, ValueError, IndexError, KeyError):
        return True
    hero_cat = int(features.get("category_rank") or 0)
    board_cat = int(board_only.get("category_rank") or 0)
    if hero_cat != board_cat:
        return hero_cat > board_cat
    category = str(features.get("category") or "")
    if category not in {"straight", "flush", "straight_flush"}:
        return False
    return tuple(features.get("rank") or (hero_cat,)) > tuple(
        board_only.get("rank") or (board_cat,)
    )


def _shown_band(
    hole_cards: Sequence[str],
    board: Sequence[str],
    features: Mapping[str, Any],
    street: str,
) -> str:
    """Exploit bands: strong=two pair+, medium=top/over/second pair, weak=else."""

    if _is_two_pair_plus(features):
        return "strong"
    if features.get("category") == "pair":
        kind = _pair_kind(hole_cards, board[: STREET_CARD_COUNT.get(street, 0)])
        if kind in {"overpair", "top_pair", "second_pair"}:
            return "medium"
        return "weak"
    return "weak"


def _pair_kind(hole_cards: Sequence[str], board: Sequence[str]) -> Optional[str]:
    if len(hole_cards) != 2 or len(board) < 3:
        return None
    hole_ranks = [_card_rank(card) for card in hole_cards]
    board_ranks = sorted((_card_rank(card) for card in board), reverse=True)
    top = board_ranks[0]
    if hole_ranks[0] == hole_ranks[1]:
        return "overpair" if hole_ranks[0] > top else "underpair"
    matched = [rank for rank in hole_ranks if rank in board_ranks]
    if not matched:
        return None
    pair_rank = max(matched)
    if pair_rank == top:
        return "top_pair"
    second = board_ranks[1] if len(board_ranks) > 1 else 0
    if pair_rank == second:
        return "second_pair"
    return "low_pair"


def _card_rank(card: str) -> int:
    return _rank_value(str(card)[:-1])


def _has_made_draw(features: Mapping[str, Any]) -> bool:
    return bool(
        features.get("flush_draw") or features.get("open_ended_draw")
    )


def _has_any_draw(features: Mapping[str, Any]) -> bool:
    return _has_made_draw(features) or bool(features.get("gutshot"))


def _is_two_pair_plus(features: Mapping[str, Any]) -> bool:
    return str(features.get("category") or "") in TWO_PAIR_PLUS


def _is_air(features: Mapping[str, Any], street: str) -> bool:
    if features.get("category") != "high_card":
        return False
    if street == "river":
        return True
    return not _has_any_draw(features)


def _is_hit(
    previous: Optional[Mapping[str, Any]], current: Mapping[str, Any]
) -> bool:
    if previous is None:
        return _is_two_pair_plus(current)
    if int(current.get("category_rank") or 0) > int(
        previous.get("category_rank") or 0
    ):
        return True
    if _has_made_draw(previous) and str(current.get("category") or "") in COMPLETED_DRAW:
        return True
    return _is_two_pair_plus(current) and not _is_two_pair_plus(previous)


def _seat_checked(actions: List[Action], seat: Optional[int]) -> bool:
    return seat is not None and any(
        action.seat == seat and _action(action.action) == "check"
        for action in actions
    )


def _seat_was_aggressive(
    actions: List[Action], seat: Optional[int]
) -> bool:
    return seat is not None and any(
        action.seat == seat and _action(action.action) in AGGRESSIVE
        for action in actions
    )


def _seat_called(actions: List[Action], seat: Optional[int]) -> bool:
    return seat is not None and any(
        action.seat == seat and _action(action.action) == "call"
        for action in actions
    )


def _facing_pot_fraction(to_call: float, pot_before: float) -> Optional[float]:
    if to_call <= 0 or pot_before <= 0:
        return None
    return to_call / pot_before


def _is_big_pot(pot_bb: Optional[float], spr: Optional[float]) -> bool:
    if pot_bb is not None and pot_bb >= 20:
        return True
    return spr is not None and spr <= 3.0


def _aggressor_checked_through(
    actions: List[Action], aggressor_seat: Optional[int]
) -> bool:
    if aggressor_seat is None or not actions:
        return False
    return (
        not any(_action(action.action) in AGGRESSIVE for action in actions)
        and any(
            action.seat == aggressor_seat
            and _action(action.action) == "check"
            for action in actions
        )
    )


def _is_in_position(
    seat: Optional[int], active: Set[int], button_seat: Optional[int]
) -> Optional[bool]:
    if seat is None or button_seat is None or seat not in active:
        return None
    ordered = sorted(active)
    first_after_button = next(
        (index for index, active_seat in enumerate(ordered) if active_seat > button_seat),
        0,
    )
    action_order = ordered[first_after_button:] + ordered[:first_after_button]
    return bool(action_order and action_order[-1] == seat)


def _action(value: str) -> str:
    normalized = value.lower().replace(" ", "_").replace("-", "_")
    return "all_in" if normalized in {"allin", "all_in"} else normalized


def _street_for_board(board: List[str]) -> str:
    return {
        0: "preflop",
        3: "flop",
        4: "turn",
        5: "river",
    }.get(min(5, len(board)), "preflop")


def _project_side_pots(
    contributions: Dict[int, float], active_seats: Set[int]
) -> List[Dict[str, Any]]:
    positive = {
        seat: max(0.0, float(amount))
        for seat, amount in contributions.items()
        if amount > 0
    }
    levels = sorted(set(positive.values()))
    pots: List[Dict[str, Any]] = []
    previous = 0.0
    for level in levels:
        contributors = [
            seat for seat, amount in positive.items() if amount >= level
        ]
        amount = (level - previous) * len(contributors)
        if amount > 0:
            pots.append(
                {
                    "amount": amount,
                    "eligible_seats": sorted(
                        seat for seat in contributors if seat in active_seats
                    ),
                }
            )
        previous = level
    return pots


def _rank_value(rank: str) -> int:
    return {"T": 10, "J": 11, "Q": 12, "K": 13, "A": 14}.get(
        rank.upper(), int(rank) if rank.isdigit() else 0
    )
