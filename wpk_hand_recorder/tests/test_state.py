import json
from pathlib import Path

from wpk_recorder.cdp import _event_hook_script
from wpk_recorder.decision_state import (
    canonical_legal_actions,
    decision_state_contract,
    decision_state_from_hand,
)
from wpk_recorder.formatting import display_card, render_hand_text
from wpk_recorder.models import Action, DecisionRequest, HandHistory, Player
from wpk_recorder.protocol import ProtocolMapper
from wpk_recorder.reasoning import live_decision_from_hand
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


def test_live_and_replay_decisions_share_projection_and_hash():
    hand = HandHistory(
        hand_id="room-1",
        button_seat=3,
        small_blind=2,
        big_blind=4,
        ante=1,
        pot=250,
        game_mode="squid",
        squid_round_id="sq-1",
    )
    hand.players = {
        1: Player(1, user_id="v1", stack_start=100),
        2: Player(2, user_id="v2", stack_start=50),
        3: Player(3, user_id="hero", stack_start=100, is_hero=True),
    }
    hand.actions = [
        Action("preflop", 1, "V1", "all_in", 100, 100, "v1", stack_after=0, sequence=1),
        Action("preflop", 2, "V2", "all_in", 50, 50, "v2", stack_after=0, sequence=2),
        Action("preflop", 3, "Hero", "call", 100, 100, "hero", stack_after=0, sequence=3),
    ]
    request = DecisionRequest(
        event_sequence=17,
        captured_at="2026-01-01T00:00:00+00:00",
        hand_id=hand.hand_id,
        seat=3,
        user_id="hero",
        cards=["As", "Ks"],
        legal_actions=["fold", "call"],
        countdown=20,
        current_score=100,
    )

    live = decision_state_from_hand(
        hand, request, source="live", remaining_ms=15_000
    )
    replay = decision_state_from_hand(
        hand, request, source="replay", remaining_ms=20_000
    )

    assert live is not None and replay is not None
    assert live["state_hash"] == replay["state_hash"]
    assert sum(pot["amount"] for pot in live["side_pots"]) == 250
    assert live["side_pots"][0]["eligible_seats"] == [1, 2, 3]
    assert live["side_pots"][1]["eligible_seats"] == [1, 3]
    assert live["players"][1]["all_in"] is True
    assert live["action_order"] == [3, 1, 2]


def test_decision_position_prefers_button_derived_position_over_seat_label():
    hand = HandHistory(
        hand_id="position-1",
        button_seat=7,
        small_blind=2,
        big_blind=4,
        ante=1,
    )
    hand.players = {
        seat: Player(
            seat,
            user_id=f"p{seat}",
            stack_start=800,
            position=f"Seat {seat}",
        )
        for seat in range(1, 10)
    }
    request = DecisionRequest(
        event_sequence=18,
        captured_at="2026-01-01T00:00:00+00:00",
        hand_id=hand.hand_id,
        seat=8,
        user_id="p8",
        legal_actions=["fold", "call", "raise"],
        call_score=2,
        seat_score=2,
        current_score=798,
        countdown=20,
    )

    decision = decision_state_from_hand(
        hand,
        request,
        remaining_ms=15_000,
    )

    assert decision is not None
    assert decision["hero_position"] == "SB"
    assert next(
        player for player in decision["players"] if player["seat"] == 7
    )["position"] == "BTN"
    assert next(
        player for player in decision["players"] if player["seat"] == 9
    )["position"] == "BB"


def test_pending_decision_is_invalidated_by_next_action():
    state = HandStateMachine()
    state.apply({"event": "start", "hand_id": "h1"})
    state.apply(
        {
            "event": "player",
            "seat": 1,
            "user_id": "hero",
            "is_hero": True,
            "stack": 100,
        }
    )
    state.apply(
        {
            "event": "decision_request",
            "hand_id": "h1",
            "seat": 1,
            "user_id": "hero",
            "is_hero": True,
            "cards": ["As", "Ks"],
            "legal_actions": ["fold", "call"],
            "_event_sequence": 7,
        }
    )
    assert state.current is not None
    assert state.current.pending_decision is not None
    state.apply({"event": "action", "seat": 1, "action": "call", "amount": 4})
    assert state.current.pending_decision is None


def test_check_removes_dominated_fold_from_canonical_actions():
    assert canonical_legal_actions(
        ["FOLD", "RAISE", "ALL_IN", "CHECK"]
    ) == ("raise", "all_in", "check")


def test_observer_decision_is_kept_for_full_range_inference():
    state = HandStateMachine()
    state.apply({"event": "start", "hand_id": "observer-hand"})
    state.apply(
        {
            "event": "player",
            "seat": 1,
            "user_id": "villain-1",
            "alias": "Villain 1",
            "stack": 100,
            "is_hero": False,
        }
    )
    state.apply(
        {
            "event": "player",
            "seat": 2,
            "user_id": "villain",
            "alias": "Villain",
            "stack": 100,
            "is_hero": False,
        }
    )
    state.apply(
        {
            "event": "decision_request",
            "hand_id": "observer-hand",
            "seat": 2,
            "user_id": "villain",
            "is_hero": False,
            "cards": ["As", "Ks"],
            "legal_actions": ["check", "raise"],
            "countdown": 15,
            "_event_sequence": 8,
        }
    )
    assert state.current is not None
    assert state.current.pending_decision is not None
    assert state.current.pending_decision.cards == []
    assert state.current.players[1].is_hero is False
    assert state.current.players[2].hole_cards == []
    decision = live_decision_from_hand(state.current)
    assert decision is not None
    assert decision["decision_subject"] == "observer"
    assert decision["hero_cards"] == []


def test_seated_hero_does_not_get_advice_during_opponent_action():
    state = HandStateMachine()
    state.apply({"event": "start", "hand_id": "seated-hand"})
    state.apply(
        {
            "event": "player",
            "seat": 1,
            "user_id": "hero",
            "is_hero": True,
            "stack": 100,
        }
    )
    state.apply(
        {
            "event": "player",
            "seat": 2,
            "user_id": "villain",
            "stack": 100,
        }
    )
    state.apply(
        {
            "event": "decision_request",
            "hand_id": "seated-hand",
            "seat": 2,
            "user_id": "villain",
            "is_hero": False,
            "legal_actions": ["check", "raise"],
            "countdown": 15,
            "_event_sequence": 9,
        }
    )

    assert state.current is not None
    assert live_decision_from_hand(state.current) is None


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


def test_history_does_not_replace_captured_hand_start_time():
    state = HandStateMachine()
    state.apply({"event": "start", "hand_id": "room-1"})
    assert state.current is not None
    state.current.started_at = "2026-09-08T10:44:18.500000+00:00"
    completed = state.apply({"event": "end"})
    assert completed is not None

    updated = state.apply(
        {
            "event": "history",
            "hand_id": "room-1",
            "started_at": "1788864314625",
            "players": [],
        }
    )

    assert updated is not None
    assert updated.started_at == "2026-09-08T10:44:18.500000+00:00"


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


def test_banker_change_updates_next_hand_button_without_roster_change():
    mapper = ProtocolMapper()
    mapper.room_id = "99"
    mapper.bout = "8"
    mapper.button_seat = 1

    assert list(
        mapper.canonical_events(
            {
                "event": "bankerChangeNotify",
                "data": {"bankerSeatNum": 2},
            }
        )
    ) == []
    events = list(
        mapper.canonical_events(
            {
                "event": "dealNotify",
                "data": {"currentBoutNum": 9, "list": []},
            }
        )
    )

    assert events[0]["event"] == "start"
    assert events[0]["hand_id"] == "99-9"
    assert events[0]["button_seat"] == 2


def test_user_option_event_does_not_treat_spectated_actor_as_hero():
    mapper = ProtocolMapper()
    state = HandStateMachine()
    room_events = mapper.canonical_events(
        {
            "event": "upDateRoomNotify",
            "data": {
                "roomId": 99,
                "currentBoutNum": 8,
                "round": "PRE_FLOP",
                "currentUserSeatNum": 0,
                "sitUserList": [
                    {
                        "seatNum": 1,
                        "userId": 123,
                        "nickname": "Hero",
                        "currentScore": 200,
                        "handCards": [],
                    }
                ],
            },
        }
    )
    state.apply_many(room_events)
    decision = next(
        iter(
            mapper.canonical_events(
                {
                    "event": "userOptNotify",
                    "data": {
                        "userId": 123,
                        "handCards": [101, 113],
                        "canActionList": ["FOLD", "CALL", "RAISE"],
                        "callScore": 4,
                        "countDown": 15,
                    },
                }
            )
        )
    )
    assert decision["event"] == "decision_request"
    assert decision["user_id"] == "123"
    assert decision["is_hero"] is False
    state.apply(decision)
    assert state.current is not None
    assert state.current.players[1].is_hero is False
    assert state.current.players[1].hole_cards == []


def test_runtime_current_user_identifies_hero_without_current_seat():
    mapper = ProtocolMapper()
    state = HandStateMachine()
    room_events = mapper.canonical_events(
        {
            "event": "upDateRoomNotify",
            "_recorderCurrentUserId": 123,
            "data": {
                "roomId": 99,
                "currentBoutNum": 8,
                "round": "PRE_FLOP",
                "currentUserSeatNum": 0,
                "sitUserList": [
                    {
                        "seatNum": 1,
                        "userId": 123,
                        "nickname": "Hero",
                        "currentScore": 200,
                        "handCards": [101, 113],
                    }
                ],
            },
        }
    )
    state.apply_many(room_events)
    decision = next(
        iter(
            mapper.canonical_events(
                {
                    "event": "userOptNotify",
                    "_recorderCurrentUserId": 123,
                    "data": {
                        "userId": 123,
                        "handCards": [101, 113],
                        "canActionList": ["FOLD", "CALL", "RAISE"],
                    },
                }
            )
        )
    )
    assert decision["is_hero"] is True
    state.apply(decision)
    assert state.current is not None
    assert state.current.players[1].is_hero is True
    assert state.current.players[1].hole_cards == ["As", "Ks"]


def test_runtime_uses_decrypted_client_cards_over_wire_placeholders():
    mapper = ProtocolMapper()
    state = HandStateMachine()
    state.apply_many(
        mapper.canonical_events(
            {
                "event": "upDateRoomNotify",
                "_recorderCurrentUserId": 123,
                "data": {
                    "roomId": 99,
                    "currentBoutNum": 8,
                    "round": "RIVER",
                    "currentUserSeatNum": 1,
                    "sitUserList": [
                        {
                            "seatNum": 1,
                            "userId": 123,
                            "nickname": "Hero",
                            "handCards": [],
                        }
                    ],
                },
            }
        )
    )
    decision = next(
        iter(
            mapper.canonical_events(
                {
                    "event": "userOptNotify",
                    "_recorderCurrentUserId": 123,
                    "_recorderHeroCards": [209, 106],
                    "data": {
                        "userId": 123,
                        "handCards": [-1, -1],
                        "canActionList": ["FOLD", "CHECK", "RAISE"],
                    },
                }
            )
        )
    )
    state.apply(decision)

    assert decision["cards"] == ["9h", "6s"]
    assert state.current is not None
    assert state.current.pending_decision is not None
    assert state.current.pending_decision.cards == ["9h", "6s"]
    assert state.current.players[1].hole_cards == ["9h", "6s"]


def test_runtime_does_not_attach_hero_cards_to_opponent_action():
    mapper = ProtocolMapper()
    list(
        mapper.canonical_events(
            {
                "event": "upDateRoomNotify",
                "_recorderCurrentUserId": 123,
                "data": {
                    "roomId": 99,
                    "currentBoutNum": 8,
                    "round": "FLOP",
                    "currentUserSeatNum": 1,
                    "sitUserList": [
                        {"seatNum": 1, "userId": 123},
                        {"seatNum": 5, "userId": 456},
                    ],
                },
            }
        )
    )
    decision = next(
        iter(
            mapper.canonical_events(
                {
                    "event": "userOptNotify",
                    "_recorderCurrentUserId": 123,
                    "_recorderHeroCards": [209, 106],
                    "data": {
                        "userId": 456,
                        "handCards": [],
                        "canActionList": ["FOLD", "CHECK", "RAISE"],
                    },
                }
            )
        )
    )

    assert decision["is_hero"] is False
    assert decision["cards"] == []


def test_round_change_captures_hero_first_to_act_decision():
    mapper = ProtocolMapper()
    state = HandStateMachine()
    state.apply_many(
        mapper.canonical_events(
            {
                "event": "upDateRoomNotify",
                "_recorderCurrentUserId": 123,
                "data": {
                    "roomId": 99,
                    "currentBoutNum": 8,
                    "round": "PRE_FLOP",
                    "currentUserSeatNum": 1,
                    "sitUserList": [
                        {"seatNum": 1, "userId": 123, "nickname": "Hero"},
                        {"seatNum": 5, "userId": 456, "nickname": "Villain"},
                    ],
                },
            }
        )
    )
    events = list(
        mapper.canonical_events(
            {
                "event": "roundChangeNotify",
                "_recorderCurrentUserId": 123,
                "_recorderHeroCards": [204, 403],
                "data": {
                    "round": "FLOP",
                    "dealPublicCards": [112, 202, 310],
                    "totalPot": 4,
                    "userAciton": {
                        "userId": 123,
                        "handCards": [204, 403],
                        "canActionList": ["FOLD", "RAISE", "ALL_IN", "CHECK"],
                        "callScore": 0,
                        "countDown": 12,
                        "currentScore": 195,
                    },
                },
            }
        )
    )
    state.apply_many(events)

    assert [event["event"] for event in events] == ["board", "decision_request"]
    assert state.current is not None
    assert state.current.board == ["Qs", "2h", "Tc"]
    assert state.current.pending_decision is not None
    assert state.current.pending_decision.user_id == "123"
    assert state.current.pending_decision.cards == ["4h", "3d"]
    assert state.current.pending_decision.legal_actions == [
        "fold",
        "raise",
        "all_in",
        "check",
    ]


def test_wpk_raise_increments_and_short_stack_all_in_are_canonicalized():
    mapper = ProtocolMapper()
    decision = next(
        iter(
            mapper.canonical_events(
                {
                    "event": "userOptNotify",
                    "data": {
                        "userId": 11821240,
                        "canActionList": ["FOLD", "ALL_IN"],
                        "callScore": 544,
                        "minRaiseScore": 1088,
                        "maxRaiseScore": 66,
                        "seatScore": 272,
                        "currentScore": 0,
                    },
                }
            )
        )
    )

    assert decision["min_raise_to"] == 1360
    assert decision["max_raise_to"] == 338
    assert decision["seat_score"] == 272
    assert decision["current_score"] == 66

    contract = decision_state_contract(
        {
            "decision_id": "short-all-in",
            "sequence": 1,
            "hand_id": "hand-1",
            "street": "river",
            "acting_seat": 5,
            "hero_cards": ["As", "Ah"],
            "legal_actions": ["fold", "all_in"],
            "board": ["2c", "3d", "4h", "5s", "Kd"],
            "pot": 2000,
            "reconstructed_pot": 2000,
            "players": [{"seat": 5, "active": True, "is_hero": True}],
            "call_score": 544,
            "min_raise_to": decision["min_raise_to"],
            "max_raise_to": decision["max_raise_to"],
            "hero_street_contribution": decision["seat_score"],
            "hero_stack": decision["current_score"],
            "big_blind": 4,
            "players_in_hand": 2,
            "table_players": 9,
            "game_mode": "squid",
            "remaining_ms": 10000,
        }
    )
    assert contract["quality"]["valid"] is True
    assert "剩余筹码不足常规最小加注，仅允许不足额全下" in (
        contract["quality"]["warnings"]
    )

    invalid_raise = decision_state_contract(
        {
            **contract,
            "decision_id": "invalid-raise",
            "legal_actions": ["fold", "raise", "all_in"],
        }
    )
    assert invalid_raise["quality"]["valid"] is False
    assert "最小加注额高于最大加注额" in (
        invalid_raise["quality"]["blocking_reasons"]
    )


def test_browser_hook_reads_decrypted_deal_list_for_hero():
    script = _event_hook_script()

    assert "const hookVersion = 9" in script
    assert "component._dealList" in script
    assert "currentHeroCards(currentUserId)" in script
    assert "const recorderHeroCards = isDealEvent" in script
    assert "_recorderHeroCards: recorderHeroCards" in script
    assert 'safe("recorderHeroCards"' in script
    assert "3000" in script
    assert "typeof component.isFold" in script
    assert "_recorderPlayerStates: currentPlayerStates()" in script


def test_delayed_browser_hero_cards_update_current_hand_before_action():
    mapper = ProtocolMapper()
    state = HandStateMachine()
    state.apply_many(
        mapper.canonical_events(
            {
                "event": "upDateRoomNotify",
                "_recorderCurrentUserId": 123,
                "data": {
                    "roomId": 99,
                    "currentBoutNum": 9,
                    "round": "PRE_FLOP",
                    "currentUserSeatNum": 5,
                    "sitUserList": [
                        {
                            "seatNum": 5,
                            "userId": 123,
                            "nickname": "Hero",
                        },
                        {
                            "seatNum": 6,
                            "userId": 456,
                            "nickname": "Villain",
                        },
                    ],
                },
            }
        )
    )
    events = list(
        mapper.canonical_events(
            {
                "event": "recorderHeroCards",
                "_recorderCurrentUserId": 123,
                "_recorderHeroCards": [209, 106],
                "_recorderPlayerStates": [
                    {
                        "seatNum": 5,
                        "userId": 123,
                        "alias": "Hero",
                    }
                ],
                "data": {},
            }
        )
    )
    state.apply_many(events)

    assert [event["event"] for event in events] == ["player"]
    assert events[0]["is_hero"] is True
    assert state.current is not None
    assert state.current.pending_decision is None
    assert state.current.players[5].hole_cards == ["9h", "6s"]


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


def test_history_maps_insurance_invest_and_payout_to_signed_result():
    mapper = ProtocolMapper()
    history = next(
        event
        for event in mapper.canonical_events(
            {
                "event": "updateHistoryData",
                "data": {
                    "handList1": [
                        {
                            "roomId": 1,
                            "handNum": 2,
                            "seatNum": 1,
                            "userId": 10,
                            "changeScore": 468,
                            "insuranceInvest": 93,
                            "insuranceWin": 561,
                        }
                    ]
                },
            }
        )
        if event["event"] == "history"
    )

    assert history["players"][0]["insurance_result"] == 468


def test_open_card_events_normalize_numeric_cards_before_formatting():
    mapper = ProtocolMapper()
    mapper.user_seats["10"] = 3
    mapper.user_aliases["10"] = "Player"

    event = next(
        iter(
            mapper.canonical_events(
                {
                    "event": "openCardNotify",
                    "data": {
                        "userId": 10,
                        "publicCards": [403, -100],
                    },
                }
            )
        )
    )

    assert event["event"] == "player"
    assert event["seat"] == 3
    assert event["cards"] == ["3d"]
    assert display_card(403) == "403"


def test_incremental_open_card_events_merge_both_revealed_slots():
    mapper = ProtocolMapper()
    mapper.user_seats["10"] = 3
    state = HandStateMachine()
    state.apply({"event": "start", "hand_id": "showdown-1"})

    for public_cards, action_card in (
        ([110, -100], 0),
        ([-100, 309], 1),
    ):
        state.apply_many(
            mapper.canonical_events(
                {
                    "event": "openCardNotify",
                    "data": {
                        "userId": 10,
                        "publicCards": public_cards,
                        "actionCard": action_card,
                    },
                }
            )
        )

    assert state.current is not None
    assert state.current.players[3].hole_cards == ["Ts", "9c"]


def test_play_result_prefers_complete_public_cards_over_high_subset():
    mapper = ProtocolMapper()
    events = list(
        mapper.canonical_events(
            {
                "event": "playResultNotify",
                "data": {
                    "thanList": [
                        {
                            "seatNum": 3,
                            "userId": 10,
                            "publicCards": [404, 306],
                            "highHandCards": [404],
                            "changeAfterScore": 100,
                        }
                    ]
                },
            }
        )
    )

    result = next(event for event in events if event["event"] == "result")
    assert result["cards"] == ["4d", "6c"]


def test_history_reconstruction_starts_action_sequence_at_one():
    state = HandStateMachine()
    state._sequence = 20

    hand = state.apply(
        {
            "event": "history",
            "hand_id": "room-7",
            "players": [
                {
                    "seat": 1,
                    "actions": [
                        {"street": "preflop", "action": "call", "amount": 2}
                    ],
                }
            ],
        }
    )

    assert [action.sequence for action in hand.actions] == [1]


def test_action_id_starts_correct_hand_before_deal_notification():
    mapper = ProtocolMapper()
    mapper.room_id = "99"
    mapper.bout = "33"

    events = list(
        mapper.canonical_events(
            {
                "event": "actionNotify",
                "data": {
                    "actionList": [
                        {
                            "actionId": 34001,
                            "actionType": "ANTE",
                            "round": "CLEAN",
                            "seatNum": 1,
                        }
                    ]
                },
            }
        )
    )

    assert events[0]["event"] == "start"
    assert events[0]["hand_id"] == "99-34"
    assert events[1]["hand_id"] == "99-34"
    assert events[1]["street"] == "preflop"


def test_forced_blinds_repair_stale_button_after_player_changes():
    state = HandStateMachine()
    state.apply(
        {
            "event": "start",
            "hand_id": "room-34",
            "button_seat": 1,
        }
    )
    for seat in (1, 2, 3, 4, 5):
        state.apply(
            {
                "event": "player",
                "seat": seat,
                "user_id": f"user-{seat}",
            }
        )
        state.apply(
            {
                "event": "action",
                "hand_id": "room-34",
                "seat": seat,
                "street": "preflop",
                "action": "ante",
                "action_id": f"3400{seat}",
            }
        )
    state.apply(
        {
            "event": "action",
            "hand_id": "room-34",
            "seat": 3,
            "street": "preflop",
            "action": "small_blind",
            "action_id": "34010",
        }
    )

    assert state.current is not None
    assert state.current.button_seat == 2


def test_heads_up_small_blind_is_also_button():
    state = HandStateMachine()
    state.apply(
        {
            "event": "start",
            "hand_id": "heads-up",
            "button_seat": 5,
        }
    )
    for seat in (2, 5):
        state.apply({"event": "player", "seat": seat})
    state.apply(
        {
            "event": "action",
            "hand_id": "heads-up",
            "seat": 2,
            "street": "preflop",
            "action": "small_blind",
        }
    )

    assert state.current is not None
    assert state.current.button_seat == 2


def test_pending_action_is_not_attached_to_different_hand():
    state = HandStateMachine()
    state.apply(
        {
            "event": "action",
            "hand_id": "room-34",
            "action_id": "34001",
            "action": "ante",
        }
    )
    state.apply({"event": "start", "hand_id": "room-35"})

    assert state.current is not None
    assert state.current.actions == []


def test_history_for_previous_hand_does_not_advance_live_sequence():
    state = HandStateMachine()
    state.apply({"event": "start", "hand_id": "room-35"})
    state.apply({"event": "action", "hand_id": "room-35", "action": "call"})
    state.apply(
        {
            "event": "history",
            "hand_id": "room-34",
            "players": [
                {
                    "seat": 1,
                    "actions": [
                        {"street": "preflop", "action": "call", "amount": 2}
                    ],
                }
            ],
        }
    )
    state.apply({"event": "action", "hand_id": "room-35", "action": "check"})

    assert state.current is not None
    assert [action.sequence for action in state.current.actions] == [1, 2]
