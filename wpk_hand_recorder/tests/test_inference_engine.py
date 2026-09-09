from __future__ import annotations

import sqlite3

from wpk_recorder.inference_engine import (
    InferenceEngine,
    freeze_inference_context,
    inference_context_hash,
)
from wpk_recorder.inference_eval import _chosen_action, evaluate_prompt_templates
from wpk_recorder.inference_templates import (
    ADVICE_SCHEMA_VERSION,
    CONTEXT_SCHEMA_VERSION,
    get_template,
)
from wpk_recorder.storage import (
    RecorderStore,
    inference_context_rows,
    load_inference_context,
    save_inference_context,
)


def _range_context():
    return {
        "decision": {
            "decision_id": "decision-1",
            "state_hash": "state-1",
            "sequence": 7,
            "hand_id": "hand-1",
            "decision_subject": "observer",
            "street": "turn",
            "hero_cards": [],
            "board": ["2h", "7s", "5s", "Jh"],
            "legal_actions": ["check", "raise"],
            "hero_position": "BB",
            "players_in_hand": 3,
            "table_players": 6,
            "pot": 20,
            "call_score": 0,
            "llm_confidence_ceiling": "low",
        },
        "action_history": [],
        "active_opponent_profiles": [
            {
                "player": "seat_2",
                "display_name": "Visible Name",
                "observations": [],
            }
        ],
        "limitations": ["test"],
    }


class FakeReasoner:
    configured = True
    timeout_seconds = 1.0
    deep_timeout_seconds = 2.0

    @staticmethod
    def analyze_exploit(
        _context,
        _timeout,
        reasoning_depth="light",
        template_id=None,
    ):
        return {
            "summary": "保持范围基线。",
            "confidence": "low",
            "opponent_reads": [],
            "range_adjustments": [],
            "frequency_shifts": [],
            "evidence_details": [],
            "latency_ms": 5,
            "source": "fake-model",
            "reasoning_depth": reasoning_depth,
            "prompt_hash": f"prompt:{template_id}",
        }


def test_unified_contract_and_frozen_context_are_versioned_and_anonymous():
    context = _range_context()
    snapshot = freeze_inference_context(context)
    assert snapshot["schema_version"] == CONTEXT_SCHEMA_VERSION
    assert snapshot["display_names"] == {"seat_2": "Visible Name"}
    assert "display_name" not in str(snapshot["context"])
    assert inference_context_hash(snapshot) == inference_context_hash(snapshot)

    engine = InferenceEngine(FakeReasoner())
    remote = engine.run_remote(
        context,
        1,
        "light",
        "full-range-v1",
    )
    advice = engine.build_advice(
        context,
        analysis=remote["analysis"],
        route={"llm_called": True, "source": "fake-model"},
    )
    assert advice["schema_version"] == ADVICE_SCHEMA_VERSION
    assert advice["subject"] == "full_range"
    assert advice["recommendation"]["kind"] == "full_range"
    assert advice["recommendation"]["action"] is None
    assert advice["route"]["template_id"] == "full-range-v1"


def test_exact_advice_keeps_offline_money_action_when_llm_disagrees():
    context = {
        "decision": {
            "decision_id": "decision-exact",
            "state_hash": "state-exact",
            "sequence": 8,
            "hand_id": "hand-exact",
            "decision_subject": "self",
            "street": "flop",
            "hero_cards": ["Jd", "7h"],
            "board": ["Jc", "2d", "Tc"],
            "legal_actions": ["check", "raise", "all_in"],
            "hero_position": "BTN/SB",
            "players_in_hand": 2,
            "table_players": 2,
            "pot": 4,
            "call_score": 0,
        },
        "action_history": [],
        "active_opponent_profiles": [],
    }
    advice = InferenceEngine(FakeReasoner()).build_advice(
        context,
        money_strategy={
            "recommended": {
                "action": "raise",
                "raise_to": 2,
            },
            "confidence": "low",
        },
        analysis={
            "recommended_action": "check",
            "raise_to": None,
            "confidence": "low",
            "factors": ["LLM disagreed"],
        },
    )

    assert advice["recommendation"]["action"] == "raise"
    assert advice["recommendation"]["raise_to"] == 2


def test_frozen_context_persistence_and_offline_template_comparison(tmp_path):
    store = RecorderStore(tmp_path)
    store.close()
    context = _range_context()
    snapshot = freeze_inference_context(context)
    context_hash = inference_context_hash(snapshot)
    assert save_inference_context(tmp_path, context_hash, snapshot)
    rows = inference_context_rows(tmp_path)
    assert len(rows) == 1
    assert "Visible Name" not in rows[0]["payload_json"]
    restored = load_inference_context(
        tmp_path,
        7,
        state_hash="state-1",
        hand_id="hand-1",
    )
    assert restored is not None
    assert restored["decision"]["board"] == ["2h", "7s", "5s", "Jh"]
    assert restored["context_integrity"]["frozen_replay"] is True
    assert load_inference_context(tmp_path, 7, state_hash="wrong") is None

    report = evaluate_prompt_templates(
        tmp_path,
        FakeReasoner(),
        ["full-range-v1", "full-range-evidence-v2"],
        repeats=2,
        reasoning_depth="light",
        persist=False,
    )
    assert report["context_count"] == 1
    assert len(report["templates"]) == 2
    assert all(
        item["valid_output_rate"] == 100
        for item in report["templates"]
    )
    assert all(
        item["stable_context_rate"] == 100
        for item in report["templates"]
    )
    assert get_template("full-range-evidence-v2").production_ready is False


def test_offline_eval_falls_back_to_action_sequence(tmp_path):
    connection = sqlite3.connect(tmp_path / "hands.sqlite3")
    connection.execute(
        """
        CREATE TABLE decision_snapshots(
            hand_id TEXT,
            action_sequence INTEGER,
            event_sequence INTEGER,
            chosen_action TEXT
        )
        """
    )
    connection.execute(
        "INSERT INTO decision_snapshots VALUES ('hand-1', 7, 99, 'raise')"
    )
    connection.commit()
    connection.close()

    assert _chosen_action(tmp_path, "hand-1", 7) == "raise"
