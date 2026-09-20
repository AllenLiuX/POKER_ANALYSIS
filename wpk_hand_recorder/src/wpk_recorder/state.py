from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Set

from .models import Action, DecisionRequest, HandHistory, Player, utc_now


STREETS = ("preflop", "flop", "turn", "river", "showdown")
FORCED_PREFLOP_ACTIONS = {
    "ante",
    "small_blind",
    "big_blind",
    "straddle",
}


def _number(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _seat(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _cards(value: Any) -> List[str]:
    if isinstance(value, str):
        return [part for part in value.replace(",", " ").split() if part]
    if isinstance(value, (list, tuple)):
        return [str(card) for card in value]
    return []


class HandStateMachine:
    def __init__(self) -> None:
        self.current: Optional[HandHistory] = None
        self._seen: Set[str] = set()
        self._sequence = 0
        self._pending_actions: List[tuple] = []
        self._recent: Dict[str, HandHistory] = {}
        self._known_action_ids: Set[str] = set()
        self._restored_actions: Counter = Counter()

    def restore(self, hand: HandHistory) -> None:
        hand.status = "in_progress"
        hand.ended_at = None
        self.current = hand
        self._sequence = max((action.sequence for action in hand.actions), default=0)
        self._seen.clear()
        self._known_action_ids = {
            action.action_id for action in hand.actions if action.action_id
        }
        self._restored_actions = Counter(
            _action_signature(
                action.seat,
                action.action,
                action.street,
                action.amount,
                action.amount_to,
            )
            for action in hand.actions
        )
        self._consume_pending(hand.hand_id)

    def apply_many(self, events: Iterable[Dict[str, Any]], source: str = "") -> List[HandHistory]:
        completed: List[HandHistory] = []
        for event in events:
            hand = self.apply(event, source)
            if hand:
                completed.append(hand)
        return completed

    def apply(self, event: Dict[str, Any], source: str = "") -> Optional[HandHistory]:
        signature = repr(sorted(event.items(), key=lambda item: item[0]))
        if signature in self._seen:
            return None
        self._seen.add(signature)
        kind = event.get("event")
        if kind == "history":
            return self._apply_history(event)
        if kind == "start":
            hand_id = str(event.get("hand_id") or f"unknown-{utc_now()}")
            if (
                self.current
                and self.current.hand_id != hand_id
                and _same_unknown_hand(self.current.hand_id, hand_id)
            ):
                self.current.hand_id = hand_id
            if self.current and self.current.hand_id == hand_id:
                self.current.table_id = (
                    _optional_text(event.get("table_id")) or self.current.table_id
                )
                self.current.button_seat = (
                    _seat(event.get("button_seat")) or self.current.button_seat
                )
                self.current.small_blind = (
                    _number(event.get("small_blind"))
                    if event.get("small_blind") is not None
                    else self.current.small_blind
                )
                self.current.big_blind = (
                    _number(event.get("big_blind"))
                    if event.get("big_blind") is not None
                    else self.current.big_blind
                )
                self.current.ante = (
                    _number(event.get("ante"))
                    if event.get("ante") is not None
                    else self.current.ante
                )
                self._refresh_button_from_blinds()
                return None
            previous = self._close("interrupted") if self.current else None
            self.current = HandHistory(
                hand_id=hand_id,
                table_id=_optional_text(event.get("table_id")),
                started_at=utc_now(),
                button_seat=_seat(event.get("button_seat")),
                small_blind=_number(event.get("small_blind")),
                big_blind=_number(event.get("big_blind")),
                ante=_number(event.get("ante")),
            )
            self._seen = {signature}
            self._sequence = 0
            self._known_action_ids.clear()
            self._restored_actions.clear()
            self._consume_pending(hand_id)
            return previous
        if not self.current:
            if kind == "action":
                self._pending_actions.append((event, source))
                self._pending_actions = self._pending_actions[-20:]
            return None

        self.current.raw_message_count += 1
        if kind == "player":
            self._apply_player(event)
        elif kind == "decision_request":
            self._apply_decision_request(event)
        elif kind == "action":
            self.current.pending_decision = None
            self._apply_action(event, source)
        elif kind == "board":
            self.current.pending_decision = None
            append_cards = _cards(event.get("append_cards"))
            full_board = _cards(event.get("board") or event.get("cards"))
            if append_cards:
                self.current.board.extend(
                    card for card in append_cards if card not in self.current.board
                )
            if full_board and len(full_board) >= len(self.current.board):
                self.current.board = full_board
            if event.get("pot") is not None:
                self.current.pot = _number(event["pot"])
        elif kind == "result":
            self.current.pending_decision = None
            self._apply_result(event)
            if event.get("complete") is True:
                return self._close("completed")
        elif kind == "end":
            return self._close("completed")
        return None

    def flush(self) -> Optional[HandHistory]:
        return self._close("partial") if self.current else None

    def _apply_player(self, event: Dict[str, Any]) -> None:
        seat = _seat(event.get("seat"))
        if seat is None:
            return
        player = self.current.players.setdefault(seat, Player(seat=seat))
        player.user_id = _optional_text(event.get("user_id")) or player.user_id
        player.alias = _optional_text(event.get("alias")) or player.alias
        player.position = _optional_text(event.get("position")) or player.position
        stack = _number(event.get("stack"))
        if stack is not None:
            if player.stack_start is None:
                player.stack_start = stack
            player.stack_end = stack
        player.is_hero = bool(event.get("is_hero", player.is_hero))
        if event.get("net") is not None:
            player.net = _number(event.get("net"))
        cards = _cards(event.get("cards"))
        if cards:
            player.hole_cards = cards
        self._refresh_button_from_blinds()

    def _apply_decision_request(self, event: Dict[str, Any]) -> None:
        is_hero = event.get("is_hero") is True
        user_id = _optional_text(event.get("user_id"))
        seat = _seat(event.get("seat"))
        player = next(
            (
                item
                for item in self.current.players.values()
                if user_id and item.user_id == user_id
            ),
            None,
        )
        if player is None and seat is not None:
            player = self.current.players.setdefault(seat, Player(seat=seat))
        cards = _cards(event.get("cards"))
        if player is not None:
            if is_hero:
                for item in self.current.players.values():
                    item.is_hero = item is player
            player.user_id = user_id or player.user_id
            if cards and is_hero:
                player.hole_cards = cards
        legal_actions = []
        for value in event.get("legal_actions") or []:
            action = str(value or "").strip().lower().replace("-", "_")
            if action == "allin":
                action = "all_in"
            if action in {"fold", "check", "call", "raise", "all_in"}:
                legal_actions.append(action)
        self.current.pending_decision = DecisionRequest(
            event_sequence=_seat(event.get("_event_sequence")) or 0,
            captured_at=_optional_text(event.get("_captured_at")) or utc_now(),
            hand_id=_optional_text(event.get("hand_id")) or self.current.hand_id,
            seat=seat,
            user_id=user_id,
            cards=cards if is_hero else [],
            legal_actions=list(dict.fromkeys(legal_actions)),
            call_score=_number(event.get("call_score")) or 0.0,
            min_raise_to=_number(event.get("min_raise_to")),
            max_raise_to=_number(event.get("max_raise_to")),
            countdown=_number(event.get("countdown")) or 0.0,
            last_bet=_number(event.get("last_bet")),
            seat_score=_number(event.get("seat_score")),
            current_score=_number(event.get("current_score")),
        )

    def _apply_action(self, event: Dict[str, Any], source: str) -> None:
        event_hand_id = _optional_text(event.get("hand_id"))
        if (
            event_hand_id
            and event_hand_id != self.current.hand_id
            and not _same_unknown_hand(event_hand_id, self.current.hand_id)
        ):
            return
        seat = _seat(event.get("seat"))
        action = str(event.get("action") or "unknown").lower()
        street = str(event.get("street") or self._street_from_board()).lower()
        if street not in STREETS:
            street = self._street_from_board()
        action_id = _optional_text(event.get("action_id"))
        if action_id and action_id in self._known_action_ids:
            return
        restored_signature = _action_signature(
            seat,
            action,
            street,
            _number(event.get("amount")),
            _number(event.get("amount_to")),
        )
        if (
            event.get("_source_event") == "actionHistoryNotify"
            and self._restored_actions[restored_signature] > 0
        ):
            self._restored_actions[restored_signature] -= 1
            return
        self._sequence += 1
        self.current.actions.append(
            Action(
                street=street,
                seat=seat,
                player=_optional_text(event.get("alias")),
                action=action,
                amount=_number(event.get("amount")),
                amount_to=_number(event.get("amount_to")),
                user_id=_optional_text(event.get("user_id")),
                action_id=action_id,
                stack_after=_number(event.get("stack_after")),
                event_sequence=_seat(event.get("_event_sequence")),
                sequence=self._sequence,
                source_message=source or None,
            )
        )
        if action_id:
            self._known_action_ids.add(action_id)
        if event.get("pot") is not None:
            self.current.pot = _number(event["pot"])
        self._refresh_button_from_blinds()

    def _refresh_button_from_blinds(self) -> None:
        """Use this hand's forced bets to repair stale room-level Button data."""

        if self.current is None:
            return
        preflop_actions = [
            action
            for action in self.current.actions
            if action.street == "preflop" and action.seat is not None
        ]
        small_blind_seat = next(
            (
                action.seat
                for action in reversed(preflop_actions)
                if action.action == "small_blind"
            ),
            None,
        )
        if small_blind_seat is None:
            return
        ante_seats = {
            action.seat
            for action in preflop_actions
            if action.action == "ante"
        }
        if len(ante_seats) >= 2:
            participating_seats = sorted(ante_seats)
        else:
            if any(
                action.action not in FORCED_PREFLOP_ACTIONS
                for action in preflop_actions
            ):
                return
            participating_seats = sorted(self.current.players)
        if small_blind_seat not in participating_seats:
            return
        if len(participating_seats) == 2:
            self.current.button_seat = small_blind_seat
            return
        if len(participating_seats) < 3:
            return
        small_blind_index = participating_seats.index(small_blind_seat)
        self.current.button_seat = participating_seats[
            small_blind_index - 1
        ]

    def _consume_pending(self, hand_id: str) -> None:
        pending, self._pending_actions = self._pending_actions, []
        for pending_event, pending_source in pending:
            pending_hand_id = _optional_text(pending_event.get("hand_id"))
            if pending_hand_id and not (
                pending_hand_id == hand_id
                or _same_unknown_hand(pending_hand_id, hand_id)
            ):
                continue
            self._apply_action(pending_event, pending_source)

    def _apply_result(self, event: Dict[str, Any]) -> None:
        seat = _seat(event.get("seat"))
        if seat is not None:
            player = self.current.players.setdefault(seat, Player(seat=seat))
            player.user_id = _optional_text(event.get("user_id")) or player.user_id
            if event.get("net") is not None:
                player.net = _number(event.get("net"))
            if event.get("stack") is not None:
                player.stack_end = _number(event.get("stack"))
            if event.get("insurance_result") is not None:
                player.insurance_result = _number(event.get("insurance_result"))
            if event.get("fund") is not None:
                player.fund = _number(event.get("fund"))
            cards = _cards(event.get("cards"))
            if cards:
                player.hole_cards = cards
        board = _cards(event.get("board"))
        if board:
            self.current.board = board
        if event.get("pot") is not None:
            self.current.pot = _number(event["pot"])

    def _street_from_board(self) -> str:
        count = len(self.current.board)
        if count == 0:
            return "preflop"
        if count == 3:
            return "flop"
        if count == 4:
            return "turn"
        if count >= 5:
            return "river"
        return "preflop"

    def _close(self, status: str) -> HandHistory:
        hand = self.current
        assert hand is not None
        hand.pending_decision = None
        hand.status = status
        hand.ended_at = utc_now()
        self._validate(hand)
        self.current = None
        self._seen.clear()
        self._known_action_ids.clear()
        self._restored_actions.clear()
        self._recent[hand.hand_id] = hand
        if len(self._recent) > 10:
            self._recent.pop(next(iter(self._recent)))
        return hand

    def _apply_history(self, event: Dict[str, Any]) -> HandHistory:
        hand_id = str(event.get("hand_id") or f"unknown-{utc_now()}")
        was_current = bool(self.current and self.current.hand_id == hand_id)
        if self.current and self.current.hand_id == hand_id:
            hand = self.current
        elif hand_id in self._recent:
            hand = self._recent[hand_id]
        else:
            hand = HandHistory(hand_id=hand_id)
        hand.table_id = _optional_text(event.get("table_id")) or hand.table_id
        if hand.started_at is None:
            hand.started_at = _optional_text(event.get("started_at"))
        hand.button_seat = _seat(event.get("button_seat")) or hand.button_seat
        if event.get("small_blind") is not None:
            hand.small_blind = _number(event.get("small_blind"))
        if event.get("big_blind") is not None:
            hand.big_blind = _number(event.get("big_blind"))
        if event.get("ante") is not None:
            hand.ante = _number(event.get("ante"))
        history_board = _cards(event.get("board"))
        if history_board or not hand.board:
            hand.board = history_board
        if event.get("pot") is not None:
            hand.pot = _number(event.get("pot"))
        history_players = event.get("players") or []
        for item in history_players:
            seat = _seat(item.get("seat"))
            if seat is None:
                continue
            player = hand.players.setdefault(seat, Player(seat=seat))
            player.user_id = _optional_text(item.get("user_id")) or player.user_id
            player.alias = _optional_text(item.get("alias")) or player.alias
            player.position = _optional_text(item.get("position")) or player.position
            player.hole_cards = _cards(item.get("cards")) or player.hole_cards
            player.net = _number(item.get("net"))
            if item.get("insurance_result") is not None:
                player.insurance_result = _number(item.get("insurance_result"))
            player.is_hero = bool(item.get("is_hero", player.is_hero))
        if not hand.actions:
            hand.warnings.append("action order reconstructed from per-player history")
            by_street: Dict[str, List[tuple]] = {street: [] for street in STREETS}
            for item in history_players:
                for index, action in enumerate(item.get("actions") or []):
                    street = action.get("street", "preflop")
                    by_street.setdefault(street, []).append((index, item, action))
            history_sequence = 0
            for street in STREETS:
                for _, item, action in sorted(
                    by_street.get(street, []), key=lambda row: (row[0], row[1].get("seat") or 0)
                ):
                    history_sequence += 1
                    hand.actions.append(
                        Action(
                            street=street,
                            seat=_seat(item.get("seat")),
                            player=_optional_text(item.get("alias")),
                            action=str(action.get("action") or "unknown"),
                            amount=_number(action.get("amount")),
                            amount_to=None,
                            user_id=_optional_text(item.get("user_id")),
                            sequence=history_sequence,
                            source_message="history",
                        )
                    )
        hand.status = "completed"
        hand.ended_at = utc_now()
        hand.warnings = [
            warning
            for warning in hand.warnings
            if warning != "no actions decoded"
        ]
        self._validate(hand)
        self._recent[hand.hand_id] = hand
        if was_current:
            self.current = None
            self._seen.clear()
            self._known_action_ids.clear()
            self._restored_actions.clear()
        return hand

    @staticmethod
    def _validate(hand: HandHistory) -> None:
        dynamic_prefixes = (
            "invalid board length:",
            "action sequence is not contiguous",
            "player net does not balance:",
            "no actions decoded",
        )
        hand.warnings = [
            warning
            for warning in hand.warnings
            if not warning.startswith(dynamic_prefixes)
        ]

        def warn(message: str) -> None:
            if message not in hand.warnings:
                hand.warnings.append(message)

        if len(hand.board) not in {0, 3, 4, 5}:
            warn(f"invalid board length: {len(hand.board)}")
        if any(action.sequence != index for index, action in enumerate(hand.actions, 1)):
            warn("action sequence is not contiguous")
        nets = [p.net for p in hand.players.values() if p.net is not None]
        insurance = sum(p.insurance_result or 0 for p in hand.players.values())
        funds = sum(p.fund or 0 for p in hand.players.values())
        adjusted_balance = sum(nets) - insurance + funds
        if len(nets) >= 2 and abs(adjusted_balance) > 0.02:
            warn(f"player net does not balance: {adjusted_balance:.2f}")
        if not hand.actions:
            warn("no actions decoded")


def _optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _action_signature(
    seat: Optional[int],
    action: str,
    street: str,
    amount: Optional[float],
    amount_to: Optional[float],
) -> tuple:
    return (seat, action.lower(), street.lower(), amount, amount_to)


def _same_unknown_hand(first: str, second: str) -> bool:
    first_room, _, first_bout = first.rpartition("-")
    second_room, _, second_bout = second.rpartition("-")
    return (
        bool(first_bout)
        and first_bout == second_bout
        and ("unknown" in {first_room, second_room})
    )
