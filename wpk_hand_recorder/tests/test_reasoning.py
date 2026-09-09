from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from fastapi.testclient import TestClient

import wpk_recorder.opponent_model as opponent_model_module
import wpk_recorder.reasoning as reasoning_module
import wpk_recorder.server as server_module
from wpk_recorder.models import Action, HandHistory, Player, RawEvent
from wpk_recorder.reasoning import (
    LLMReasoner,
    ReasoningError,
    _validate_exploit_result,
    _validate_profile_result,
    _player_effective_stack_bb,
    _side_pots,
    apply_exploit_frequency_shifts,
    build_exploit_prompt,
    build_prompt,
    enrich_reasoning_context,
    live_decision,
    local_fast_analysis,
    opponent_node_profile,
    preflop_preview_context_from_hand,
    public_decision,
    reasoning_context,
    simplified_range_strategy,
)
from wpk_recorder.server import create_app
from wpk_recorder.storage import RecorderStore


def test_realtime_response_gate_skips_cold_backtest(tmp_path, monkeypatch):
    store = RecorderStore(tmp_path)
    store.close()

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("cold backtest must not run on the live path")

    monkeypatch.setattr(
        opponent_model_module,
        "action_response_backtest",
        fail_if_called,
    )
    connection = sqlite3.connect(tmp_path / "hands.sqlite3")
    try:
        quality = opponent_model_module.cached_action_response_backtest(
            connection,
            "squid",
            refresh_on_miss=False,
        )
    finally:
        connection.close()

    assert quality["enabled"] is False
    assert quality["stale"] is True
    assert quality["groups"] == []


def test_preflop_preview_tracks_hero_position_and_prior_action(tmp_path):
    hand = HandHistory(
        hand_id="preview-1",
        button_seat=5,
        small_blind=2,
        big_blind=4,
        status="in_progress",
    )
    hand.players = {
        seat: Player(
            seat,
            user_id=f"p{seat}",
            alias=f"P{seat}",
            is_hero=seat == 4,
            stack_start=400,
            hole_cards=["As", "Kd"] if seat == 4 else [],
        )
        for seat in range(1, 6)
    }
    hand.actions = [
        Action("preflop", 1, "P1", "small_blind", 2, 2, "p1", sequence=1),
        Action("preflop", 2, "P2", "big_blind", 4, 4, "p2", sequence=2),
        Action("preflop", 3, "P3", "raise", 12, 12, "p3", sequence=3),
    ]

    context = preflop_preview_context_from_hand(hand)

    assert context is not None
    assert context["decision"]["decision_subject"] == "self_preview"
    assert context["decision"]["hero_position"] == "CO"
    assert context["decision"]["call_score"] == 12
    strategy = simplified_range_strategy(context)
    assert strategy["scenario"] == "facing_raise"
    assert strategy["hero_hand"] == "AKo"

    store = RecorderStore(tmp_path)
    store.save_hand(hand, final=False)
    store.close()
    client = TestClient(create_app(tmp_path))
    response = client.get("/api/strategy/preflop-preview")
    assert response.status_code == 200
    assert response.json()["baseline"]["scenario"] == "facing_raise"

    client = TestClient(create_app(tmp_path, live_hand_provider=lambda: hand))
    hand.actions.append(
        Action("preflop", 4, "P4", "call", 12, 12, "p4", sequence=4)
    )
    assert preflop_preview_context_from_hand(hand) is None
    assert client.get("/api/strategy/preflop-preview").status_code == 409


def _decision_store(
    tmp_path,
    hand_cards=None,
    recorder_hero_cards=None,
    event_name="userOptNotify",
    actor_user_id="hero",
    runtime_user_id=None,
    board=None,
    legal_actions=None,
    call_score=6,
    min_raise_score=12,
    max_raise_score=300,
    seat_score=0,
    current_score=300,
    extra_player=False,
) -> int:
    store = RecorderStore(tmp_path)
    hand = HandHistory(
        hand_id="room-7",
        table_id="room",
        started_at=datetime.now(timezone.utc).isoformat(),
        button_seat=2,
        small_blind=1,
        big_blind=2,
        ante=1,
        pot=15,
        status="in_progress",
        game_mode="squid",
    )
    hand.board = list(board or [])
    hand.players = {
        1: Player(1, user_id="hero", alias="Hero", stack_start=300),
        2: Player(2, user_id="villain", alias="Villain", stack_start=300),
    }
    if extra_player:
        hand.players[3] = Player(
            3,
            user_id="villain-2",
            alias="Villain 2",
            stack_start=300,
        )
    hand.actions = [
        Action(
            "preflop",
            2,
            "Villain",
            "raise",
            amount=6,
            amount_to=6,
            user_id="villain",
            action_id="a1",
            stack_after=294,
            sequence=1,
        )
    ]
    store.save_hand(hand, final=False)
    action_data = {
        "canActionList": legal_actions
        or ["FOLD", "CALL", "RAISE", "ALL_IN"],
        "handCards": [101, 113] if hand_cards is None else hand_cards,
        "userId": actor_user_id,
        "minRaiseScore": min_raise_score,
        "maxRaiseScore": max_raise_score,
        "callScore": call_score,
        "countDown": 30,
        "lastBet": 6,
        "seatScore": seat_score,
        "currentScore": current_score,
    }
    payload = {
        "event": event_name,
        "data": (
            {
                "round": "FLOP",
                "dealPublicCards": list(board or []),
                "userAciton": action_data,
            }
            if event_name == "roundChangeNotify"
            else action_data
        ),
    }
    if runtime_user_id is not None:
        payload["_recorderCurrentUserId"] = runtime_user_id
    if recorder_hero_cards is not None:
        payload["_recorderHeroCards"] = recorder_hero_cards
    store.save_raw_event(
        RawEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_name=event_name,
            payload=payload,
            sequence=1,
            table_id="room",
            hand_id="room-7",
            sha256=hashlib.sha256(repr(payload).encode()).hexdigest(),
        )
    )
    store.close()
    return 1


def test_current_strategy_uses_fast_preflop_context(tmp_path, monkeypatch):
    sequence = _decision_store(tmp_path)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("full opponent context must not block preflop EV")

    monkeypatch.setattr(server_module, "reasoning_context", fail_if_called)
    response = TestClient(create_app(tmp_path)).get(
        "/api/strategy/current",
        params={"sequence": sequence},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["decision"]["street"] == "preflop"
    assert payload["baseline"]["hero_hand"] == "AKs"
    assert payload["money_strategy"]["equity"]["trials"] == 48
    assert payload["inference"]["recommendation"]["action"] in {
        "fold",
        "call",
        "raise",
        "all_in",
    }


def test_side_pot_reconstruction_keeps_eligibility():
    pots = _side_pots(
        [
            {"seat": 1, "amount": 50},
            {"seat": 2, "amount": 100},
            {"seat": 3, "amount": 100},
        ],
        {1, 2, 3},
    )

    assert pots == [
        {"amount": 150, "eligible_seats": [1, 2, 3]},
        {"amount": 100, "eligible_seats": [2, 3]},
    ]


def test_opponent_context_uses_effective_stack_to_hero():
    decision = {
        "big_blind": 4,
        "hero_stack_bb": 200,
        "players": [
            {
                "seat": 2,
                "effective_stack_to_hero": 160,
            }
        ],
    }

    assert _player_effective_stack_bb(decision, 2) == 40
    assert _player_effective_stack_bb(decision, 3) == 200


def test_reasoning_range_excludes_current_hand(monkeypatch, tmp_path):
    sequence = _decision_store(tmp_path)
    excluded = []

    def fake_preflop_range(
        _connection,
        _user_id,
        *,
        position,
        line,
        mode,
        _exclude_hand_id=None,
        **_kwargs,
    ):
        excluded.append(_exclude_hand_id)
        return {
            "line": line,
            "estimated_range_pct": 40,
            "confidence": "low",
            "evidence": {"action_opportunities": 1},
            "matrix": [],
        }

    monkeypatch.setattr(
        reasoning_module,
        "preflop_range_profile",
        fake_preflop_range,
    )
    connection = sqlite3.connect(tmp_path / "hands.sqlite3")
    try:
        context = reasoning_context(connection, sequence)
    finally:
        connection.close()

    assert context is not None
    assert excluded == ["room-7"]


def test_single_opponent_node_matches_reasoning_context(tmp_path):
    sequence = _decision_store(tmp_path, extra_player=True)
    connection = sqlite3.connect(tmp_path / "hands.sqlite3")
    connection.row_factory = sqlite3.Row
    try:
        decision = live_decision(connection, sequence=sequence)
        context = reasoning_context(
            connection,
            sequence,
            decision=decision,
        )
        node = opponent_node_profile(
            connection,
            decision,
            "villain",
        )
    finally:
        connection.close()

    assert len(context["active_opponent_profiles"]) == 2
    profile = next(
        item
        for item in context["active_opponent_profiles"]
        if item["seat"] == 2
    )
    node_weights = {
        item["hand"]: item["baseline_pct"]
        for item in node["range"]["matrix"]
    }
    assert node["player"]["seat"] == 2
    assert node["player"]["folded"] is False
    assert node_weights == profile["preflop_range"]["weights"]
    assert node["evidence"]["current_hand_excluded"] is True


def test_single_opponent_node_never_promotes_preview_weights(
    monkeypatch,
    tmp_path,
):
    sequence = _decision_store(tmp_path)

    def gated_posterior(
        _connection,
        _user_id,
        base_profile,
        *_args,
        **_kwargs,
    ):
        return {
            "enabled": False,
            "weights": {},
            "preview_weights": {
                hand: 100.0
                for hand in base_profile["weights"]
            },
            "updates": [],
            "reason": "时间外门控失败",
        }

    monkeypatch.setattr(
        reasoning_module,
        "action_line_range_profile",
        gated_posterior,
    )
    connection = sqlite3.connect(tmp_path / "hands.sqlite3")
    connection.row_factory = sqlite3.Row
    try:
        decision = live_decision(connection, sequence=sequence)
        node = opponent_node_profile(
            connection,
            decision,
            "villain",
        )
    finally:
        connection.close()

    assert node["range"]["source"] == "preflop_prior"
    assert node["range"]["production_enabled"] is False
    assert any(
        item["weight_pct"] < 100
        for item in node["range"]["matrix"]
    )


def test_single_opponent_strength_uses_known_hero_blockers(
    monkeypatch,
    tmp_path,
):
    sequence = _decision_store(tmp_path)
    blockers_seen = []
    original = reasoning_module.range_strength_distribution

    def capture(weights, board, blockers=()):
        blockers_seen.append(list(blockers))
        return original(weights, board, blockers)

    monkeypatch.setattr(
        reasoning_module,
        "range_strength_distribution",
        capture,
    )
    connection = sqlite3.connect(tmp_path / "hands.sqlite3")
    connection.row_factory = sqlite3.Row
    try:
        decision = live_decision(connection, sequence=sequence)
        opponent_node_profile(connection, decision, "villain")
    finally:
        connection.close()

    assert blockers_seen
    assert blockers_seen[0] == ["As", "Ks"]


def test_observer_opponent_node_does_not_invent_blockers(
    monkeypatch,
    tmp_path,
):
    sequence = _decision_store(
        tmp_path,
        hand_cards=[],
        actor_user_id="villain",
        runtime_user_id="spectator",
    )
    blockers_seen = []
    original = reasoning_module.range_strength_distribution

    def capture(weights, board, blockers=()):
        blockers_seen.append(list(blockers))
        return original(weights, board, blockers)

    monkeypatch.setattr(
        reasoning_module,
        "range_strength_distribution",
        capture,
    )
    connection = sqlite3.connect(tmp_path / "hands.sqlite3")
    connection.row_factory = sqlite3.Row
    try:
        decision = live_decision(connection, sequence=sequence)
        opponent_node_profile(connection, decision, "hero")
    finally:
        connection.close()

    assert blockers_seen
    assert blockers_seen[0] == []


def test_live_decision_is_sanitized_and_auto_gated(tmp_path):
    sequence = _decision_store(tmp_path)
    connection = sqlite3.connect(tmp_path / "hands.sqlite3")
    decision = live_decision(connection)
    assert decision is not None
    assert decision["sequence"] == sequence
    assert decision["hero_cards"] == ["As", "Ks"]
    assert decision["legal_actions"] == ["fold", "call", "raise", "all_in"]
    assert decision["auto_reasoning"] is True
    assert "鱿鱼模式" in decision["auto_reason"]
    public = public_decision(decision)
    assert public is not None
    assert not any(key.startswith("_") for key in public)
    context = reasoning_context(connection, sequence)
    connection.close()
    assert context is not None
    assert context["decision"]["hero_cards"] == ["As", "Ks"]
    assert context["model_training_policy"]["validation_rows_reused_after_gate"]
    assert (
        context["model_training_policy"]["production_fit"]
        == "all_completed_good_history"
    )
    encoded = json.dumps(context)
    assert '"hero"' not in encoded
    assert '"villain"' not in encoded


def test_live_decision_prefers_decrypted_client_state_cards(tmp_path):
    sequence = _decision_store(
        tmp_path,
        hand_cards=[-1, -1],
        recorder_hero_cards=[209, 106],
    )
    connection = sqlite3.connect(tmp_path / "hands.sqlite3")
    decision = live_decision(connection, sequence=sequence)
    connection.close()

    assert decision is not None
    assert decision["hero_cards"] == ["9h", "6s"]
    assert decision["decision_subject"] == "self"


def test_live_decision_ignores_opponent_action_with_captured_hero_cards(tmp_path):
    sequence = _decision_store(
        tmp_path,
        hand_cards=[],
        recorder_hero_cards=[209, 106],
        actor_user_id="villain",
        runtime_user_id="hero",
    )
    connection = sqlite3.connect(tmp_path / "hands.sqlite3")
    decision = live_decision(connection, sequence=sequence)
    connection.close()

    assert decision is None


def test_live_decision_allows_observer_when_runtime_user_is_not_seated(tmp_path):
    sequence = _decision_store(
        tmp_path,
        hand_cards=[],
        recorder_hero_cards=[209, 106],
        actor_user_id="villain",
        runtime_user_id="spectator",
    )
    connection = sqlite3.connect(tmp_path / "hands.sqlite3")
    decision = live_decision(connection, sequence=sequence)
    connection.close()

    assert decision is not None
    assert decision["decision_subject"] == "observer"
    assert decision["subject_seat"] == 2
    assert decision["hero_cards"] == []


def test_live_decision_accepts_short_stack_underraise_all_in(tmp_path):
    sequence = _decision_store(
        tmp_path,
        hand_cards=[],
        actor_user_id="villain",
        runtime_user_id="spectator",
        board=[102, 203, 304, 405, 113],
        legal_actions=["FOLD", "ALL_IN"],
        call_score=544,
        min_raise_score=1088,
        max_raise_score=66,
        seat_score=272,
        current_score=0,
    )
    connection = sqlite3.connect(tmp_path / "hands.sqlite3")
    decision = live_decision(connection, sequence=sequence)
    connection.close()

    assert decision is not None
    assert decision["min_raise_to"] == 1360
    assert decision["max_raise_to"] == 338
    assert decision["hero_street_contribution"] == 272
    assert decision["hero_stack"] == 66
    assert decision["state_quality"]["valid"] is True
    assert "剩余筹码不足常规最小加注，仅允许不足额全下" in (
        decision["state_quality"]["warnings"]
    )


def test_live_decision_reads_hero_first_action_from_round_change(tmp_path):
    sequence = _decision_store(
        tmp_path,
        hand_cards=[204, 403],
        recorder_hero_cards=[204, 403],
        event_name="roundChangeNotify",
        actor_user_id="hero",
        runtime_user_id="hero",
        board=["Qs", "2h", "Tc"],
    )
    connection = sqlite3.connect(tmp_path / "hands.sqlite3")
    decision = live_decision(connection)
    connection.close()

    assert decision is not None
    assert decision["sequence"] == sequence
    assert decision["street"] == "flop"
    assert decision["hero_cards"] == ["4h", "3d"]
    assert decision["decision_subject"] == "self"


def test_current_equity_api_uses_live_board_and_hero_range(
    monkeypatch,
    tmp_path,
):
    sequence = _decision_store(
        tmp_path,
        hand_cards=[204, 403],
        recorder_hero_cards=[204, 403],
        event_name="roundChangeNotify",
        actor_user_id="hero",
        runtime_user_id="hero",
        board=["Qs", "2h", "Tc"],
    )
    captured = {}

    def fake_curve(context, hero_range):
        captured["board"] = context["decision"]["board"]
        captured["hero_range"] = hero_range
        return {
            "status": "ok",
            "curve_version": "test",
            "points": [],
        }

    monkeypatch.setattr(server_module, "build_range_equity_curve", fake_curve)
    client = TestClient(create_app(tmp_path))
    response = client.get(f"/api/equity/current?sequence={sequence}")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["sequence"] == sequence
    assert captured["board"] == ["Qs", "2h", "Tc"]
    assert captured["hero_range"]["weights"]


def test_current_equity_api_falls_back_to_in_progress_hand_snapshot(
    monkeypatch,
    tmp_path,
):
    _decision_store(
        tmp_path,
        event_name="roundChangeNotify",
        actor_user_id="villain",
        runtime_user_id="spectator",
        hand_cards=[],
        board=["Qs", "2h", "Tc", "Jc"],
    )
    captured = {}

    def fake_curve(context, hero_range):
        captured["decision"] = context["decision"]
        captured["hero_range"] = hero_range
        return {
            "status": "ok",
            "curve_version": "test",
            "points": [],
        }

    monkeypatch.setattr(server_module, "build_range_equity_curve", fake_curve)
    client = TestClient(create_app(tmp_path))
    response = client.get("/api/equity/current?hand_id=room-7")

    assert response.status_code == 200
    assert response.json()["context_source"] == "hand_snapshot"
    assert response.json()["stale"] is False
    assert captured["decision"]["street"] == "turn"
    assert captured["decision"]["decision_subject"] == "observer"
    assert captured["decision"]["subject_seat"] == 2
    assert captured["hero_range"]["weights"]


def test_current_equity_api_can_select_each_active_subject_seat(
    monkeypatch,
    tmp_path,
):
    _decision_store(
        tmp_path,
        event_name="roundChangeNotify",
        actor_user_id="villain",
        runtime_user_id="spectator",
        hand_cards=[],
        board=["Qs", "2h", "Tc"],
        extra_player=True,
    )
    captured_seats = []

    def fake_curve(context, _hero_range):
        captured_seats.append(context["decision"]["subject_seat"])
        return {
            "status": "ok",
            "curve_version": "test",
            "points": [],
        }

    monkeypatch.setattr(server_module, "build_range_equity_curve", fake_curve)
    client = TestClient(create_app(tmp_path))

    first = client.get(
        "/api/equity/current",
        params={"hand_id": "room-7", "subject_seat": 1},
    )
    third = client.get(
        "/api/equity/current",
        params={"hand_id": "room-7", "subject_seat": 3},
    )

    assert first.status_code == 200
    assert third.status_code == 200
    assert first.json()["subject_seat"] == 1
    assert third.json()["subject_seat"] == 3
    assert captured_seats == [1, 3]


def test_reasoner_calls_gateway_and_validates_legal_action(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            content = json.dumps(
                {
                    "recommended_action": "call",
                    "raise_to": None,
                    "confidence": "high",
                    "summary": "多人池先控制底池。",
                    "factors": ["合法跟注额明确"],
                    "risks": ["对手范围未知"],
                },
                ensure_ascii=False,
            )
            return json.dumps(
                {"choices": [{"message": {"content": content}}]}
            ).encode()

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(
        "wpk_recorder.reasoning.urllib.request.urlopen", fake_urlopen
    )
    reasoner = LLMReasoner("test-key", timeout_seconds=4, max_completion_tokens=900)
    context = {
        "decision": {
            "legal_actions": ["fold", "call", "raise"],
            "min_raise_to": 12,
            "max_raise_to": 100,
        }
    }
    result = reasoner.analyze(context, timeout_seconds=2)
    assert result["recommended_action"] == "call"
    assert result["confidence"] == "low"
    assert result["source"] == "gpt-5.6-sol"
    assert captured["timeout"] == 2
    payload = json.loads(captured["request"].data)
    assert payload["max_completion_tokens"] == 700
    assert payload["reasoning_effort"] == "low"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"][0]["role"] == "system"
    assert "derived_facts" in payload["messages"][1]["content"]

    custom = LLMReasoner(
        "test-key",
        endpoint=(
            "https://example.test/openai/deployments/"
            "custom-poker-model/chat/completions"
        ),
    )
    custom_result = custom.analyze(context)
    assert custom.status()["model"] == "custom-poker-model"
    assert custom_result["source"] == "custom-poker-model"


def test_exploit_reasoner_uses_compact_json_mode(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            content = json.dumps(
                {
                    "s": "对手翻牌弃牌偏多",
                    "i": [
                        {
                            "p": "seat_2",
                            "r": "翻牌弃牌偏高",
                            "a": "略增小尺度半诈唬",
                            "f": {
                                "s": "air",
                                "x": "check",
                                "t": "raise",
                                "d": 8,
                                "z": "弃牌率高，增加空气施压",
                            },
                            "e": ["seat_2:metric:fold_to_flop_bet"],
                        }
                    ],
                    "c": "high",
                },
                ensure_ascii=False,
            )
            return json.dumps(
                {"choices": [{"message": {"content": content}}]}
            ).encode()

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(
        "wpk_recorder.reasoning.urllib.request.urlopen",
        fake_urlopen,
    )
    context = {
        "decision": {
            "street": "flop",
            "legal_actions": ["check", "raise"],
            "llm_confidence_ceiling": "low",
        },
        "active_opponent_profiles": [
            {
                "player": "seat_2",
                "display_name": "测试对手",
                "position": "BTN",
                "observations": [
                    {
                        "evidence_id": "seat_2:metric:fold_to_flop_bet",
                        "metric": "fold_to_flop_bet",
                        "mean_pct": 72,
                        "population_mean_pct": 50,
                        "deviation_pp": 22,
                        "opportunities": 30,
                        "confidence": "medium",
                    }
                ],
            }
        ],
    }
    reasoner = LLMReasoner(
        "test-key",
        timeout_seconds=4,
        max_completion_tokens=900,
    )
    result = reasoner.analyze_exploit(context, timeout_seconds=2)
    payload = json.loads(captured["request"].data)
    assert payload["max_completion_tokens"] == 600
    assert payload["response_format"] == {"type": "json_object"}
    assert result["confidence"] == "low"
    assert result["opponent_reads"][0]["player"] == "seat_2"
    assert result["range_adjustments"][0]["evidence"] == [
        "seat_2:metric:fold_to_flop_bet"
    ]
    assert result["frequency_shifts"][0]["display_player"] == "测试对手"
    assert result["frequency_shifts"][0]["delta_pp"] == 4
    deep_result = reasoner.analyze_exploit(
        context,
        timeout_seconds=12,
        reasoning_depth="deep",
    )
    deep_payload = json.loads(captured["request"].data)
    assert captured["timeout"] == 12
    assert deep_payload["max_completion_tokens"] == 2000
    assert deep_payload["reasoning_effort"] == "medium"
    assert "这是重推理" in deep_payload["messages"][1]["content"]
    assert deep_result["reasoning_depth"] == "deep"


def test_profile_reasoner_forces_json_response_mode(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            content = json.dumps(
                {
                    "style_summary": "当前样本不足。",
                    "vulnerabilities": [],
                    "counter_strategy": ["继续收集样本"],
                    "caveats": ["范围依赖先验"],
                    "confidence": "low",
                },
                ensure_ascii=False,
            )
            return json.dumps(
                {"choices": [{"message": {"content": content}}]}
            ).encode()

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(
        "wpk_recorder.reasoning.urllib.request.urlopen",
        fake_urlopen,
    )
    reasoner = LLMReasoner(
        "test-key",
        timeout_seconds=4,
        max_completion_tokens=900,
    )

    result = reasoner.analyze_profile(
        {
            "confidence_ceiling": "low",
            "population_deviations": [],
            "evidence_cases": {"cases": []},
        },
        timeout_seconds=2,
    )
    payload = json.loads(captured["request"].data)

    assert captured["timeout"] == 1.4
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["max_completion_tokens"] == 2000
    assert result["style_summary"] == "当前样本不足。"


def test_profile_reasoner_retries_truncated_json(monkeypatch):
    calls = []

    class Response:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps(self.body, ensure_ascii=False).encode()

    def fake_urlopen(request, timeout):
        calls.append(
            {
                "payload": json.loads(request.data),
                "timeout": timeout,
                "log_id": request.headers["X-tt-logid"],
            }
        )
        if len(calls) == 1:
            return Response(
                {
                    "choices": [
                        {
                            "message": {
                                "content": '{"style_summary":"输出被截断"',
                            },
                            "finish_reason": "length",
                        }
                    ]
                }
            )
        content = json.dumps(
            {
                "style_summary": "缩短后返回成功。",
                "vulnerabilities": [],
                "counter_strategy": ["继续收集样本"],
                "caveats": ["低样本"],
                "confidence": "low",
            },
            ensure_ascii=False,
        )
        return Response(
            {
                "choices": [
                    {
                        "message": {"content": content},
                        "finish_reason": "stop",
                    }
                ]
            }
        )

    monkeypatch.setattr(
        "wpk_recorder.reasoning.urllib.request.urlopen",
        fake_urlopen,
    )
    reasoner = LLMReasoner(
        "test-key",
        timeout_seconds=4,
        profile_timeout_seconds=20,
        max_completion_tokens=800,
    )

    result = reasoner.analyze_profile(
        {
            "confidence_ceiling": "low",
            "population_deviations": [],
            "evidence_cases": {"cases": []},
        },
        timeout_seconds=20,
    )

    assert result["style_summary"] == "缩短后返回成功。"
    assert len(calls) == 2
    assert calls[0]["payload"]["max_completion_tokens"] == 2000
    assert calls[1]["payload"]["reasoning_effort"] == "low"
    assert "上次输出未形成完整 JSON" in calls[1]["payload"]["messages"][1][
        "content"
    ]
    assert calls[0]["log_id"].startswith("wpk-profile-")
    assert calls[1]["log_id"].startswith("wpk-profile-retry-")


def test_profile_findings_require_real_evidence_references():
    result = _validate_profile_result(
        {
            "style_summary": "偏被动",
            "vulnerabilities": [
                {
                    "finding": "跟注偏多",
                    "evidence": ["metric:all:vpip", "invented"],
                    "confidence": "high",
                },
                {
                    "finding": "没有证据的结论",
                    "evidence": ["invented"],
                    "confidence": "high",
                },
                {
                    "finding": "翻牌领打偏多",
                    "evidence": ["pattern:flop_donk_lead"],
                    "confidence": "medium",
                },
            ],
            "counter_strategy": ["扩大价值范围"],
            "caveats": [],
            "confidence": "high",
        },
        {
            "confidence_ceiling": "low",
            "population_deviations": [{"evidence_id": "metric:all:vpip"}],
            "evidence_cases": {
                "cases": [{"case_id": "case_01"}],
                "postflop_patterns": [
                    {"evidence_id": "pattern:flop_donk_lead"}
                ],
            },
        },
        12,
    )
    assert result["confidence"] == "low"
    assert result["vulnerabilities"] == [
        {
            "finding": "跟注偏多",
            "evidence": ["metric:all:vpip"],
            "confidence": "low",
        },
        {
            "finding": "翻牌领打偏多",
            "evidence": ["pattern:flop_donk_lead"],
            "confidence": "low",
        },
    ]


def test_derived_poker_facts_and_local_fast_paths():
    def context(**decision):
        return {
            "decision": {
                "min_raise_to": None,
                "max_raise_to": None,
                "players_in_hand": 2,
                **decision,
            },
            "action_history": [],
            "active_opponent_profiles": [],
            "limitations": [],
        }

    cheap_draw = context(
        street="turn",
        hero_cards=["As", "5s"],
        board=["Ks", "7s", "2d", "9c"],
        legal_actions=["fold", "call"],
        pot=100,
        call_score=10,
        hero_stack=190,
    )
    enriched = enrich_reasoning_context(cheap_draw)
    assert enriched["derived_facts"]["hand"]["flush_draw"] is True
    assert enriched["derived_facts"]["required_equity_to_call_pct"] == 9.1
    assert enriched["derived_facts"]["equity_vs_one_random_hand_pct"] > 9.1

    cases = [
        (
            "call",
            context(
                street="preflop",
                hero_cards=["As", "Ah"],
                board=[],
                legal_actions=["fold", "call"],
                pot=103,
                call_score=100,
            ),
        ),
        (
            "call",
            context(
                street="preflop",
                hero_cards=["As", "Ks"],
                board=[],
                legal_actions=["fold", "call", "raise"],
                pot=15,
                call_score=6,
                hero_stack_bb=94,
                table_players=6,
                game_mode="holdem",
                ante=0,
                min_raise_to=22,
                max_raise_to=100,
            ),
        ),
        (
            "fold",
            context(
                street="preflop",
                hero_cards=["7c", "2d"],
                board=[],
                legal_actions=["fold", "call"],
                pot=23,
                call_score=20,
            ),
        ),
        (
            "check",
            context(
                street="preflop",
                hero_cards=["7c", "2d"],
                board=[],
                legal_actions=["check", "raise"],
                pot=3,
                call_score=0,
                min_raise_to=4,
                max_raise_to=100,
            ),
        ),
        (
            "raise",
            context(
                street="river",
                hero_cards=["As", "Ks"],
                board=["Qs", "Js", "Ts", "2d", "3c"],
                legal_actions=["check", "raise"],
                pot=40,
                call_score=0,
                min_raise_to=20,
                max_raise_to=200,
            ),
        ),
        ("call", cheap_draw),
        (
            "fold",
            context(
                street="turn",
                hero_cards=["5c", "4d"],
                board=["As", "7h", "8d", "Kd"],
                legal_actions=["fold", "call"],
                pot=100,
                call_score=100,
            ),
        ),
        (
            "raise",
            context(
                street="flop",
                hero_cards=["Ah", "Ad"],
                board=["As", "Js", "Ts"],
                legal_actions=["fold", "call", "raise"],
                pot=90,
                call_score=30,
                min_raise_to=60,
                max_raise_to=200,
            ),
        ),
        (
            "call",
            context(
                street="flop",
                hero_cards=["Ah", "5h"],
                board=["Kh", "7h", "2c"],
                legal_actions=["fold", "call", "raise"],
                pot=100,
                call_score=50,
                min_raise_to=120,
                max_raise_to=200,
            ),
        ),
        (
            "fold",
            context(
                street="river",
                hero_cards=["As", "7c"],
                board=["Ah", "Kd", "Qd", "4s", "2c"],
                legal_actions=["fold", "call"],
                pot=100,
                call_score=400,
            ),
        ),
        (
            "call",
            context(
                street="river",
                hero_cards=["As", "7c"],
                board=["Ah", "Kd", "Qd", "4s", "2c"],
                legal_actions=["fold", "call"],
                pot=100,
                call_score=10,
            ),
        ),
    ]
    for expected, item in cases:
        result = local_fast_analysis(item)
        assert result is not None
        assert result["recommended_action"] == expected
        assert result["source"] == "local-rules-v1"

    overfold = context(
        street="flop",
        hero_cards=["8c", "7c"],
        board=["Ah", "Kd", "2s"],
        legal_actions=["check", "raise"],
        pot=50,
        call_score=0,
        min_raise_to=25,
        max_raise_to=100,
    )
    overfold["active_opponent_profiles"] = [
        {
            "observations": [
                {
                    "metric": "fold_to_flop_cbet",
                    "successes": 85,
                    "opportunities": 100,
                    "observed_pct": 85,
                }
            ]
        }
    ]
    exploit = local_fast_analysis(overfold)
    assert exploit is not None
    assert exploit["recommended_action"] == "raise"
    assert exploit["raise_to"] == 25


def test_simplified_range_strategy_covers_unknown_and_known_hands():
    preflop = simplified_range_strategy(
        {
            "decision": {
                "street": "preflop",
                "hero_cards": ["As", "Ks"],
                "hero_position": "BTN",
                "legal_actions": ["fold", "call", "raise"],
                "call_score": 6,
                "pot": 15,
                "players_in_hand": 2,
            },
            "action_history": [
                {
                    "street": "preflop",
                    "position": "CO",
                    "action": "raise",
                    "amount_to": 6,
                }
            ],
        }
    )
    assert preflop["kind"] == "preflop_matrix"
    assert preflop["hero_hand"] == "AKs"
    assert len(preflop["cells"]) == 169
    assert sum(preflop["action_mix"].values()) == 100
    assert all(
        sum(cell["frequencies"].values()) == 100
        for cell in preflop["cells"]
    )
    assert "CO 加注" in preflop["spot"]["action_line"]

    postflop = simplified_range_strategy(
        {
            "decision": {
                "street": "turn",
                "hero_cards": [],
                "hero_position": "BB",
                "board": ["Js", "Jh", "2d", "6d"],
                "legal_actions": ["fold", "call", "raise"],
                "call_score": 120,
                "pot": 359,
                "players_in_hand": 3,
            },
            "action_history": [
                {
                    "street": "turn",
                    "position": "BTN",
                    "action": "bet",
                    "amount": 120,
                }
            ],
        }
    )
    assert postflop["kind"] == "postflop_buckets"
    assert postflop["hero_bucket"] is None
    assert len(postflop["buckets"]) == 7
    assert all(
        sum(bucket["frequencies"].values()) == 100
        for bucket in postflop["buckets"]
    )


def test_solver_fit_bets_vulnerable_weak_top_pair_heads_up_but_tightens_multiway():
    decision = {
        "street": "flop",
        "hero_cards": ["Jd", "7h"],
        "hero_position": "BTN/SB",
        "acting_seat": 1,
        "action_order": [5, 1],
        "board": ["Jc", "2d", "Tc"],
        "legal_actions": ["check", "raise", "all_in"],
        "call_score": 0,
        "pot": 4,
        "hero_stack": 229,
        "big_blind": 2,
        "players_in_hand": 2,
        "table_players": 2,
    }
    actions = [
        {
            "street": "preflop",
            "seat": 1,
            "position": "BTN/SB",
            "action": "call",
        },
        {
            "street": "preflop",
            "seat": 5,
            "position": "BB",
            "action": "check",
        },
        {
            "street": "flop",
            "seat": 5,
            "position": "BB",
            "action": "check",
        },
    ]
    heads_up = simplified_range_strategy(
        {"decision": decision, "action_history": actions}
    )
    assert heads_up["hero_bucket"] == "top_pair_weak"
    assert heads_up["fit_profile"]["profile_id"] == "hu-limped-ip-flop"
    weak_top_pair = next(
        bucket
        for bucket in heads_up["buckets"]
        if bucket["key"] == "top_pair_weak"
    )
    assert weak_top_pair["frequencies"]["raise"] > 65
    assert weak_top_pair["primary_action"] == "raise"

    multiway_decision = {
        **decision,
        "players_in_hand": 4,
        "table_players": 6,
        "acting_seat": 4,
        "action_order": [1, 2, 3, 4],
    }
    multiway = simplified_range_strategy(
        {"decision": multiway_decision, "action_history": actions}
    )
    weak_top_pair_multiway = next(
        bucket
        for bucket in multiway["buckets"]
        if bucket["key"] == "top_pair_weak"
    )
    assert weak_top_pair_multiway["frequencies"]["check"] > 70


def test_heads_up_button_preflop_uses_raise_limp_fit_not_facing_limper():
    strategy = simplified_range_strategy(
        {
            "decision": {
                "street": "preflop",
                "hero_cards": ["Jd", "7h"],
                "hero_position": "BTN/SB",
                "legal_actions": ["fold", "call", "raise"],
                "call_score": 1,
                "pot": 3,
                "big_blind": 2,
                "hero_stack_bb": 100,
                "players_in_hand": 2,
                "table_players": 2,
            },
            "action_history": [],
        }
    )

    assert strategy["scenario"] == "heads_up_button"
    hero = next(
        cell for cell in strategy["cells"] if cell["hand"] == "J7o"
    )
    assert hero["frequencies"].get("fold", 0) == 0
    assert hero["frequencies"].get("raise", 0) > 0
    assert hero["frequencies"].get("call", 0) > 0


def test_ante_small_blind_first_in_plays_j8o_as_call_raise_mix():
    strategy = simplified_range_strategy(
        {
            "decision": {
                "street": "preflop",
                "hero_cards": ["Jh", "8c"],
                "hero_position": "SB",
                "legal_actions": ["fold", "call", "raise"],
                "call_score": 2,
                "pot": 13,
                "small_blind": 2,
                "big_blind": 4,
                "ante": 1,
                "hero_stack_bb": 200,
                "players_in_hand": 2,
                "table_players": 9,
            },
            "action_history": [
                {
                    "street": "preflop",
                    "position": position,
                    "action": "fold",
                }
                for position in ("UTG", "MP", "HJ", "CO", "BTN")
            ],
        }
    )

    assert strategy["scenario"] == "small_blind_first_in"
    assert strategy["action_mix"]["fold"] <= 10
    hero = next(
        cell for cell in strategy["cells"] if cell["hand"] == "J8o"
    )
    assert hero["frequencies"] == {"raise": 35, "call": 65}


def test_small_blind_multi_limper_range_tightens_weak_offsuit_raises():
    decision = {
        "street": "preflop",
        "hero_cards": [],
        "hero_position": "SB",
        "legal_actions": ["fold", "call", "raise", "all_in"],
        "call_score": 2,
        "pot": 23,
        "big_blind": 4,
        "ante": 1,
        "hero_stack_bb": 200,
        "players_in_hand": 4,
        "table_players": 9,
    }
    actions = [
        {"street": "preflop", "position": "UTG", "action": "call"},
        {"street": "preflop", "position": "MP", "action": "fold"},
        {"street": "preflop", "position": "HJ", "action": "fold"},
        {"street": "preflop", "position": "CO", "action": "call"},
        {"street": "preflop", "position": "BTN", "action": "fold"},
    ]
    multi_limp = simplified_range_strategy(
        {"decision": decision, "action_history": actions}
    )
    one_limp = simplified_range_strategy(
        {"decision": decision, "action_history": actions[1:]}
    )

    assert multi_limp["scenario"] == "facing_limp"
    assert multi_limp["spot"]["limper_count"] == 2
    assert multi_limp["action_mix"]["raise"] <= 18
    assert (
        one_limp["action_mix"]["raise"]
        > multi_limp["action_mix"]["raise"]
    )
    for hand in ("A6o", "K8o", "Q8o", "J7o", "T7o"):
        cell = next(
            item for item in multi_limp["cells"] if item["hand"] == hand
        )
        assert cell["frequencies"].get("raise", 0) == 0


def test_full_ring_deep_ante_range_prioritizes_suited_playability():
    strategy = simplified_range_strategy(
        {
            "decision": {
                "street": "preflop",
                "hero_position": "UTG",
                "legal_actions": ["fold", "raise"],
                "call_score": 0,
                "pot": 13,
                "big_blind": 4,
                "ante": 1,
                "hero_stack_bb": 200,
                "players_in_hand": 9,
                "table_players": 9,
                "game_mode": "holdem",
            },
            "action_history": [],
            "active_opponent_profiles": [],
        }
    )
    cells = {item["hand"]: item["frequencies"] for item in strategy["cells"]}

    for suited, offsuit in (
        ("KTs", "KTo"),
        ("A5s", "A5o"),
        ("A2s", "A2o"),
        ("98s", "98o"),
    ):
        assert cells[suited].get("raise", 0) > cells[offsuit].get("raise", 0)
    assert strategy["range_context"]["table_format"] == "full_ring"
    assert strategy["range_context"]["total_ante_bb"] == 2.25


def test_lag_pressure_tightens_fringe_preflop_range():
    decision = {
        "street": "preflop",
        "hero_position": "UTG",
        "legal_actions": ["fold", "raise"],
        "call_score": 0,
        "pot": 13,
        "big_blind": 4,
        "ante": 1,
        "hero_stack_bb": 200,
        "players_in_hand": 9,
        "table_players": 9,
        "game_mode": "holdem",
    }
    passive = simplified_range_strategy(
        {
            "decision": decision,
            "action_history": [],
            "active_opponent_profiles": [],
        }
    )
    pressured = simplified_range_strategy(
        {
            "decision": decision,
            "action_history": [],
            "active_opponent_profiles": [
                {
                    "observations": [
                        {
                            "metric": "three_bet",
                            "mean_pct": 18,
                            "population_mean_pct": 8,
                            "opportunities": 50,
                        }
                    ]
                }
            ],
        }
    )

    assert pressured["range_context"]["aggressive_pressure"] == 1
    assert pressured["action_mix"]["raise"] < passive["action_mix"]["raise"]
    assert "3bet 压力高" in pressured["summary"]


def test_late_squid_pressure_widens_preflop_without_flat_suited_boost():
    decision = {
        "street": "preflop",
        "hero_position": "UTG",
        "legal_actions": ["fold", "raise"],
        "call_score": 0,
        "pot": 13,
        "big_blind": 4,
        "ante": 1,
        "hero_stack_bb": 200,
        "players_in_hand": 9,
        "table_players": 9,
        "game_mode": "squid",
    }
    regular = simplified_range_strategy(
        {
            "decision": {**decision, "game_mode": "holdem"},
            "action_history": [],
        }
    )
    squid = simplified_range_strategy(
        {
            "decision": decision,
            "action_history": [],
            "squid_round": {
                "counts": {"acting_player": 0, "seat_2": 1},
                "zero_squid_players": 2,
            },
        }
    )
    cells = {item["hand"]: item["frequencies"] for item in squid["cells"]}

    assert squid["range_context"]["squid_pressure"] == "high"
    assert squid["action_mix"]["raise"] > regular["action_mix"]["raise"]
    assert cells["A5s"].get("raise", 0) > cells["A5o"].get("raise", 0)
    assert cells["76s"].get("raise", 0) == 0


def test_preflop_folds_without_limper_remain_unopened():
    strategy = simplified_range_strategy(
        {
            "decision": {
                "street": "preflop",
                "hero_position": "HJ",
                "legal_actions": ["fold", "call", "raise"],
                "call_score": 4,
                "pot": 13,
                "big_blind": 4,
                "table_players": 9,
            },
            "action_history": [
                {
                    "street": "preflop",
                    "position": "UTG",
                    "action": "fold",
                }
            ],
        }
    )

    assert strategy["scenario"] == "unopened"


def test_reasoning_api_returns_range_when_hole_cards_are_missing(tmp_path):
    sequence = _decision_store(tmp_path, hand_cards=[])

    class UnconfiguredReasoner:
        configured = False
        timeout_seconds = 1.0

        @staticmethod
        def status():
            return {"configured": False, "model": "test"}

    client = TestClient(create_app(tmp_path, llm_reasoner=UnconfiguredReasoner()))
    response = client.post(
        "/api/reasoning/analyze",
        json={"sequence": sequence, "force": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["analysis"] is None
    assert body["decision"]["hero_position"] == "BB"
    assert body["strategy"]["kind"] == "preflop_matrix"
    assert len(body["strategy"]["cells"]) == 169
    assert (
        body["strategy"]["spot"]["action_line"]
        == "BTN/SB 加注 → BB 决策"
    )


def test_reasoning_api_can_force_llm_range_exploit_without_cards(tmp_path):
    sequence = _decision_store(tmp_path, hand_cards=[])

    class ExploitReasoner:
        configured = True
        timeout_seconds = 1.0
        deep_timeout_seconds = 20.0
        reasoning_effort = "low"
        deep_reasoning_effort = "high"

        @staticmethod
        def status():
            return {
                "configured": True,
                "model": "gpt-5.6-sol",
                "timeout_seconds": 1.0,
            }

        @staticmethod
        def analyze_exploit(context, timeout, reasoning_depth="light"):
            assert context["decision"]["hero_cards"] == []
            assert timeout <= (
                20.0 if reasoning_depth == "deep" else 1.0
            )
            return {
                "summary": "没有底牌，只调整整个范围。",
                "opponent_reads": [],
                "range_adjustments": [],
                "caveats": ["当前样本不足"],
                "confidence": "low",
                "latency_ms": 21,
                "source": "gpt-5.6-sol",
            }

    client = TestClient(create_app(tmp_path, llm_reasoner=ExploitReasoner()))
    response = client.post(
        "/api/reasoning/analyze",
        json={
            "sequence": sequence,
            "force": True,
            "analysis_mode": "llm",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["analysis"] is None
    assert body["exploit_analysis"]["source"] == "gpt-5.6-sol"
    assert body["route"]["llm_called"] is True
    assert body["inference"]["schema_version"] == "inference-advice-v1"
    assert body["inference"]["subject"] == "full_range"
    unified = client.post(
        "/api/inference/analyze",
        json={
            "sequence": sequence,
            "force": True,
            "analysis_mode": "llm",
        },
    )
    assert unified.status_code == 200
    assert unified.json()["recommendation"]["kind"] == "full_range"
    runs = client.get("/api/inference/runs").json()["runs"]
    assert runs
    assert runs[0]["template_id"] == "full-range-v1"
    deep_response = client.post(
        "/api/reasoning/analyze",
        json={
            "sequence": sequence,
            "force": True,
            "analysis_mode": "llm",
            "reasoning_depth": "deep",
        },
    )
    assert deep_response.status_code == 200
    deep_route = deep_response.json()["route"]
    assert deep_route["reasoning_depth"] == "deep"
    assert deep_route["timeout_seconds"] == 20.0
    assert deep_route["reasoning_effort"] == "high"


def test_llm_prompts_keep_complete_action_line_board_and_required_cards():
    actions = [
        {
            "street": "preflop" if index < 20 else "flop",
            "seat": (index % 6) + 1,
            "position": "BTN",
            "action": "call",
            "amount": index,
            "amount_to": index,
            "sequence": index,
        }
        for index in range(1, 41)
    ]
    decision = {
        "decision_subject": "self",
        "street": "river",
        "hero_cards": ["As", "Kd"],
        "board": ["Ah", "7c", "4d", "2s", "Jc"],
        "legal_actions": ["check", "raise"],
        "call_score": 0,
        "min_raise_to": 20,
        "max_raise_to": 100,
        "hero_stack": 100,
        "hero_stack_bb": 50,
        "pot": 20,
        "big_blind": 2,
        "players_in_hand": 2,
        "table_players": 6,
        "game_mode": "holdem",
        "llm_confidence_ceiling": "low",
    }
    context = {
        "decision": decision,
        "action_history": actions,
        "active_opponent_profiles": [],
        "context_integrity": {
            "complete_action_history": True,
            "hole_cards_required": True,
            "hole_cards_included": True,
        },
    }

    exact_prompt = build_prompt(context)
    assert '"hero_cards":["As","Kd"]' in exact_prompt
    assert '"board":["Ah","7c","4d","2s","Jc"]' in exact_prompt
    assert '"sequence":1' in exact_prompt
    assert '"sequence":40' in exact_prompt

    observer_context = {
        **context,
        "decision": {
            **decision,
            "decision_subject": "observer",
            "hero_cards": [],
        },
    }
    observer_prompt = build_exploit_prompt(observer_context)
    assert '"board":["Ah","7c","4d","2s","Jc"]' in observer_prompt
    assert '"sequence":1' in observer_prompt
    assert '"sequence":40' in observer_prompt
    assert '"hero_cards"' not in observer_prompt


def test_forced_llm_can_replay_frozen_node_after_hand_advances(tmp_path):
    sequence = _decision_store(
        tmp_path,
        hand_cards=[],
        actor_user_id="villain",
        runtime_user_id="spectator",
        board=["2h", "7s", "Jd"],
        legal_actions=["CHECK", "RAISE"],
        call_score=0,
    )

    class ReplayReasoner:
        configured = True
        timeout_seconds = 1.0
        deep_timeout_seconds = 2.0
        reasoning_effort = "low"
        deep_reasoning_effort = "high"

        @staticmethod
        def status():
            return {
                "configured": True,
                "model": "gpt-5.6-sol",
                "timeout_seconds": 1.0,
            }

        @staticmethod
        def analyze_exploit(context, _timeout, reasoning_depth="light"):
            assert context["decision"]["decision_subject"] == "observer"
            assert context["decision"]["hero_cards"] == []
            assert len(context["decision"]["board"]) == 3
            assert context["action_history"][0]["action"] == "raise"
            assert context["context_integrity"]["frozen_replay"] is True
            return {
                "summary": "按冻结行动点复盘。",
                "opponent_reads": [],
                "range_adjustments": [],
                "frequency_shifts": [],
                "confidence": "low",
                "latency_ms": 5,
                "source": "gpt-5.6-sol",
            }

    client = TestClient(create_app(tmp_path, llm_reasoner=ReplayReasoner()))
    current = client.get(
        "/api/strategy/current",
        params={"sequence": sequence},
    )
    assert current.status_code == 200
    decision = current.json()["decision"]

    store = RecorderStore(tmp_path)
    store.save_raw_event(
        RawEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_name="playResultNotify",
            payload={"event": "playResultNotify", "data": {}},
            sequence=sequence + 1,
            table_id="room",
            hand_id="room-7",
            sha256="advanced",
        )
    )
    store.close()

    replay = client.post(
        "/api/reasoning/analyze",
        json={
            "sequence": sequence,
            "state_hash": decision["state_hash"],
            "hand_id": decision["hand_id"],
            "force": True,
            "analysis_mode": "llm",
        },
    )
    assert replay.status_code == 200
    payload = replay.json()
    assert payload["stale"] is True
    assert payload["route"]["temporal_quality"] == (
        "frozen_point_in_time_replay"
    )
    assert payload["route"]["reason"] == "基于已冻结行动点复盘；牌局已继续"


def test_reasoning_api_falls_back_when_llm_fails(tmp_path):
    sequence = _decision_store(tmp_path, hand_cards=[])

    class FailingReasoner:
        configured = True
        timeout_seconds = 1.0

        @staticmethod
        def status():
            return {
                "configured": True,
                "model": "gpt-5.6-sol",
                "timeout_seconds": 1.0,
            }

        @staticmethod
        def analyze_exploit(_context, _timeout):
            raise ReasoningError("LLM 网关连接失败或超时")

    client = TestClient(create_app(tmp_path, llm_reasoner=FailingReasoner()))
    response = client.post(
        "/api/reasoning/analyze",
        json={
            "sequence": sequence,
            "force": True,
            "analysis_mode": "llm",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["route"]["llm_called"] is True
    assert body["route"]["llm_completed"] is False
    assert body["exploit_analysis"]["source"] == (
        "local-exploit-fallback-v1"
    )


def test_range_exploit_validation_requires_real_opponent_evidence():
    context = {
        "decision": {"llm_confidence_ceiling": "low"},
        "active_opponent_profiles": [
            {
                "player": "seat_2",
                "observations": [
                    {
                        "evidence_id": "seat_2:metric:fold_to_flop_cbet",
                    }
                ],
            }
        ],
    }
    result = _validate_exploit_result(
        {
            "summary": "扩大对高弃牌率对手的施压范围。",
            "opponent_reads": [
                {
                    "player": "seat_2",
                    "finding": "翻牌弃牌偏多",
                    "evidence": ["seat_2:metric:fold_to_flop_cbet"],
                },
                {
                    "player": "seat_9",
                    "finding": "编造判断",
                    "evidence": ["invented"],
                },
            ],
            "range_adjustments": [
                {
                    "adjustment": "增加有后门权益的小尺度下注",
                    "evidence": ["seat_2:metric:fold_to_flop_cbet"],
                }
            ],
            "confidence": "high",
        },
        context,
        20,
    )
    assert result["confidence"] == "low"
    assert len(result["opponent_reads"]) == 1
    assert len(result["range_adjustments"]) == 1
    assert result["evidence_details"][0]["evidence_id"] == (
        "seat_2:metric:fold_to_flop_cbet"
    )


def test_structured_frequency_shift_is_named_capped_and_applied():
    evidence_id = "seat_2:metric:fold_to_three_bet"
    context = {
        "decision": {
            "street": "preflop",
            "hero_position": "BTN",
            "legal_actions": ["fold", "call", "raise"],
            "call_score": 0,
            "llm_confidence_ceiling": "medium",
        },
        "action_history": [],
        "active_opponent_profiles": [
            {
                "player": "seat_2",
                "display_name": "Meooowww",
                "observations": [
                    {
                        "evidence_id": evidence_id,
                        "metric": "fold_to_three_bet",
                        "mean_pct": 72,
                        "population_mean_pct": 51,
                        "deviation_pp": 21,
                        "opportunities": 30,
                        "confidence": "medium",
                    }
                ],
            }
        ],
    }
    result = _validate_exploit_result(
        {
            "summary": "扩大弱牌偷盲。",
            "confidence": "medium",
            "frequency_shifts": [
                {
                    "player": "seat_2",
                    "scope": "weak",
                    "from_action": "fold",
                    "to_action": "raise",
                    "delta_pp": 12,
                    "reason": "对手弃牌率显著偏高",
                    "evidence": [evidence_id],
                }
            ],
        },
        context,
        20,
    )
    shift = result["frequency_shifts"][0]
    assert shift["display_player"] == "Meooowww"
    assert shift["requested_delta_pp"] == 12
    assert shift["delta_pp"] == 8
    assert result["evidence_details"][0]["display_player"] == "Meooowww"
    assert "Meooowww" not in build_exploit_prompt(context)

    baseline = simplified_range_strategy(context)
    adjusted = apply_exploit_frequency_shifts(baseline, result)
    assert adjusted is not None
    assert adjusted["source"] == "llm-bounded-exploit-v1"
    applied = adjusted["applied_frequency_shifts"][0]
    assert applied["adjusted_to_pct"] > applied["baseline_to_pct"]
    assert all(
        round(sum(cell["frequencies"].values()), 6) == 100
        for cell in adjusted["cells"]
    )


def test_reasoning_api_uses_injected_reasoner(tmp_path):
    sequence = _decision_store(tmp_path)
    store = RecorderStore(tmp_path)
    completed = HandHistory(
        hand_id="room-6",
        table_id="room",
        started_at="2026-01-01T00:00:00+00:00",
        status="completed",
        button_seat=2,
        big_blind=2,
    )
    completed.players = {
        2: Player(
            2,
            user_id="villain",
            alias="Villain",
            stack_start=300,
            hole_cards=["Qs", "Qh"],
        )
    }
    completed.actions = [
        Action(
            "preflop",
            2,
            "Villain",
            "raise",
            amount=6,
            amount_to=6,
            user_id="villain",
            action_id="profile-1",
            sequence=1,
        )
    ]
    store.save_hand(completed)
    store.close()

    profile_calls = {"count": 0}

    class FakeReasoner:
        configured = True
        timeout_seconds = 1.0

        @staticmethod
        def status():
            return {
                "configured": True,
                "model": "gpt-5.6-sol",
                "timeout_seconds": 1.0,
                "max_completion_tokens": 800,
            }

        @staticmethod
        def analyze(context, timeout):
            assert context["decision"]["sequence"] == sequence
            assert timeout <= 1.0
            return {
                "recommended_action": "call",
                "raise_to": None,
                "confidence": "low",
                "summary": "测试建议",
                "factors": ["测试因素"],
                "risks": [],
                "latency_ms": 5,
                "source": "gpt-5.6-sol",
            }

        @staticmethod
        def analyze_profile(context, timeout):
            profile_calls["count"] += 1
            assert context["player"]["identity"] == "target_player"
            assert context["selected_preflop_range"]["line"] == "vpip"
            assert context["evidence_cases"]["selected_cases"] == 1
            assert "hand_id" not in context["evidence_cases"]["cases"][0]
            assert "villain" not in json.dumps(context).lower()
            assert timeout <= 1.0
            return {
                "style_summary": "样本不足，暂按本地先验。",
                "vulnerabilities": [],
                "counter_strategy": ["继续收集公开亮牌"],
                "caveats": ["当前没有公开底牌"],
                "confidence": "low",
                "latency_ms": 5,
                "source": "gpt-5.6-sol",
            }

    client = TestClient(create_app(tmp_path, llm_reasoner=FakeReasoner()))
    status = client.get("/api/reasoning/status")
    assert status.status_code == 200
    assert status.json()["configured"] is True
    snapshot = client.get("/api/snapshot").json()
    assert snapshot["live_decision"]["sequence"] == sequence
    response = client.post(
        "/api/reasoning/analyze", json={"sequence": sequence}
    )
    assert response.status_code == 200
    assert response.json()["analysis"]["recommended_action"] == "call"
    assert response.json()["strategy"]["hero_hand"] == "AKs"
    assert (
        response.json()["money_strategy"]["engine_version"]
        == "money-ev-dynamic-sizing-v13"
    )
    assert response.json()["stale"] is False
    strategy_response = client.get(f"/api/strategy/current?sequence={sequence}")
    assert strategy_response.status_code == 200
    assert strategy_response.json()["audit_saved"] is True
    assert strategy_response.json()["stale"] is False
    evaluations = client.get("/api/strategy/evaluations").json()["evaluations"]
    assert evaluations[0]["sequence"] == sequence
    assert evaluations[0]["engine_version"] == "money-ev-dynamic-sizing-v13"
    profile_response = client.post(
        "/api/players/villain/profile/analyze",
        json={"position": "ALL", "line": "vpip"},
    )
    assert profile_response.status_code == 200
    assert profile_response.json()["analysis"]["confidence"] == "low"
    assert profile_response.json()["persistence"]["saved"] is True
    assert profile_response.json()["persistence"]["restored"] is False
    saved_profile = client.get(
        "/api/players/villain/profile/analysis",
        params={"position": "ALL", "line": "vpip"},
    )
    assert saved_profile.status_code == 200
    assert saved_profile.json()["persistence"]["restored"] is True
    assert saved_profile.json()["persistence"]["stale"] is False

    restarted_client = TestClient(
        create_app(tmp_path, llm_reasoner=FakeReasoner())
    )
    restored_profile = restarted_client.post(
        "/api/players/villain/profile/analyze",
        json={"position": "ALL", "line": "vpip"},
    )
    assert restored_profile.status_code == 200
    assert restored_profile.json()["persistence"]["restored"] is True
    assert profile_calls["count"] == 1
