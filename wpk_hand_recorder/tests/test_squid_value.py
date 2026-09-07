import sqlite3

from wpk_recorder.decision_state import decision_state_contract
from wpk_recorder.models import HandHistory, SquidEvent
from wpk_recorder.squid_value import (
    SquidRoundState,
    calibrate_squid_rules,
    estimate_award_probabilities,
    hero_values_after_next_award,
    terminal_payoffs,
)
from wpk_recorder.storage import RecorderStore


def test_user_described_squid_settlement_is_zero_sum():
    counts = {
        "p1": 4,
        "p2": 3,
        "p3": 2,
        "p4": 2,
        "p5": 1,
        "p6": 1,
        "p7": 0,
        "p8": 0,
        "p9": 0,
    }
    payoffs = terminal_payoffs(counts, 10)

    assert payoffs["p1"] == 120
    assert payoffs["p3"] == 60
    assert payoffs["p7"] == -130
    assert sum(payoffs.values()) == 0


def test_completed_round_calibrates_awarded_squid_value(tmp_path):
    store = RecorderStore(tmp_path)
    participants = ["a", "b", "c", "d"]
    store.save_squid_event(
        SquidEvent(
            round_id="round-1",
            scene=1,
            timestamp="2026-01-01T00:00:00+00:00",
            users=participants,
            raw={"scene": 1, "users": participants},
            sequence=1,
        )
    )
    store.save_squid_event(
        SquidEvent(
            round_id="round-1",
            scene=3,
            timestamp="2026-01-01T00:01:00+00:00",
            users=participants,
            ext={"winSquidNum": 1},
            raw={"scene": 3, "users": ["a"], "ext": {"winSquidNum": 1}},
            sequence=2,
        )
    )
    store.save_squid_event(
        SquidEvent(
            round_id="round-1",
            scene=4,
            timestamp="2026-01-01T00:02:00+00:00",
            users=participants,
            settlements=[
                {"userId": "a", "addScore": 30, "curScore": 130},
                {"userId": "b", "addScore": -10, "curScore": 90},
                {"userId": "c", "addScore": -10, "curScore": 90},
                {"userId": "d", "addScore": -10, "curScore": 90},
            ],
            raw={"scene": 4, "users": participants},
            sequence=3,
        )
    )
    store.close()

    connection = sqlite3.connect(tmp_path / "hands.sqlite3")
    calibration = calibrate_squid_rules(connection)
    connection.close()

    assert calibration["payout_basis"] == "awarded"
    assert calibration["squid_value"] == 10
    assert calibration["accepted_rounds"] == 1


def test_rule_mismatch_is_a_hard_calibration_failure(tmp_path):
    store = RecorderStore(tmp_path)
    participants = ["a", "b", "c", "d"]
    store.save_squid_event(
        SquidEvent(
            round_id="bad-round",
            scene=1,
            timestamp="2026-01-01T00:00:00+00:00",
            users=participants,
            raw={"scene": 1, "users": participants},
            sequence=1,
        )
    )
    store.save_squid_event(
        SquidEvent(
            round_id="bad-round",
            scene=3,
            timestamp="2026-01-01T00:01:00+00:00",
            users=participants,
            ext={"winSquidNum": 1},
            raw={"scene": 3, "users": ["a"], "ext": {"winSquidNum": 1}},
            sequence=2,
        )
    )
    store.save_squid_event(
        SquidEvent(
            round_id="bad-round",
            scene=4,
            timestamp="2026-01-01T00:02:00+00:00",
            users=participants,
            settlements=[
                {"userId": "a", "addScore": 30},
                {"userId": "b", "addScore": 10},
                {"userId": "c", "addScore": 10},
                {"userId": "d", "addScore": 10},
            ],
            raw={"scene": 4, "users": participants},
            sequence=3,
        )
    )
    store.close()
    connection = sqlite3.connect(tmp_path / "hands.sqlite3")
    calibration = calibrate_squid_rules(connection)
    connection.close()

    assert calibration["rule_status"] == "mismatch"
    assert calibration["squid_value"] is None


def test_award_probabilities_use_decayed_player_pot_wins(tmp_path):
    store = RecorderStore(tmp_path)
    for index in range(20):
        hand_id = f"h-{index:02d}"
        store.save_hand(
            HandHistory(
                hand_id=hand_id,
                ended_at=f"2026-01-{index + 1:02d}T00:00:00+00:00",
                status="completed",
                game_mode="squid",
            )
        )
        store.connection.executemany(
            """
            INSERT INTO hand_squid_players(
                hand_id, user_id, squid_start, squid_end
            ) VALUES (?, ?, 0, 0)
            """,
            [(hand_id, "a"), (hand_id, "b")],
        )
        winner = "a" if index < 15 else "b"
        store.connection.execute(
            """
            INSERT INTO squid_awards(
                hand_id, user_id, award_count, event_sequence
            ) VALUES (?, ?, 1, ?)
            """,
            (hand_id, winner, index + 1),
        )
    store.connection.commit()
    model = estimate_award_probabilities(
        store.connection, ["a", "b"], {"a": 0, "b": 0}
    )
    store.close()

    assert model["evidence_hands"] == 20
    assert model["probabilities"]["a"] > model["probabilities"]["b"]
    assert abs(sum(model["probabilities"].values()) - 1.0) < 1e-6


def test_first_squid_removes_terminal_loser_risk():
    state = SquidRoundState(
        round_id="round-2",
        participants=("a", "b", "hero", "villain"),
        counts=(("a", 1), ("b", 1), ("hero", 0), ("villain", 0)),
        participant_count=4,
        total_squids=8,
        awarded_squids=2,
        remaining_squids=6,
        zero_squid_players=2,
        terminal=False,
        squid_value=10,
        payout_basis="awarded",
        calibration_confidence="high",
        calibration_rounds=12,
    )

    values = hero_values_after_next_award(
        state,
        "hero",
        {player: 0.25 for player in state.participants},
        simulations_per_winner=5,
    )

    assert values["hero"] == 10
    assert values["villain"] == -30


def test_target_decision_contract_separates_validity_from_support():
    decision = {
        "decision_id": "h1:7",
        "sequence": 7,
        "hand_id": "h1",
        "street": "flop",
        "hero_cards": ["As", "Ks"],
        "hero_position": "HJ",
        "legal_actions": ["fold", "call", "raise"],
        "board": ["Qs", "7d", "2c"],
        "pot": 80,
        "side_pots": [{"amount": 80, "eligible_seats": [1, 2, 3, 4]}],
        "call_score": 20,
        "min_raise_to": 60,
        "max_raise_to": 800,
        "hero_street_contribution": 0,
        "hero_stack": 800,
        "hero_stack_bb": 200,
        "small_blind": 2,
        "big_blind": 4,
        "ante": 1,
        "players_in_hand": 4,
        "table_players": 9,
        "game_mode": "squid",
        "remaining_ms": 20_000,
    }
    contract = decision_state_contract(decision)
    later = decision_state_contract({**decision, "remaining_ms": 5_000})

    assert contract["quality"]["valid"] is True
    assert contract["quality"]["target_game_supported"] is True
    assert len(contract["state_hash"]) == 20
    assert later["state_hash"] == contract["state_hash"]
