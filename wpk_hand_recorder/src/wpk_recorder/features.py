from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .models import Action, HandHistory


AGGRESSIVE = {"bet", "raise", "all_in"}
VOLUNTARY = AGGRESSIVE | {"call"}
BLINDS = {"small_blind", "big_blind", "ante", "straddle"}
STREETS_ORDER = {"preflop": 0, "flop": 1, "turn": 2, "river": 3}


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
                is_ip=_is_in_position(seat, active, hand.button_seat),
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
    count = {"preflop": 0, "flop": 3, "turn": 4, "river": 5}.get(street, len(board))
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
        if not prior_aggression and to_call == 0 and action.seat == preflop_aggressor:
            result.append(Opportunity(f"{action.street}_cbet", aggressive))
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
        if (
            not prior_aggression
            and to_call == 0
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
        if to_call > 0 and street_raises >= 2:
            result.append(
                Opportunity(f"fold_to_{action.street}_raise", kind == "fold")
            )
    return result


def _seat_was_aggressive(
    actions: List[Action], seat: Optional[int]
) -> bool:
    return seat is not None and any(
        action.seat == seat and _action(action.action) in AGGRESSIVE
        for action in actions
    )


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
