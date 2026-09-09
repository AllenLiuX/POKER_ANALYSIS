import sqlite3

from wpk_recorder.opponent_model import (
    action_response_backtest,
    contextual_action_response,
    contextual_action_responses,
    exploit_directives,
    joint_fold_backtest,
    joint_fold_calibration,
    opponent_metrics,
    response_probability,
)
from wpk_recorder.poker import (
    equity_vs_weighted_ranges,
    showdown_stats_vs_random_multiway,
)
from wpk_recorder.offline_gto import (
    classify_postflop_hand,
    postflop_strategy,
    preflop_open_fraction,
)
from wpk_recorder.reasoning import simplified_range_strategy
from wpk_recorder.strategy import (
    _candidate_response_totals,
    _candidate_size_ratio,
    _engine_confidence,
    _weighted_random_selection,
    evaluate_money_strategy,
)


def test_short_stack_all_in_uses_only_remaining_stack_as_cost():
    ratio = _candidate_size_ratio(
        {"action": "all_in", "raise_to": None},
        {
            "pot": 2000,
            "call_score": 544,
            "hero_street_contribution": 272,
            "hero_stack": 66,
            "max_raise_to": 338,
        },
    )

    assert ratio == 66 / 2000


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

    result = evaluate_money_strategy(context, baseline, trials_cap=48)

    assert result["status"] == "experimental"
    assert result["equity"]["trials"] == 48
    assert result["squid"]["calibrated"] is True
    assert round(
        sum(item["frequency_pct"] for item in result["policy"]),
        8,
    ) == 100
    fold = next(item for item in result["candidates"] if item["id"] == "fold")
    assert fold["award_probability"] == 0
    assert fold["squid_ev"] == 0
    aggressive = next(
        item for item in result["candidates"] if item["action"] == "raise"
    )
    assert aggressive["independent_fold_probability"] >= 0
    assert aggressive["joint_fold_adjustment_pp"] == 0
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


def test_contextual_action_response_learns_any_player_type_and_size():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE hands(
            hand_id TEXT PRIMARY KEY,
            excluded_from_stats INTEGER,
            played_at TEXT
        );
        CREATE TABLE decision_snapshots(
            hand_id TEXT,
            action_sequence INTEGER,
            user_id TEXT,
            chosen_action TEXT,
            position TEXT,
            spr REAL,
            to_call REAL,
            pot_before REAL,
            is_ip INTEGER,
            players_in_hand INTEGER,
            game_mode TEXT,
            street TEXT,
            facing_action TEXT,
            board_texture_json TEXT
        );
        """
    )
    sequence = 0
    for player, action in (("sticky", "call"), ("nit", "fold")):
        for index in range(60):
            sequence += 1
            hand_id = f"{player}-{index}"
            fraction = 0.25 if index % 2 == 0 else 1.4
            timeline = index * 3 + (0 if player == "sticky" else 1)
            connection.execute(
                "INSERT INTO hands VALUES (?, 0, ?)",
                (
                    hand_id,
                    f"2026-01-{1 + timeline // 24:02d}"
                    f"T{timeline % 24:02d}:00:00+00:00",
                ),
            )
            connection.execute(
                """
                INSERT INTO decision_snapshots VALUES (
                    ?, 1, ?, ?, 'BB', 8, ?, 100, 0, 2, 'squid', 'river',
                    'bet', '{"paired": false, "connected": false}'
                )
                """,
                (hand_id, player, action, 100 * fraction),
            )
    for index in range(60):
        sequence += 1
        hand_id = f"switcher-{index}"
        timeline = index * 3 + 2
        connection.execute(
            "INSERT INTO hands VALUES (?, 0, ?)",
            (
                hand_id,
                f"2026-01-{1 + timeline // 24:02d}"
                f"T{timeline % 24:02d}:00:00+00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO decision_snapshots VALUES (
                ?, 1, 'switcher', ?, 'BB', 8, 50, 100, 0, 2,
                'squid', 'river', 'bet',
                '{"paired": false, "connected": false}'
            )
            """,
            (hand_id, "call" if index < 40 else "fold"),
        )
    connection.commit()

    sticky = contextual_action_response(
        connection,
        "sticky",
        "squid",
        street="river",
        position="BB",
        players_in_hand=2,
        spr=8,
        is_ip=False,
        faced_bet=True,
    )
    nit = contextual_action_response(
        connection,
        "nit",
        "squid",
        street="river",
        position="BB",
        players_in_hand=2,
        spr=8,
        is_ip=False,
        faced_bet=True,
    )
    switcher = contextual_action_response(
        connection,
        "switcher",
        "squid",
        street="river",
        position="BB",
        players_in_hand=2,
        spr=8,
        is_ip=False,
        faced_bet=True,
    )
    quality = action_response_backtest(connection, "squid")
    gated = contextual_action_responses(
        connection,
        [
            {"user_id": "sticky", "position": "BB", "is_ip": False},
            {"user_id": "nit", "position": "BB", "is_ip": False},
        ],
        "squid",
        street="river",
        players_in_hand=2,
        spr=8,
        faced_bet=True,
        facing_action="bet",
        quality=quality,
    )
    population_only = contextual_action_responses(
        connection,
        [
            {"user_id": "sticky", "position": "BB", "is_ip": False},
            {"user_id": "nit", "position": "BB", "is_ip": False},
        ],
        "squid",
        street="river",
        players_in_hand=2,
        spr=8,
        faced_bet=True,
        facing_action="bet",
        quality={
            "groups": [
                {
                    "street": "river",
                    "faced_bet": True,
                    "enabled": False,
                }
            ]
        },
    )
    connection.close()

    assert (
        sticky["method"]
        == "hierarchical-player-gated-action-and-size-v5"
    )
    assert sticky["confidence"] == "high"
    assert sticky["strategy_drift"]["available"] is True
    assert sticky["strategy_drift"]["status"] == "stable"
    assert sticky["strategy_drift"]["used_for_prediction"] is False
    assert switcher["strategy_drift"]["status"] == "shifted"
    assert (
        switcher["strategy_drift"]["recent_probabilities_pct"]["fold"]
        > switcher["strategy_drift"]["long_term_probabilities_pct"]["fold"]
    )
    assert response_probability(sticky, "call") > response_probability(
        sticky, "fold"
    )
    assert response_probability(nit, "fold") > response_probability(
        sticky, "fold"
    )
    assert response_probability(sticky, "fold", 1.4) < response_probability(
        nit, "fold", 1.4
    )
    assert any(
        item["adjustment"] == "expand_value_reduce_bluffs"
        for item in exploit_directives(sticky)
    )
    assert any(
        item["adjustment"] == "expand_blocker_bluffs"
        for item in exploit_directives(nit)
    )
    assert quality["model_log_loss"] < quality["population_log_loss"]
    assert quality["enabled_coverage_pct"] > 0
    assert gated["sticky"]["player_residual_enabled"] is True
    assert response_probability(
        gated["sticky"], "fold"
    ) < response_probability(gated["nit"], "fold")
    assert population_only["sticky"]["player_residual_enabled"] is False
    assert response_probability(
        population_only["sticky"], "fold"
    ) == response_probability(population_only["nit"], "fold")


def test_raise_size_model_is_prequentially_gated_and_player_specific():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE hands(
            hand_id TEXT PRIMARY KEY,
            excluded_from_stats INTEGER,
            played_at TEXT
        );
        CREATE TABLE decision_snapshots(
            hand_id TEXT,
            action_sequence INTEGER,
            user_id TEXT,
            chosen_action TEXT,
            position TEXT,
            spr REAL,
            to_call REAL,
            pot_before REAL,
            is_ip INTEGER,
            players_in_hand INTEGER,
            game_mode TEXT,
            street TEXT,
            facing_action TEXT,
            board_texture_json TEXT,
            raise_multiple REAL
        );
        """
    )
    for index in range(50):
        for offset, (player, multiple) in enumerate(
            (("large_raiser", 4.5), ("small_raiser", 2.0))
        ):
            hand_id = f"{player}-{index}"
            hour = index * 2 + offset
            connection.execute(
                "INSERT INTO hands VALUES (?, 0, ?)",
                (
                    hand_id,
                    f"2026-02-{1 + hour // 24:02d}"
                    f"T{hour % 24:02d}:00:00+00:00",
                ),
            )
            connection.execute(
                """
                INSERT INTO decision_snapshots VALUES (
                    ?, 1, ?, 'raise', 'BTN', 8, 25, 100, 1, 2,
                    'squid', 'flop', 'bet', '{}', ?
                )
                """,
                (hand_id, player, multiple),
            )
    connection.commit()

    quality = action_response_backtest(connection, "squid")
    response = contextual_action_response(
        connection,
        "large_raiser",
        "squid",
        street="flop",
        position="BTN",
        players_in_hand=2,
        spr=8,
        is_ip=True,
        faced_bet=True,
        facing_action="bet",
        quality=quality,
    )
    connection.close()

    group = next(
        item
        for item in quality["groups"]
        if item["street"] == "flop" and item["faced_bet"]
    )
    assert group["raise_size_test_observations"] == 30
    assert group["raise_size_player_enabled"] is True
    assert response["raise_size_model"]["enabled"] is True
    assert response["raise_size_model"]["player_residual_enabled"] is True
    assert (
        response["raise_size_model"]["personal_expected_multiple"]
        > response["raise_size_model"]["population_expected_multiple"]
    )


def test_player_residual_gate_rejects_persistent_out_of_time_failure():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE hands(
            hand_id TEXT PRIMARY KEY,
            excluded_from_stats INTEGER,
            played_at TEXT
        );
        CREATE TABLE decision_snapshots(
            hand_id TEXT,
            action_sequence INTEGER,
            user_id TEXT,
            chosen_action TEXT,
            position TEXT,
            spr REAL,
            to_call REAL,
            pot_before REAL,
            is_ip INTEGER,
            players_in_hand INTEGER,
            game_mode TEXT,
            street TEXT,
            facing_action TEXT,
            board_texture_json TEXT
        );
        """
    )
    players = (
        ("stable_call", lambda _: "call"),
        ("stable_fold", lambda _: "fold"),
        ("stable_raise", lambda _: "raise"),
        ("switcher", lambda index: "fold" if index < 70 else "call"),
    )
    for index in range(100):
        for offset, (player, action_fn) in enumerate(players):
            hand_id = f"player-gate-{index:03d}-{offset}"
            hour = index * len(players) + offset
            connection.execute(
                "INSERT INTO hands VALUES (?, 0, ?)",
                (
                    hand_id,
                    f"2026-04-{1 + hour // 24:02d}"
                    f"T{hour % 24:02d}:00:00+00:00",
                ),
            )
            connection.execute(
                """
                INSERT INTO decision_snapshots VALUES (
                    ?, 1, ?, ?, 'BTN', 8, 25, 100, 1, 2,
                    'squid', 'river', 'bet', '{}'
                )
                """,
                (hand_id, player, action_fn(index)),
            )
    connection.commit()

    quality = action_response_backtest(connection, "squid")
    responses = contextual_action_responses(
        connection,
        [
            {"user_id": "stable_call", "position": "BTN", "is_ip": True},
            {"user_id": "switcher", "position": "BTN", "is_ip": True},
        ],
        "squid",
        street="river",
        players_in_hand=2,
        spr=8,
        faced_bet=True,
        facing_action="bet",
        quality=quality,
    )
    connection.close()

    group = next(
        item
        for item in quality["groups"]
        if item["street"] == "river" and item["faced_bet"]
    )
    switcher_gate = next(
        item for item in group["players"] if item["user_id"] == "switcher"
    )
    assert group["enabled"] is True
    assert switcher_gate["status"] == "rejected"
    assert switcher_gate["evaluation_log_loss_improvement"] < 0
    assert quality["player_gate_log_loss_improvement"] > 0
    assert responses["switcher"]["player_residual_enabled"] is False
    assert (
        responses["switcher"]["player_residual_gate"]["fallback"]
        == "population_context"
    )
    assert responses["stable_call"]["player_residual_enabled"] is True


def test_prequential_drift_gate_adapts_after_a_real_regime_change():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE hands(
            hand_id TEXT PRIMARY KEY,
            excluded_from_stats INTEGER,
            played_at TEXT
        );
        CREATE TABLE decision_snapshots(
            hand_id TEXT,
            action_sequence INTEGER,
            user_id TEXT,
            chosen_action TEXT,
            position TEXT,
            spr REAL,
            to_call REAL,
            pot_before REAL,
            is_ip INTEGER,
            players_in_hand INTEGER,
            game_mode TEXT,
            street TEXT,
            facing_action TEXT,
            board_texture_json TEXT
        );
        """
    )
    for index in range(60):
        for player_index in range(4):
            hand_id = f"shift-{index:03d}-{player_index}"
            timeline = index * 4 + player_index
            old_action = "call" if player_index < 2 else "fold"
            new_action = "fold" if player_index < 2 else "call"
            action = old_action if index < 30 else new_action
            connection.execute(
                "INSERT INTO hands VALUES (?, 0, ?)",
                (
                    hand_id,
                    f"2026-01-{1 + timeline // 24:02d}"
                    f"T{timeline % 24:02d}:00:00+00:00",
                ),
            )
            connection.execute(
                """
                INSERT INTO decision_snapshots VALUES (
                    ?, 1, ?, ?, 'BB', 8, 50, 100, 0, 2,
                    'squid', 'river', 'bet', '{}'
                )
                """,
                (hand_id, f"player-{player_index}", action),
            )
    connection.commit()

    quality = action_response_backtest(connection, "squid")
    adaptive = contextual_action_response(
        connection,
        "player-0",
        "squid",
        street="river",
        position="BB",
        players_in_hand=2,
        spr=8,
        is_ip=False,
        faced_bet=True,
        quality=quality,
    )
    control = contextual_action_response(
        connection,
        "player-0",
        "squid",
        street="river",
        position="BB",
        players_in_hand=2,
        spr=8,
        is_ip=False,
        faced_bet=True,
        quality={
            "groups": [
                {
                    "street": "river",
                    "faced_bet": True,
                    "enabled": False,
                    "drift_enabled": False,
                }
            ]
        },
    )
    connection.close()

    assert quality["drift_enabled_groups"] == 1
    assert adaptive["player_residual_enabled"] is False
    assert adaptive["strategy_drift"]["used_for_prediction"] is True
    assert adaptive["strategy_drift"]["blend_weight"] <= 0.30
    assert response_probability(adaptive, "fold") > response_probability(
        control, "fold"
    )
    assert response_probability(
        adaptive, "fold", 1.4
    ) > response_probability(control, "fold", 1.4)


def test_candidate_response_uses_observed_size_curve():
    fold_model = {
        "per_opponent_pct": [30.0],
        "opponents": [
            {
                "fold_probability": 0.30,
                "size_curve": {
                    "small": {"probabilities_pct": {"fold": 20.0}},
                    "overbet": {"probabilities_pct": {"fold": 24.0}},
                },
            }
        ],
    }

    small_fold, _ = _candidate_response_totals(fold_model, 0.5, 1)
    overbet_fold, _ = _candidate_response_totals(fold_model, 1.4, 1)

    assert small_fold == 0.20
    assert overbet_fold == 0.24


def test_dynamic_sizing_compares_all_buckets_and_exposes_player_response():
    size_curve = {
        bucket: {
            "probabilities_pct": {
                "fold": fold,
                "call": 90.0 - fold,
                "raise": 10.0,
            },
            "effective_samples": 14.0,
            "confidence": "medium",
        }
        for bucket, fold in {
            "tiny": 18.0,
            "small": 24.0,
            "medium": 31.0,
            "large": 39.0,
            "overbet": 52.0,
        }.items()
    }
    context = {
        "decision": {
            "street": "flop",
            "hero_cards": ["Ks", "Qh"],
            "legal_actions": ["check", "raise"],
            "board": ["Kd", "7s", "2c"],
            "pot": 100,
            "call_score": 0,
            "min_raise_to": 20,
            "max_raise_to": 200,
            "hero_street_contribution": 0,
            "hero_stack": 200,
            "big_blind": 2,
            "players_in_hand": 2,
            "table_players": 2,
            "game_mode": "holdem",
            "state_quality": {
                "valid": True,
                "target_game_supported": True,
            },
        },
        "active_opponent_profiles": [
            {
                "player": "seat_2",
                "display_name": "Loose Caller",
                "observations": [],
                "response_model": {
                    "probabilities_pct": {
                        "fold": 30.0,
                        "call": 60.0,
                        "raise": 10.0,
                    },
                    "player_residual_enabled": True,
                    "size_curve": size_curve,
                },
            }
        ],
    }
    baseline = {
        "kind": "postflop_buckets",
        "hero_bucket": "top_pair_strong",
        "buckets": [
            {
                "key": "top_pair_strong",
                "frequencies": {"raise": 100},
            }
        ],
    }

    result = evaluate_money_strategy(context, baseline, random_draw_pct=0)
    sizing = result["sizing_recommendation"]

    assert result["recommended"]["action"] == "raise"
    assert sizing["enabled"] is True
    assert sizing["source"] == "active_opponent_history"
    assert sizing["recommended_raise_to"] == result["recommended"]["raise_to"]
    assert {
        row["pot_fraction_pct"] for row in sizing["candidates"]
    } == {20.0, 30.0, 50.0, 75.0, 100.0, 125.0}
    response = sizing["opponent_responses"][0]
    assert response["display_name"] == "Loose Caller"
    assert response["effective_samples"] == 14.0
    assert response["source"] == "player_history"


def test_holdem_confidence_does_not_require_squid_calibration():
    confidence = _engine_confidence(
        {
            "game_mode": "holdem",
            "state_quality": {
                "valid": True,
                "target_game_supported": True,
            },
        },
        {"evidence_trials": 120},
        {"calibrated": False},
    )

    assert confidence == "high"


def test_joint_fold_model_calibrates_correlated_multiway_responses():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE hands(
            hand_id TEXT PRIMARY KEY,
            excluded_from_stats INTEGER,
            played_at TEXT
        );
        CREATE TABLE decision_snapshots(
            hand_id TEXT,
            action_sequence INTEGER,
            user_id TEXT,
            seat INTEGER,
            street TEXT,
            game_mode TEXT,
            chosen_action TEXT,
            players_in_hand INTEGER,
            bet_fraction REAL,
            facing_action TEXT
        );
        """
    )
    for index in range(80):
        hand_id = f"joint-{index:03d}"
        connection.execute(
            "INSERT INTO hands VALUES (?, 0, ?)",
            (
                hand_id,
                f"2026-01-{1 + index // 24:02d}"
                f"T{index % 24:02d}:00:00Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO decision_snapshots VALUES (
                ?, 1, 'hero', 1, 'flop', 'squid', 'bet', 3, 0.6, 'check'
            )
            """,
            (hand_id,),
        )
        response = "fold" if index % 2 == 0 else "call"
        connection.execute(
            """
            INSERT INTO decision_snapshots VALUES (
                ?, 2, 'v1', 2, 'flop', 'squid', ?, 3, 0.6, 'bet'
            )
            """,
            (hand_id, response),
        )
        connection.execute(
            """
            INSERT INTO decision_snapshots VALUES (
                ?, 3, 'v2', 3, 'flop', 'squid', ?, 3, 0.6, 'bet'
            )
            """,
            (hand_id, response),
        )
    connection.commit()

    quality = joint_fold_backtest(connection, "squid")
    model = joint_fold_calibration(
        connection,
        "squid",
        street="flop",
        opponents=2,
        action_type="bet",
        quality=quality,
    )
    calibrated, expected_callers = _candidate_response_totals(
        {
            "opponents": [
                {"fold_probability": 0.5},
                {"fold_probability": 0.5},
            ],
            "joint_response_model": model,
        },
        0.6,
        2,
    )

    assert quality["enabled"] is True
    assert quality["model_log_loss"] < quality["independent_log_loss"]
    assert quality["enabled_coverage_pct"] == 100.0
    assert model["enabled"] is True
    assert model["logit_correction"] > 0
    assert calibrated > 0.25
    assert 0.9 < expected_callers < 1.0


def test_weighted_range_equity_respects_strong_opponent_range():
    equity = equity_vs_weighted_ranges(
        ["7c", "2d"],
        [],
        [{"AA": 100}],
        trials=250,
    )

    assert equity < 0.2


def test_candidate_ev_uses_action_conditioned_caller_range():
    context = {
        "decision": {
            "street": "flop",
            "hero_cards": ["7c", "2d"],
            "legal_actions": ["check", "raise"],
            "board": ["Ks", "8h", "3c"],
            "pot": 100,
            "call_score": 0,
            "min_raise_to": 50,
            "max_raise_to": 300,
            "hero_street_contribution": 0,
            "hero_stack": 300,
            "big_blind": 4,
            "players_in_hand": 2,
            "table_players": 2,
            "game_mode": "squid",
            "state_quality": {
                "valid": True,
                "target_game_supported": True,
            },
        },
        "active_opponent_profiles": [
            {
                "player": "seat_2",
                "observations": [],
                "preflop_range": {
                    "enabled": True,
                    "weights": {"72o": 100.0},
                },
                "response_model": {
                    "probabilities_pct": {
                        "fold": 30.0,
                        "call": 50.0,
                        "raise": 20.0,
                    },
                },
                "continuation_ranges": {
                    "enabled": True,
                    "buckets": {
                        "medium": {
                            "weights": {"AA": 100.0},
                            "baseline_strength_distribution_pct": {
                                "high_card": 100.0
                            },
                            "strength_distribution_pct": {
                                "pair": 100.0
                            },
                            "top_weight_shifts": [
                                {
                                    "hand": "AA",
                                    "multiplier": 2.0,
                                }
                            ],
                        }
                    },
                },
            }
        ],
    }

    result = evaluate_money_strategy(context, {})
    half_pot = next(
        candidate
        for candidate in result["candidates"]
        if candidate["id"] == "raise:50"
    )

    assert half_pot["conditional_range_enabled"] is True
    assert half_pot["called_equity"] < 0.2
    assert half_pot["raise_risk_penalty"] > 0
    assert (
        half_pot["continuation_range_model"]["any_raise_probability"]
        == 0.2
    )
    assert (
        half_pot["continuation_range_model"]["method"]
        == "action-conditioned-caller-ranges-v1"
    )
    assert result["equity"]["candidate_continuation_ranges_enabled"] is True


def test_candidate_ev_uses_explicit_reraise_branch_when_all_gates_pass():
    context = {
        "decision": {
            "street": "flop",
            "hero_cards": ["Kc", "Qd"],
            "legal_actions": ["check", "raise"],
            "board": ["Ks", "8h", "3c"],
            "pot": 100,
            "call_score": 0,
            "min_raise_to": 50,
            "max_raise_to": 300,
            "hero_street_contribution": 0,
            "hero_stack": 300,
            "big_blind": 4,
            "players_in_hand": 2,
            "table_players": 2,
            "game_mode": "squid",
            "state_quality": {
                "valid": True,
                "target_game_supported": True,
            },
        },
        "active_opponent_profiles": [
            {
                "player": "seat_2",
                "observations": [],
                "preflop_range": {
                    "enabled": True,
                    "weights": {"AA": 50.0, "KQo": 50.0},
                },
                "response_model": {
                    "probabilities_pct": {
                        "fold": 30.0,
                        "call": 50.0,
                        "raise": 20.0,
                    },
                    "raise_size_model": {
                        "enabled": True,
                        "expected_multiple": 3.0,
                    },
                },
                "continuation_ranges": {
                    "enabled": True,
                    "buckets": {
                        "medium": {
                            "weights": {"AA": 75.0, "KQo": 25.0},
                            "call_weights": {"KQo": 100.0},
                            "raise_weights": {"AA": 100.0},
                            "call_enabled": True,
                            "raise_enabled": True,
                        }
                    },
                },
            }
        ],
    }

    result = evaluate_money_strategy(context, {})
    half_pot = next(
        candidate
        for candidate in result["candidates"]
        if candidate["id"] == "raise:50"
    )
    branch = half_pot["explicit_raise_branch"]

    assert branch["enabled"] is True
    assert branch["expected_raise_to"] == 150
    assert branch["hero_response_to_raise"] == "fold"
    assert (
        round(
            branch["fold_probability"]
            + branch["call_probability"]
            + branch["raise_probability"],
            5,
        )
        == 1.0
    )
    assert half_pot["raise_risk_penalty"] == 0


def test_pot_share_is_not_squid_award_probability_on_tie_board():
    stats = showdown_stats_vs_random_multiway(
        ["2c", "3d"],
        ["As", "Ks", "Qs", "Js", "Ts"],
        opponents=1,
        trials=20,
    )

    assert stats["pot_share"] == 0.5
    assert stats["award_probability"] == 1.0


def test_raise_equity_uses_conditional_caller_count():
    context = {
        "decision": {
            "hero_cards": ["7c", "2d"],
            "legal_actions": ["fold", "raise"],
            "board": ["As", "Kh", "Qc"],
            "pot": 100,
            "call_score": 20,
            "min_raise_to": 60,
            "max_raise_to": 400,
            "hero_street_contribution": 0,
            "hero_stack": 400,
            "big_blind": 4,
            "players_in_hand": 9,
            "table_players": 9,
            "game_mode": "squid",
            "state_quality": {
                "valid": True,
                "target_game_supported": True,
            },
        },
        "active_opponent_profiles": [
            {"player": f"seat_{seat}", "observations": []}
            for seat in range(2, 10)
        ],
    }

    result = evaluate_money_strategy(context, {})

    assert result["equity"]["raise_caller_count"] < 8
    assert (
        result["equity"]["raise_pot_share_pct"]
        >= result["equity"]["pot_share_pct"]
    )


def test_solver_fit_recommends_half_pot_for_screenshot_weak_top_pair():
    context = {
        "decision": {
            "street": "flop",
            "hero_cards": ["Jd", "7h"],
            "hero_position": "BTN/SB",
            "acting_seat": 1,
            "action_order": [5, 1],
            "legal_actions": ["check", "raise", "all_in"],
            "board": ["Jc", "2d", "Tc"],
            "pot": 4,
            "call_score": 0,
            "min_raise_to": 2,
            "max_raise_to": 167,
            "hero_street_contribution": 0,
            "hero_stack": 229,
            "hero_stack_bb": 114.5,
            "small_blind": 1,
            "big_blind": 2,
            "ante": 0,
            "players_in_hand": 2,
            "table_players": 2,
            "game_mode": "holdem",
            "state_quality": {
                "valid": True,
                "target_game_supported": False,
            },
            "players": [
                {
                    "seat": 1,
                    "active": True,
                    "is_hero": True,
                    "effective_stack_to_hero": 229,
                },
                {
                    "seat": 5,
                    "active": True,
                    "is_hero": False,
                    "effective_stack_to_hero": 167,
                },
            ],
        },
        "action_history": [
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
        ],
        "active_opponent_profiles": [],
    }
    baseline = simplified_range_strategy(context)
    result = evaluate_money_strategy(context, baseline, random_draw_pct=0)

    assert result["hero_bucket"] == "top_pair_weak"
    assert result["recommended"]["action"] == "raise"
    assert result["recommended"]["raise_to"] == 2
    assert result["most_frequent"]["id"] == result["recommended"]["id"]
    assert result["random_selection"]["draw_pct"] == 0
    all_in = next(
        item for item in result["policy"] if item["action"] == "all_in"
    )
    assert all_in["frequency_pct"] == 0
    assert result["preferred_bet_fraction"] == 0.5


def test_big_blind_free_option_never_recommends_fold():
    context = {
        "decision": {
            "street": "preflop",
            "hero_cards": ["7c", "2d"],
            "hero_position": "BB",
            "acting_seat": 1,
            "action_order": [5, 1],
            "legal_actions": ["fold", "raise", "all_in", "check"],
            "board": [],
            "pot": 4,
            "call_score": 0,
            "min_raise_to": 4,
            "max_raise_to": 200,
            "hero_street_contribution": 2,
            "hero_stack": 198,
            "hero_stack_bb": 99,
            "small_blind": 1,
            "big_blind": 2,
            "ante": 0,
            "players_in_hand": 2,
            "table_players": 2,
            "game_mode": "holdem",
            "state_quality": {
                "valid": True,
                "target_game_supported": False,
            },
        },
        "action_history": [
            {
                "street": "preflop",
                "seat": 5,
                "position": "BTN/SB",
                "action": "call",
            }
        ],
        "active_opponent_profiles": [],
    }

    baseline = simplified_range_strategy(context)
    result = evaluate_money_strategy(context, baseline, random_draw_pct=0)

    assert baseline["legal_actions"] == ["raise", "all_in", "check"]
    assert all("fold" not in cell["frequencies"] for cell in baseline["cells"])
    assert all(item["action"] != "fold" for item in result["candidates"])
    assert all(item["action"] != "fold" for item in result["policy"])
    assert result["recommended"]["action"] != "fold"


def test_preflop_rfi_fit_changes_with_table_size_and_ante():
    common = {
        "hero_position": "UTG",
        "big_blind": 2,
        "hero_stack_bb": 100,
    }
    nine_max = preflop_open_fraction({**common, "table_players": 9})
    six_max = preflop_open_fraction({**common, "table_players": 6})
    ante_nine_max = preflop_open_fraction(
        {**common, "table_players": 9, "ante": 0.25}
    )
    heads_up = preflop_open_fraction(
        {
            **common,
            "hero_position": "BTN/SB",
            "table_players": 2,
        }
    )

    assert nine_max < six_max < heads_up
    assert ante_nine_max > nine_max


def test_ante_open_sizing_uses_three_bb_and_larger_from_small_blind():
    for position, contribution, call_score, expected in (
        ("UTG", 0, 4, 12),
        ("SB", 2, 2, 14),
    ):
        decision = {
            "street": "preflop",
            "hero_cards": ["As", "Ah"],
            "hero_position": position,
            "legal_actions": ["fold", "call", "raise", "all_in"],
            "call_score": call_score,
            "pot": 13,
            "small_blind": 2,
            "big_blind": 4,
            "ante": 1,
            "hero_street_contribution": contribution,
            "hero_stack": 800,
            "hero_stack_bb": 200,
            "players_in_hand": 9,
            "table_players": 9,
            "game_mode": "holdem",
            "min_raise_to": 8,
            "max_raise_to": 802,
            "state_quality": {"target_game_supported": True},
        }
        context = {
            "decision": decision,
            "action_history": (
                [{"street": "preflop", "position": "BTN", "action": "fold"}]
                if position == "SB"
                else []
            ),
            "active_opponent_profiles": [],
        }
        baseline = simplified_range_strategy(context)
        result = evaluate_money_strategy(
            context,
            baseline,
            trials_cap=48,
            random_draw_pct=0,
        )

        assert result["preferred_preflop_raise_to"] == expected
        assert result["recommended"]["raise_to"] == expected
        raise_sizes = [
            item["raise_to"]
            for item in result["candidates"]
            if item["action"] == "raise"
        ]
        assert min(raise_sizes) >= (12 if position == "SB" else 10)


def test_postflop_fit_keeps_legal_normalized_mixes_across_formats():
    cases = [
        ("flop", ["As", "7h", "2d"], ["check", "raise"], 0),
        ("turn", ["9s", "8s", "2d", "Kc"], ["fold", "call", "raise"], 8),
        ("river", ["Js", "Jh", "2d", "6d", "3c"], ["check", "raise"], 0),
    ]
    for players in (2, 3, 6):
        for street, board, legal, call_score in cases:
            fitted = postflop_strategy(
                {
                    "decision": {
                        "street": street,
                        "hero_cards": [],
                        "board": board,
                        "legal_actions": legal,
                        "call_score": call_score,
                        "pot": 20,
                        "hero_stack": 100,
                        "players_in_hand": players,
                        "table_players": max(players, 6),
                    },
                    "action_history": [],
                },
                legal,
            )
            for bucket in fitted["buckets"]:
                frequencies = bucket["frequencies"]
                assert set(frequencies) <= set(legal)
                assert sum(frequencies.values()) == 100
                assert all(value >= 0 for value in frequencies.values())


def test_board_made_hands_are_not_treated_as_hero_strong_value():
    assert classify_postflop_hand(
        {"hero_cards": ["As", "Kh"], "board": ["Jc", "Jd", "2s", "2h", "5c"]}
    ) == "showdown"
    assert classify_postflop_hand(
        {"hero_cards": ["As", "Kh"], "board": ["Jc", "Jd", "Js", "2h"]}
    ) == "showdown"
    assert classify_postflop_hand(
        {"hero_cards": ["2s", "3h"], "board": ["Ac", "Kd", "Qs", "Jh", "Tc"]}
    ) == "showdown"

    assert classify_postflop_hand(
        {"hero_cards": ["9s", "9h"], "board": ["Jc", "Jd", "2s", "2h", "5c"]}
    ) == "strong_value"
    assert classify_postflop_hand(
        {"hero_cards": ["Ts", "2h"], "board": ["9c", "8d", "7s", "6h", "5c"]}
    ) == "strong_value"


def test_money_strategy_uses_cbet_fold_metric_for_preflop_aggressor():
    context = {
        "decision": {
            "street": "flop",
            "hero_cards": ["As", "Ah"],
            "board": ["Kd", "7s", "2c"],
            "acting_seat": 1,
            "hero_position": "BTN",
            "legal_actions": ["check", "raise"],
            "pot": 10,
            "call_score": 0,
            "min_raise_to": 3,
            "max_raise_to": 100,
            "hero_street_contribution": 0,
            "hero_stack": 100,
            "big_blind": 2,
            "players_in_hand": 2,
            "table_players": 6,
            "state_quality": {"valid": True, "target_game_supported": True},
        },
        "action_history": [
            {"street": "preflop", "seat": 1, "position": "BTN", "action": "raise"},
            {"street": "preflop", "seat": 2, "position": "BB", "action": "call"},
        ],
        "active_opponent_profiles": [
            {
                "player": "villain",
                "observations": [
                    {
                        "metric": "fold_to_flop_cbet",
                        "opportunities": 50,
                        "successes": 40,
                        "mean_pct": 80,
                    },
                    {
                        "metric": "fold_to_flop_bet",
                        "opportunities": 50,
                        "successes": 5,
                        "mean_pct": 10,
                    },
                ],
            }
        ],
    }

    result = evaluate_money_strategy(context, simplified_range_strategy(context))

    assert result["fold_model"]["metric"] == "fold_to_flop_cbet"
    assert result["fold_model"]["per_opponent_pct"] == [80.0]


def test_weighted_random_selection_maps_draw_to_visible_interval():
    policy = [
        {
            "id": "raise_half_pot",
            "action": "raise",
            "raise_to": 20,
            "frequency_pct": 70.0,
            "robust_ev": 4.0,
        },
        {
            "id": "check",
            "action": "check",
            "raise_to": None,
            "frequency_pct": 30.0,
            "robust_ev": 3.0,
        },
    ]

    first = _weighted_random_selection(policy, 69.9999)
    second = _weighted_random_selection(policy, 70.0)

    assert first["selected_id"] == "raise_half_pot"
    assert first["intervals"][0] == {
        "id": "raise_half_pot",
        "action": "raise",
        "raise_to": 20,
        "frequency_pct": 70.0,
        "start_pct": 0.0,
        "end_pct": 70.0,
        "selected": True,
    }
    assert second["selected_id"] == "check"
    assert second["intervals"][1]["selected"] is True
