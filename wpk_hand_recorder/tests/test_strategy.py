import sqlite3

from wpk_recorder.opponent_model import opponent_metrics
from wpk_recorder.poker import equity_vs_weighted_ranges
from wpk_recorder.strategy import evaluate_money_strategy


def test_money_strategy_separates_chip_and_squid_edges():
    participants = ["acting_player", *[f"seat_{seat}" for seat in range(2, 10)]]
    counts = {
        "acting_player": 0,
        "seat_2": 0,
        **{f"seat_{seat}": 1 for seat in range(3, 10)},
    }
    context = {
        "decision": {
            "decision_id": "table-1:10",
            "sequence": 10,
            "hand_id": "table-1",
            "street": "flop",
            "hero_cards": ["As", "Ah"],
            "hero_position": "HJ",
            "legal_actions": ["fold", "call", "raise"],
            "board": ["Ad", "7s", "2c"],
            "pot": 100,
            "call_score": 20,
            "min_raise_to": 60,
            "max_raise_to": 800,
            "hero_street_contribution": 0,
            "hero_stack": 800,
            "hero_stack_bb": 200,
            "small_blind": 2,
            "big_blind": 4,
            "ante": 1,
            "players_in_hand": 9,
            "table_players": 9,
            "game_mode": "squid",
            "state_quality": {
                "valid": True,
                "target_game_supported": True,
            },
        },
        "active_opponent_profiles": [
            {"player": player, "observations": []}
            for player in participants
            if player != "hero"
        ],
        "squid_round": {
            "round_id": "active",
            "participants": participants,
            "counts": counts,
            "participant_count": 9,
            "total_squids": 13,
            "awarded_squids": 7,
            "remaining_squids": 6,
            "zero_squid_players": 2,
            "terminal": False,
            "squid_value": 10,
            "payout_basis": "awarded",
            "calibration_confidence": "high",
            "calibration_rounds": 12,
                "rule_status": "validated",
        },
    }
    baseline = {
        "kind": "postflop_buckets",
        "hero_bucket": "strong_value",
        "buckets": [
            {
                "key": "strong_value",
                "frequencies": {"raise": 72, "call": 28},
            }
        ],
    }

    result = evaluate_money_strategy(context, baseline)

    assert result["status"] == "experimental"
    assert result["squid"]["calibrated"] is True
    assert sum(item["frequency_pct"] for item in result["policy"]) == 100
    fold = next(item for item in result["candidates"] if item["id"] == "fold")
    assert fold["award_probability"] == 0
    assert fold["squid_ev"] == 0
    assert any(
        item["squid_ev"] > 0
        for item in result["candidates"]
        if item["action"] in {"call", "raise"}
    )


def test_opponent_metrics_shrink_small_context_to_population():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE hands(
            hand_id TEXT PRIMARY KEY,
            excluded_from_stats INTEGER,
            game_mode TEXT
        );
        CREATE TABLE opportunities(
            hand_id TEXT,
            user_id TEXT,
            metric TEXT,
            success INTEGER,
            position TEXT,
            game_mode TEXT,
            effective_stack_bb REAL
        );
        """
    )
    for index in range(40):
        hand_id = f"h{index}"
        user_id = "villain" if index < 4 else f"pool-{index}"
        success = 1 if index % 2 == 0 else 0
        connection.execute(
            "INSERT INTO hands VALUES (?, 0, 'squid')",
            (hand_id,),
        )
        connection.execute(
            "INSERT INTO opportunities VALUES (?, ?, 'fold_to_flop_bet', ?, 'BTN', 'squid', 220)",
            (hand_id, user_id, success),
        )
    connection.commit()

    metrics = opponent_metrics(
        connection,
        "villain",
        "squid",
        position="BTN",
        effective_stack_bb=220,
    )
    connection.close()

    fold = next(item for item in metrics if item["metric"] == "fold_to_flop_bet")
    assert fold["observed_pct"] == 50
    assert fold["mean_pct"] == 50
    assert fold["confidence"] == "very_low"


def test_weighted_range_equity_respects_strong_opponent_range():
    equity = equity_vs_weighted_ranges(
        ["7c", "2d"],
        [],
        [{"AA": 100}],
        trials=250,
    )

    assert equity < 0.2
