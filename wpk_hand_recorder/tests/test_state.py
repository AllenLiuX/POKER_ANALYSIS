import json
from pathlib import Path

from wpk_recorder.formatting import render_hand_text
from wpk_recorder.models import Action, HandHistory
from wpk_recorder.protocol import ProtocolMapper
from wpk_recorder.state import HandStateMachine
from wpk_recorder.squid import SquidStateMachine


def test_protocol_mapping_and_complete_hand():
    messages = [
        {"event": "hand_start", "handId": "H1", "tableId": "T1"},
        {"event": "seat", "seat": 1, "nickname": "Hero", "stack": 100},
        {"event": "player_action", "seat": 1, "action": "raise", "amount": 6},
        {"event": "deal", "communityCards": ["As", "Kd", "2c"]},
        {"event": "player_action", "seat": 1, "action": "bet", "amount": 8},
        {"event": "settlement", "seat": 1, "net": 10, "complete": False},
        {"event": "hand_end"},
    ]
    mapper = ProtocolMapper()
    state = HandStateMachine()
    completed = []
    for message in messages:
        completed.extend(state.apply_many(mapper.canonical_events(message)))

    assert len(completed) == 1
    hand = completed[0]
    assert hand.hand_id == "H1"
    assert hand.board == ["As", "Kd", "2c"]
    assert [action.street for action in hand.actions] == ["preflop", "flop"]
    assert hand.players[1].alias == "Hero"


def test_duplicate_events_are_ignored():
    state = HandStateMachine()
    state.apply({"event": "start", "hand_id": "H2"})
    action = {"event": "action", "seat": 2, "action": "fold"}
    state.apply(action)
    state.apply(action)
    hand = state.flush()
    assert len(hand.actions) == 1
    assert hand.status == "partial"


def test_sanitized_wpk_runtime_fixture_reconstructs_exact_hand():
    fixture = Path(__file__).parent / "fixtures" / "wpk_hand_events.json"
    messages = json.loads(fixture.read_text(encoding="utf-8"))
    mapper = ProtocolMapper()
    state = HandStateMachine()
    completed = []
    for message in messages:
        completed.extend(state.apply_many(mapper.canonical_events(message)))

    hand = completed[-1]
    assert hand.hand_id == "999-7"
    assert hand.status == "completed"
    assert hand.board == ["3d", "5d", "Js", "5h", "Ad"]
    assert hand.players[1].hole_cards == ["6d", "Jd"]
    assert hand.players[1].is_hero is True
    assert [action.action for action in hand.actions] == [
        "small_blind",
        "big_blind",
        "raise",
        "call",
    ]
    assert hand.actions[2].amount == 9
    assert hand.actions[2].amount_to == 10
    assert sum(player.net or 0 for player in hand.players.values()) == 0
    text = render_hand_text(hand)
    assert "6♦ J♦" in text
    assert "Villain：加注 9（到 10）" in text


def test_all_squid_scenes_are_losslessly_mapped():
    fixture = Path(__file__).parent / "fixtures" / "wpk_squid_events.json"
    messages = json.loads(fixture.read_text(encoding="utf-8"))
    mapper = ProtocolMapper()
    events = [
        event
        for message in messages
        for event in mapper.canonical_events(message)
        if event["event"] == "squid"
    ]
    assert [event["scene"] for event in events] == [1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 4]
    assert events[0]["users"] == ["101", "202", "303"]
    assert events[-2]["hand_refs"] == ["7", "8"]
    assert events[-1]["settlements"][0]["addScore"] == 30
    state = SquidStateMachine()
    aggregated = [
        state.apply(event, "2026-01-01T00:00:00+00:00", sequence)
        for sequence, event in enumerate(events, 1)
    ]
    assert aggregated[1].users == ["101", "202", "303", "404"]
    assert aggregated[-1].hand_refs == ["7", "8"]
    assert aggregated[-1].round_id in state.closed_rounds


def test_same_hand_room_refresh_and_empty_history_do_not_erase_board():
    state = HandStateMachine()
    state.apply({"event": "start", "hand_id": "room-1", "big_blind": 2})
    state.apply(
        {
            "event": "board",
            "street": "flop",
            "append_cards": ["Js", "Tc", "2h"],
            "pot": 151,
        }
    )
    assert state.current is not None
    state.apply({"event": "start", "hand_id": "room-1", "big_blind": 2})
    assert state.current.board == ["Js", "Tc", "2h"]
    completed = state.apply({"event": "end"})
    assert completed is not None
    updated = state.apply(
        {
            "event": "history",
            "hand_id": "room-1",
            "board": [],
            "players": [],
        }
    )
    assert updated is not None
    assert updated.board == ["Js", "Tc", "2h"]


def test_room_snapshot_resynchronizes_board_after_background_resume():
    mapper = ProtocolMapper()
    events = list(
        mapper.canonical_events(
            {
                "event": "upDateRoomNotify",
                "data": {
                    "roomId": 99,
                    "currentBoutNum": 8,
                    "round": "TURN",
                    "publicCards": [307, 313, 204, 111],
                    "totalPot": 2469,
                    "sitUserList": [],
                },
            }
        )
    )
    board = next(event for event in events if event["event"] == "board")
    assert board["board"] == ["7c", "Kc", "4h", "Js"]
    assert board["pot"] == 2469


def test_restored_hand_skips_replayed_action_history():
    restored = HandHistory(hand_id="room-2")
    restored.actions = [
        Action(
            "preflop",
            1,
            "Player",
            "raise",
            amount=4,
            amount_to=6,
            user_id="1",
            action_id="a1",
            sequence=1,
        )
    ]
    state = HandStateMachine()
    state.restore(restored)
    state.apply(
        {
            "event": "action",
            "seat": 1,
            "action": "raise",
            "amount": 4,
            "amount_to": 6,
            "action_id": "a1",
            "_source_event": "actionHistoryNotify",
        }
    )
    assert len(state.current.actions) == 1
    state.apply(
        {
            "event": "action",
            "seat": 2,
            "action": "call",
            "amount": 4,
            "amount_to": 6,
            "action_id": "a2",
            "_source_event": "actionNotify",
        }
    )
    assert len(state.current.actions) == 2
