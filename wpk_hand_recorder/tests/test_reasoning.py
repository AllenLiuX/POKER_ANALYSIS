from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from wpk_recorder.models import Action, HandHistory, Player, RawEvent
from wpk_recorder.reasoning import (
    LLMReasoner,
    ReasoningError,
    _validate_exploit_result,
    _validate_profile_result,
    _side_pots,
    enrich_reasoning_context,
    live_decision,
    local_fast_analysis,
    public_decision,
    reasoning_context,
    simplified_range_strategy,
)
from wpk_recorder.server import create_app
from wpk_recorder.storage import RecorderStore


def _decision_store(tmp_path, hand_cards=None) -> int:
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
    hand.players = {
        1: Player(1, user_id="hero", alias="Hero", stack_start=300),
        2: Player(2, user_id="villain", alias="Villain", stack_start=300),
    }
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
    payload = {
        "event": "userOptNotify",
        "data": {
            "canActionList": ["FOLD", "CALL", "RAISE", "ALL_IN"],
            "handCards": [101, 113] if hand_cards is None else hand_cards,
            "userId": "hero",
            "minRaiseScore": 12,
            "maxRaiseScore": 300,
            "callScore": 6,
            "countDown": 30,
            "lastBet": 6,
            "seatScore": 0,
            "currentScore": 300,
        },
    }
    store.save_raw_event(
        RawEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_name="userOptNotify",
            payload=payload,
            sequence=1,
            table_id="room",
            hand_id="room-7",
            sha256=hashlib.sha256(repr(payload).encode()).hexdigest(),
        )
    )
    store.close()
    return 1


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
    encoded = json.dumps(context)
    assert '"hero"' not in encoded
    assert '"villain"' not in encoded


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
    deep_result = reasoner.analyze_exploit(
        context,
        timeout_seconds=12,
        reasoning_depth="deep",
    )
    deep_payload = json.loads(captured["request"].data)
    assert captured["timeout"] == 12
    assert deep_payload["max_completion_tokens"] == 1600
    assert deep_payload["reasoning_effort"] == "high"
    assert "这是重推理" in deep_payload["messages"][1]["content"]
    assert deep_result["reasoning_depth"] == "deep"


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
            ],
            "counter_strategy": ["扩大价值范围"],
            "caveats": [],
            "confidence": "high",
        },
        {
            "confidence_ceiling": "low",
            "population_deviations": [{"evidence_id": "metric:all:vpip"}],
            "evidence_cases": {"cases": [{"case_id": "case_01"}]},
        },
        12,
    )
    assert result["confidence"] == "low"
    assert result["vulnerabilities"] == [
        {
            "finding": "跟注偏多",
            "evidence": ["metric:all:vpip"],
            "confidence": "low",
        }
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
    assert len(postflop["buckets"]) == 5
    assert all(
        sum(bucket["frequencies"].values()) == 100
        for bucket in postflop["buckets"]
    )


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
    assert response.json()["money_strategy"]["engine_version"] == "money-ev-baseline-v1"
    assert response.json()["stale"] is False
    strategy_response = client.get(f"/api/strategy/current?sequence={sequence}")
    assert strategy_response.status_code == 200
    assert strategy_response.json()["audit_saved"] is True
    assert strategy_response.json()["stale"] is False
    evaluations = client.get("/api/strategy/evaluations").json()["evaluations"]
    assert evaluations[0]["sequence"] == sequence
    assert evaluations[0]["engine_version"] == "money-ev-baseline-v1"
    profile_response = client.post(
        "/api/players/villain/profile/analyze",
        json={"position": "ALL", "line": "vpip"},
    )
    assert profile_response.status_code == 200
    assert profile_response.json()["analysis"]["confidence"] == "low"
