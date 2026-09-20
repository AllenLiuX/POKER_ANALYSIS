import hashlib
import asyncio
import json
import sqlite3

from fastapi.testclient import TestClient

from wpk_recorder.analytics import _apply_runtime_player_states, snapshot
from wpk_recorder.models import (
    Action,
    HandHistory,
    Player,
    RawEvent,
    SquidEvent,
)
from wpk_recorder.server import _event_stream, create_app
from wpk_recorder.storage import RecorderStore


def _save_range_node_hand(
    tmp_path,
    *,
    status="completed",
    folded=False,
):
    store = RecorderStore(tmp_path)
    hand = HandHistory(
        hand_id="node-1",
        table_id="node",
        started_at="2026-01-01T00:00:00+00:00",
        ended_at=(
            "2026-01-01T00:02:00+00:00"
            if status == "completed"
            else None
        ),
        button_seat=1,
        small_blind=1,
        big_blind=2,
        board=["As", "Kh", "2c", "7d", "9s"],
        pot=40,
        status=status,
    )
    hand.players = {
        1: Player(
            1,
            user_id="hero",
            alias="Hero",
            is_hero=True,
            stack_start=200,
            hole_cards=["Qh", "Jh"],
        ),
        2: Player(
            2,
            user_id="villain",
            alias="Villain",
            stack_start=200,
        ),
    }
    hand.actions = [
        Action(
            "preflop",
            2,
            "Villain",
            "ante",
            amount=1,
            amount_to=1,
            user_id="villain",
            sequence=0,
        ),
        Action(
            "preflop",
            1,
            "Hero",
            "raise",
            amount=6,
            amount_to=6,
            user_id="hero",
            sequence=1,
        ),
        Action(
            "preflop",
            2,
            "Villain",
            "call",
            amount=4,
            amount_to=6,
            user_id="villain",
            sequence=2,
        ),
        Action(
            "flop",
            2,
            "Villain",
            "fold" if folded else "check",
            user_id="villain",
            sequence=3,
        ),
        Action(
            "river",
            1,
            "Hero",
            "bet",
            amount=20,
            amount_to=20,
            user_id="hero",
            sequence=4,
        ),
    ]
    store.save_hand(hand, final=status == "completed")
    store.close()


def test_latest_runtime_player_states_overlay_live_fold_status():
    hand = {
        "hand_id": "T1-9",
        "status": "in_progress",
        "players": [
            {"seat": 1, "user_id": "one"},
            {"seat": 2, "user_id": "two"},
        ],
    }
    events = [
        {
            "sequence": 12,
            "hand_id": "T1-9",
            "payload": {
                "_recorderPlayerStates": [
                    {"userId": "one", "isFold": False},
                    {"userId": "two", "isFold": True},
                ]
            },
        },
        {
            "sequence": 11,
            "hand_id": "T1-9",
            "payload": {
                "_recorderPlayerStates": [
                    {"userId": "one", "isFold": True},
                    {"userId": "two", "isFold": False},
                ]
            },
        },
    ]

    _apply_runtime_player_states(hand, events)

    assert hand["players"][0]["folded"] is False
    assert hand["players"][1]["folded"] is True
    assert hand["player_state_source"] == "client_runtime"
    assert hand["player_state_sequence"] == 12


def test_opportunity_schema_upgrade_rebuilds_historical_metrics(tmp_path):
    store = RecorderStore(tmp_path)
    hand = HandHistory(
        hand_id="historical-probe",
        button_seat=2,
        big_blind=2,
        board=["As", "7h", "2c", "Kd"],
        status="completed",
    )
    hand.players = {
        1: Player(1, user_id="bb", alias="BB", stack_start=200),
        2: Player(2, user_id="btn", alias="BTN", stack_start=200),
    }
    hand.actions = [
        Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
        Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
        Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
        Action("flop", 2, "BTN", "check", sequence=4, user_id="btn"),
        Action("turn", 1, "BB", "bet", amount=8, sequence=5, user_id="bb"),
    ]
    store.save_hand(hand)
    with store.connection:
        store.connection.execute(
            """
            DELETE FROM opportunities
            WHERE hand_id = ? AND metric = 'turn_probe'
            """,
            (hand.hand_id,),
        )
        store.connection.execute(
            """
            UPDATE recorder_metadata SET value = '1'
            WHERE key = 'opportunity_schema_version'
            """
        )
    store.close()

    upgraded = RecorderStore(tmp_path)
    metric = upgraded.connection.execute(
        """
        SELECT success FROM opportunities
        WHERE hand_id = ? AND metric = 'turn_probe'
        """,
        (hand.hand_id,),
    ).fetchone()
    version = upgraded.connection.execute(
        """
        SELECT value FROM recorder_metadata
        WHERE key = 'opportunity_schema_version'
        """
    ).fetchone()
    upgraded.close()

    assert tuple(metric) == (1,)
    assert tuple(version) == ("2",)


def test_normalized_storage_analytics_and_dashboard(tmp_path):
    store = RecorderStore(tmp_path)
    payload = {"event": "actionNotify", "data": {"actionList": [{"actionId": 1}]}}
    store.save_raw_event(
        RawEvent(
            timestamp="2026-01-01T00:00:00+00:00",
            event_name="actionNotify",
            payload=payload,
            sequence=1,
            table_id="T1",
            hand_id="T1-1",
            sha256=hashlib.sha256(repr(payload).encode()).hexdigest(),
        )
    )
    hand = HandHistory(
        hand_id="T1-1",
        table_id="T1",
        started_at="2026-01-01T00:00:00+00:00",
        ended_at="2026-01-01T00:01:00+00:00",
        button_seat=2,
        big_blind=2,
        board=["As", "Kh", "2c"],
        pot=20,
        status="completed",
    )
    hand.players = {
        1: Player(1, user_id="hero", alias="Hero", is_hero=True, net=10),
        2: Player(2, user_id="villain", alias="Villain", net=-10),
    }
    hand.actions = [
        Action("preflop", 2, "Villain", "raise", 5, 6, "villain", "a1", 94, 1, 1),
        Action("preflop", 1, "Hero", "call", 4, 6, "hero", "a2", 94, 1, 2),
    ]
    store.save_hand(hand)
    store.append_live("翻牌：[ A♠ K♥ 2♣ ]")
    room_payload = {
        "event": "upDateRoomNotify",
        "data": {
            "sitUserList": [
                {"userId": "hero", "nickname": "Hero", "squidWin": 0},
                {"userId": "villain", "nickname": "Villain", "squidWin": 2},
            ]
        },
    }
    store.save_raw_event(
        RawEvent(
            timestamp="2026-01-01T00:00:30+00:00",
            event_name="upDateRoomNotify",
            payload=room_payload,
            sequence=2,
            hand_id="T1-1",
            sha256="room",
        )
    )
    award_payload = {
        "event": "squidGameNotify",
        "data": {
            "scene": 3,
            "users": ["villain"],
            "ext": '{"winSquidNum":1}',
        },
    }
    store.save_raw_event(
        RawEvent(
            timestamp="2026-01-01T00:00:45+00:00",
            event_name="squidGameNotify",
            payload=award_payload,
            sequence=3,
            hand_id="T1-1",
            sha256="award",
        )
    )
    store.save_squid_event(
        SquidEvent(
            round_id="T1-squid-1",
            scene=4,
            timestamp="2026-01-01T00:02:00+00:00",
            users=["hero", "villain"],
            settlements=[
                {"userId": "hero", "addScore": 10, "curScore": 110},
                {"userId": "villain", "addScore": -10, "curScore": 90},
            ],
            ext={"squidSettHands": [1]},
            raw={"scene": 4},
            sequence=2,
            hand_refs=["1"],
        ),
        "T1",
    )
    store.close()
    assert "A♠ K♥ 2♣" in (tmp_path / "live.log").read_text(encoding="utf-8")

    data = snapshot(tmp_path)
    assert data["last_sequence"] == 3
    assert data["hands"][0]["board"] == ["As", "Kh", "2c"]
    assert data["hands"][0]["hand_number"] == 1
    assert data["hands"][0]["played_at_cn"] == "2026-01-01 08:00:00"
    assert data["hands"][0]["quality_status"] == "good"
    assert data["opponents"][0]["alias"] == "Villain"
    assert data["opponents"][0]["metrics"]["vpip"]["observed_pct"] == 100.0
    assert data["opponents"][0]["metrics"]["vpip"]["mean_pct"] < 100.0
    assert data["opponents"][0]["metrics"]["vpip"]["opportunities"] == 1
    assert data["hero"]["alias"] == "Hero"
    assert data["hero"]["user_id"] == "hero"
    assert data["hero"]["metrics"]["vpip"]["opportunities"] == 1
    assert all(
        player["user_id"] != data["hero"]["user_id"]
        for player in data["opponents"]
    )
    assert data["squid"]["rounds"][0]["status"] == "completed"
    assert data["hands"][0]["squid_hand"]["players"][0]["alias"] == "Villain"
    assert data["hands"][0]["squid_hand"]["players"][0]["squid_start"] == 2
    assert data["hands"][0]["squid_hand"]["awards"][0]["award_count"] == 1

    client = TestClient(create_app(tmp_path))
    assert client.get("/").status_code == 200
    assert client.get("/assets/app.css").status_code == 200
    response = client.get("/api/snapshot")
    assert response.status_code == 200
    assert response.json()["hands"][0]["hand_id"] == "T1-1"
    range_response = client.get(
        "/api/players/villain/preflop-range?position=ALL&line=open_raise"
    )
    assert range_response.status_code == 200
    assert len(range_response.json()["matrix"]) == 169
    csv_response = client.get("/api/export/hands.csv")
    assert "Villain" in csv_response.text
    assert client.get("/api/export/hands.json").json()[0]["hand_id"] == "T1-1"
    assert client.get("/api/snapshot?mode=squid").json()["hands"][0]["game_mode"] == "squid"
    db = sqlite3.connect(tmp_path / "hands.sqlite3")
    assert db.execute("SELECT COUNT(*) FROM decision_snapshots").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] >= 4
    db.close()

    class ConnectedRequest:
        query_params = {}

        async def is_disconnected(self):
            return False

    async def first_sse_event():
        stream = _event_stream(ConnectedRequest(), tmp_path)
        event = await stream.__anext__()
        await stream.aclose()
        return event

    assert "event: snapshot" in asyncio.run(first_sse_event())

    async def first_live_sse_event():
        stream = _event_stream(
            ConnectedRequest(),
            tmp_path,
            create_app(tmp_path),
        )
        event = await stream.__anext__()
        await stream.aclose()
        return event

    live_event = asyncio.run(first_live_sse_event())
    live_payload = json.loads(live_event.split("data: ", 1)[1])
    assert set(live_payload) == {
        "last_sequence",
        "current_hand",
        "live_decision",
        "preflop_preview",
        "assistance_policy",
    }
    assert "opponents" not in live_payload


def test_opponent_stats_do_not_mix_hero_opportunity_samples(tmp_path):
    store = RecorderStore(tmp_path)
    for index in range(3):
        hand = HandHistory(
            hand_id=f"hero-main-{index}",
            table_id="T-mix",
            started_at="2026-01-01T00:00:00+00:00",
            ended_at="2026-01-01T00:01:00+00:00",
            button_seat=2,
            big_blind=2,
            board=["As", "Kh", "2c"],
            pot=20,
            status="completed",
        )
        hand.players = {
            1: Player(
                1,
                user_id="88671540",
                alias="先清清兵",
                is_hero=True,
                net=2,
            ),
            2: Player(
                2,
                user_id="villain",
                alias="Villain",
                net=-2,
            ),
        }
        hand.actions = [
            Action(
                "preflop",
                2,
                "Villain",
                "raise",
                5,
                6,
                "villain",
                f"v{index}",
                94,
                1,
                1,
            ),
            Action(
                "preflop",
                1,
                "先清清兵",
                "call",
                4,
                6,
                "88671540",
                f"h{index}",
                94,
                1,
                2,
            ),
        ]
        store.save_hand(hand)

    mistagged = HandHistory(
        hand_id="hero-mistagged",
        table_id="T-mix",
        started_at="2026-01-01T00:02:00+00:00",
        ended_at="2026-01-01T00:03:00+00:00",
        button_seat=1,
        big_blind=2,
        board=["As", "Kh", "2c"],
        pot=12,
        status="completed",
    )
    mistagged.players = {
        1: Player(
            1,
            user_id="88671540",
            alias="先清清兵",
            is_hero=False,
            net=-2,
        ),
        2: Player(
            2,
            user_id="other",
            alias="Other",
            is_hero=True,
            net=2,
        ),
    }
    mistagged.actions = [
        Action(
            "preflop",
            1,
            "先清清兵",
            "raise",
            5,
            6,
            "88671540",
            "mistag-raise",
            94,
            1,
            1,
        ),
        Action(
            "preflop",
            2,
            "Other",
            "fold",
            0,
            0,
            "other",
            "mistag-fold",
            94,
            1,
            2,
        ),
    ]
    store.save_hand(mistagged)
    store.close()

    data = snapshot(tmp_path)
    assert data["hero"]["user_id"] == "88671540"
    assert data["hero"]["hands"] == 3
    assert data["hero"]["metrics"]["vpip"]["opportunities"] == 3
    assert all(player["user_id"] != "88671540" for player in data["opponents"])
    villain = next(player for player in data["opponents"] if player["user_id"] == "villain")
    assert villain["hands"] == 3
    assert villain["metrics"]["vpip"]["opportunities"] == 3


def test_range_at_node_contract_and_stale_hash(tmp_path):
    _save_range_node_hand(tmp_path)
    client = TestClient(create_app(tmp_path))

    response = client.get(
        "/api/players/villain/range-at-node",
        params={"hand_id": "node-1"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["model_version"] == "opponent-node-range-v6"
    assert data["progressive"] == {"phase": "full", "complete": True}
    assert data["node"]["scope"] == "review"
    assert data["node"]["street"] == "river"
    assert data["node"]["stale"] is False
    assert data["player"]["user_id"] == "villain"
    assert data["player"]["last_action"]["action"] == "check"
    assert [
        action["action"] for action in data["player"]["action_line"]
    ] == ["call", "check"]
    assert len(data["range"]["matrix"]) == 169
    assert abs(
        sum(
            item["probability_pct"]
            for item in data["range"]["matrix"]
        )
        - 100
    ) < 0.5
    assert data["range"]["core_80_class_count"] > 0
    assert data["range"]["source"] in {
        "preflop_prior",
        "action_line_posterior",
        "board_action_heuristic",
    }
    assert all(
        item["available_combos"] >= 0
        for item in data["range"]["matrix"]
    )
    assert set(data["strength"]) == {
        "baseline",
        "posterior",
        "shifts",
    }
    assert set(data["composition"]) == {
        "baseline",
        "posterior",
        "shifts",
        "aggressive_action",
            "method",
        "interpretation",
    }
    assert data["composition"]["aggressive_action"] is False
    assert data["composition"]["posterior"]["bluff_candidates"] is None
    assert abs(
        sum(
            data["composition"]["posterior"][key]
            for key in (
                "strong_value",
                "marginal_showdown",
                "draws",
                "air",
            )
        )
        - 100
    ) < 0.2
    assert set(data["evidence"]["gates"]) == {
        "range",
        "continuation",
        "response",
    }
    assert data["evidence"]["limitations"]
    assert data["evidence"]["current_hand_excluded"] is True
    assert data["policy"]["mode"] == "operator_approved_live"

    stale = client.get(
        "/api/players/villain/range-at-node",
        params={
            "hand_id": "node-1",
            "state_hash": "stale-state-hash",
        },
    )
    assert stale.status_code == 409
    assert client.get(
        "/api/players/missing/range-at-node",
        params={"hand_id": "node-1"},
    ).status_code == 404


def test_range_at_node_preview_returns_before_expensive_models(tmp_path):
    _save_range_node_hand(tmp_path)
    client = TestClient(create_app(tmp_path))

    response = client.get(
        "/api/players/villain/range-at-node",
        params={"hand_id": "node-1", "phase": "preview"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["progressive"] == {
        "phase": "preview",
        "complete": False,
    }
    assert data["range"]["source"] == "board_action_heuristic"
    assert len(data["range"]["matrix"]) == 169
    assert data["composition"]["posterior"]
    assert data["response_model"] == {}
    assert data["evidence"]["range_gate"]["preview"] is True
    assert data["evidence"]["continuation_gate"]["preview"] is True
    assert data["evidence"]["response_gate"]["preview"] is True


def test_range_at_node_freezes_folded_player_to_last_action(tmp_path):
    _save_range_node_hand(tmp_path, folded=True)
    client = TestClient(create_app(tmp_path))

    response = client.get(
        "/api/players/villain/range-at-node",
        params={"hand_id": "node-1"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["player"]["folded"] is True
    assert data["player"]["last_action"]["action"] == "fold"
    assert data["node"]["sequence"] == 3
    assert data["node"]["street"] == "flop"
    assert data["node"]["board"] == ["As", "Kh", "2c"]
    assert data["response_model"] == {}


def test_assistance_policy_enforces_all_four_modes(tmp_path):
    _save_range_node_hand(tmp_path)
    expected_history = {
        "operator_approved_live": 200,
        "post_session": 200,
        "static_history": 200,
        "capture_only": 403,
    }
    for mode, expected_status in expected_history.items():
        client = TestClient(
            create_app(tmp_path, assistance_mode=mode)
        )
        status = client.get("/api/assistance/status")
        assert status.status_code == 200
        assert status.json()["mode"] == mode
        assert (
            status.json()["capabilities"]["history_range"]
            is (mode != "capture_only")
        )
        response = client.get(
            "/api/players/villain/range-at-node",
            params={"hand_id": "node-1"},
        )
        assert response.status_code == expected_status


def test_non_live_modes_cannot_bypass_policy_with_live_hand_id(tmp_path):
    _save_range_node_hand(tmp_path, status="in_progress")
    for mode in ("post_session", "static_history", "capture_only"):
        client = TestClient(
            create_app(tmp_path, assistance_mode=mode)
        )
        response = client.get(
            "/api/players/villain/range-at-node",
            params={"hand_id": "node-1"},
        )
        assert response.status_code == 403
        assert client.get("/api/strategy/current").status_code == 403
        assert client.get(
            "/api/equity/current",
            params={"hand_id": "node-1"},
        ).status_code == 403
    allowed = TestClient(
        create_app(
            tmp_path,
            assistance_mode="operator_approved_live",
        )
    )
    assert allowed.get(
        "/api/players/villain/range-at-node",
        params={"hand_id": "node-1"},
    ).status_code == 200


def test_migrates_existing_hands_table(tmp_path):
    db = sqlite3.connect(tmp_path / "hands.sqlite3")
    db.execute(
        """
        CREATE TABLE hands(
            hand_id TEXT PRIMARY KEY, table_id TEXT, started_at TEXT, ended_at TEXT,
            status TEXT NOT NULL, hand_json TEXT NOT NULL
        )
        """
    )
    db.commit()
    db.close()
    store = RecorderStore(tmp_path)
    columns = {
        row[1] for row in store.connection.execute("PRAGMA table_info(hands)").fetchall()
    }
    store.close()
    assert {
        "game_mode",
        "pot",
        "board_json",
        "quality_status",
        "quality_reasons_json",
        "excluded_from_stats",
        "hand_number",
        "played_at",
    } <= columns


def test_hands_sort_by_normalized_time_and_show_numeric_hand_number(tmp_path):
    store = RecorderStore(tmp_path)
    older = HandHistory(
        hand_id="room-39",
        started_at="1788796054840",
        status="completed",
    )
    older.actions = [Action("preflop", 1, "A", "fold", sequence=1)]
    newer = HandHistory(
        hand_id="room-40",
        started_at="2026-09-07T15:47:35.162748+00:00",
        status="completed",
    )
    newer.actions = [Action("preflop", 1, "A", "fold", sequence=1)]
    store.save_hand(older)
    store.save_hand(newer)
    store.close()

    hands = snapshot(tmp_path)["hands"]
    assert [hand["hand_number"] for hand in hands] == [40, 39]
    assert hands[0]["played_at_cn"] == "2026-09-07 23:47:35"


def test_hands_api_paginates_and_filters_revealed_opponent_hands(tmp_path):
    store = RecorderStore(tmp_path)
    for number in range(1, 36):
        hand = HandHistory(
            hand_id=f"room-{number}",
            table_id="room",
            started_at="2026-01-01T00:00:00+00:00",
            board=(
                ["As", "Kh", "2c", "7d", "9s"]
                if number % 4 == 0
                else ["As", "Kh", "2c", "7d"]
                if number % 4 == 2
                else ["As", "Kh", "2c"]
                if number % 4 == 1
                else []
            ),
            pot=number,
            status="completed",
            game_mode="squid" if number % 2 == 0 else "holdem",
        )
        hand.players = {
            1: Player(
                1,
                user_id="hero",
                alias="Hero",
                is_hero=True,
                net=10 if number % 3 == 0 else -10,
            ),
            2: Player(
                2,
                user_id="villain",
                alias="Villain",
                hole_cards=["As", "Kh"] if number % 2 == 0 else [],
                net=-10 if number % 3 == 0 else 10,
            ),
        }
        hand.actions = [
            Action("preflop", 2, "Villain", "fold", user_id="villain", sequence=1)
        ]
        store.save_hand(hand)
    store.close()

    client = TestClient(create_app(tmp_path))
    first = client.get("/api/hands", params={"limit": 10})
    second = client.get("/api/hands", params={"limit": 10, "offset": 10})
    revealed = client.get(
        "/api/hands",
        params={
            "limit": 100,
            "user_id": "villain",
            "revealed_only": True,
        },
    )
    searched = client.get("/api/hands", params={"q": "room-35"})
    winners = client.get("/api/hands", params={"result": "won"})
    river = client.get("/api/hands", params={"street": "river"})
    squid = client.get("/api/hands", params={"mode": "squid"})
    any_revealed = client.get("/api/hands", params={"revealed_only": True})

    assert first.status_code == 200
    assert first.json()["total"] == 35
    assert first.json()["has_more"] is True
    assert len(first.json()["hands"]) == 10
    assert {
        hand["hand_id"] for hand in first.json()["hands"]
    }.isdisjoint(hand["hand_id"] for hand in second.json()["hands"])
    assert revealed.status_code == 200
    assert revealed.json()["total"] == 17
    assert revealed.json()["has_more"] is False
    assert all(
        next(
            player
            for player in hand["players"]
            if player["user_id"] == "villain"
        )["hole_cards"]
        for hand in revealed.json()["hands"]
    )
    assert searched.json()["total"] == 1
    assert winners.json()["total"] == 11
    assert river.json()["total"] == 8
    assert squid.json()["total"] == 17
    assert any_revealed.json()["total"] == 17


def test_live_hand_is_current_even_if_previous_time_is_slightly_later(tmp_path):
    store = RecorderStore(tmp_path)
    previous = HandHistory(
        hand_id="room-3",
        started_at="2026-09-07T16:16:43.180000+00:00",
        status="completed",
    )
    previous.actions = [Action("preflop", 1, "A", "fold", sequence=1)]
    current = HandHistory(
        hand_id="room-4",
        started_at="2026-09-07T16:16:43.174000+00:00",
        status="in_progress",
    )
    current.actions = [Action("preflop", 1, "A", "call", sequence=1)]
    store.save_hand(previous)
    store.save_hand(current, final=False)
    store.close()

    data = snapshot(tmp_path)
    assert data["current_hand"]["hand_id"] == "room-4"
    assert [hand["hand_number"] for hand in data["hands"][:2]] == [4, 3]


def test_completed_current_hand_uses_latest_event_identity_not_hand_number(tmp_path):
    store = RecorderStore(tmp_path)
    hands = [
        HandHistory(
            hand_id="historic-room-999",
            table_id="historic-room",
            started_at="2026-09-01T00:00:00+00:00",
            status="completed",
        ),
        HandHistory(
            hand_id="current-room-33",
            table_id="current-room",
            started_at="2026-09-08T10:45:14.625000+00:00",
            status="completed",
        ),
        HandHistory(
            hand_id="current-room-34",
            table_id="current-room",
            started_at="2026-09-08T10:45:14.464485+00:00",
            status="completed",
        ),
    ]
    for hand in hands:
        hand.actions = [Action("preflop", 1, "A", "fold", sequence=1)]
        store.save_hand(hand)
    for sequence, hand in enumerate(hands, 1):
        store.save_raw_event(
            RawEvent(
                timestamp=f"2026-09-08T10:45:1{sequence}+00:00",
                event_name="playResultNotify",
                payload={},
                sequence=sequence,
                table_id=hand.table_id,
                hand_id=hand.hand_id,
            )
        )
    store.close()

    data = snapshot(tmp_path)

    assert data["hands"][0]["hand_id"] == "current-room-33"
    assert data["current_hand"]["hand_id"] == "current-room-34"
    assert data["current_hand"]["hand_number"] == 34


def test_startup_repair_removes_cross_hand_actions_and_renumbers(tmp_path):
    store = RecorderStore(tmp_path)
    hand = HandHistory(hand_id="room-35", status="completed")
    hand.actions = [
        Action(
            "flop",
            1,
            "Previous",
            "fold",
            action_id="34019",
            sequence=1,
        ),
        Action(
            "flop",
            2,
            "Current",
            "ante",
            action_id="35001",
            sequence=20,
        ),
        Action(
            "preflop",
            2,
            "Current",
            "call",
            action_id="35002",
            sequence=36,
        ),
    ]
    store.save_hand(hand, final=False)
    store.close()

    repaired_store = RecorderStore(tmp_path)
    repaired = repaired_store.load_hand("room-35")
    repaired_store.close()

    assert repaired is not None
    assert [action.action_id for action in repaired.actions] == ["35001", "35002"]
    assert [action.sequence for action in repaired.actions] == [1, 2]
    assert [action.street for action in repaired.actions] == ["preflop", "preflop"]
    assert repaired.quality_status == "good"


def test_existing_insured_hand_is_repaired_from_raw_result(tmp_path):
    hand = HandHistory(hand_id="room-5", status="completed")
    hand.actions = [Action("preflop", 1, "Buyer", "call", sequence=1)]
    hand.players = {
        1: Player(1, user_id="buyer", alias="Buyer", net=395),
        2: Player(2, user_id="other", alias="Other", net=-416),
    }
    store = RecorderStore(tmp_path)
    store.save_hand(hand)
    store.save_raw_event(
        RawEvent(
            timestamp="2026-01-01T00:00:00+00:00",
            event_name="playResultNotify",
            sequence=1,
            hand_id="room-5",
            payload={
                "event": "playResultNotify",
                "data": {
                    "thanList": [
                        {
                            "seatNum": 1,
                            "userId": "buyer",
                            "insuranceResult": -10,
                            "fund": 11,
                        },
                        {
                            "seatNum": 2,
                            "userId": "other",
                            "insuranceResult": 0,
                            "fund": 0,
                        },
                    ]
                },
            },
        )
    )
    store.close()

    migrated = RecorderStore(tmp_path)
    migrated.close()
    repaired = snapshot(tmp_path)["hands"][0]
    buyer = next(player for player in repaired["players"] if player["seat"] == 1)
    assert repaired["quality_status"] == "good"
    assert buyer["insurance_result"] == -10
    assert buyer["fund"] == 11


def test_existing_insured_hand_falls_back_to_history_fields(tmp_path):
    hand = HandHistory(hand_id="room-6", status="completed")
    hand.actions = [Action("preflop", 1, "Buyer", "call", sequence=1)]
    hand.players = {
        1: Player(1, user_id="buyer", net=90),
        2: Player(2, user_id="other", net=-100),
    }
    store = RecorderStore(tmp_path)
    store.save_hand(hand)
    store.save_raw_event(
        RawEvent(
            timestamp="2026-01-01T00:00:00+00:00",
            event_name="updateHistoryData",
            sequence=1,
            hand_id="room-6",
            payload={
                "event": "updateHistoryData",
                "data": {
                    "handList1": [
                        {
                            "roomId": "room",
                            "handNum": 6,
                            "seatNum": 1,
                            "userId": "buyer",
                            "changeScore": 90,
                            "insuranceInvest": 10,
                            "insuranceWin": 0,
                        },
                        {
                            "roomId": "room",
                            "handNum": 6,
                            "seatNum": 2,
                            "userId": "other",
                            "changeScore": -100,
                            "insuranceInvest": 0,
                            "insuranceWin": 0,
                        },
                    ]
                },
            },
        )
    )
    store.close()

    migrated = RecorderStore(tmp_path)
    migrated.close()
    repaired = snapshot(tmp_path)["hands"][0]
    assert repaired["quality_status"] == "good"
    assert repaired["players"][0]["insurance_result"] == -10


def test_migration_deduplicates_ids_and_repairs_offset_sequences(tmp_path):
    hand = HandHistory(hand_id="room-8", status="completed")
    hand.actions = [
        Action("preflop", 1, "A", "call", action_id=100, sequence=10),
        Action("preflop", 1, "A", "call", action_id="100", sequence=11),
    ]
    store = RecorderStore(tmp_path)
    store.save_hand(hand)
    store.close()

    migrated = RecorderStore(tmp_path)
    migrated.close()
    repaired = snapshot(tmp_path)["hands"][0]
    assert repaired["quality_status"] == "good"
    assert [action["sequence"] for action in repaired["actions"]] == [1]
    assert repaired["actions"][0]["action_id"] == "100"
