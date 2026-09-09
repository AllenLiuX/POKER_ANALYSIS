from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.request import urlopen

from websockets.asyncio.client import connect

from .decoder import cdp_payload, decode_payload, summarize_shape
from .decision_state import decision_state_from_hand
from .formatting import render_event, render_hand_text
from .models import RawEvent, RawFrame, utc_now
from .privacy import safe_url
from .protocol import ProtocolMapper
from .state import HandStateMachine
from .storage import RecorderStore
from .squid import SquidStateMachine

BINDING_NAME = "__wpkRecorderEmit"
RELEVANT_EVENTS = (
    "dealNotify",
    "dealNotify_reconnection",
    "actionNotify",
    "actionHistoryNotify",
    "userOptNotify",
    "roundChangeNotify",
    "openCardNotify",
    "openCardByAllinNotify",
    "handCardsNotify",
    "playResultNotify",
    "updateHistoryData",
    "cleanGameNotify",
    "cleanNotify",
    "updateRoomUserNotify",
    "upDateRoomNotify",
    "squidGameNotify",
    "showCardNotify",
    "scoreRefillNotify",
    "updateUserInfoNotify",
    "bankerChangeNotify",
    "insuranceNotify",
    "insurancePackageNotify",
    "insuranceScoreHint",
    "forceSeeCardNotify",
    "seeComCardNotify",
    "addScoreStatusNotify",
    "retraceScoreNotify",
    "waitHandsNotify",
    "raiseBlind",
)


def page_target(debug_port: int, host: str = "h5.sxkxys.com") -> Dict[str, Any]:
    with urlopen(f"http://127.0.0.1:{debug_port}/json/list", timeout=3) as response:
        targets = json.load(response)
    for target in targets:
        if target.get("type") == "page" and host in target.get("url", ""):
            return target
    raise RuntimeError(f"No Chrome page for {host!r}; open the site in the debug Chrome window")


class CDPRecorder:
    def __init__(
        self,
        store: RecorderStore,
        mapper: ProtocolMapper,
        debug_port: int = 9223,
        include_sent: bool = False,
    ):
        self.store = store
        self.mapper = mapper
        self.debug_port = debug_port
        self.include_sent = include_sent
        self.state = HandStateMachine()
        self.squid_state = SquidStateMachine()
        self.websocket_urls: Dict[str, str] = {}
        self.encodings: Counter[str] = Counter()
        self.shapes: Counter[str] = Counter()
        self.frames = 0
        self.hands = 0
        self.event_sequence = store.max_event_sequence()
        self.squid_events = 0
        self.active_squid_round_id: Optional[str] = None

    def current_hand_snapshot(self) -> Any:
        """Return an isolated in-memory snapshot for the embedded strategy API."""

        return copy.deepcopy(self.state.current)

    async def run(self, duration: Optional[float] = None) -> None:
        target = page_target(self.debug_port)
        ws_url = target["webSocketDebuggerUrl"]
        async with connect(ws_url, max_size=None, origin=None) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "id": 1,
                        "method": "Network.enable",
                        "params": {"maxTotalBufferSize": 0, "maxResourceBufferSize": 0},
                    }
                )
            )
            await self._wait_for_id(websocket, 1, "Network.enable")
            await self._try_command(
                websocket,
                2,
                "Page.setWebLifecycleState",
                {"state": "active"},
            )
            await self._try_command(
                websocket,
                3,
                "Emulation.setFocusEmulationEnabled",
                {"enabled": True},
            )
            await self._try_command(
                websocket,
                4,
                "Runtime.addBinding",
                {"name": BINDING_NAME},
            )
            await websocket.send(
                json.dumps(
                    {
                        "id": 5,
                        "method": "Runtime.evaluate",
                        "params": {"expression": _event_hook_script(), "awaitPromise": False},
                    }
                )
            )
            await self._wait_for_id(websocket, 5, "Runtime.evaluate")
            if duration:
                try:
                    await asyncio.wait_for(self._receive(websocket), timeout=duration)
                except asyncio.TimeoutError:
                    pass
            else:
                await self._receive(websocket)
        partial = self.state.flush()
        if partial:
            self.store.save_hand(partial)
            self.hands += 1

    async def _wait_for_id(self, websocket: Any, command_id: int, label: str) -> None:
        while True:
            message = json.loads(await websocket.recv())
            if message.get("id") == command_id:
                if "error" in message:
                    raise RuntimeError(f"CDP {label} failed: {message['error']}")
                return
            await self._event(message)

    async def _try_command(
        self,
        websocket: Any,
        command_id: int,
        method: str,
        params: Dict[str, Any],
    ) -> None:
        await websocket.send(
            json.dumps(
                {"id": command_id, "method": method, "params": params}
            )
        )
        try:
            await self._wait_for_id(websocket, command_id, method)
        except RuntimeError:
            return

    async def _receive(self, websocket: Any) -> None:
        try:
            async for raw in websocket:
                await self._event(json.loads(raw))
        except asyncio.CancelledError:
            raise

    async def _event(self, message: Dict[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params", {})
        if method == "Runtime.bindingCalled" and params.get("name") == BINDING_NAME:
            payload = params.get("payload", "").encode("utf-8", errors="replace")
            self._record_payload("decoded", "runtime-binding", 1, payload, "")
            return
        if method == "Network.webSocketCreated":
            self.websocket_urls[params.get("requestId", "")] = safe_url(params.get("url", ""))
            return
        direction = {
            "Network.webSocketFrameReceived": "received",
            "Network.webSocketFrameSent": "sent",
        }.get(method)
        if not direction or (direction == "sent" and not self.include_sent):
            return
        response = params.get("response", {})
        opcode = int(response.get("opcode", 1))
        payload_data = response.get("payloadData", "")
        payload = cdp_payload(opcode, payload_data)
        request_id = params.get("requestId", "")
        self._record_payload(
            direction,
            request_id,
            opcode,
            payload,
            self.websocket_urls.get(request_id, ""),
        )

    def _record_payload(
        self,
        direction: str,
        request_id: str,
        opcode: int,
        payload: bytes,
        websocket_url: str,
    ) -> None:
        result = decode_payload(payload)
        frame = RawFrame(
            timestamp=utc_now(),
            direction=direction,
            request_id=request_id,
            opcode=opcode,
            payload_b64=base64.b64encode(payload).decode("ascii"),
            byte_length=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            websocket_url=websocket_url,
            decoded=result.value,
            encoding=result.encoding,
        )
        self.store.save_frame(frame)
        self.frames += 1
        self.encodings[result.encoding] += 1
        if result.value is not None:
            events = list(self.mapper.canonical_events(result.value))
            if direction == "decoded" and isinstance(result.value, dict):
                self.event_sequence += 1
                event_name = str(result.value.get("event") or "unknown")
                canonical_hand_id = next(
                    (
                        str(event["hand_id"])
                        for event in events
                        if event.get("hand_id") is not None
                    ),
                    None,
                )
                raw_event = RawEvent(
                    timestamp=utc_now(),
                    event_name=event_name,
                    payload=result.value,
                    sequence=self.event_sequence,
                    table_id=self.mapper.room_id,
                    hand_id=(
                        canonical_hand_id
                        or (
                            f"{self.mapper.room_id}-{self.mapper.bout}"
                            if self.mapper.room_id and self.mapper.bout
                            else None
                        )
                    ),
                    sha256=frame.sha256,
                )
                self.store.save_raw_event(raw_event)
            self.shapes[summarize_shape(result.value)] += 1
            for event in events:
                event["_event_sequence"] = self.event_sequence
                event["_captured_at"] = utc_now()
                try:
                    friendly = render_event(event)
                except Exception as error:
                    print(
                        f"Skipping live-log formatting error: {error}",
                        file=sys.stderr,
                    )
                    friendly = None
                if friendly:
                    self.store.append_live(friendly)
                if event.get("event") == "squid":
                    squid = self.squid_state.apply(
                        event,
                        timestamp=utc_now(),
                        sequence=self.event_sequence,
                    )
                    self.store.save_squid_event(squid, self.mapper.room_id)
                    self.squid_events += 1
                    if squid.scene in {1, 2, 3, 5, 6}:
                        self.active_squid_round_id = squid.round_id
                    elif squid.scene == 4:
                        self.active_squid_round_id = None
                    continue
                if event.get("event") == "start" and self.state.current is None:
                    existing = self.store.load_hand(str(event.get("hand_id")))
                    if existing and existing.status != "completed":
                        self.state.restore(existing)
                completed = self.state.apply_many([event], source=frame.sha256[:12])
                if self.state.current is not None:
                    current = self.state.current
                    if self.active_squid_round_id:
                        current.game_mode = "squid"
                        current.squid_round_id = self.active_squid_round_id
                    if event.get("event") == "decision_request":
                        decision = decision_state_from_hand(current)
                        if decision is not None:
                            self.store.save_decision_state(decision)
                    self.store.save_hand(current, final=False)
                for hand in completed:
                    if self.active_squid_round_id:
                        hand.game_mode = "squid"
                        hand.squid_round_id = self.active_squid_round_id
                    self.store.save_hand(hand)
                    self.store.append_live(render_hand_text(hand))
                    self.hands += 1

    def summary(self) -> Dict[str, Any]:
        return {
            "frames": self.frames,
            "hands": self.hands,
            "raw_events": self.event_sequence,
            "squid_events": self.squid_events,
            "encodings": dict(self.encodings.most_common()),
            "top_shapes": dict(self.shapes.most_common(20)),
        }


def _event_hook_script() -> str:
    events = json.dumps(RELEVANT_EVENTS)
    return f"""
(() => {{
  const hookVersion = 9;
  const keys = {events};
  const validCard = value => {{
    const card = Number(value);
    const suit = Math.floor(card / 100);
    const rank = card % 100;
    return Number.isInteger(card) && suit >= 1 && suit <= 4 && rank >= 1 && rank <= 13;
  }};
  const currentHeroCards = currentUserId => {{
    if (currentUserId == null || !window.cc || !cc.director) return null;
    try {{
      const scene = cc.director.getScene();
      const direct = window.cc.find ? cc.find("gameContr", scene) : null;
      const queue = direct ? [direct] : [scene];
      while (queue.length) {{
        const node = queue.shift();
        for (const component of (node && node._components) || []) {{
          if (!Array.isArray(component._dealList)) continue;
          const hero = component._dealList.find(
            player => player && String(player.userId) === String(currentUserId)
          );
          const cards = hero && hero.handCards;
          if (Array.isArray(cards) && cards.length === 2 && cards.every(validCard)) {{
            return cards.slice();
          }}
        }}
        if (!direct) queue.push(...((node && node.children) || []));
      }}
    }} catch (_error) {{}}
    return null;
  }};
  const currentPlayerStates = () => {{
    if (!window.cc || !cc.director) return null;
    try {{
      const scene = cc.director.getScene();
      const direct = window.cc.find ? cc.find("gameContr", scene) : null;
      const controller = ((direct && direct._components) || []).find(
        component => Array.isArray(component._dealList)
      );
      const seats = controller && controller._gameUI && controller._gameUI.seats;
      if (!Array.isArray(seats)) return null;
      return seats.map((root, index) => {{
        let userId = null;
        let isFold = null;
        let currentScore = null;
        let alias = null;
        let seatNum = null;
        const queue = [root];
        while (queue.length) {{
          const node = queue.shift();
          for (const component of (node && node._components) || []) {{
            try {{
              if (component.userId != null) userId = component.userId;
              if (typeof component.isFold === "boolean") isFold = component.isFold;
              if (component.currentScore != null) currentScore = component.currentScore;
              if (component._playerFullName) alias = component._playerFullName;
              if (component._curSeatNum != null) seatNum = component._curSeatNum;
            }} catch (_error) {{}}
          }}
          queue.push(...((node && node.children) || []));
        }}
        return userId == null ? null : {{
          userId,
          isFold,
          currentScore,
          alias,
          seatNum,
          localSeatNum: index,
        }};
      }}).filter(Boolean);
    }} catch (_error) {{}}
    return null;
  }};
  const safe = (key, event) => {{
    try {{
      const envelope = event && event.getUserData ? event.getUserData() : (event && event.detail);
      const body = envelope && Object.prototype.hasOwnProperty.call(envelope, "msgBody")
        ? envelope.msgBody : envelope;
      const currentUserId = window.CurrentUserInfo && window.CurrentUserInfo.user
        ? window.CurrentUserInfo.user.userId : null;
      const isDealEvent = (
        key === "dealNotify" || key === "dealNotify_reconnection"
      );
      // The deal callback can run before Cocos replaces the previous hand's
      // decrypted _dealList. Never persist that synchronous value.
      const recorderHeroCards = isDealEvent
        ? null : currentHeroCards(currentUserId);
      const payload = JSON.stringify(
        {{
          event: key,
          data: body,
          sysTime: envelope && envelope.sysTime,
          _recorderCurrentUserId: currentUserId,
          _recorderHeroCards: recorderHeroCards,
          _recorderPlayerStates: currentPlayerStates(),
        }},
        (_key, value) => typeof value === "bigint" ? value.toString() : value
      );
      window.{BINDING_NAME}(payload);
      if (isDealEvent) {{
        for (const delay of [0, 100, 300, 700, 1500, 3000]) {{
          setTimeout(
            () => safe("recorderHeroCards", {{ detail: {{}} }}),
            delay,
          );
        }}
      }}
    }} catch (_error) {{}}
  }};
  const install = () => {{
    if (!window.cc || !cc.director || !window.WePokerWebSocketMsgTypes) return false;
    if (window.__wpkRecorderHookVersion === hookVersion) return true;
    for (const old of (window.__wpkRecorderHandlers || [])) {{
      try {{ cc.director.off(old[0], old[1]); }} catch (_error) {{}}
    }}
    window.__wpkRecorderHandlers = [];
    for (const key of keys) {{
      const eventName = window.WePokerWebSocketMsgTypes[key];
      if (!eventName) continue;
      const handler = event => safe(key, event);
      cc.director.on(eventName, handler);
      window.__wpkRecorderHandlers.push([eventName, handler]);
    }}
    window.__wpkRecorderHooked = true;
    window.__wpkRecorderHookVersion = hookVersion;
    setTimeout(() => safe("recorderPlayerState", {{ detail: {{}} }}), 0);
    return true;
  }};
  if (install()) return `hooked:${{window.__wpkRecorderHandlers.length}}`;
  window.__wpkRecorderInstallTimer = setInterval(() => {{
    if (install()) clearInterval(window.__wpkRecorderInstallTimer);
  }}, 500);
  return "waiting-for-client";
}})()
"""
