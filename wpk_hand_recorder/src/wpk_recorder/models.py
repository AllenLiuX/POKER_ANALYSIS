from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RawFrame:
    timestamp: str
    direction: str
    request_id: str
    opcode: int
    payload_b64: str
    byte_length: int
    sha256: str
    websocket_url: str = ""
    decoded: Optional[Any] = None
    encoding: str = "unknown"

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RawEvent:
    timestamp: str
    event_name: str
    payload: Dict[str, Any]
    sequence: int = 0
    table_id: Optional[str] = None
    hand_id: Optional[str] = None
    source: str = "runtime-binding"
    sha256: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Action:
    street: str
    seat: Optional[int]
    player: Optional[str]
    action: str
    amount: Optional[float] = None
    amount_to: Optional[float] = None
    user_id: Optional[str] = None
    action_id: Optional[str] = None
    stack_after: Optional[float] = None
    event_sequence: Optional[int] = None
    sequence: int = 0
    source_message: Optional[str] = None


@dataclass
class Player:
    seat: int
    user_id: Optional[str] = None
    alias: Optional[str] = None
    position: Optional[str] = None
    stack_start: Optional[float] = None
    stack_end: Optional[float] = None
    hole_cards: List[str] = field(default_factory=list)
    is_hero: bool = False
    net: Optional[float] = None


@dataclass
class HandHistory:
    hand_id: str
    table_id: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    button_seat: Optional[int] = None
    small_blind: Optional[float] = None
    big_blind: Optional[float] = None
    ante: Optional[float] = None
    board: List[str] = field(default_factory=list)
    players: Dict[int, Player] = field(default_factory=dict)
    actions: List[Action] = field(default_factory=list)
    pot: Optional[float] = None
    status: str = "in_progress"
    warnings: List[str] = field(default_factory=list)
    raw_message_count: int = 0
    game_mode: str = "holdem"
    squid_round_id: Optional[str] = None
    quality_status: str = "unknown"
    quality_reasons: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["players"] = [asdict(self.players[k]) for k in sorted(self.players)]
        return data


@dataclass
class SquidEvent:
    round_id: str
    scene: int
    timestamp: str
    users: List[str] = field(default_factory=list)
    settlements: List[Dict[str, Any]] = field(default_factory=list)
    ext: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)
    sequence: int = 0
    hand_refs: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)
