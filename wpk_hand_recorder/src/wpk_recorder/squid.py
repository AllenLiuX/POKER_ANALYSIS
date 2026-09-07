from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from .models import SquidEvent


class SquidStateMachine:
    """Aggregates sparse squid notifications without discarding their raw payload."""

    def __init__(self) -> None:
        self.round_id: Optional[str] = None
        self.participants: Set[str] = set()
        self.ext: Dict[str, Any] = {}
        self.hand_refs: List[str] = []
        self.closed_rounds: List[str] = []

    def apply(self, event: Dict[str, Any], timestamp: str, sequence: int) -> SquidEvent:
        round_id = str(event["round_id"])
        scene = int(event.get("scene") or 0)
        if scene == 1 or self.round_id != round_id:
            self.round_id = round_id
            self.participants.clear()
            self.ext.clear()
            self.hand_refs.clear()

        self.participants.update(str(user) for user in event.get("users") or [])
        self.ext.update(dict(event.get("ext") or {}))
        for reference in event.get("hand_refs") or self.ext.get("squidSettHands") or []:
            reference = str(reference)
            if reference not in self.hand_refs:
                self.hand_refs.append(reference)

        ext = dict(self.ext)
        if not 1 <= scene <= 11:
            ext.setdefault("_warnings", []).append(f"unknown squid scene: {scene}")
        result = SquidEvent(
            round_id=round_id,
            scene=scene,
            timestamp=timestamp,
            users=sorted(self.participants),
            settlements=list(event.get("settlements") or []),
            ext=ext,
            raw=dict(event.get("raw") or {}),
            sequence=sequence,
            hand_refs=list(self.hand_refs),
        )
        if scene == 4:
            self.closed_rounds.append(round_id)
            self.closed_rounds = self.closed_rounds[-20:]
            self.round_id = None
        return result
