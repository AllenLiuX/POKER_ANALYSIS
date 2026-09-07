from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional


DEFAULT_ALIASES: Dict[str, List[str]] = {
    "event": ["event", "type", "cmd", "command", "name", "op", "opcode", "msgId", "messageId"],
    "hand_id": ["hand_id", "handId", "game_id", "gameId", "round_id", "roundId"],
    "table_id": ["table_id", "tableId", "room_id", "roomId"],
    "seat": ["seat", "seat_id", "seatId", "chair", "chairId"],
    "alias": ["alias", "nickname", "nickName", "name", "playerName"],
    "action": ["action", "action_type", "actionType", "operate", "operation"],
    "amount": ["amount", "bet", "chips", "value", "raiseTo"],
    "board": ["board", "community_cards", "communityCards", "publicCards"],
    "cards": ["cards", "hole_cards", "holeCards", "handCards"],
    "pot": ["pot", "pot_size", "potSize", "totalPot"],
    "street": ["street", "round", "stage"],
    "stack": ["stack", "chips", "balance"],
    "net": ["net", "win", "profit", "result"],
    "complete": ["complete", "completed", "isEnd", "finished"],
}

EVENT_ALIASES = {
    "start": {"start", "new_hand", "hand_start", "gamestart", "roundstart", "dealNotify"},
    "player": {"player", "seat", "sit", "playerinfo", "seatinfo"},
    "action": {
        "action",
        "operate",
        "operation",
        "bet",
        "playeraction",
        "actionNotify",
        "actionHistoryNotify",
    },
    "board": {
        "board",
        "deal",
        "community",
        "publiccards",
        "dealpubliccard",
        "roundChangeNotify",
        "openCardNotify",
        "openCardByAllinNotify",
        "handCardsNotify",
    },
    "result": {
        "result",
        "settle",
        "settlement",
        "showdown",
        "gameover",
        "playResultNotify",
        "updateHistoryData",
    },
    "end": {"end", "hand_end", "roundend", "cleanGameNotify", "cleanNotify"},
}


def _norm(text: Any) -> str:
    return str(text).replace("-", "").replace("_", "").lower()


class ProtocolMapper:
    """Maps decoded messages to canonical events without guessing numeric IDs."""

    def __init__(self, config_path: Optional[Path] = None):
        config: Dict[str, Any] = {}
        if config_path and config_path.exists():
            config = json.loads(config_path.read_text(encoding="utf-8"))
        self.aliases = {**DEFAULT_ALIASES, **config.get("field_aliases", {})}
        self.event_ids = {str(k): str(v) for k, v in config.get("event_ids", {}).items()}
        self.action_ids = {str(k): str(v) for k, v in config.get("action_ids", {}).items()}
        self.user_seats: Dict[str, int] = {}
        self.user_aliases: Dict[str, str] = {}
        self.room_id: Optional[str] = None
        self.bout: Optional[str] = None
        self.small_blind: Optional[float] = None
        self.big_blind: Optional[float] = None
        self.ante: Optional[float] = None
        self.hero_seat: Optional[int] = None
        self.button_seat: Optional[int] = None
        self.squid_round_id: Optional[str] = None
        self.squid_counter = 0

    def canonical_events(self, value: Any) -> Iterable[Dict[str, Any]]:
        for message in self._message_maps(value):
            flattened = self._unwrap(message)
            raw_event = self._get(flattened, "event")
            if isinstance(raw_event, str):
                special = list(self._wpk_runtime_events(raw_event, flattened))
                if special:
                    yield from special
                    continue
            mapped = self.event_ids.get(str(raw_event), raw_event)
            event = self._event_kind(mapped)
            if not event:
                continue
            item: Dict[str, Any] = {"event": event, "_raw_event": raw_event}
            for field in self.aliases:
                if field != "event":
                    found = self._get(flattened, field)
                    if found is not None:
                        item[field] = found
            if "action" in item:
                item["action"] = self.action_ids.get(str(item["action"]), item["action"])
            yield item

    def _wpk_runtime_events(
        self, raw_event: str, message: Mapping[str, Any]
    ) -> Iterable[Dict[str, Any]]:
        if raw_event == "upDateRoomNotify":
            self._update_room_context(message)
            round_name = str(message.get("round", "")).upper()
            if round_name in {"BET_BLIND", "PRE_FLOP", "FLOP", "TURN", "RIVER"}:
                yield self._start_event(message.get("currentBoutNum"))
            for player in message.get("sitUserList") or []:
                if isinstance(player, Mapping):
                    yield self._player_event(player)
            if round_name != "CLEAN":
                yield {
                    "event": "board",
                    "board": _card_list(message.get("publicCards")),
                    "street": _street(message.get("round")),
                    "pot": message.get("totalPot"),
                    "_source_event": raw_event,
                }
            return
        if raw_event in {"dealNotify", "dealNotify_reconnection"}:
            if message.get("currentBoutNum") is not None:
                self.bout = str(message["currentBoutNum"])
            yield self._start_event(self.bout)
            for player in message.get("list") or []:
                if isinstance(player, Mapping):
                    yield self._player_event(player)
            return
        if raw_event in {"actionNotify", "actionHistoryNotify"}:
            for action in message.get("actionList") or []:
                if not isinstance(action, Mapping):
                    continue
                user_id = str(action.get("userId")) if action.get("userId") is not None else ""
                yield {
                    "event": "action",
                    "seat": action.get("seatNum", self.user_seats.get(user_id)),
                    "user_id": user_id or None,
                    "alias": self.user_aliases.get(user_id),
                    "action": str(action.get("actionType", "unknown")).lower(),
                    "amount": action.get("actionScore"),
                    "amount_to": action.get("seatScore"),
                    "stack_after": action.get("currentScore"),
                    "street": _street(action.get("round")),
                    "action_id": action.get("actionId"),
                    "pot": message.get("totalPot"),
                    "_source_event": raw_event,
                }
            return
        if raw_event == "roundChangeNotify":
            yield {
                "event": "board",
                "append_cards": _card_list(message.get("dealPublicCards")),
                "street": _street(message.get("round")),
                "pot": message.get("totalPot"),
            }
            return
        if raw_event == "playResultNotify":
            for result in message.get("thanList") or []:
                if isinstance(result, Mapping):
                    yield {
                        "event": "result",
                        "seat": result.get("seatNum"),
                        "cards": _card_list(result.get("highHandCards")),
                        "complete": False,
                    }
            yield {"event": "end"}
            return
        if raw_event == "updateHistoryData":
            hand = self._history_event(message)
            if hand:
                yield hand
            return
        if raw_event == "squidGameNotify":
            scene = _int(message.get("scene")) or 0
            ext = _json_object(message.get("ext"))
            if scene == 1 or not self.squid_round_id:
                self.squid_counter += 1
                stamp = message.get("sysTime") or self.squid_counter
                self.squid_round_id = f"{self.room_id or 'unknown'}-squid-{stamp}"
            hand_refs = message.get("squidSettHands") or ext.get("squidSettHands") or []
            if isinstance(hand_refs, str):
                try:
                    hand_refs = json.loads(hand_refs)
                except json.JSONDecodeError:
                    hand_refs = [hand_refs]
            yield {
                "event": "squid",
                "round_id": self.squid_round_id,
                "scene": scene,
                "users": [str(user) for user in (message.get("users") or [])],
                "settlements": list(message.get("sett") or []),
                "ext": ext,
                "hand_refs": [str(ref) for ref in hand_refs],
                "raw": dict(message),
            }
            if scene == 4:
                self.squid_round_id = None
            return

    def _update_room_context(self, message: Mapping[str, Any]) -> None:
        if message.get("roomId") is not None:
            self.room_id = str(message["roomId"])
        if message.get("currentBoutNum") is not None:
            self.bout = str(message["currentBoutNum"])
        grade = message.get("gradeCfg") or {}
        if isinstance(grade, Mapping):
            self.small_blind = _float(grade.get("smallBlind"))
            self.big_blind = _float(grade.get("bigBlind"))
        self.ante = _float(message.get("ante"))
        self.hero_seat = _int(message.get("currentUserSeatNum"))
        self.button_seat = _int(message.get("bankerSeatNum"))

    def _start_event(self, bout: Any) -> Dict[str, Any]:
        return {
            "event": "start",
            "hand_id": _hand_id(self.room_id, bout),
            "table_id": self.room_id,
            "small_blind": self.small_blind,
            "big_blind": self.big_blind,
            "ante": self.ante,
            "button_seat": self.button_seat,
        }

    def _player_event(self, player: Mapping[str, Any]) -> Dict[str, Any]:
        user_id = str(player.get("userId")) if player.get("userId") is not None else ""
        seat = _int(player.get("seatNum"))
        if seat is None and user_id:
            seat = self.user_seats.get(user_id)
        if user_id and seat is not None:
            self.user_seats[user_id] = seat
        if user_id and player.get("nickname"):
            self.user_aliases[user_id] = str(player["nickname"])
        return {
            "event": "player",
            "seat": seat,
            "user_id": user_id or None,
            "alias": player.get("nickname") or self.user_aliases.get(user_id),
            "stack": player.get("currentScore"),
            "cards": _card_list(player.get("handCards")),
            "is_hero": seat is not None and seat == self.hero_seat,
        }

    def _history_event(self, message: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        players = message.get("handList1") or []
        if not players or not isinstance(players[0], Mapping):
            return None
        first = players[0]
        room_id = str(first.get("roomId") or self.room_id or "")
        hand_num = first.get("handNum", message.get("handNum"))
        board = _card_list(first.get("publicCardAll") or first.get("publicCard"))
        normalized_players = []
        for player in players:
            if not isinstance(player, Mapping):
                continue
            normalized_players.append(
                {
                    "seat": _int(player.get("seatNum")),
                    "user_id": (
                        str(player.get("userId")) if player.get("userId") is not None else None
                    ),
                    "alias": player.get("nickname"),
                    "cards": _card_list(player.get("handCard")),
                    "net": player.get("changeScore"),
                    "is_hero": _int(player.get("seatNum")) == self.hero_seat,
                    "position": player.get("seatPos"),
                    "actions": _history_actions(player.get("betList")),
                }
            )
        return {
            "event": "history",
            "hand_id": _hand_id(room_id, hand_num),
            "table_id": room_id,
            "started_at": first.get("createTime"),
            "small_blind": first.get("grade", self.small_blind),
            "big_blind": (
                2 * float(first["grade"]) if first.get("grade") is not None else self.big_blind
            ),
            "ante": first.get("ante", self.ante),
            "button_seat": _int(first.get("bankerSeatNum")) or self.button_seat,
            "board": board,
            "pot": message.get("totalPot"),
            "players": normalized_players,
        }

    def _message_maps(self, value: Any) -> Iterable[Mapping[str, Any]]:
        if isinstance(value, Mapping):
            yield value
            for key in ("messages", "events", "items"):
                nested = value.get(key)
                if isinstance(nested, list):
                    for item in nested:
                        if isinstance(item, Mapping):
                            yield item
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, Mapping):
                    yield item

    @staticmethod
    def _unwrap(message: Mapping[str, Any]) -> Dict[str, Any]:
        result = dict(message)
        for key in ("data", "body", "payload", "params"):
            nested = result.get(key)
            if isinstance(nested, Mapping):
                result.update(nested)
        return result

    def _get(self, message: Mapping[str, Any], field: str) -> Any:
        normalized = {_norm(k): v for k, v in message.items()}
        for alias in self.aliases[field]:
            if _norm(alias) in normalized:
                return normalized[_norm(alias)]
        return None

    @staticmethod
    def _event_kind(value: Any) -> Optional[str]:
        normalized = _norm(value)
        for kind, names in EVENT_ALIASES.items():
            if normalized in {_norm(name) for name in names}:
                return kind
        return None


def _hand_id(room_id: Any, hand_num: Any) -> str:
    return f"{room_id or 'unknown'}-{hand_num or 'unknown'}"


def _int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _street(value: Any) -> str:
    name = str(value or "").upper()
    return {
        "BET_BLIND": "preflop",
        "PRE_FLOP": "preflop",
        "FLOP": "flop",
        "TURN": "turn",
        "RIVER": "river",
        "THAN": "showdown",
    }.get(name, name.lower() or "preflop")


def _card(value: Any) -> Optional[str]:
    number = _int(value)
    if number is None or number <= 0:
        return None
    suit, rank = divmod(number, 100)
    suits = {1: "s", 2: "h", 3: "c", 4: "d"}
    ranks = {1: "A", 10: "T", 11: "J", 12: "Q", 13: "K"}
    if suit not in suits or not 1 <= rank <= 13:
        return None
    return f"{ranks.get(rank, str(rank))}{suits[suit]}"


def _card_list(value: Any) -> List[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = value.replace(",", " ").split()
    if not isinstance(value, (list, tuple)):
        return []
    return [card for item in value if (card := _card(item))]


def _history_actions(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []
    return [
        {
            "street": _street(item.get("round")),
            "action": str(item.get("action") or "").strip().lower(),
            "amount": item.get("score"),
        }
        for item in value
        if isinstance(item, Mapping) and str(item.get("action") or "").strip()
    ]


def _json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return dict(parsed) if isinstance(parsed, Mapping) else {}
        except json.JSONDecodeError:
            return {}
    return {}
