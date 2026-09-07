import hashlib
import asyncio
import sqlite3

from fastapi.testclient import TestClient

from wpk_recorder.analytics import snapshot
from wpk_recorder.models import (
    Action,
    HandHistory,
    Player,
    RawEvent,
    SquidEvent,
)
from wpk_recorder.server import _event_stream, create_app
from wpk_recorder.storage import RecorderStore


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
