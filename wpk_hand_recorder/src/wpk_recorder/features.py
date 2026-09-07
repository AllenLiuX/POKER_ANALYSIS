from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .models import Action, HandHistory


AGGRESSIVE = {"bet", "raise", "all_in"}
VOLUNTARY = AGGRESSIVE | {"call"}
BLINDS = {"small_blind", "big_blind", "ante", "straddle"}


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


def derive_decisions(hand: HandHistory) -> List[DecisionSnapshot]:
    positions = derive_positions(hand)
    for seat, position in positions.items():
        if seat in hand.players and not hand.players[seat].position:
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
    snapshots: List[DecisionSnapshot] = []

    for action in sorted(hand.actions, key=lambda item: item.sequence):
        if action.street != current_street:
            current_street = action.street
            contributions = {}
            street_actions = []
            street_raises = 0
            checked = set()
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
        if not prior_aggression and to_call == 0 and action.seat == preflop_aggressor:
            result.append(Opportunity(f"{action.street}_cbet", aggressive))
        if (
            to_call > 0
            and prior_aggression
            and prior_aggression[-1].seat == preflop_aggressor
        ):
            result.append(
                Opportunity(f"fold_to_{action.street}_cbet", kind == "fold")
            )
        if (
            not prior_aggression
            and to_call == 0
            and action.seat != preflop_aggressor
            and any(item.seat == preflop_aggressor and _action(item.action) == "check" for item in street_actions)
        ):
            result.append(Opportunity(f"{action.street}_probe", aggressive))
        if action.seat in checked and to_call > 0:
            result.append(Opportunity(f"{action.street}_check_raise", aggressive))
        if to_call > 0:
            result.append(Opportunity(f"fold_to_{action.street}_bet", kind == "fold"))
        if previous := (street_actions[-1] if street_actions else None):
            if _action(previous.action) in {"raise", "all_in"}:
                result.append(
                    Opportunity(f"fold_to_{action.street}_raise", kind == "fold")
                )
    return result


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


def _rank_value(rank: str) -> int:
    return {"T": 10, "J": 11, "Q": 12, "K": 13, "A": 14}.get(
        rank.upper(), int(rank) if rank.isdigit() else 0
    )
