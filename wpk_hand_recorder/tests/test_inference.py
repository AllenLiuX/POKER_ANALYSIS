import sqlite3

from wpk_recorder.analytics import connect_readonly, hand_detail
from wpk_recorder.inference import (
    _action_likelihoods_by_strength,
    _player_postflop_patterns,
    action_line_range_profile,
    action_line_range_quality,
    available_starting_hand_combos,
    board_action_range_profile,
    conditional_continuation_ranges,
    continuation_range_quality,
    current_hand_action_contexts,
    inference_backtest,
    normalize_preflop_position,
    player_profile_evidence,
    preflop_range_profile,
    range_exploit_composition,
    range_strength_distribution,
    selected_opponent_range,
)
from wpk_recorder.models import Action, HandHistory, Player
from wpk_recorder.poker import best_rank, equity_vs_random, hand_features
from wpk_recorder.storage import RecorderStore


def test_hand_evaluator_and_equity_labels():
    assert best_rank(["As", "Ah", "Ad", "Kc", "Kd", "2s", "3h"])[0] == 6
    features = hand_features(["Ah", "Kh"], ["Qh", "2h", "7c"])
    assert features["flush_draw"] is True
    river = hand_features(
        ["Ah", "Kh"],
        ["2h", "Ts", "4d", "3d", "6d"],
    )
    assert river["flush_draw"] is False
    assert river["open_ended_draw"] is False
    assert river["gutshot"] is False
    assert equity_vs_random(["As", "Ah"], [], trials=400) > 0.8
    assert equity_vs_random(["7s", "2h"], [], trials=400) < 0.45


def test_board_action_fallback_promotes_river_value_classes():
    board = ["5s", "4h", "6c", "Ac", "3c"]
    baseline = {
        "AKo": 92.2,
        "A7s": 47.1,
        "87s": 25.1,
        "KQs": 92.2,
    }
    profile = board_action_range_profile(
        baseline,
        [
            {"seat": 7, "street": "flop", "action": "check"},
            {"seat": 7, "street": "turn", "action": "call"},
            {"seat": 7, "street": "river", "action": "bet"},
            {"seat": 7, "street": "river", "action": "raise"},
        ],
        7,
        board,
        action_context_by_street={
            "river": {"size_bucket": "overbet"},
        },
    )

    assert profile["enabled"] is True
    assert profile["confidence"] == "low"
    assert profile["weights"]["A7s"] > profile["weights"]["AKo"]
    assert profile["weights"]["87s"] > profile["weights"]["AKo"]
    assert (
        range_strength_distribution(profile["weights"], board)[
            "straight_plus"
        ]
        > range_strength_distribution(baseline, board)["straight_plus"]
    )
    assert all(update["heuristic"] for update in profile["updates"])
    assert available_starting_hand_combos("AA", board) == 3
    assert available_starting_hand_combos("AKo", board) == 9


def test_range_exploit_composition_marks_only_aggressive_air_as_bluff():
    weights = {
        "22": 100,
        "AKo": 100,
        "QJs": 100,
        "86o": 100,
    }
    board = ["Kh", "Th", "2c"]

    passive = range_exploit_composition(weights, board)
    aggressive = range_exploit_composition(
        weights,
        board,
        aggressive_action=True,
    )

    assert all(
        aggressive[key] > 0
        for key in (
            "strong_value",
            "marginal_showdown",
            "draws",
            "air",
        )
    )
    assert abs(
        sum(
            aggressive[key]
            for key in (
                "strong_value",
                "marginal_showdown",
                "draws",
                "air",
            )
        )
        - 100
    ) < 0.2
    assert passive["bluff_candidates"] is None
    assert aggressive["bluff_candidates"] == aggressive["air"]
    assert aggressive["semi_bluff_candidates"] == aggressive["draws"]


def test_range_composition_does_not_call_board_two_pair_all_value():
    board = ["Jd", "Ac", "Jc", "Ah"]

    weak_kicker = range_exploit_composition(
        {"97o": 100},
        board,
        aggressive_action=True,
    )
    ace = range_exploit_composition(
        {"AKo": 100},
        board,
        aggressive_action=True,
    )

    assert weak_kicker["strong_value"] == 0
    assert weak_kicker["bluff_candidates"] == 100
    assert ace["strong_value"] == 100
    assert ace["bluff_candidates"] == 0


def test_selected_opponent_range_uses_only_enabled_action_line_posterior():
    profile = {
        "player": "seat_2",
        "preflop_range": {
            "confidence": "medium",
            "line": "cold_call",
            "weights": {"AA": 100},
        },
        "range_posterior": {
            "enabled": False,
            "weights": {"72o": 100},
        },
    }

    fallback = selected_opponent_range(profile)
    profile["range_posterior"].update(
        {
            "enabled": True,
            "confidence": "low",
            "updates": [{"street": "flop", "applied": True}],
        }
    )
    posterior = selected_opponent_range(profile)

    assert fallback["source"] == "preflop_range"
    assert fallback["weights"] == {"AA": 100}
    assert posterior["source"] == "action_line_posterior"
    assert posterior["weights"] == {"72o": 100}
    assert posterior["line"] == "cold_call"
    assert posterior["applied_streets"] == ["flop"]


def test_range_likelihood_falls_back_to_action_family_with_size():
    paths = ("raise", "check-raise", "call-raise")
    records = []
    for index in range(48):
        path = paths[index % len(paths)]
        size = "overbet" if index < 24 else "small"
        records.append(
            {
                "user_id": f"pool-{index % 5}",
                "street": "river",
                "game_mode": "squid",
                "label": "pair" if size == "overbet" else "high_card",
                "signature": "raise",
                "action_path": path,
                "size_bucket": size,
                "path_size": f"{path}|{size}",
                "signature_size": f"raise|{size}",
                "preflop_path": "",
            }
        )

    likelihoods, evidence = _action_likelihoods_by_strength(
        records,
        "villain",
        "river",
        "raise",
        "squid",
        action_path="raise",
        size_bucket="overbet",
    )

    assert likelihoods
    assert evidence["feature_level"] == "action_family_size"
    assert evidence["matching_action_samples"] == 24
    assert evidence["size_increment_applied"] is True


def test_conditional_size_factor_does_not_invent_incremental_signal():
    records = []
    for index in range(48):
        size = "overbet" if index % 2 == 0 else "small"
        label = "pair" if (index // 2) % 2 else "high_card"
        records.append(
            {
                "user_id": f"pool-{index % 6}",
                "street": "river",
                "game_mode": "squid",
                "label": label,
                "signature": "raise",
                "action_path": "raise",
                "size_bucket": size,
                "path_size": f"raise|{size}",
                "signature_size": f"raise|{size}",
                "preflop_path": "",
            }
        )

    with_size, evidence = _action_likelihoods_by_strength(
        records,
        "villain",
        "river",
        "raise",
        "squid",
        action_path="raise",
        size_bucket="overbet",
    )
    action_only, _ = _action_likelihoods_by_strength(
        records,
        "villain",
        "river",
        "raise",
        "squid",
        action_path="raise",
    )

    assert evidence["feature_level"] == "path_size"
    assert all(
        abs(with_size[label] - action_only[label]) < 1e-9
        for label in with_size
    )


def test_showdown_labels_predictions_and_leave_one_hand_backtest(tmp_path):
    store = RecorderStore(tmp_path)
    samples = [
        ("room-1", "u1", ["As", "Ah"], "raise"),
        ("room-2", "u2", ["Ks", "Kh"], "raise"),
        ("room-3", "u3", ["7s", "2h"], "fold"),
    ]
    for number, (hand_id, user_id, cards, action) in enumerate(samples, 1):
        hand = HandHistory(
            hand_id=hand_id,
            started_at=f"2026-01-01T00:0{number}:00+00:00",
            status="completed",
            button_seat=1,
            big_blind=2,
        )
        hand.players = {
            1: Player(
                1,
                user_id=user_id,
                alias=user_id,
                stack_start=200,
                hole_cards=cards,
            )
        }
        hand.actions = [
            Action(
                "preflop",
                1,
                user_id,
                action,
                amount=6 if action == "raise" else 0,
                amount_to=6 if action == "raise" else None,
                user_id=user_id,
                action_id=f"a{number}",
                sequence=1,
            )
        ]
        store.save_hand(hand)
    store.close()

    detail = hand_detail(tmp_path, "room-3")
    assert detail is not None
    assert detail["range_predictions"][0]["training_samples"] == 2
    connection = connect_readonly(tmp_path)
    try:
        backtest = inference_backtest(connection)
        range_profile = preflop_range_profile(
            connection, "u1", position="SB", line="open_raise"
        )
        prior_only = preflop_range_profile(
            connection, "u3", position="SB", line="vpip"
        )
        evidence = player_profile_evidence(connection, "u1")
        action_contexts = current_hand_action_contexts(
            connection, "room-1"
        )
    finally:
        connection.close()
    assert backtest["labeled_observations"] == 3
    assert backtest["scored_observations"] == 3
    assert backtest["method"] == "leave-one-hand-out"
    assert len(range_profile["matrix"]) == 169
    assert range_profile["evidence"]["player_revealed_samples"] == 1
    weights = {item["hand"]: item["weight_pct"] for item in range_profile["matrix"]}
    assert weights["AA"] > weights["72o"]
    assert range_profile["evidence"]["prior_dependence_pct"] < 100
    assert prior_only["evidence"]["prior_dependence_pct"] == 100
    assert normalize_preflop_position("BTN/SB") == "BTN/SB"
    assert normalize_preflop_position("SMALL_BLIND") == "SB"
    assert evidence["available_cases"] == 1
    assert evidence["cases"][0]["case_id"] == "case_01"
    assert evidence["cases"][0]["shown_cards"] == ["As", "Ah"]
    assert evidence["cases"][0]["shown_hand"] == "AA"
    assert evidence["cases"][0]["preflop_line"] == "open_raise"
    assert evidence["cases"][0]["actions"][0]["action"] == "raise"
    assert range_profile["method"] == "hierarchical-bayesian-shown-hand-v1"
    assert action_contexts["u1"]["preflop"]["action"] == "raise"


def test_profile_patterns_measure_donk_leads_and_sizing_against_pool():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE hands(
            hand_id TEXT PRIMARY KEY,
            excluded_from_stats INTEGER,
            game_mode TEXT
        );
        CREATE TABLE decision_snapshots(
            hand_id TEXT,
            action_sequence INTEGER,
            user_id TEXT,
            seat INTEGER,
            street TEXT,
            chosen_action TEXT,
            to_call REAL,
            bet_fraction REAL,
            is_preflop_aggressor INTEGER,
            game_mode TEXT
        );
        """
    )
    for index in range(6):
        hand_id = f"donk-{index}"
        connection.execute(
            "INSERT INTO hands VALUES (?, 0, 'holdem')",
            (hand_id,),
        )
        action = "bet" if index < 4 else "check"
        connection.execute(
            """
            INSERT INTO decision_snapshots
            VALUES (?, 1, 'target', 2, 'flop', ?, 0, ?, 0, 'holdem')
            """,
            (hand_id, action, 0.25 if action == "bet" else None),
        )
        connection.execute(
            """
            INSERT INTO decision_snapshots
            VALUES (?, 2, 'pool-pfa', 1, 'flop', 'check', 0, NULL, 1, 'holdem')
            """,
            (hand_id,),
        )
    connection.commit()

    patterns = _player_postflop_patterns(connection, "target", "holdem")
    by_pattern = {item["pattern"]: item for item in patterns}

    assert by_pattern["flop_donk_lead"]["successes"] == 4
    assert by_pattern["flop_donk_lead"]["opportunities"] == 6
    assert by_pattern["flop_donk_lead"]["rate_pct"] == 66.7
    assert by_pattern["flop_small_bet"]["rate_pct"] == 100.0
    connection.close()


def test_action_line_range_only_enables_after_time_holdout_beats_baseline():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE hands(
            hand_id TEXT PRIMARY KEY,
            excluded_from_stats INTEGER,
            game_mode TEXT,
            played_at TEXT
        );
        CREATE TABLE showdown_observations(
            hand_id TEXT,
            user_id TEXT,
            street TEXT,
            strength_category TEXT,
            draws_json TEXT,
            action_line TEXT,
            feature_tokens_json TEXT
        );
        """
    )
    for index in range(100):
        hand_id = f"shown-{index:03d}"
        strong = index % 2 == 0
        connection.execute(
            "INSERT INTO hands VALUES (?, 0, 'squid', ?)",
            (hand_id, f"2026-01-{1 + index // 24:02d}T{index % 24:02d}:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO showdown_observations VALUES (?, ?, 'flop', ?, '{}', ?, ?)",
            (
                hand_id,
                "villain" if index % 5 == 0 else f"pool-{index % 8}",
                "pair" if strong else "high_card",
                "raise",
                (
                    '["size=overbet"]'
                    if strong
                    else '["size=small"]'
                ),
            ),
        )
    connection.execute(
        "INSERT INTO hands VALUES ('excluded-current', 1, 'squid', ?)",
        ("2026-02-01T00:00:00+00:00",),
    )
    connection.execute(
        """
        INSERT INTO showdown_observations VALUES (
            'excluded-current', 'villain', 'flop', 'pair', '{}',
            'raise', '["size=overbet"]'
        )
        """
    )
    connection.commit()

    quality = action_line_range_quality(connection, "squid")
    posterior = action_line_range_profile(
        connection,
        "villain",
        {
            "weights": {"AA": 50.0, "72o": 50.0},
            "estimated_range_pct": 1.0,
        },
        [{"street": "flop", "seat": 2, "action": "raise"}],
        2,
        ["Kc", "8d", "3s"],
        mode="squid",
        quality=quality,
        exclude_hand_id="shown-099",
        action_context_by_street={
            "flop": {"size_bucket": "overbet"}
        },
    )
    coarse_fallback = action_line_range_profile(
        connection,
        "villain",
        {
            "weights": {"AA": 50.0, "72o": 50.0},
            "estimated_range_pct": 1.0,
        },
        [{"street": "flop", "seat": 2, "action": "raise"}],
        2,
        ["Kc", "8d", "3s"],
        mode="squid",
        quality={
            "enabled": True,
            "feature_groups": [
                {"feature_level": "path_size", "enabled": False},
                {
                    "feature_level": "action_family_size",
                    "enabled": False,
                },
                {"feature_level": "exact_path", "enabled": False},
                {"feature_level": "action_family", "enabled": True},
            ],
        },
        exclude_hand_id="shown-099",
        action_context_by_street={
            "flop": {"size_bucket": "overbet"}
        },
    )
    connection.close()

    assert quality["enabled"] is True
    assert quality["model_log_loss"] < quality["baseline_log_loss"]
    assert quality["enabled_size_conditioned_coverage_pct"] == 100.0
    assert (
        quality["method"]
        == "prequential-conditional-size-action-line-gate-v5"
    )
    size_quality = next(
        group
        for group in quality["feature_groups"]
        if group["feature_level"] == "path_size"
    )
    assert size_quality["comparator"] == "action_only"
    assert size_quality["enabled"] is True
    assert quality["size_increment_log_loss_improvement"] > 0
    assert (
        posterior["production_training"]["eligible_showdown_observations"]
        == 99
    )
    assert (
        posterior["production_training"]["eligible_showdown_observations"]
        > quality["train_hands"]
    )
    assert posterior["enabled"] is True
    assert posterior["weights"]["AA"] > posterior["weights"]["72o"]
    assert posterior["updates"][0]["signature"] == "raise"
    assert posterior["updates"][0]["feature_level"] == "path_size"
    assert (
        posterior["strength_distribution_pct"]["pair"]
        > posterior["baseline_strength_distribution_pct"]["pair"]
    )
    assert posterior["top_class_shifts"]
    assert posterior["entropy_bits"]["posterior"] is not None
    assert coarse_fallback["enabled"] is True
    assert coarse_fallback["updates"][0]["applied"] is True
    assert coarse_fallback["updates"][0]["feature_level"] == "action_family"
    assert coarse_fallback["updates"][0]["gate_fallback_chain"] == [
        "path_size",
        "exact_path",
        "action_family_size",
        "action_family",
    ]


def test_same_board_bet_separates_draw_heavy_and_value_heavy_pools():
    def build_pool(draw_bets):
        connection = sqlite3.connect(":memory:")
        connection.executescript(
            """
            CREATE TABLE hands(
                hand_id TEXT PRIMARY KEY,
                excluded_from_stats INTEGER,
                game_mode TEXT,
                played_at TEXT
            );
            CREATE TABLE showdown_observations(
                hand_id TEXT,
                user_id TEXT,
                street TEXT,
                strength_category TEXT,
                draws_json TEXT,
                action_line TEXT,
                feature_tokens_json TEXT
            );
            """
        )
        index = 0
        for label, bets in (
            ("draw", draw_bets),
            ("value", 80 - draw_bets),
        ):
            for sample in range(80):
                hand_id = f"{label}-{sample}"
                action = "bet" if sample < bets else "check"
                connection.execute(
                    "INSERT INTO hands VALUES (?, 0, 'holdem', ?)",
                    (
                        hand_id,
                        f"2026-01-{1 + index // 24:02d}"
                        f"T{index % 24:02d}:00:00+00:00",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO showdown_observations
                    VALUES (?, ?, 'flop', ?, ?, ?, '[]')
                    """,
                    (
                        hand_id,
                        f"pool-{sample % 12}",
                        "high_card" if label == "draw" else "trips",
                        (
                            '{"flush_draw": true}'
                            if label == "draw"
                            else "{}"
                        ),
                        action,
                    ),
                )
                index += 1
        connection.commit()
        return connection

    def posterior(connection):
        return action_line_range_profile(
            connection,
            "target",
            {
                "weights": {"KQs": 50.0, "AA": 50.0},
                "estimated_range_pct": 1.0,
            },
            [{"street": "flop", "seat": 2, "action": "bet"}],
            2,
            ["Ah", "7h", "2c"],
            mode="holdem",
            quality={"enabled": True, "feature_groups": []},
            exclude_hand_id="current-node",
        )

    draw_connection = build_pool(draw_bets=64)
    value_connection = build_pool(draw_bets=16)
    try:
        draw_heavy = posterior(draw_connection)
        value_heavy = posterior(value_connection)
    finally:
        draw_connection.close()
        value_connection.close()

    assert draw_heavy["enabled"] is True
    assert value_heavy["enabled"] is True
    assert draw_heavy["weights"]["KQs"] > value_heavy["weights"]["KQs"]
    assert value_heavy["weights"]["AA"] > draw_heavy["weights"]["AA"]
    assert (
        draw_heavy["strength_distribution_pct"]["draw"]
        > value_heavy["strength_distribution_pct"]["draw"]
    )
    assert (
        value_heavy["strength_distribution_pct"]["two_pair_plus"]
        > draw_heavy["strength_distribution_pct"]["two_pair_plus"]
    )
    assert all(
        0 <= weight <= 100
        for profile in (draw_heavy, value_heavy)
        for weight in profile["weights"].values()
    )
    assert all(
        update["population_samples"] == 160
        for profile in (draw_heavy, value_heavy)
        for update in profile["updates"]
    )


def test_continuation_range_reweights_callers_after_prequential_gate():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE hands(
            hand_id TEXT PRIMARY KEY,
            excluded_from_stats INTEGER,
            game_mode TEXT,
            played_at TEXT
        );
        CREATE TABLE showdown_observations(
            hand_id TEXT,
            user_id TEXT,
            street TEXT,
            strength_category TEXT,
            draws_json TEXT,
            action_line TEXT,
            feature_tokens_json TEXT
        );
        """
    )
    for index in range(140):
        strong = index % 2 == 0
        hand_id = f"continue-{index:03d}"
        connection.execute(
            "INSERT INTO hands VALUES (?, 0, 'squid', ?)",
            (
                hand_id,
                f"2026-01-{1 + index // 24:02d}"
                f"T{index % 24:02d}:00:00+00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO showdown_observations VALUES (
                ?, ?, 'flop', ?, '{}', ?, '["size=medium"]'
            )
            """,
            (
                hand_id,
                "villain" if index % 7 == 0 else f"pool-{index % 9}",
                "pair" if strong else "high_card",
                "call" if strong else "check",
            ),
        )
    connection.commit()

    quality = continuation_range_quality(connection, "squid")
    profile = conditional_continuation_ranges(
        connection,
        "villain",
        {"AA": 50.0, "72o": 50.0},
        ["Kc", "8d", "3s"],
        street="flop",
        mode="squid",
        blockers=["As", "Qh"],
        quality=quality,
    )
    connection.close()

    assert quality["enabled"] is True
    assert quality["enabled_coverage_pct"] == 100.0
    assert profile["enabled"] is True
    assert 0 < profile["model_weight"] <= 0.75
    flop_quality = quality["groups"][0]
    assert flop_quality["call_enabled"] is True
    assert flop_quality["raise_enabled"] is False
    conditioned = profile["buckets"]["medium"]["weights"]
    assert conditioned["AA"] > 50.0
    assert conditioned["72o"] < 50.0
    assert profile["buckets"]["medium"]["call_enabled"] is True
    assert profile["buckets"]["medium"]["raise_enabled"] is False


def test_continuation_range_gates_call_and_raise_submodels_separately():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE hands(
            hand_id TEXT PRIMARY KEY,
            excluded_from_stats INTEGER,
            game_mode TEXT,
            played_at TEXT
        );
        CREATE TABLE showdown_observations(
            hand_id TEXT,
            user_id TEXT,
            street TEXT,
            strength_category TEXT,
            draws_json TEXT,
            action_line TEXT,
            feature_tokens_json TEXT
        );
        """
    )
    actions = (
        ("call", "pair"),
        ("raise", "straight"),
        ("check", "high_card"),
    )
    for index in range(210):
        action, strength = actions[index % len(actions)]
        hand_id = f"split-continue-{index:03d}"
        connection.execute(
            "INSERT INTO hands VALUES (?, 0, 'squid', ?)",
            (
                hand_id,
                f"2026-03-{1 + index // 24:02d}"
                f"T{index % 24:02d}:00:00+00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO showdown_observations VALUES (
                ?, ?, 'flop', ?, '{}', ?, '["size=medium"]'
            )
            """,
            (hand_id, f"pool-{index % 12}", strength, action),
        )
    connection.commit()

    quality = continuation_range_quality(connection, "squid")
    profile = conditional_continuation_ranges(
        connection,
        "pool-1",
        {"AA": 34.0, "AKo": 33.0, "72o": 33.0},
        ["Kc", "8d", "3s"],
        street="flop",
        mode="squid",
        blockers=["As", "Qh"],
        quality=quality,
    )
    connection.close()

    group = quality["groups"][0]
    bucket = profile["buckets"]["medium"]
    assert group["call_enabled"] is True
    assert group["raise_enabled"] is True
    assert group["call_test_observations"] >= 12
    assert group["raise_test_observations"] >= 12
    assert bucket["call_enabled"] is True
    assert bucket["raise_enabled"] is True
    assert bucket["call_weights"]
    assert bucket["raise_weights"]
