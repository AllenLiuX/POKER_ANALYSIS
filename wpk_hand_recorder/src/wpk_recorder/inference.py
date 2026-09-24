from __future__ import annotations

import json
import math
import sqlite3
import threading
from collections import Counter, defaultdict
from functools import lru_cache
from itertools import combinations
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .features import stack_bucket
from .opponent_model import action_response_backtest, joint_fold_backtest
from .poker import equity_vs_random, hand_features


STREETS = ("preflop", "flop", "turn", "river")
STREET_CARD_COUNT = {"preflop": 0, "flop": 3, "turn": 4, "river": 5}
FORCED_ACTIONS = {"ante", "small_blind", "big_blind", "straddle"}
RANKS = "AKQJT98765432"
PREFLOP_LINES = ("vpip", "open_raise", "three_bet", "cold_call", "limp")
QUALITY_GATE_HAND_CAP = 160
PREFLOP_LINE_LABELS = {
    "vpip": "主动入池总体",
    "open_raise": "无人入池时加注",
    "three_bet": "3bet",
    "cold_call": "面对加注跟注",
    "limp": "无人加注时跟入",
}
POSITION_ALIASES = {
    "SMALL_BLIND": "SB",
    "BIG_BLIND": "BB",
    "BUTTON": "BTN",
    "CUTOFF": "CO",
    "HIJACK": "HJ",
}
_REVEALED_PREFLOP_CACHE: Dict[
    Tuple[str, Optional[str], Tuple[int, int, int]],
    List[Dict[str, Any]],
] = {}
_REVEALED_PREFLOP_CACHE_LOCK = threading.Lock()
_ACTION_STRENGTH_RECORDS_CACHE: Dict[
    Tuple[str, Optional[str], Tuple[int, int, int]],
    List[Dict[str, Any]],
] = {}
_ACTION_STRENGTH_RECORDS_CACHE_LOCK = threading.Lock()
POSITION_ORDER = {
    name: index
    for index, name in enumerate(
        (
            "ALL",
            "UTG",
            "UTG+1",
            "MP",
            "MP+1",
            "HJ",
            "CO",
            "BTN",
            "BTN/SB",
            "SB",
            "BB",
        )
    )
}
_ACTION_LINE_QUALITY_CACHE: Dict[
    Tuple[str, str, int], Dict[str, Any]
] = {}
_CONTINUATION_QUALITY_CACHE: Dict[
    Tuple[str, str, int], Dict[str, Any]
] = {}


def rebuild_showdown_observations(
    connection: sqlite3.Connection, hand_id: Optional[str] = None
) -> None:
    net_before = _cumulative_net_before(connection)
    parameters: Tuple[Any, ...] = ()
    hand_filter = ""
    if hand_id:
        hand_filter = " AND h.hand_id = ?"
        parameters = (hand_id,)
    rows = connection.execute(
        f"""
        SELECT h.hand_id, h.board_json, h.big_blind, hp.user_id, hp.alias,
               hp.hole_cards_json
        FROM hands h
        JOIN hand_players hp ON hp.hand_id = h.hand_id
        WHERE h.excluded_from_stats = 0
          AND hp.user_id IS NOT NULL
          AND hp.hole_cards_json != '[]'
          {hand_filter}
        """,
        parameters,
    ).fetchall()
    observations = []
    for row in rows:
        current_hand_id, board_json, big_blind, user_id, alias, hole_json = row
        hole_cards = json.loads(hole_json)
        board = json.loads(board_json)
        decisions = connection.execute(
            """
            SELECT street, position, chosen_action, bet_fraction,
                   effective_stack_bb, is_ip, players_in_hand,
                   board_texture_json, action_sequence
            FROM decision_snapshots
            WHERE hand_id = ? AND user_id = ?
            ORDER BY action_sequence
            """,
            (current_hand_id, user_id),
        ).fetchall()
        by_street: Dict[str, List[sqlite3.Row]] = defaultdict(list)
        for decision in decisions:
            by_street[decision[0]].append(decision)
        squid_row = connection.execute(
            """
            SELECT squid_start FROM hand_squid_players
            WHERE hand_id = ? AND user_id = ?
            """,
            (current_hand_id, user_id),
        ).fetchone()
        squid_count = int(squid_row[0]) if squid_row else 0
        cumulative = net_before.get((current_hand_id, user_id), 0.0)
        profit_state = _profit_state(
            cumulative / big_blind if big_blind else cumulative
        )
        full_lines: Dict[str, str] = {}
        for street in STREETS:
            street_decisions = [
                decision
                for decision in by_street.get(street) or []
                if decision[2] not in FORCED_ACTIONS
            ]
            if not street_decisions:
                continue
            line = "-".join(decision[2] for decision in street_decisions)
            full_lines[street] = line
            last = street_decisions[-1]
            visible_board = board[: STREET_CARD_COUNT[street]]
            features = hand_features(hole_cards, visible_board)
            equity = equity_vs_random(hole_cards, visible_board)
            tokens = _feature_tokens(
                street=street,
                position=last[1],
                action=last[2],
                bet_fraction=last[3],
                stack_bb=last[4],
                is_ip=last[5],
                players=last[6],
                texture=json.loads(last[7] or "{}"),
                squid_count=squid_count,
                profit_state=profit_state,
                lines=full_lines,
            )
            draws = {
                key: features[key]
                for key in ("flush_draw", "open_ended_draw", "gutshot")
            }
            observations.append(
                (
                    current_hand_id,
                    user_id,
                    alias,
                    street,
                    json.dumps(hole_cards),
                    json.dumps(visible_board),
                    features["preflop_class"],
                    features["category"],
                    json.dumps(draws),
                    equity,
                    last[1],
                    last[4],
                    squid_count,
                    cumulative,
                    profit_state,
                    line,
                    json.dumps(tokens),
                ),
            )
    with connection:
        if hand_id:
            connection.execute(
                "DELETE FROM showdown_observations WHERE hand_id = ?",
                (hand_id,),
            )
        else:
            connection.execute("DELETE FROM showdown_observations")
        connection.executemany(
            """
            INSERT INTO showdown_observations(
                hand_id, user_id, alias, street, hole_cards_json, board_json,
                preflop_class, strength_category, draws_json, equity_vs_random,
                position, effective_stack_bb, squid_count,
                cumulative_net_before, profit_state, action_line,
                feature_tokens_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            observations,
        )


def hand_range_predictions(
    connection: sqlite3.Connection, hand_id: str
) -> List[Dict[str, Any]]:
    training = _training_rows(connection)
    targets = _hand_targets(connection, hand_id)
    return [_predict(training, target, exclude_hand=hand_id) for target in targets]


def inference_backtest(connection: sqlite3.Connection) -> Dict[str, Any]:
    records = _training_rows(connection)
    action_line_gate = action_line_range_quality(connection)
    continuation_range_gate = continuation_range_quality(connection)
    action_response_gate = action_response_backtest(connection)
    joint_response_gate = joint_fold_backtest(connection)
    scored = []
    for record in records:
        prediction = _predict(records, record, exclude_hand=record["hand_id"])
        if prediction["training_samples"] < 2:
            continue
        actual = _target_label(record)
        probabilities = prediction["distribution"]
        actual_probability = max(1e-9, probabilities.get(actual, 0.0))
        labels = set(probabilities) | {actual}
        brier = sum(
            (probabilities.get(label, 0.0) - (1.0 if label == actual else 0.0)) ** 2
            for label in labels
        )
        scored.append(
            {
                "hand_id": record["hand_id"],
                "user_id": record["user_id"],
                "street": record["street"],
                "actual": actual,
                "predicted": prediction["top_label"],
                "actual_probability": round(actual_probability, 4),
                "correct": prediction["top_label"] == actual,
                "brier": round(brier, 4),
                "log_loss": round(-math.log(actual_probability), 4),
            }
        )
    count = len(scored)
    return {
        "labeled_observations": len(records),
        "scored_observations": count,
        "top1_accuracy": round(
            sum(item["correct"] for item in scored) / count, 4
        )
        if count
        else None,
        "mean_brier": round(
            sum(item["brier"] for item in scored) / count, 4
        )
        if count
        else None,
        "mean_log_loss": round(
            sum(item["log_loss"] for item in scored) / count, 4
        )
        if count
        else None,
        "method": "leave-one-hand-out",
        "action_line_gate": action_line_gate,
        "continuation_range_gate": continuation_range_gate,
        "action_response_gate": action_response_gate,
        "joint_response_gate": joint_response_gate,
        "results": scored[-100:],
    }


def preflop_range_profile(
    connection: sqlite3.Connection,
    user_id: str,
    position: str = "ALL",
    line: str = "vpip",
    mode: Optional[str] = None,
    _exclude_hand_id: Optional[str] = None,
    _use_player_evidence: bool = True,
) -> Dict[str, Any]:
    """Estimate a player's preflop range without treating showdowns as IID samples.

    The range width comes from all action opportunities. Revealed hands only adjust
    the shape of a structural/population prior and are deliberately down-weighted
    because hands that reach showdown are selection-biased.
    """
    line = str(line or "vpip").strip().lower()
    if line not in PREFLOP_LINES:
        raise ValueError(f"unsupported preflop line: {line}")
    position = normalize_preflop_position(position)
    if position == "UNKNOWN":
        position = "ALL"
    player = connection.execute(
        """
        SELECT p.user_id, COALESCE(
                 p.latest_alias,
                 (SELECT hp.alias FROM hand_players hp
                  WHERE hp.user_id = p.user_id AND hp.alias IS NOT NULL
                  ORDER BY hp.hand_id DESC LIMIT 1)
               ) AS alias
        FROM players p WHERE p.user_id = ?
        """,
        (user_id,),
    ).fetchone()
    if player is None:
        raise LookupError("player not found")
    player_alias = (
        player["alias"]
        if isinstance(player, sqlite3.Row)
        else player[1]
    )

    shown = [
        record
        for record in _revealed_preflop_records(connection, mode)
        if record["hand_id"] != _exclude_hand_id
    ]
    matching_population = [
        record
        for record in shown
        if record["user_id"] != user_id
        and (position == "ALL" or record["position"] == position)
        and line in record["lines"]
    ]
    matching_player = [
        record
        for record in shown
        if record["user_id"] == user_id
        and (position == "ALL" or record["position"] == position)
        and line in record["lines"]
    ] if _use_player_evidence else []
    frequency = _preflop_line_frequency(
        connection, user_id, position, line, mode
    )
    classes = _starting_hand_classes()
    structural = _structural_range_distribution(
        classes, line, frequency["population_mean_pct"] / 100
    )
    population_counts = Counter(
        record["hand_class"] for record in matching_population
    )
    player_counts = Counter(record["hand_class"] for record in matching_player)
    structural_families = defaultdict(float)
    for item in classes:
        structural_families[_preflop_family(item["hand"])] += structural[item["hand"]]
    population_family_counts = Counter(
        _preflop_family(record["hand_class"]) for record in matching_population
    )
    player_family_counts = Counter(
        _preflop_family(record["hand_class"]) for record in matching_player
    )

    population_effect = 0.35
    population_prior_strength = 36.0
    population_total = population_effect * sum(population_family_counts.values())
    population_family_distribution = {
        family: (
            population_prior_strength * structural_families[family]
            + population_effect * population_family_counts[family]
        )
        / (population_prior_strength + population_total)
        for family in structural_families
    }
    player_effect = 0.75
    player_prior_strength = 12.0
    player_total = player_effect * sum(player_family_counts.values())
    player_family_distribution = {
        family: (
            player_prior_strength * population_family_distribution[family]
            + player_effect * player_family_counts[family]
        )
        / (player_prior_strength + player_total)
        for family in structural_families
    }
    posterior = {
        item["hand"]: player_family_distribution[_preflop_family(item["hand"])]
        * structural[item["hand"]]
        / structural_families[_preflop_family(item["hand"])]
        for item in classes
    }
    inclusion = _calibrate_range_inclusion(
        classes, posterior, frequency["mean_pct"] / 100
    )
    range_fraction = max(1e-9, frequency["mean_pct"] / 100)
    matrix = []
    for item in classes:
        hand = item["hand"]
        conditional = (
            item["deal_probability"] * inclusion[hand] / range_fraction
            if range_fraction > 0
            else 0.0
        )
        matrix.append(
            {
                **item,
                "weight_pct": round(100 * inclusion[hand], 1),
                "conditional_pct": round(100 * conditional, 2),
                "player_reveals": player_counts[hand],
                "population_reveals": population_counts[hand],
            }
        )
    top_hands = sorted(
        matrix,
        key=lambda item: (
            -item["weight_pct"],
            -_starting_hand_score(item["hand"], line),
            -item["conditional_pct"],
            item["hand"],
        ),
    )[:15]
    positions = _preflop_position_options(connection, user_id, shown, mode)
    lines = []
    for candidate in PREFLOP_LINES:
        candidate_frequency = _preflop_line_frequency(
            connection, user_id, position, candidate, mode
        )
        direct = sum(
            1
            for record in shown
            if record["user_id"] == user_id
            and (position == "ALL" or record["position"] == position)
            and candidate in record["lines"]
        )
        lines.append(
            {
                "value": candidate,
                "label": PREFLOP_LINE_LABELS[candidate],
                "opportunities": candidate_frequency["opportunities"],
                "revealed_samples": direct,
            }
        )
    confidence = _range_confidence(
        len(matching_player), frequency["opportunities"]
    )
    return {
        "user_id": user_id,
        "alias": player_alias or user_id,
        "position": position,
        "line": line,
        "line_label": PREFLOP_LINE_LABELS[line],
        "estimated_range_pct": frequency["mean_pct"],
        "frequency": frequency,
        "confidence": confidence,
        "evidence": {
            "player_revealed_samples": len(matching_player),
            "population_revealed_samples": len(matching_population),
            "action_opportunities": frequency["opportunities"],
            "prior_dependence_pct": round(
                100
                * player_prior_strength
                / (player_prior_strength + player_total),
                1,
            ),
            "showdown_evidence_weight": player_effect,
        },
        "options": {"positions": positions, "lines": lines},
        "matrix": matrix,
        "top_hands": top_hands,
        "method": "hierarchical-bayesian-shown-hand-v1",
        "limitations": [
            "亮牌样本有摊牌选择偏差，单手证据按 0.75 个样本降权",
            "范围宽度来自全部行动机会，亮牌只调整范围形状",
            "很低或低置信度只适合作为先验，不应覆盖实时牌面与赔率",
        ],
    }


def _quality_gate_window(
    records: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    hand_order = list(
        dict.fromkeys(record["hand_id"] for record in records)
    )
    if len(hand_order) <= QUALITY_GATE_HAND_CAP:
        return records
    included = set(hand_order[-QUALITY_GATE_HAND_CAP:])
    return [
        record
        for record in records
        if record["hand_id"] in included
    ]


def action_line_range_quality(
    connection: sqlite3.Connection,
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Time-split gate for action-line hand-strength inference."""

    records = _quality_gate_window(
        _action_strength_records(connection, mode)
    )
    hand_order = []
    seen_hands = set()
    for record in records:
        if record["hand_id"] not in seen_hands:
            seen_hands.add(record["hand_id"])
            hand_order.append(record["hand_id"])
    if len(hand_order) < 6:
        return {
            "enabled": False,
            "train_hands": 0,
            "test_observations": 0,
            "reason": "需要至少 6 个按时间排序的摊牌手牌",
            "method": "prequential-conditional-size-action-line-gate-v5",
        }
    split = max(1, min(len(hand_order) - 1, int(len(hand_order) * 0.7)))
    training_hands = set(hand_order[:split])
    test_hands = set(hand_order[split:])
    training = [
        record for record in records if record["hand_id"] in training_hands
    ]
    tests_by_hand = {
        hand_id: [
            record for record in records if record["hand_id"] == hand_id
        ]
        for hand_id in hand_order[split:]
    }
    model_loss = 0.0
    baseline_loss = 0.0
    model_correct = 0
    baseline_correct = 0
    scored = 0
    scored_rows = []
    feature_scores: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {
            "model_loss": 0.0,
            "baseline_loss": 0.0,
            "prior_loss": 0.0,
            "count": 0.0,
        }
    )
    feature_comparators: Dict[str, str] = {}
    for hand_id in hand_order[split:]:
        hand_targets = tests_by_hand.get(hand_id) or []
        for target in hand_targets:
            prediction, evidence = _strength_prediction(training, target)
            baseline = _strength_prior(training, target["street"])
            if not prediction or not baseline:
                continue
            actual = target["label"]
            model_probability = max(
                1e-9, prediction.get(actual, 0.0)
            )
            baseline_probability = max(
                1e-9, baseline.get(actual, 0.0)
            )
            model_loss -= math.log(model_probability)
            baseline_loss -= math.log(baseline_probability)
            model_correct += int(
                max(prediction, key=prediction.get) == actual
            )
            baseline_correct += int(
                max(baseline, key=baseline.get) == actual
            )
            feature_level = str(
                evidence.get("feature_level") or "prior_only"
            )
            action_feature_level = str(
                evidence.get("action_feature_level") or feature_level
            )
            fallback_prediction = prediction
            feature_comparator_probability = baseline_probability
            feature_comparator = "strength_prior"
            if feature_level in {
                "path_size",
                "action_family_size",
                "street_size",
            }:
                fallback_prediction, _ = _strength_prediction(
                    training,
                    {**target, "size_bucket": "none"},
                )
                feature_comparator_probability = max(
                    1e-9, fallback_prediction.get(actual, 0.0)
                )
                feature_comparator = "action_only"
            feature_scores[feature_level]["model_loss"] -= math.log(
                model_probability
            )
            feature_scores[feature_level]["baseline_loss"] -= math.log(
                feature_comparator_probability
            )
            feature_scores[feature_level]["prior_loss"] -= math.log(
                baseline_probability
            )
            feature_scores[feature_level]["count"] += 1
            feature_comparators[feature_level] = feature_comparator
            scored_rows.append(
                (
                    actual,
                    prediction,
                    fallback_prediction,
                    baseline,
                    feature_level,
                    action_feature_level,
                )
            )
            scored += 1
        training.extend(hand_targets)
    if not scored:
        return {
            "enabled": False,
            "train_hands": len(training_hands),
            "test_observations": 0,
            "reason": "时间外区间没有可评分行动线",
            "method": "prequential-conditional-size-action-line-gate-v5",
        }
    preview_model_mean_loss = model_loss / scored
    baseline_mean_loss = baseline_loss / scored
    feature_groups = []
    for feature_level, score in sorted(feature_scores.items()):
        count = int(score["count"])
        feature_model_loss = score["model_loss"] / max(1, count)
        feature_baseline_loss = score["baseline_loss"] / max(1, count)
        feature_prior_loss = score["prior_loss"] / max(1, count)
        feature_enabled = (
            count >= 20
            and feature_model_loss + 0.005 < feature_baseline_loss
            and feature_model_loss < feature_prior_loss
        )
        feature_groups.append(
            {
                "feature_level": feature_level,
                "comparator": feature_comparators[feature_level],
                "enabled": feature_enabled,
                "test_observations": count,
                "model_log_loss": round(feature_model_loss, 4),
                "baseline_log_loss": round(feature_baseline_loss, 4),
                "strength_prior_log_loss": round(
                    feature_prior_loss, 4
                ),
                "log_loss_improvement": round(
                    feature_baseline_loss - feature_model_loss, 4
                ),
            }
        )
    feature_enabled = {
        str(group["feature_level"]): bool(group["enabled"])
        for group in feature_groups
    }
    size_levels = {"path_size", "action_family_size", "street_size"}
    gated_model_loss = 0.0
    gated_model_correct = 0
    production_enabled_count = 0
    for (
        actual,
        prediction,
        fallback_prediction,
        baseline,
        feature_level,
        action_feature_level,
    ) in scored_rows:
        gated_prediction = prediction
        uses_feature = feature_enabled.get(feature_level, False)
        if feature_level in size_levels and not uses_feature:
            uses_feature = feature_enabled.get(
                action_feature_level, False
            )
            gated_prediction = (
                fallback_prediction if uses_feature else baseline
            )
        elif not uses_feature:
            gated_prediction = baseline
        production_enabled_count += int(uses_feature)
        probability = max(
            1e-9, gated_prediction.get(actual, 0.0)
        )
        gated_model_loss -= math.log(probability)
        gated_model_correct += int(
            max(gated_prediction, key=gated_prediction.get) == actual
        )
    model_mean_loss = gated_model_loss / scored
    enabled = (
        production_enabled_count > 0
        and model_mean_loss < baseline_mean_loss
    )
    preview_size_count = sum(
        int(group["test_observations"])
        for group in feature_groups
        if group["feature_level"] in size_levels
    )
    enabled_size_count = sum(
        int(group["test_observations"])
        for group in feature_groups
        if group["feature_level"] in size_levels and group["enabled"]
    )
    size_model_loss = sum(
        feature_scores[level]["model_loss"]
        for level in size_levels
        if level in feature_scores
    )
    size_action_only_loss = sum(
        feature_scores[level]["baseline_loss"]
        for level in size_levels
        if level in feature_scores
    )
    return {
        "enabled": enabled,
        "train_hands": len(training_hands),
        "test_hands": len(test_hands),
        "test_observations": scored,
        "validation_policy": (
            "score_each_test_hand_then_add_it_to_training"
        ),
        "model_log_loss": round(model_mean_loss, 4),
        "preview_model_log_loss": round(preview_model_mean_loss, 4),
        "baseline_log_loss": round(baseline_mean_loss, 4),
        "log_loss_improvement": round(
            baseline_mean_loss - model_mean_loss, 4
        ),
        "model_top1_accuracy": round(gated_model_correct / scored, 4),
        "preview_model_top1_accuracy": round(model_correct / scored, 4),
        "baseline_top1_accuracy": round(baseline_correct / scored, 4),
        "feature_groups": feature_groups,
        "preview_size_conditioned_observations": preview_size_count,
        "preview_size_conditioned_coverage_pct": round(
            100 * preview_size_count / scored, 1
        ),
        "enabled_size_conditioned_observations": enabled_size_count,
        "enabled_size_conditioned_coverage_pct": round(
            100 * enabled_size_count / scored, 1
        ),
        "size_increment_model_log_loss": (
            round(size_model_loss / preview_size_count, 4)
            if preview_size_count
            else None
        ),
        "size_action_only_log_loss": (
            round(size_action_only_loss / preview_size_count, 4)
            if preview_size_count
            else None
        ),
        "size_increment_log_loss_improvement": (
            round(
                (size_action_only_loss - size_model_loss)
                / preview_size_count,
                4,
            )
            if preview_size_count
            else None
        ),
        "production_action_line_coverage_pct": (
            round(100 * production_enabled_count / scored, 1)
            if enabled
            else 0.0
        ),
        "reason": (
            "至少一个行动特征层在时间外超过对应回退模型"
            if enabled
            else "尚无行动特征层在至少 20 个时间外样本上超过对应回退模型"
        ),
        "method": "prequential-conditional-size-action-line-gate-v5",
    }


def continuation_range_quality(
    connection: sqlite3.Connection,
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Gate action-conditioned caller ranges by street and incremental size."""

    records = _quality_gate_window(
        _action_strength_records(connection, mode)
    )
    hand_order = list(dict.fromkeys(record["hand_id"] for record in records))
    if len(hand_order) < 12:
        return {
            "enabled": False,
            "groups": [],
            "test_observations": 0,
            "reason": "继续范围需要至少 12 手牌的公开底牌",
            "method": "prequential-split-continuation-range-gate-v2",
        }
    split = max(1, min(len(hand_order) - 1, int(0.70 * len(hand_order))))
    training_hands = set(hand_order[:split])
    training = [
        record for record in records if record["hand_id"] in training_hands
    ]
    tests_by_hand = {
        hand_id: [
            record
            for record in records
            if record["hand_id"] == hand_id
            and record["signature"] in {"call", "raise"}
        ]
        for hand_id in hand_order[split:]
    }
    scores: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {
            "action_loss": 0.0,
            "prior_loss": 0.0,
            "heuristic_loss": 0.0,
            "size_loss": 0.0,
            "size_action_loss": 0.0,
            "count": 0.0,
            "size_count": 0.0,
            "call_action_loss": 0.0,
            "call_prior_loss": 0.0,
            "call_heuristic_loss": 0.0,
            "call_count": 0.0,
            "raise_action_loss": 0.0,
            "raise_prior_loss": 0.0,
            "raise_heuristic_loss": 0.0,
            "raise_count": 0.0,
        }
    )
    for hand_id in hand_order[split:]:
        for target in tests_by_hand.get(hand_id) or []:
            prior = _strength_prior(training, target["street"])
            action_prediction, _ = _action_family_strength_prediction(
                training, target, use_size=False
            )
            size_prediction, size_evidence = (
                _action_family_strength_prediction(
                    training, target, use_size=True
                )
            )
            if not prior or not action_prediction:
                continue
            heuristic = _heuristic_continuation_strength_prediction(prior)
            actual = target["label"]
            score = scores[target["street"]]
            action_probability = max(
                1e-9, action_prediction.get(actual, 0.0)
            )
            score["action_loss"] -= math.log(action_probability)
            score["prior_loss"] -= math.log(
                max(1e-9, prior.get(actual, 0.0))
            )
            score["heuristic_loss"] -= math.log(
                max(1e-9, heuristic.get(actual, 0.0))
            )
            score["count"] += 1
            action_family = str(target["signature"])
            score[f"{action_family}_action_loss"] -= math.log(
                action_probability
            )
            score[f"{action_family}_prior_loss"] -= math.log(
                max(1e-9, prior.get(actual, 0.0))
            )
            score[f"{action_family}_heuristic_loss"] -= math.log(
                max(1e-9, heuristic.get(actual, 0.0))
            )
            score[f"{action_family}_count"] += 1
            if (
                size_evidence.get("size_increment_applied")
                and size_prediction
            ):
                score["size_loss"] -= math.log(
                    max(1e-9, size_prediction.get(actual, 0.0))
                )
                score["size_action_loss"] -= math.log(action_probability)
                score["size_count"] += 1
        training.extend(
            record for record in records if record["hand_id"] == hand_id
        )
    groups = []
    for street, score in sorted(scores.items()):
        count = int(score["count"])
        size_count = int(score["size_count"])
        action_loss = score["action_loss"] / max(1, count)
        prior_loss = score["prior_loss"] / max(1, count)
        heuristic_loss = score["heuristic_loss"] / max(1, count)
        size_loss = (
            score["size_loss"] / size_count if size_count else None
        )
        size_action_loss = (
            score["size_action_loss"] / size_count
            if size_count
            else None
        )
        action_family_metrics = {}
        for action_family in ("call", "raise"):
            family_count = int(score[f"{action_family}_count"])
            family_action_loss = (
                score[f"{action_family}_action_loss"] / family_count
                if family_count
                else None
            )
            family_prior_loss = (
                score[f"{action_family}_prior_loss"] / family_count
                if family_count
                else None
            )
            family_heuristic_loss = (
                score[f"{action_family}_heuristic_loss"] / family_count
                if family_count
                else None
            )
            family_enabled = bool(
                family_count >= 12
                and family_action_loss is not None
                and family_prior_loss is not None
                and family_heuristic_loss is not None
                and family_action_loss + 0.002 < family_heuristic_loss
                and family_action_loss < family_prior_loss
            )
            family_gain = (
                max(0.0, family_heuristic_loss - family_action_loss)
                if family_action_loss is not None
                and family_heuristic_loss is not None
                else 0.0
            )
            family_weight = (
                min(
                    0.75,
                    math.sqrt(
                        min(1.0, family_count / 100.0)
                        * min(1.0, family_gain / 0.02)
                    ),
                )
                if family_enabled
                else 0.0
            )
            action_family_metrics[action_family] = {
                "enabled": family_enabled,
                "test_observations": family_count,
                "action_model_log_loss": family_action_loss,
                "strength_prior_log_loss": family_prior_loss,
                "heuristic_range_log_loss": family_heuristic_loss,
                "model_weight": family_weight,
            }
        action_enabled = (
            count >= 20
            and action_loss + 0.002 < heuristic_loss
            and action_loss < prior_loss
        )
        action_gain = max(0.0, heuristic_loss - action_loss)
        model_weight = (
            min(
                0.75,
                math.sqrt(
                    min(1.0, count / 100.0)
                    * min(1.0, action_gain / 0.02)
                ),
            )
            if action_enabled
            else 0.0
        )
        size_enabled = bool(
            action_enabled
            and size_count >= 20
            and size_loss is not None
            and size_action_loss is not None
            and size_loss + 0.005 < size_action_loss
        )
        groups.append(
            {
                "street": street,
                "enabled": action_enabled,
                "test_observations": count,
                "action_model_log_loss": round(action_loss, 4),
                "strength_prior_log_loss": round(prior_loss, 4),
                "heuristic_range_log_loss": round(
                    heuristic_loss, 4
                ),
                "log_loss_improvement": round(
                    heuristic_loss - action_loss, 4
                ),
                "model_weight": round(model_weight, 3),
                "call_enabled": action_family_metrics["call"]["enabled"],
                "call_test_observations": action_family_metrics["call"][
                    "test_observations"
                ],
                "call_action_model_log_loss": (
                    round(
                        action_family_metrics["call"][
                            "action_model_log_loss"
                        ],
                        4,
                    )
                    if action_family_metrics["call"][
                        "action_model_log_loss"
                    ]
                    is not None
                    else None
                ),
                "call_model_weight": round(
                    action_family_metrics["call"]["model_weight"], 3
                ),
                "raise_enabled": action_family_metrics["raise"]["enabled"],
                "raise_test_observations": action_family_metrics["raise"][
                    "test_observations"
                ],
                "raise_action_model_log_loss": (
                    round(
                        action_family_metrics["raise"][
                            "action_model_log_loss"
                        ],
                        4,
                    )
                    if action_family_metrics["raise"][
                        "action_model_log_loss"
                    ]
                    is not None
                    else None
                ),
                "raise_model_weight": round(
                    action_family_metrics["raise"]["model_weight"], 3
                ),
                "size_enabled": size_enabled,
                "size_test_observations": size_count,
                "size_model_log_loss": (
                    round(size_loss, 4)
                    if size_loss is not None
                    else None
                ),
                "action_only_log_loss": (
                    round(size_action_loss, 4)
                    if size_action_loss is not None
                    else None
                ),
                "size_log_loss_improvement": (
                    round(size_action_loss - size_loss, 4)
                    if size_loss is not None
                    and size_action_loss is not None
                    else None
                ),
            }
        )
    total = sum(group["test_observations"] for group in groups)
    enabled_observations = sum(
        (
            group["test_observations"]
            if group["enabled"]
            else group["call_test_observations"]
        )
        for group in groups
        if group["enabled"] or group["call_enabled"]
    )
    any_enabled = any(
        group["enabled"] or group["call_enabled"] for group in groups
    )
    return {
        "enabled": any_enabled,
        "train_hands": len(training_hands),
        "test_hands": len(hand_order) - split,
        "test_observations": total,
        "enabled_test_observations": enabled_observations,
        "enabled_coverage_pct": round(
            100 * enabled_observations / max(1, total), 1
        ),
        "groups": groups,
        "reason": (
            "至少一个街道的综合或 call 条件范围通过时间外门控"
            if any_enabled
            else "综合与 call 条件范围均未超过原继续范围启发式"
        ),
        "method": "prequential-split-continuation-range-gate-v2",
    }


def cached_action_line_range_quality(
    connection: sqlite3.Connection,
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Reuse the expensive global range gate across opponent selections."""

    return _cached_range_quality(
        connection,
        mode,
        _ACTION_LINE_QUALITY_CACHE,
        action_line_range_quality,
    )


def cached_continuation_range_quality(
    connection: sqlite3.Connection,
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Reuse the continuation gate until enough new showdowns arrive."""

    return _cached_range_quality(
        connection,
        mode,
        _CONTINUATION_QUALITY_CACHE,
        continuation_range_quality,
    )


def _cached_range_quality(
    connection: sqlite3.Connection,
    mode: Optional[str],
    cache: Dict[Tuple[str, str, int], Dict[str, Any]],
    compute: Any,
) -> Dict[str, Any]:
    database_row = connection.execute("PRAGMA database_list").fetchone()
    database_path = str(database_row[2] or "") if database_row else ""
    if not database_path:
        database_path = f"memory:{id(connection)}"
    row = connection.execute(
        """
        SELECT COUNT(DISTINCT s.hand_id)
        FROM showdown_observations s
        JOIN hands h ON h.hand_id = s.hand_id
        WHERE s.street IN ('flop','turn','river')
          AND h.excluded_from_stats = 0
          AND (? IS NULL OR h.game_mode = ?)
        """,
        (mode, mode),
    ).fetchone()
    eligible_hands = int(row[0] or 0) if row else 0
    cache_key = (
        database_path,
        str(mode or "all"),
        eligible_hands // 10,
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    result = compute(connection, mode)
    result["eligible_showdown_hands"] = eligible_hands
    result["refresh_bucket"] = eligible_hands // 10
    cache[cache_key] = result
    while len(cache) > 16:
        del cache[next(iter(cache))]
    return result


def _action_family_strength_prediction(
    training: Sequence[Dict[str, Any]],
    target: Dict[str, Any],
    *,
    use_size: bool,
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    prior = _strength_prior(training, target["street"])
    if not prior:
        return {}, {"applied": False}
    likelihoods, evidence = _action_likelihoods_by_strength(
        training,
        target["user_id"],
        target["street"],
        target["signature"],
        target.get("game_mode"),
        size_bucket=target.get("size_bucket") if use_size else None,
    )
    if not likelihoods:
        return prior, {**evidence, "applied": False}
    unnormalized = {
        label: probability * likelihoods.get(label, evidence["fallback"])
        for label, probability in prior.items()
    }
    total = sum(unnormalized.values())
    posterior = (
        {
            label: value / total
            for label, value in unnormalized.items()
        }
        if total > 0
        else prior
    )
    return posterior, {**evidence, "applied": True}


def _heuristic_continuation_strength_prediction(
    prior: Dict[str, float],
) -> Dict[str, float]:
    """Probabilistic proxy for the previous generic range-tightening rule."""

    multipliers = {
        "high_card": 0.65,
        "draw": 1.15,
        "pair": 1.15,
        "two_pair_plus": 1.50,
        "straight_plus": 1.75,
    }
    weighted = {
        label: probability * multipliers.get(label, 1.0)
        for label, probability in prior.items()
    }
    total = sum(weighted.values())
    return (
        {label: value / total for label, value in weighted.items()}
        if total > 0
        else prior
    )


def action_line_range_profile(
    connection: sqlite3.Connection,
    user_id: str,
    base_profile: Dict[str, Any],
    action_history: Sequence[Dict[str, Any]],
    seat: Any,
    board: Sequence[str],
    *,
    mode: Optional[str] = None,
    blockers: Sequence[str] = (),
    quality: Optional[Dict[str, Any]] = None,
    exclude_hand_id: Optional[str] = None,
    action_context_by_street: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Update a 169-class preflop range through observed postflop actions.

    Revealed hands only estimate bounded action likelihood ratios. The update is
    enabled for money decisions only after a chronological holdout beats the
    population strength prior.
    """

    quality = quality or action_line_range_quality(connection, mode)
    base_weights = {
        str(hand): max(0.0, float(weight))
        for hand, weight in (base_profile.get("weights") or {}).items()
    }
    if not quality.get("enabled"):
        return {
            "enabled": False,
            "weights": {},
            "updates": [],
            "quality": quality,
            "reason": str(
                quality.get("reason") or "行动线范围模型未通过时间外门"
            ),
            "method": "bounded-conditional-size-range-v5",
        }
    if not base_weights:
        return {
            "enabled": False,
            "weights": {},
            "updates": [],
            "quality": quality,
            "reason": "缺少翻前范围先验",
            "method": "bounded-conditional-size-range-v5",
        }
    by_street: Dict[str, List[str]] = defaultdict(list)
    for action in action_history:
        if action.get("seat") != seat:
            continue
        street = str(action.get("street") or "")
        normalized = _normalize_observed_action(action.get("action"))
        if street in STREETS and normalized:
            by_street[street].append(normalized)
    preflop_path = _normalized_action_path(
        "-".join(by_street.get("preflop") or [])
    )
    classes = _starting_hand_classes()
    raw = {
        item["hand"]: base_weights.get(item["hand"], 0.0) / 100.0
        for item in classes
    }
    target_frequency = sum(
        item["deal_probability"] * raw[item["hand"]] for item in classes
    )
    all_records = [
        record
        for record in _action_strength_records(connection, mode)
        if not exclude_hand_id or record["hand_id"] != exclude_hand_id
    ]
    updates = []
    blocked = tuple(dict.fromkeys([*board, *blockers]))
    action_context_by_street = action_context_by_street or {}
    for street in ("flop", "turn", "river"):
        actions = by_street.get(street) or []
        visible_count = STREET_CARD_COUNT[street]
        if not actions or len(board) < visible_count:
            continue
        action_path = _normalized_action_path("-".join(actions))
        signature = _coarse_action_signature(action_path)
        size_bucket = str(
            (action_context_by_street.get(street) or {}).get(
                "size_bucket"
            )
            or ""
        )
        likelihoods, evidence = _action_likelihoods_by_strength(
            all_records,
            user_id,
            street,
            signature,
            mode,
            action_path=action_path,
            preflop_path=preflop_path,
            size_bucket=size_bucket,
        )
        feature_quality = next(
            (
                group
                for group in quality.get("feature_groups") or []
                if group.get("feature_level")
                == evidence.get("feature_level")
            ),
            None,
        )
        has_feature_gates = bool(quality.get("feature_groups"))
        if (
            evidence.get("feature_level")
            in {"path_size", "action_family_size", "street_size"}
            and has_feature_gates
            and (
                feature_quality is None
                or not feature_quality.get("enabled")
            )
        ):
            preview_evidence = evidence
            likelihoods, evidence = _action_likelihoods_by_strength(
                all_records,
                user_id,
                street,
                signature,
                mode,
                action_path=action_path,
                preflop_path=preflop_path,
                size_bucket=None,
            )
            evidence = {
                **evidence,
                "gated_fallback_from": preview_evidence.get(
                    "feature_level"
                ),
                "preview_size_matching_samples": preview_evidence.get(
                    "matching_action_samples", 0
                ),
                "size_feature_quality": feature_quality,
            }
        elif feature_quality is not None:
            evidence = {
                **evidence,
                "size_feature_quality": feature_quality,
            }
        active_feature_quality = next(
            (
                group
                for group in quality.get("feature_groups") or []
                if group.get("feature_level")
                == evidence.get("feature_level")
            ),
            None,
        )
        if (
            has_feature_gates
            and (
                active_feature_quality is None
                or not active_feature_quality.get("enabled")
            )
        ):
            detailed_evidence = evidence
            fallback_chain = [
                level
                for level in (
                    detailed_evidence.get("gated_fallback_from"),
                    detailed_evidence.get("feature_level"),
                )
                if level
            ]
            selected_fallback = None
            fallback_sizes = (
                [size_bucket, None]
                if size_bucket and size_bucket != "none"
                else [None]
            )
            for fallback_size in fallback_sizes:
                fallback_likelihoods, fallback_evidence = (
                    _action_likelihoods_by_strength(
                        all_records,
                        user_id,
                        street,
                        signature,
                        mode,
                        action_path=None,
                        preflop_path=preflop_path,
                        size_bucket=fallback_size,
                    )
                )
                fallback_level = fallback_evidence.get(
                    "feature_level"
                )
                if fallback_level not in fallback_chain:
                    fallback_chain.append(fallback_level)
                fallback_quality = next(
                    (
                        group
                        for group in quality.get("feature_groups") or []
                        if group.get("feature_level")
                        == fallback_level
                    ),
                    None,
                )
                if (
                    fallback_likelihoods
                    and fallback_quality is not None
                    and fallback_quality.get("enabled")
                ):
                    selected_fallback = (
                        fallback_likelihoods,
                        fallback_evidence,
                        fallback_quality,
                    )
                    break
            if selected_fallback is not None:
                (
                    likelihoods,
                    fallback_evidence,
                    active_feature_quality,
                ) = selected_fallback
                evidence = {
                    **fallback_evidence,
                    "gated_fallback_from": (
                        detailed_evidence.get("gated_fallback_from")
                        or detailed_evidence.get("feature_level")
                    ),
                    "gate_fallback_chain": fallback_chain,
                    "feature_quality": active_feature_quality,
                    "preview_size_matching_samples": (
                        detailed_evidence.get(
                            "preview_size_matching_samples",
                            detailed_evidence.get(
                                "matching_action_samples", 0
                            ),
                        )
                    ),
                    "size_feature_quality": (
                        detailed_evidence.get("size_feature_quality")
                    ),
                    "feature_gate_blocked": False,
                }
            else:
                likelihoods = {}
                evidence = {
                    **detailed_evidence,
                    "gate_fallback_chain": fallback_chain,
                    "feature_quality": active_feature_quality,
                    "feature_gate_blocked": True,
                }
        if not likelihoods or evidence["population_samples"] < 12:
            updates.append(
                {
                    "street": street,
                    "signature": signature,
                    "action_path": action_path,
                    "preflop_path": preflop_path,
                    "size_bucket": size_bucket or None,
                    **evidence,
                    "applied": False,
                    "reason": (
                        "该特征层未通过独立时间外门控"
                        if evidence.get("feature_gate_blocked")
                        else "同类公开底牌行动不足"
                    ),
                }
            )
            continue
        visible_board = tuple(board[:visible_count])
        structural_likelihoods, structural_weight = (
            _structural_action_likelihoods(
                street,
                signature,
                action_path,
                size_bucket or "none",
            )
        )
        class_likelihoods = {}
        for item in classes:
            mix = _class_exploit_mix(
                item["hand"], visible_board, blocked
            )
            class_likelihoods[item["hand"]] = sum(
                probability
                * structural_likelihoods.get(label, 0.08)
                for label, probability in mix.items()
            )
        if likelihoods:
            statistical_scores = {}
            for item in classes:
                mix = _class_strength_mix(
                    item["hand"], visible_board, blocked
                )
                statistical_scores[item["hand"]] = sum(
                    probability
                    * likelihoods.get(label, evidence["fallback"])
                    for label, probability in mix.items()
                )
            statistical_peak = max(
                statistical_scores.values(),
                default=0.0,
            )
            if statistical_peak > 0:
                residual = 0.28 if quality.get("display_only") else 0.40
                for item in classes:
                    hand = item["hand"]
                    class_likelihoods[hand] *= 1.0 - residual + residual * (
                        statistical_scores[hand] / statistical_peak
                    )
        average = sum(
            item["deal_probability"]
            * max(0.0, raw[item["hand"]])
            * class_likelihoods[item["hand"]]
            for item in classes
        ) / max(
            1e-9,
            sum(
                item["deal_probability"] * max(0.0, raw[item["hand"]])
                for item in classes
            ),
        )
        update_weight = max(
            structural_weight,
            _statistical_action_update_weight(
                street,
                signature,
                size_bucket,
                evidence,
                display_only=bool(quality.get("display_only")),
            ),
        )
        _apply_strength_likelihood_update(
            raw,
            classes,
            class_likelihoods,
            update_weight,
            min_ratio=(
                0.04
                if signature in {"bet", "raise"}
                else 0.28
            ),
        )
        updates.append(
            {
                "street": street,
                "signature": signature,
                "action_path": action_path,
                "preflop_path": preflop_path,
                "size_bucket": size_bucket or None,
                **evidence,
                "mean_likelihood": round(average, 4),
                "update_weight": round(update_weight, 3),
                "applied": True,
            }
        )
    applied = any(update.get("applied") for update in updates)
    if applied:
        inclusion = {
            item["hand"]: min(1.0, max(0.0, raw[item["hand"]]))
            for item in classes
        }
        posterior_frequency = min(
            target_frequency,
            _inclusion_frequency(classes, inclusion),
        )
    else:
        inclusion = _calibrate_raw_propensities(
            classes,
            raw,
            max(0.001, min(0.999, target_frequency)),
        )
        posterior_frequency = target_frequency
    weights = {
        item["hand"]: round(100 * inclusion[item["hand"]], 2)
        for item in classes
    }
    changed = [
        hand
        for hand in weights
        if abs(weights[hand] - base_weights.get(hand, 0.0)) >= 0.5
    ]
    baseline_strength = _range_strength_distribution(
        base_weights,
        tuple(board),
        blocked,
    )
    posterior_strength = _range_strength_distribution(
        weights,
        tuple(board),
        blocked,
    )
    strength_labels = set(baseline_strength) | set(posterior_strength)
    strength_shift = {
        label: round(
            posterior_strength.get(label, 0.0)
            - baseline_strength.get(label, 0.0),
            1,
        )
        for label in strength_labels
    }
    class_shifts = sorted(
        (
            {
                "hand": hand,
                "baseline_pct": round(base_weights.get(hand, 0.0), 2),
                "posterior_pct": weights[hand],
                "delta_pp": round(
                    weights[hand] - base_weights.get(hand, 0.0), 2
                ),
            }
            for hand in weights
        ),
        key=lambda item: (-abs(item["delta_pp"]), item["hand"]),
    )[:12]
    enabled = bool(quality.get("enabled")) and any(
        update.get("applied") for update in updates
    )
    return {
        "enabled": enabled,
        "weights": weights if enabled else {},
        "preview_weights": weights,
        "estimated_range_pct": round(100 * posterior_frequency, 1),
        "preflop_range_pct": round(100 * target_frequency, 1),
        "confidence": (
            "medium"
            if enabled and sum(item.get("player_samples", 0) for item in updates) >= 20
            else "low"
        ),
        "updates": updates,
        "changed_classes": len(changed),
        "baseline_strength_distribution_pct": baseline_strength,
        "strength_distribution_pct": posterior_strength,
        "strength_shift_pp": dict(
            sorted(
                strength_shift.items(),
                key=lambda item: (-abs(item[1]), item[0]),
            )
        ),
        "top_class_shifts": class_shifts,
        "entropy_bits": {
            "baseline": _range_entropy_bits(
                base_weights, tuple(board), blocked
            ),
            "posterior": _range_entropy_bits(
                weights, tuple(board), blocked
            ),
        },
        "quality": quality,
        "production_training": {
            "eligible_showdown_observations": len(all_records),
            "scope": "all_completed_good_history",
            "current_hand_excluded": bool(exclude_hand_id),
            "validation_rows_reused_after_gate": True,
        },
        "reason": (
            "时间外门已通过，行动线后验可进入 EV"
            if enabled
            else str(quality.get("reason") or "行动线模型未启用")
        ),
        "method": "bounded-conditional-size-range-v5",
    }


def board_action_range_profile(
    base_weights: Dict[str, float],
    action_history: Sequence[Dict[str, Any]],
    seat: Any,
    board: Sequence[str],
    *,
    blockers: Sequence[str] = (),
    action_context_by_street: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Return a conservative board/action-conditioned display range.

    This deterministic fallback is intentionally kept out of the money-EV
    path. It prevents the opponent-node panel from presenting an unchanged
    preflop prior when a statistically trained feature level is still gated.
    """

    normalized_weights = {
        str(hand): max(0.0, float(weight))
        for hand, weight in base_weights.items()
    }
    if len(board) < 3 or not normalized_weights:
        return {
            "enabled": False,
            "weights": {},
            "updates": [],
            "method": "board-action-structural-v2",
            "reason": "翻牌前无需牌面行动回退",
        }
    classes = _starting_hand_classes()
    raw = {
        item["hand"]: normalized_weights.get(item["hand"], 0.0) / 100.0
        for item in classes
    }
    target_frequency = sum(
        item["deal_probability"] * raw[item["hand"]] for item in classes
    )
    by_street: Dict[str, List[str]] = defaultdict(list)
    for action in action_history:
        if action.get("seat") != seat:
            continue
        street = str(action.get("street") or "")
        normalized = _normalize_observed_action(action.get("action"))
        if street in STREETS and normalized:
            by_street[street].append(normalized)
    blocked = tuple(dict.fromkeys([*board, *blockers]))
    contexts = action_context_by_street or {}
    updates = []
    for street in ("flop", "turn", "river"):
        actions = by_street.get(street) or []
        visible_count = STREET_CARD_COUNT[street]
        if not actions or len(board) < visible_count:
            continue
        action_path = _normalized_action_path("-".join(actions))
        signature = _coarse_action_signature(action_path)
        size_bucket = str(
            (contexts.get(street) or {}).get("size_bucket") or "none"
        )
        likelihoods, update_weight = _structural_action_likelihoods(
            street,
            signature,
            action_path,
            size_bucket,
        )
        visible_board = tuple(board[:visible_count])
        class_likelihoods = {}
        weighted_score = 0.0
        weighted_mass = 0.0
        for item in classes:
            hand = item["hand"]
            available = _available_combo_count(hand, blocked)
            if available <= 0 or raw[hand] <= 0:
                class_likelihoods[hand] = 0.0
                continue
            mix = _class_exploit_mix(hand, visible_board, blocked)
            score = sum(
                probability * likelihoods.get(label, 0.08)
                for label, probability in mix.items()
            )
            class_likelihoods[hand] = score
            mass = available * raw[hand]
            weighted_score += mass * score
            weighted_mass += mass
        _apply_strength_likelihood_update(
            raw,
            classes,
            class_likelihoods,
            update_weight,
            min_ratio=0.03 if signature in {"bet", "raise"} else 0.22,
        )
        updates.append(
            {
                "street": street,
                "signature": signature,
                "action_path": action_path,
                "size_bucket": size_bucket,
                "feature_level": "board_action_structural",
                "mean_likelihood": round(
                    weighted_score / max(1e-9, weighted_mass),
                    4,
                ),
                "update_weight": round(update_weight, 3),
                "applied": True,
                "heuristic": True,
                "reason": "统计特征门控未通过，使用保守牌面/行动结构回退",
            }
        )
    if not updates:
        return {
            "enabled": False,
            "weights": {},
            "updates": [],
            "method": "board-action-structural-v2",
            "reason": "该玩家尚无可用于收紧范围的翻后行动",
        }
    inclusion = {
        item["hand"]: min(1.0, max(0.0, raw[item["hand"]]))
        for item in classes
    }
    posterior_frequency = min(
        target_frequency,
        _inclusion_frequency(classes, inclusion),
    )
    weights = {
        item["hand"]: round(100 * inclusion[item["hand"]], 2)
        for item in classes
    }
    class_shifts = sorted(
        (
            {
                "hand": hand,
                "baseline_pct": round(
                    normalized_weights.get(hand, 0.0), 2
                ),
                "posterior_pct": weight,
                "delta_pp": round(
                    weight - normalized_weights.get(hand, 0.0), 2
                ),
            }
            for hand, weight in weights.items()
        ),
        key=lambda item: (-abs(item["delta_pp"]), item["hand"]),
    )[:12]
    return {
        "enabled": True,
        "weights": weights,
        "updates": updates,
        "top_class_shifts": class_shifts,
        "confidence": "low",
        "method": "board-action-structural-v2",
        "estimated_range_pct": round(100 * posterior_frequency, 1),
        "preflop_range_pct": round(100 * target_frequency, 1),
        "reason": "低置信度牌面/行动结构回退；不进入 Money-EV",
    }


def _apply_strength_likelihood_update(
    raw: Dict[str, float],
    classes: Sequence[Dict[str, Any]],
    class_likelihoods: Dict[str, float],
    update_weight: float,
    *,
    min_ratio: float = 0.04,
) -> None:
    """Shrink a range toward hands that actually take the observed action.

    Likelihoods are normalized to the strongest class on this street, not to
    the range-wide mean. Mean-preserving updates keep preflop width and leave
    high-combo junk looking like the mode of the posterior.
    """

    peak = max(
        (
            float(class_likelihoods.get(item["hand"], 0.0) or 0.0)
            for item in classes
        ),
        default=0.0,
    )
    if peak <= 0:
        return
    exponent = max(0.0, min(1.0, float(update_weight)))
    floor = max(0.0, min(1.0, float(min_ratio)))
    for item in classes:
        hand = item["hand"]
        if raw[hand] <= 0:
            continue
        relative = max(
            floor,
            float(class_likelihoods.get(hand, 0.0) or 0.0) / peak,
        )
        raw[hand] *= relative ** exponent


def _inclusion_frequency(
    classes: Sequence[Dict[str, Any]],
    inclusion: Dict[str, float],
) -> float:
    return sum(
        item["deal_probability"]
        * max(0.0, min(1.0, float(inclusion.get(item["hand"], 0.0) or 0.0)))
        for item in classes
    )


def _statistical_action_update_weight(
    street: str,
    signature: str,
    size_bucket: str,
    evidence: Dict[str, Any],
    *,
    display_only: bool,
) -> float:
    individual_weight = min(
        0.15, float(evidence.get("player_samples") or 0) / 120.0
    )
    sample_weight = min(
        0.22, float(evidence.get("population_samples") or 0) / 500.0
    )
    if signature not in {"bet", "raise"}:
        return min(
            0.40 if display_only else 0.28,
            0.10 + sample_weight + individual_weight,
        )
    weight = 0.46 + sample_weight + individual_weight
    if signature == "raise":
        weight += 0.14
    if street == "river":
        weight += 0.12
    elif street == "turn":
        weight += 0.06
    if size_bucket in {"pot", "overbet"}:
        weight += 0.08
    if display_only:
        weight += 0.08
    return min(0.92, weight)


def _class_exploit_mix(
    hand_class: str,
    board: Tuple[str, ...],
    blocked: Tuple[str, ...],
) -> Dict[str, float]:
    counts = _class_exploit_counts(hand_class, board, blocked)
    total = sum(counts.values())
    if total <= 0:
        return {"air": 1.0}
    return {
        bucket: count / total
        for bucket, count in counts.items()
        if count > 0
    }


def _structural_action_likelihoods(
    street: str,
    signature: str,
    action_path: str,
    size_bucket: str,
) -> Tuple[Dict[str, float], float]:
    mappings = {
        "check": {
            "air": 1.0,
            "marginal_showdown": 0.90,
            "draws": 0.82,
            "strong_value": 0.58,
        },
        "call": {
            "air": 0.08,
            "marginal_showdown": 0.62,
            "draws": 0.88,
            "strong_value": 0.90,
        },
        "bet": {
            "air": 0.12,
            "marginal_showdown": 0.34,
            "draws": 0.72,
            "strong_value": 1.0,
        },
        "raise": {
            "air": 0.05,
            "marginal_showdown": 0.12,
            "draws": 0.52,
            "strong_value": 1.0,
        },
        "fold": {
            "air": 1.0,
            "marginal_showdown": 0.48,
            "draws": 0.28,
            "strong_value": 0.06,
        },
    }
    likelihoods = dict(mappings.get(signature) or mappings["check"])
    weights = {
        "check": 0.10,
        "call": 0.42,
        "bet": 0.52,
        "raise": 0.68,
        "fold": 0.38,
    }
    update_weight = weights.get(signature, 0.10)
    if street == "turn":
        update_weight += 0.06
    elif street == "river":
        update_weight += 0.16
        if signature == "bet":
            likelihoods.update(
                {
                    "air": 0.06,
                    "marginal_showdown": 0.16,
                    "draws": 0.04,
                    "strong_value": 1.0,
                }
            )
            update_weight = max(update_weight, 0.74)
        elif signature == "raise":
            likelihoods.update(
                {
                    "air": 0.04,
                    "marginal_showdown": 0.05,
                    "draws": 0.02,
                    "strong_value": 1.0,
                }
            )
            update_weight = max(update_weight, 0.86)
    if signature in {"bet", "raise"} and size_bucket in {"pot", "overbet"}:
        likelihoods["air"] *= 0.45
        likelihoods["marginal_showdown"] *= 0.62
        likelihoods["draws"] *= 0.70
        likelihoods["strong_value"] *= 1.05
        update_weight += 0.08
    if street == "river" and action_path.endswith("bet-raise"):
        update_weight = max(update_weight, 0.90)
    return likelihoods, min(0.92, update_weight)


def conditional_continuation_ranges(
    connection: sqlite3.Connection,
    user_id: str,
    base_weights: Dict[str, float],
    board: Sequence[str],
    *,
    street: str,
    mode: Optional[str] = None,
    blockers: Sequence[str] = (),
    quality: Optional[Dict[str, Any]] = None,
    exclude_hand_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build auditable call-or-raise ranges for each candidate size bucket."""

    quality = quality or continuation_range_quality(connection, mode)
    group = next(
        (
            item
            for item in quality.get("groups") or []
            if item.get("street") == street
        ),
        None,
    )
    if (
        street not in {"flop", "turn", "river"}
        or not group
        or not (group.get("enabled") or group.get("call_enabled"))
        or not base_weights
    ):
        return {
            "enabled": False,
            "buckets": {},
            "quality": group,
            "reason": (
                "该街道继续范围未通过时间外门控"
                if street in {"flop", "turn", "river"}
                else "翻前继续范围仍使用现有结构模型"
            ),
            "method": "split-action-conditioned-continuation-range-v2",
        }
    records = [
        record
        for record in _action_strength_records(connection, mode)
        if not exclude_hand_id or record["hand_id"] != exclude_hand_id
    ]
    classes = _starting_hand_classes()
    model_weight = max(
        0.0, min(0.75, float(group.get("model_weight") or 0.0))
    )
    call_model_weight = max(
        0.0,
        min(
            0.75,
            float(group.get("call_model_weight") or 0.0)
            if group.get("call_enabled")
            else 0.0,
        ),
    )
    raise_model_weight = max(
        0.0,
        min(
            0.75,
            float(group.get("raise_model_weight") or 0.0)
            if group.get("raise_enabled")
            else 0.0,
        ),
    )
    blocked = tuple(dict.fromkeys([*board, *blockers]))
    baseline_strength = _range_strength_distribution(
        base_weights,
        tuple(board),
        blocked,
    )
    buckets = {}
    for size_bucket in ("small", "medium", "pot", "overbet"):
        selected_size = size_bucket if group.get("size_enabled") else None
        call_likelihoods, call_evidence = (
            _action_likelihoods_by_strength(
                records,
                user_id,
                street,
                "call",
                mode,
                size_bucket=selected_size,
            )
        )
        raise_likelihoods, raise_evidence = (
            _action_likelihoods_by_strength(
                records,
                user_id,
                street,
                "raise",
                mode,
                size_bucket=selected_size,
            )
        )
        labels = set(call_likelihoods) | set(raise_likelihoods)
        if not labels:
            continue
        continue_likelihoods = {
            label: (
                call_likelihoods.get(
                    label, call_evidence.get("fallback", 0.0)
                )
                + raise_likelihoods.get(
                    label, raise_evidence.get("fallback", 0.0)
                )
            )
            for label in labels
        }
        combined_conditioned = _conditioned_range_weights(
            classes,
            base_weights,
            tuple(board),
            blocked,
            continue_likelihoods,
            model_weight,
        )
        call_conditioned = _conditioned_range_weights(
            classes,
            base_weights,
            tuple(board),
            blocked,
            call_likelihoods,
            call_model_weight,
        )
        raise_conditioned = _conditioned_range_weights(
            classes,
            base_weights,
            tuple(board),
            blocked,
            raise_likelihoods,
            raise_model_weight,
        )
        conditioned = (
            combined_conditioned
            if group.get("enabled")
            else call_conditioned
        )
        effective_model_weight = (
            model_weight if group.get("enabled") else call_model_weight
        )
        shifts = sorted(
            (
                {
                    "hand": hand,
                    "baseline_weight": round(
                        float(base_weights.get(hand, 0.0)), 3
                    ),
                    "conditioned_weight": value,
                    "multiplier": round(
                        value
                        / max(
                            1e-9, float(base_weights.get(hand, 0.0))
                        ),
                        3,
                    )
                    if float(base_weights.get(hand, 0.0)) > 0
                    else 0.0,
                }
                for hand, value in conditioned.items()
                if float(base_weights.get(hand, 0.0)) > 0
            ),
            key=lambda item: (-abs(item["multiplier"] - 1.0), item["hand"]),
        )[:12]
        buckets[size_bucket] = {
            "weights": conditioned,
            "call_weights": call_conditioned,
            "raise_weights": raise_conditioned,
            "call_enabled": bool(group.get("call_enabled")),
            "raise_enabled": bool(group.get("raise_enabled")),
            "baseline_strength_distribution_pct": baseline_strength,
            "strength_distribution_pct": _range_strength_distribution(
                conditioned,
                tuple(board),
                blocked,
            ),
            "entropy_bits": _range_entropy_bits(
                conditioned,
                tuple(board),
                blocked,
            ),
            "call_strength_distribution_pct": _range_strength_distribution(
                call_conditioned,
                tuple(board),
                blocked,
            ),
            "raise_strength_distribution_pct": _range_strength_distribution(
                raise_conditioned,
                tuple(board),
                blocked,
            ),
            "top_weight_shifts": shifts,
            "size_conditioned": bool(group.get("size_enabled")),
            "model_weight": round(effective_model_weight, 3),
            "call_model_weight": round(call_model_weight, 3),
            "raise_model_weight": round(raise_model_weight, 3),
            "call_evidence": call_evidence,
            "raise_evidence": raise_evidence,
        }
    return {
        "enabled": bool(buckets),
        "buckets": buckets,
        "quality": group,
        "model_weight": round(
            model_weight if group.get("enabled") else call_model_weight,
            3,
        ),
        "reason": (
            "call/raise 综合条件范围通过时间外门控"
            if group.get("enabled")
            else "call 条件范围单独通过时间外门控"
        ),
        "production_training": {
            "eligible_showdown_observations": len(records),
            "scope": "all_completed_good_history",
            "current_hand_excluded": bool(exclude_hand_id),
        },
        "method": "split-action-conditioned-continuation-range-v2",
    }


def _conditioned_range_weights(
    classes: Sequence[Dict[str, Any]],
    base_weights: Dict[str, float],
    board: Tuple[str, ...],
    blocked: Tuple[str, ...],
    likelihoods: Dict[str, float],
    model_weight: float,
) -> Dict[str, float]:
    if not likelihoods:
        return {}
    fallback = sum(likelihoods.values()) / max(1, len(likelihoods))
    class_scores = {}
    for item in classes:
        mix = _class_strength_mix(item["hand"], board, blocked)
        class_scores[item["hand"]] = sum(
            probability * likelihoods.get(label, fallback)
            for label, probability in mix.items()
        )
    weighted_mass = sum(
        item["deal_probability"]
        * max(0.0, float(base_weights.get(item["hand"], 0.0)))
        for item in classes
    )
    average_score = sum(
        item["deal_probability"]
        * max(0.0, float(base_weights.get(item["hand"], 0.0)))
        * class_scores[item["hand"]]
        for item in classes
    ) / max(1e-9, weighted_mass)
    return {
        item["hand"]: round(
            max(0.0, float(base_weights.get(item["hand"], 0.0)))
            * (
                1.0
                + model_weight
                * (
                    max(
                        0.35,
                        min(
                            3.0,
                            class_scores[item["hand"]]
                            / max(1e-9, average_score),
                        ),
                    )
                    - 1.0
                )
            ),
            4,
        )
        for item in classes
    }


def current_hand_action_contexts(
    connection: sqlite3.Connection,
    hand_id: str,
    max_sequence: Optional[int] = None,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Return the latest observed decision context for every player and street."""

    rows = connection.execute(
        """
        SELECT user_id, street, chosen_action, bet_fraction, raise_multiple,
               facing_action, to_call, pot_before, action_sequence
        FROM decision_snapshots
        WHERE hand_id = ? AND user_id IS NOT NULL
          AND chosen_action NOT IN ('ante','small_blind','big_blind','straddle')
          AND (? IS NULL OR action_sequence <= ?)
        ORDER BY action_sequence
        """,
        (hand_id, max_sequence, max_sequence),
    ).fetchall()
    result: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        result[str(row[0])][str(row[1])] = {
            "action": str(row[2] or ""),
            "bet_fraction": row[3],
            "size_bucket": _size_bucket(row[3]),
            "raise_multiple": row[4],
            "facing_action": str(row[5] or ""),
            "to_call": float(row[6] or 0.0),
            "pot_before": float(row[7] or 0.0),
            "action_sequence": int(row[8] or 0),
        }
    return {
        user_id: dict(streets) for user_id, streets in result.items()
    }


def _action_strength_records(
    connection: sqlite3.Connection,
    mode: Optional[str],
) -> List[Dict[str, Any]]:
    database_row = connection.execute("PRAGMA database_list").fetchone()
    database_path = str(database_row[2] if database_row else "")
    if not database_path:
        database_path = f"memory:{id(connection)}"
    revision_row = connection.execute(
        """
        SELECT COUNT(*), COALESCE(MAX(s.rowid), 0),
               COALESCE(SUM(h.excluded_from_stats), 0)
        FROM showdown_observations s
        JOIN hands h ON h.hand_id = s.hand_id
        WHERE s.street IN ('flop','turn','river')
          AND (? IS NULL OR h.game_mode = ?)
        """,
        (mode, mode),
    ).fetchone()
    revision = tuple(int(value or 0) for value in revision_row)
    cache_key = (database_path, mode, revision)
    with _ACTION_STRENGTH_RECORDS_CACHE_LOCK:
        cached = _ACTION_STRENGTH_RECORDS_CACHE.get(cache_key)
        if cached is not None:
            return cached
        records = _load_action_strength_records(connection, mode)
        _ACTION_STRENGTH_RECORDS_CACHE[cache_key] = records
        while len(_ACTION_STRENGTH_RECORDS_CACHE) > 16:
            _ACTION_STRENGTH_RECORDS_CACHE.pop(
                next(iter(_ACTION_STRENGTH_RECORDS_CACHE))
            )
        return records


def _load_action_strength_records(
    connection: sqlite3.Connection,
    mode: Optional[str],
) -> List[Dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT s.hand_id, s.user_id, s.street, s.strength_category,
               s.draws_json, s.action_line, h.game_mode, h.played_at,
               s.feature_tokens_json
        FROM showdown_observations s
        JOIN hands h ON h.hand_id = s.hand_id
        WHERE s.street IN ('flop','turn','river')
          AND h.excluded_from_stats = 0
          AND (? IS NULL OR h.game_mode = ?)
        ORDER BY h.played_at, s.hand_id,
                 CASE s.street
                   WHEN 'flop' THEN 1 WHEN 'turn' THEN 2 ELSE 3 END
        """,
        (mode, mode),
    ).fetchall()
    records = []
    for row in rows:
        action_path = _normalized_action_path(str(row[5] or ""))
        signature = _coarse_action_signature(action_path)
        if not signature:
            continue
        tokens = json.loads(row[8] or "[]")
        preflop_path = next(
            (
                str(token).split("=", 1)[1]
                for token in tokens
                if str(token).startswith("line_preflop=")
            ),
            "",
        )
        size_bucket = next(
            (
                str(token).split("=", 1)[1]
                for token in tokens
                if str(token).startswith("size=")
            ),
            "none",
        )
        records.append(
            {
                "hand_id": str(row[0]),
                "user_id": str(row[1]),
                "street": str(row[2]),
                "label": _strength_label(
                    str(row[3] or "high_card"),
                    (
                        {}
                        if str(row[2]) == "river"
                        else json.loads(row[4] or "{}")
                    ),
                ),
                "signature": signature,
                "action_path": action_path,
                "size_bucket": size_bucket,
                "path_size": f"{action_path}|{size_bucket}",
                "signature_size": f"{signature}|{size_bucket}",
                "preflop_path": _normalized_action_path(preflop_path),
                "game_mode": str(row[6] or ""),
                "played_at": str(row[7] or ""),
            }
        )
    return records


def _action_likelihoods_by_strength(
    records: Sequence[Dict[str, Any]],
    user_id: str,
    street: str,
    signature: str,
    mode: Optional[str],
    action_path: Optional[str] = None,
    preflop_path: Optional[str] = None,
    size_bucket: Optional[str] = None,
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    candidates = [
        record
        for record in records
        if record["street"] == street
        and (mode is None or record["game_mode"] == mode)
    ]
    matching_preflop = [
        record
        for record in candidates
        if preflop_path
        and record.get("preflop_path") == preflop_path
    ]
    uses_preflop_context = len(matching_preflop) >= 60
    if uses_preflop_context:
        candidates = matching_preflop
    labels = sorted({record["label"] for record in candidates})
    exact_path_samples = sum(
        record.get("action_path") == action_path for record in candidates
    )
    use_exact_path = bool(action_path) and exact_path_samples >= 12
    feature_key = "action_path" if use_exact_path else "signature"
    target_feature = action_path if use_exact_path else signature
    features = sorted(
        {
            str(record.get(feature_key) or "")
            for record in candidates
            if record.get(feature_key)
        }
    )
    if not labels or target_feature not in features:
        return {}, {
            "population_samples": len(candidates),
            "player_samples": 0,
            "fallback": 0.5,
            "feature_level": (
                "exact_path" if use_exact_path else "action_family"
            ),
            "preflop_context_used": uses_preflop_context,
        }
    population_by_label = Counter(record["label"] for record in candidates)
    population_joint = Counter(
        (record["label"], record.get(feature_key)) for record in candidates
    )
    player = [record for record in candidates if record["user_id"] == user_id]
    player_by_label = Counter(record["label"] for record in player)
    player_joint = Counter(
        (record["label"], record.get(feature_key)) for record in player
    )
    prior_strength = 10.0
    likelihoods = {}
    for label in labels:
        population_probability = (
            population_joint[(label, target_feature)] + 1
        ) / (population_by_label[label] + len(features))
        likelihoods[label] = (
            prior_strength * population_probability
            + player_joint[(label, target_feature)]
        ) / (prior_strength + player_by_label[label])
    size_level = None
    size_condition: List[Dict[str, Any]] = []
    valid_size = str(size_bucket or "")
    if valid_size and valid_size != "none":
        path_condition = [
            record
            for record in candidates
            if action_path
            and record.get("action_path") == action_path
        ]
        signature_condition = [
            record
            for record in candidates
            if signature and record.get("signature") == signature
        ]
        for level, condition in (
            ("path_size", path_condition),
            ("action_family_size", signature_condition),
            ("street_size", candidates),
        ):
            matching_size = sum(
                record.get("size_bucket") == valid_size
                for record in condition
            )
            observed_sizes = {
                record.get("size_bucket")
                for record in condition
                if record.get("size_bucket") not in {None, "", "none"}
            }
            if matching_size >= 12 and len(observed_sizes) >= 2:
                size_level = level
                size_condition = list(condition)
                break
    if size_level:
        observed_sizes = sorted(
            {
                str(record.get("size_bucket"))
                for record in size_condition
                if record.get("size_bucket") not in {None, "", "none"}
            }
        )
        global_size_probability = (
            sum(
                record.get("size_bucket") == valid_size
                for record in size_condition
            )
            + 1
        ) / (len(size_condition) + len(observed_sizes))
        population_condition_by_label = Counter(
            record["label"] for record in size_condition
        )
        population_size_by_label = Counter(
            record["label"]
            for record in size_condition
            if record.get("size_bucket") == valid_size
        )
        player_condition = [
            record
            for record in size_condition
            if record["user_id"] == user_id
        ]
        player_condition_by_label = Counter(
            record["label"] for record in player_condition
        )
        player_size_by_label = Counter(
            record["label"]
            for record in player_condition
            if record.get("size_bucket") == valid_size
        )
        size_population_prior = 12.0
        size_player_prior = 10.0
        for label in labels:
            population_size_probability = (
                population_size_by_label[label]
                + size_population_prior * global_size_probability
            ) / (
                population_condition_by_label[label]
                + size_population_prior
            )
            player_size_probability = (
                size_player_prior * population_size_probability
                + player_size_by_label[label]
            ) / (
                size_player_prior + player_condition_by_label[label]
            )
            size_ratio = player_size_probability / max(
                1e-9, global_size_probability
            )
            likelihoods[label] *= max(0.50, min(2.0, size_ratio))
    fallback = (sum(likelihoods.values()) / len(likelihoods))
    return likelihoods, {
        "population_samples": len(candidates),
        "player_samples": len(player),
        "matching_action_samples": (
            sum(
                record.get("size_bucket") == valid_size
                for record in size_condition
            )
            if size_level
            else sum(
                record.get(feature_key) == target_feature
                for record in candidates
            )
        ),
        "size_matching_samples": sum(
            record.get("size_bucket") == valid_size
            for record in size_condition
        ),
        "action_matching_samples": sum(
            record.get(feature_key) == target_feature
            for record in candidates
        ),
        "feature_level": size_level
        or ("exact_path" if use_exact_path else "action_family"),
        "action_feature_level": (
            "exact_path" if use_exact_path else "action_family"
        ),
        "size_increment_applied": bool(size_level),
        "size_condition_samples": len(size_condition),
        "preflop_context_used": uses_preflop_context,
        "fallback": fallback,
    }


def _strength_distribution_for_action(
    training: Sequence[Dict[str, Any]],
    target: Dict[str, Any],
) -> Dict[str, float]:
    prediction, _ = _strength_prediction(training, target)
    return prediction


def _strength_prediction(
    training: Sequence[Dict[str, Any]],
    target: Dict[str, Any],
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    prior = _strength_prior(training, target["street"])
    if not prior:
        return {}, {"feature_level": "prior_only", "applied": False}
    likelihoods, evidence = _action_likelihoods_by_strength(
        training,
        target["user_id"],
        target["street"],
        target["signature"],
        target.get("game_mode"),
        action_path=target.get("action_path"),
        preflop_path=target.get("preflop_path"),
        size_bucket=target.get("size_bucket"),
    )
    if not likelihoods:
        return prior, {**evidence, "applied": False}
    unnormalized = {
        label: probability * likelihoods.get(label, evidence["fallback"])
        for label, probability in prior.items()
    }
    total = sum(unnormalized.values())
    posterior = {
        label: value / total for label, value in unnormalized.items()
    } if total > 0 else prior
    calibration_weight = 0.30
    labels = set(prior) | set(posterior)
    prediction = {
        label: (
            (1.0 - calibration_weight) * prior.get(label, 0.0)
            + calibration_weight * posterior.get(label, 0.0)
        )
        for label in labels
    }
    return prediction, {**evidence, "applied": True}


def _strength_prior(
    records: Sequence[Dict[str, Any]],
    street: str,
) -> Dict[str, float]:
    labels = [
        record["label"] for record in records if record["street"] == street
    ]
    if not labels:
        return {}
    counts = Counter(labels)
    all_labels = sorted(counts)
    total = len(labels) + len(all_labels)
    return {
        label: (counts[label] + 1) / total for label in all_labels
    }


@lru_cache(maxsize=4096)
def _class_strength_mix(
    hand_class: str,
    board: Tuple[str, ...],
    blocked: Tuple[str, ...],
) -> Dict[str, float]:
    labels = Counter()
    blocked_cards = {str(card) for card in blocked}
    for hole_cards in _concrete_combos(hand_class):
        if any(card in blocked_cards for card in hole_cards):
            continue
        try:
            features = hand_features(list(hole_cards), list(board))
        except (TypeError, ValueError):
            continue
        labels[
            _strength_label(
                str(features.get("category") or "high_card"),
                {
                    key: bool(features.get(key))
                    for key in ("flush_draw", "open_ended_draw", "gutshot")
                },
            )
        ] += 1
    total = sum(labels.values())
    return (
        {label: count / total for label, count in labels.items()}
        if total
        else {"high_card": 1.0}
    )


def _range_strength_distribution(
    weights: Dict[str, float],
    board: Tuple[str, ...],
    blocked: Tuple[str, ...],
) -> Dict[str, float]:
    masses: Dict[str, float] = defaultdict(float)
    for hand_class, weight in weights.items():
        if weight <= 0:
            continue
        combo_count = available_starting_hand_combos(
            hand_class,
            blocked,
        )
        if combo_count <= 0:
            continue
        for label, fraction in _class_strength_mix(
            hand_class,
            board[:5],
            blocked,
        ).items():
            masses[label] += weight * combo_count * fraction
    total = sum(masses.values())
    return (
        {
            label: round(100 * mass / total, 1)
            for label, mass in sorted(masses.items())
        }
        if total > 0
        else {}
    )


def range_strength_distribution(
    weights: Dict[str, float],
    board: Sequence[str],
    blockers: Sequence[str] = (),
) -> Dict[str, float]:
    """Aggregate 169-class weights into blocker-aware made-hand buckets."""

    blocked = tuple(dict.fromkeys([*board, *blockers]))
    return _range_strength_distribution(
        weights,
        tuple(board),
        blocked,
    )


@lru_cache(maxsize=16)
def _relative_combo_buckets(
    board: Tuple[str, ...],
    blocked: Tuple[str, ...],
) -> Dict[Tuple[str, str], str]:
    """Classify legal combos by private-card value relative to the board."""

    blocked_cards = {str(card) for card in blocked}
    visible_board = list(board[:5])
    board_features = hand_features([], visible_board)
    board_category = int(board_features.get("category_rank") or 0)
    board_rank = tuple(board_features.get("rank") or ())
    draw_keys = ("flush_draw", "open_ended_draw", "gutshot")
    board_draws = {
        key: bool(board_features.get(key))
        for key in draw_keys
    }
    rows = []
    rank_counts = Counter()
    for item in _starting_hand_classes():
        for hole_cards in _concrete_combos(item["hand"]):
            if any(card in blocked_cards for card in hole_cards):
                continue
            try:
                features = hand_features(
                    list(hole_cards),
                    visible_board,
                )
            except (TypeError, ValueError):
                continue
            rank = tuple(features.get("rank") or ())
            combo = tuple(sorted(hole_cards))
            private_draw = len(visible_board) < 5 and any(
                bool(features.get(key)) and not board_draws[key]
                for key in draw_keys
            )
            rows.append(
                (
                    combo,
                    rank,
                    int(features.get("category_rank") or 0),
                    private_draw,
                )
            )
            rank_counts[rank] += 1
    total = sum(rank_counts.values())
    rank_percentiles = {}
    better = 0
    for rank, count in sorted(
        rank_counts.items(),
        reverse=True,
    ):
        rank_percentiles[rank] = (
            (better + 0.5 * count) / total if total else 1.0
        )
        better += count
    buckets = {}
    for combo, rank, category, private_draw in rows:
        percentile = rank_percentiles.get(rank, 1.0)
        improves_board = category > board_category or (
            len(visible_board) == 5
            and bool(board_rank)
            and rank > board_rank
        )
        if improves_board and category >= 2:
            bucket = "strong_value"
        elif private_draw:
            bucket = "draws"
        elif improves_board or (
            category >= 1 and percentile < 0.50
        ):
            bucket = "marginal_showdown"
        else:
            bucket = "air"
        buckets[combo] = bucket
    return buckets


@lru_cache(maxsize=4096)
def _class_exploit_counts(
    hand_class: str,
    board: Tuple[str, ...],
    blocked: Tuple[str, ...],
) -> Dict[str, int]:
    """Cache board-relative composition buckets shared by every opponent."""

    counts = {
        "strong_value": 0,
        "marginal_showdown": 0,
        "draws": 0,
        "air": 0,
    }
    blocked_cards = {str(card) for card in blocked}
    combo_buckets = _relative_combo_buckets(board, blocked)
    for hole_cards in _concrete_combos(hand_class):
        if any(card in blocked_cards for card in hole_cards):
            continue
        bucket = combo_buckets.get(tuple(sorted(hole_cards)))
        if bucket:
            counts[bucket] += 1
    return counts


def range_exploit_composition(
    weights: Dict[str, float],
    board: Sequence[str],
    blockers: Sequence[str] = (),
    *,
    aggressive_action: bool = False,
) -> Dict[str, Optional[float]]:
    """Split a range into mutually exclusive value, draw, and air buckets.

    Draws include pair-plus-draw combinations unless the made hand is already
    two pair or better. Air is only described as a bluff candidate after an
    observed aggressive action; range inference cannot prove intent.
    """

    blocked = tuple(dict.fromkeys([*board, *blockers]))
    masses = {
        "strong_value": 0.0,
        "marginal_showdown": 0.0,
        "draws": 0.0,
        "air": 0.0,
    }
    visible_board = tuple(board[:5])
    for hand_class, weight in weights.items():
        numeric_weight = max(0.0, float(weight))
        if numeric_weight <= 0:
            continue
        for bucket, combo_count in _class_exploit_counts(
            hand_class,
            visible_board,
            blocked,
        ).items():
            masses[bucket] += numeric_weight * combo_count
    total = sum(masses.values())
    percentages = {
        key: round(100 * value / total, 1) if total > 0 else 0.0
        for key, value in masses.items()
    }
    return {
        **percentages,
        "bluff_candidates": (
            percentages["air"] if aggressive_action else None
        ),
        "semi_bluff_candidates": (
            percentages["draws"] if aggressive_action else None
        ),
    }


def selected_opponent_range(
    profile: Dict[str, Any],
) -> Dict[str, Any]:
    """Select the same validated opponent range for every equity consumer."""

    posterior = profile.get("range_posterior") or {}
    preflop = profile.get("preflop_range") or {}
    if posterior.get("enabled") and posterior.get("weights"):
        return {
            **posterior,
            "player": profile.get("player"),
            "source": "action_line_posterior",
            "line": preflop.get("line"),
            "applied_streets": [
                update.get("street")
                for update in posterior.get("updates") or []
                if update.get("applied")
            ],
        }
    return {
        **preflop,
        "player": profile.get("player"),
        "source": (
            "preflop_range" if preflop.get("weights") else "uniform_random"
        ),
        "confidence": preflop.get("confidence") or "none",
        "line": preflop.get("line"),
        "applied_streets": [],
        "weights": preflop.get("weights") or {},
    }


def _range_entropy_bits(
    weights: Dict[str, float],
    board: Tuple[str, ...],
    blocked: Tuple[str, ...],
) -> Optional[float]:
    blocked_cards = {str(card) for card in blocked}
    combo_weights = [
        float(weight)
        for hand_class, weight in weights.items()
        if weight > 0
        for combo in _concrete_combos(hand_class)
        if not any(card in blocked_cards for card in combo)
    ]
    total = sum(combo_weights)
    if total <= 0:
        return None
    return round(
        -sum(
            (weight / total) * math.log2(weight / total)
            for weight in combo_weights
            if weight > 0
        ),
        4,
    )


def _concrete_combos(hand_class: str) -> List[Tuple[str, str]]:
    first, second = hand_class[0], hand_class[1]
    suits = "shdc"
    if len(hand_class) == 2:
        return [
            (f"{first}{left}", f"{second}{right}")
            for left, right in combinations(suits, 2)
        ]
    if hand_class.endswith("s"):
        return [(f"{first}{suit}", f"{second}{suit}") for suit in suits]
    return [
        (f"{first}{left}", f"{second}{right}")
        for left in suits
        for right in suits
        if left != right
    ]


@lru_cache(maxsize=4096)
def _available_combo_count(
    hand_class: str,
    blocked: Tuple[str, ...],
) -> int:
    blocked_cards = set(blocked)
    return sum(
        not any(card in blocked_cards for card in hole_cards)
        for hole_cards in _concrete_combos(hand_class)
    )


def available_starting_hand_combos(
    hand_class: str,
    blocked_cards: Sequence[str] = (),
) -> int:
    """Return legal concrete combos after board and known-card blockers."""

    return _available_combo_count(
        hand_class,
        tuple(dict.fromkeys(str(card) for card in blocked_cards)),
    )


def _strength_label(category: str, draws: Dict[str, Any]) -> str:
    if category == "high_card" and any(draws.values()):
        return "draw"
    if category in {"two_pair", "trips"}:
        return "two_pair_plus"
    if category in {
        "straight",
        "flush",
        "full_house",
        "quads",
        "straight_flush",
    }:
        return "straight_plus"
    return category


def _normalize_observed_action(value: Any) -> str:
    action = str(value or "").lower().replace(" ", "_").replace("-", "_")
    if action in {"allin", "all_in"}:
        return "all_in"
    return action if action in {"fold", "check", "call", "bet", "raise"} else ""


def _coarse_action_signature(action_line: str) -> str:
    actions = [
        _normalize_observed_action(value)
        for value in str(action_line or "").split("-")
    ]
    actions = [action for action in actions if action]
    if not actions:
        return ""
    if "fold" in actions:
        return "fold"
    if "raise" in actions or "all_in" in actions:
        return "raise"
    if "bet" in actions:
        return "bet"
    if actions[-1] == "call":
        return "call"
    return "check"


def _normalized_action_path(action_line: str) -> str:
    actions = [
        _normalize_observed_action(value)
        for value in str(action_line or "").split("-")
    ]
    actions = [action for action in actions if action]
    return "-".join(actions[-3:])


def player_profile_evidence(
    connection: sqlite3.Connection,
    user_id: str,
    mode: Optional[str] = None,
    limit: int = 16,
) -> Dict[str, Any]:
    """Return compact, anonymized shown-hand cases for manual LLM review."""
    rows = connection.execute(
        """
        SELECT s.hand_id, h.played_at, h.game_mode, s.hole_cards_json,
               s.position, s.effective_stack_bb, s.squid_count,
               s.profit_state, s.action_line, h.board_json, h.big_blind
        FROM showdown_observations s JOIN hands h ON h.hand_id = s.hand_id
        WHERE s.street = 'preflop' AND s.user_id = ?
          AND h.excluded_from_stats = 0
          AND (? IS NULL OR h.game_mode = ?)
        ORDER BY h.played_at DESC, s.hand_id DESC
        """,
        (user_id, mode, mode),
    ).fetchall()
    candidates = []
    for recency, row in enumerate(rows):
        shown_cards = json.loads(row[3] or "[]")
        board_runout = json.loads(row[9] or "[]")
        big_blind = float(row[10] or 0)
        hand_class = _canonical_starting_hand(shown_cards)
        if hand_class is None:
            continue
        opportunity_rows = connection.execute(
            """
            SELECT metric, success FROM opportunities
            WHERE hand_id = ? AND user_id = ?
            """,
            (row[0], user_id),
        ).fetchall()
        successful = {item[0] for item in opportunity_rows if int(item[1]) == 1}
        opportunities = {item[0] for item in opportunity_rows}
        semantic_lines = _shown_preflop_lines(
            str(row[8] or ""), successful, opportunities
        )
        raw_preflop_actions = [
            value for value in str(row[8] or "").split("-") if value
        ]
        if not semantic_lines:
            if "fold" in raw_preflop_actions:
                semantic_lines = ["fold"]
            elif "check" in raw_preflop_actions:
                semantic_lines = ["check"]
            else:
                semantic_lines = ["other"]
        strength_rows = connection.execute(
            """
            SELECT street, preflop_class, strength_category, draws_json
            FROM showdown_observations
            WHERE hand_id = ? AND user_id = ?
            ORDER BY CASE street
              WHEN 'preflop' THEN 0 WHEN 'flop' THEN 1
              WHEN 'turn' THEN 2 WHEN 'river' THEN 3 ELSE 4 END
            """,
            (row[0], user_id),
        ).fetchall()
        strength_by_street = {}
        for strength in strength_rows:
            draws = json.loads(strength[3] or "{}")
            strength_by_street[strength[0]] = {
                "preflop_class": strength[1] if strength[0] == "preflop" else None,
                "made_hand": strength[2] if strength[0] != "preflop" else None,
                "draws": sorted(key for key, value in draws.items() if value),
                "board": board_runout[
                    : STREET_CARD_COUNT.get(strength[0], 0)
                ],
            }
        action_rows = connection.execute(
            """
            SELECT street, chosen_action, facing_action, bet_fraction,
                   raise_multiple, players_in_hand, board_texture_json,
                   pot_before, to_call, facing_amount, spr, is_ip,
                   is_preflop_aggressor, position
            FROM decision_snapshots
            WHERE hand_id = ? AND user_id = ?
              AND chosen_action NOT IN ('ante','small_blind','big_blind','straddle')
            ORDER BY action_sequence
            """,
            (row[0], user_id),
        ).fetchall()
        action_line = []
        information_score = 2 * len(strength_by_street)
        for action in action_rows:
            texture = json.loads(action[6] or "{}")
            street = str(action[0])
            chosen_action = str(action[1])
            bet_fraction = (
                float(action[3]) if action[3] is not None else None
            )
            pot_before = float(action[7] or 0)
            to_call = float(action[8] or 0)
            facing_amount = float(action[9] or 0)
            strength = strength_by_street.get(street) or {}
            draws = list(strength.get("draws") or [])
            aggressive = chosen_action in {"bet", "raise", "all_in"}
            pattern_tags = []
            if (
                draws
                and to_call > 0
                and chosen_action in {"call", "raise", "all_in"}
            ):
                pattern_tags.append("draw_continue")
            if (
                not bool(action[12])
                and to_call <= 0
                and aggressive
            ):
                pattern_tags.append("non_pfa_open_aggression")
            if draws and aggressive:
                pattern_tags.append("draw_aggression")
            if bet_fraction is not None and bet_fraction > 1:
                pattern_tags.append("overbet")
            if bet_fraction is not None and bet_fraction <= 0.33 and aggressive:
                pattern_tags.append("small_bet")
            if (
                strength.get("made_hand") == "pair"
                and bet_fraction is not None
                and bet_fraction >= 0.75
                and aggressive
            ):
                pattern_tags.append("large_one_pair_bet")
            texture_summary = {
                key: texture.get(key)
                for key in (
                    "paired",
                    "monotone",
                    "two_tone",
                    "connected",
                    "broadway_count",
                )
                if key in texture
            }
            size = _size_bucket(action[3])
            raise_size = _raise_multiple_bucket(action[4])
            action_line.append(
                {
                    "street": street,
                    "position": normalize_preflop_position(action[13]),
                    "action": chosen_action,
                    "facing": action[2],
                    "size": size,
                    "bet_fraction_pct": (
                        round(100 * bet_fraction, 1)
                        if bet_fraction is not None
                        else None
                    ),
                    "raise_size": raise_size,
                    "raise_multiple": action[4],
                    "pot_before_bb": (
                        round(pot_before / big_blind, 1)
                        if big_blind > 0
                        else None
                    ),
                    "to_call_bb": (
                        round(to_call / big_blind, 1)
                        if big_blind > 0
                        else None
                    ),
                    "facing_amount_bb": (
                        round(facing_amount / big_blind, 1)
                        if big_blind > 0
                        else None
                    ),
                    "pot_odds_pct": (
                        round(100 * to_call / (pot_before + to_call), 1)
                        if to_call > 0 and pot_before + to_call > 0
                        else None
                    ),
                    "spr": (
                        round(float(action[10]), 2)
                        if action[10] is not None
                        else None
                    ),
                    "is_ip": (
                        bool(action[11])
                        if action[11] is not None
                        else None
                    ),
                    "is_preflop_aggressor": bool(action[12]),
                    "players_in_hand": action[5],
                    "board": board_runout[
                        : STREET_CARD_COUNT.get(street, 0)
                    ],
                    "board_texture": texture_summary,
                    "pattern_tags": pattern_tags,
                }
            )
            information_score += 0.5
            if size in {"pot", "overbet"} or raise_size in {"large", "huge"}:
                information_score += 1
            if action[1] in {"raise", "all_in"}:
                information_score += 0.5
            information_score += 0.5 * len(pattern_tags)
        if any(value in semantic_lines for value in ("three_bet", "open_raise")):
            information_score += 1
        candidates.append(
            {
                "_played_at": row[1] or "",
                "_recency": recency,
                "_score": information_score,
                "game_mode": row[2],
                "position": normalize_preflop_position(row[4]),
                "shown_cards": shown_cards,
                "shown_hand": hand_class,
                "board_runout": board_runout,
                "preflop_line": semantic_lines[-1],
                "effective_stack": _stack_bucket(row[5]),
                "squid_count": int(row[6] or 0),
                "prior_profit_state": row[7],
                "strength_by_street": strength_by_street,
                "actions": action_line,
            }
        )
    selected = sorted(
        candidates,
        key=lambda item: (-item["_score"], item["_recency"]),
    )[: max(1, min(int(limit), 24))]
    selected.sort(key=lambda item: item["_recency"])
    cases = []
    for index, item in enumerate(selected, 1):
        cases.append(
            {
                "case_id": f"case_{index:02d}",
                **{
                    key: value
                    for key, value in item.items()
                    if not key.startswith("_")
                },
            }
        )
    revealed_pattern_summary = []
    pattern_occurrences: Counter = Counter()
    pattern_hands: Dict[str, set] = defaultdict(set)
    pattern_examples: Dict[str, List[str]] = defaultdict(list)
    for index, item in enumerate(candidates, 1):
        tags_in_hand = set()
        for action in item.get("actions") or []:
            for tag in action.get("pattern_tags") or []:
                pattern_occurrences[tag] += 1
                tags_in_hand.add(tag)
        for tag in tags_in_hand:
            pattern_hands[tag].add(index)
    for case in cases:
        case_tags = {
            tag
            for action in case.get("actions") or []
            for tag in action.get("pattern_tags") or []
        }
        for tag in case_tags:
            pattern_examples[tag].append(str(case["case_id"]))
    for tag, occurrences in pattern_occurrences.most_common():
        revealed_pattern_summary.append(
            {
                "evidence_id": f"pattern:revealed_{tag}",
                "pattern": tag,
                "occurrences": occurrences,
                "hands": len(pattern_hands[tag]),
                "revealed_hands": len(candidates),
                "example_cases": pattern_examples.get(tag, [])[:4],
                "selection_bias": "showdown_only",
            }
        )
    return {
        "available_cases": len(candidates),
        "selected_cases": len(cases),
        "selection_method": "information_score_then_recency",
        "cases": cases,
        "postflop_patterns": _player_postflop_patterns(
            connection,
            user_id,
            mode,
        ),
        "revealed_pattern_summary": revealed_pattern_summary[:12],
        "limitations": [
            "案例只来自公开底牌，折叠且未公开的范围仍不可见",
            "为控制延迟，只发送信息量最高的匿名案例，不发送 hand_id 或时间戳",
        ],
    }


def _player_postflop_patterns(
    connection: sqlite3.Connection,
    user_id: str,
    mode: Optional[str],
) -> List[Dict[str, Any]]:
    """Compare common postflop initiative and sizing patterns with the pool."""

    rows = connection.execute(
        """
        SELECT d.hand_id, d.action_sequence, d.user_id, d.seat, d.street,
               d.chosen_action, d.to_call, d.bet_fraction,
               d.is_preflop_aggressor
        FROM decision_snapshots d
        JOIN hands h ON h.hand_id = d.hand_id
        WHERE h.excluded_from_stats = 0
          AND d.street IN ('flop', 'turn', 'river')
          AND d.user_id IS NOT NULL
          AND d.chosen_action NOT IN (
            'ante', 'small_blind', 'big_blind', 'straddle'
          )
          AND (? IS NULL OR d.game_mode = ?)
        ORDER BY d.hand_id, d.street, d.action_sequence
        """,
        (mode, mode),
    ).fetchall()
    grouped: Dict[Tuple[str, str], List[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[(str(row[0]), str(row[4]))].append(row)
    target: Dict[str, List[int]] = defaultdict(lambda: [0, 0])
    pool: Dict[str, List[int]] = defaultdict(lambda: [0, 0])

    def observe(key: str, player: str, success: bool) -> None:
        pool[key][0] += int(success)
        pool[key][1] += 1
        if player == user_id:
            target[key][0] += int(success)
            target[key][1] += 1

    aggressive_actions = {"bet", "raise", "all_in"}
    for (_hand_id, street), decisions in grouped.items():
        pfa_seat = next(
            (
                row[3]
                for row in decisions
                if bool(row[8]) and row[3] is not None
            ),
            None,
        )
        pfa_acted = False
        pfa_checked = False
        aggression_seen = False
        for row in decisions:
            player = str(row[2])
            seat = row[3]
            action = str(row[5] or "")
            to_call = float(row[6] or 0)
            bet_fraction = (
                float(row[7]) if row[7] is not None else None
            )
            aggressive = action in aggressive_actions
            if aggressive and bet_fraction is not None:
                observe(
                    f"{street}_small_bet",
                    player,
                    bet_fraction <= 0.33,
                )
                observe(
                    f"{street}_overbet",
                    player,
                    bet_fraction > 1.0,
                )
            if (
                pfa_seat is not None
                and seat != pfa_seat
                and to_call <= 0
                and not aggression_seen
            ):
                if not pfa_acted:
                    observe(
                        f"{street}_donk_lead",
                        player,
                        aggressive,
                    )
                elif pfa_checked:
                    observe(
                        f"{street}_probe_after_pfa_check",
                        player,
                        aggressive,
                    )
            if seat == pfa_seat:
                pfa_acted = True
                pfa_checked = action == "check"
            if aggressive:
                aggression_seen = True

    results = []
    for key, (successes, opportunities) in target.items():
        if opportunities <= 0:
            continue
        pool_successes, pool_opportunities = pool[key]
        posterior = round(100 * successes / opportunities, 1)
        population = round(
            100 * pool_successes / pool_opportunities,
            1,
        )
        delta = round(posterior - population, 1)
        confidence = (
            "high"
            if opportunities >= 30
            else "medium"
            if opportunities >= 12
            else "low"
            if opportunities >= 5
            else "very_low"
        )
        results.append(
            {
                "_score": (
                    abs(delta) * min(1.0, math.sqrt(opportunities / 20))
                    + min(opportunities, 20) / 4
                ),
                "evidence_id": f"pattern:{key}",
                "pattern": key,
                "successes": successes,
                "opportunities": opportunities,
                "rate_pct": posterior,
                "population_rate_pct": population,
                "delta_pp": delta,
                "confidence": confidence,
            }
        )
    return [
        {key: value for key, value in item.items() if key != "_score"}
        for item in sorted(
            results,
            key=lambda item: (-item["_score"], item["pattern"]),
        )[:12]
    ]


def normalize_preflop_position(value: Optional[str]) -> str:
    text = str(value or "").strip().upper().replace(" ", "_")
    if not text or text in {"ALL", "*"}:
        return "ALL"
    return POSITION_ALIASES.get(text, text)


def _raise_multiple_bucket(value: Optional[float]) -> str:
    if value is None:
        return "none"
    if value < 2.5:
        return "small"
    if value <= 3.5:
        return "standard"
    if value <= 5:
        return "large"
    return "huge"


def _starting_hand_classes() -> List[Dict[str, Any]]:
    result = []
    for row, first in enumerate(RANKS):
        for column, second in enumerate(RANKS):
            if row == column:
                hand = f"{first}{second}"
                combo_count = 6
            elif row < column:
                hand = f"{first}{second}s"
                combo_count = 4
            else:
                hand = f"{second}{first}o"
                combo_count = 12
            result.append(
                {
                    "hand": hand,
                    "row": row,
                    "column": column,
                    "combo_count": combo_count,
                    "deal_probability": combo_count / 1326,
                }
            )
    return result


def _structural_range_distribution(
    classes: Sequence[Dict[str, Any]], line: str, population_frequency: float
) -> Dict[str, float]:
    raw = {
        item["hand"]: math.exp(7.0 * _starting_hand_score(item["hand"], line))
        for item in classes
    }
    inclusion = _calibrate_raw_propensities(
        classes, raw, max(0.01, min(0.95, population_frequency))
    )
    total = sum(
        item["deal_probability"] * inclusion[item["hand"]] for item in classes
    )
    return {
        item["hand"]: item["deal_probability"] * inclusion[item["hand"]] / total
        for item in classes
    }


def _starting_hand_score(hand: str, line: str) -> float:
    first = RANKS.index(hand[0])
    second = RANKS.index(hand[1])
    high_index = min(first, second)
    low_index = max(first, second)
    high = (12 - high_index) / 12
    low = (12 - low_index) / 12
    pair = len(hand) == 2
    suited = hand.endswith("s")
    gap = max(0, low_index - high_index - 1)
    if pair:
        score = 0.58 + 0.38 * high
    else:
        score = 0.40 * high + 0.25 * low
        score += 0.09 if suited else 0.0
        score += max(0.0, 0.08 - 0.025 * gap)
        if high_index <= 3 and low_index <= 4:
            score += 0.08
        if high_index == 0:
            score += 0.05
    if line == "three_bet":
        score += 0.10 if pair else 0.0
        score += 0.08 if high_index <= 1 else 0.0
        score += 0.05 if suited and high_index == 0 else 0.0
        score -= 0.06 if low_index >= 8 and high_index >= 5 else 0.0
    elif line == "cold_call":
        score += 0.12 if pair else 0.0
        score += 0.06 if suited else 0.0
        score += 0.05 if suited and gap <= 1 else 0.0
        score -= 0.05 if not suited and low_index >= 7 else 0.0
    elif line == "limp":
        score += 0.10 if pair and high_index >= 5 else 0.0
        score += 0.08 if suited and gap <= 2 else 0.0
        score -= 0.08 if high_index <= 2 and low_index <= 4 else 0.0
    elif line == "open_raise":
        score += 0.03 if suited or pair else 0.0
    return max(0.02, min(1.15, score))


def _calibrate_raw_propensities(
    classes: Sequence[Dict[str, Any]],
    raw: Dict[str, float],
    target_frequency: float,
) -> Dict[str, float]:
    low, high = 0.0, 1.0
    while sum(
        item["deal_probability"] * min(1.0, high * raw[item["hand"]])
        for item in classes
    ) < target_frequency:
        high *= 2
        if high >= 1e9:
            break
    for _ in range(64):
        middle = (low + high) / 2
        frequency = sum(
            item["deal_probability"] * min(1.0, middle * raw[item["hand"]])
            for item in classes
        )
        if frequency < target_frequency:
            low = middle
        else:
            high = middle
    return {
        item["hand"]: min(1.0, high * raw[item["hand"]]) for item in classes
    }


def _calibrate_range_inclusion(
    classes: Sequence[Dict[str, Any]],
    distribution: Dict[str, float],
    target_frequency: float,
) -> Dict[str, float]:
    raw = {
        item["hand"]: distribution[item["hand"]] / item["deal_probability"]
        for item in classes
    }
    return _calibrate_raw_propensities(
        classes, raw, max(0.001, min(0.999, target_frequency))
    )


def _revealed_preflop_records(
    connection: sqlite3.Connection, mode: Optional[str]
) -> List[Dict[str, Any]]:
    database_row = connection.execute("PRAGMA database_list").fetchone()
    database_path = str(database_row[2] if database_row else "")
    if not database_path:
        database_path = f"memory:{id(connection)}"
    revision_row = connection.execute(
        """
        SELECT COUNT(*), COALESCE(MAX(s.rowid), 0),
               COALESCE(SUM(h.excluded_from_stats), 0)
        FROM showdown_observations s
        JOIN hands h ON h.hand_id = s.hand_id
        WHERE s.street = 'preflop'
          AND (? IS NULL OR h.game_mode = ?)
        """,
        (mode, mode),
    ).fetchone()
    revision = tuple(int(value or 0) for value in revision_row)
    cache_key = (database_path, mode, revision)
    with _REVEALED_PREFLOP_CACHE_LOCK:
        cached = _REVEALED_PREFLOP_CACHE.get(cache_key)
        if cached is not None:
            return cached
        rows = connection.execute(
            """
            SELECT s.hand_id, s.user_id, s.alias, s.position,
                   s.hole_cards_json, s.action_line,
                   GROUP_CONCAT(
                     DISTINCT CASE WHEN o.success = 1 THEN o.metric END
                   ) AS successful_metrics,
                   GROUP_CONCAT(DISTINCT o.metric) AS opportunity_metrics
            FROM showdown_observations s
            JOIN hands h ON h.hand_id = s.hand_id
            LEFT JOIN opportunities o
              ON o.hand_id = s.hand_id AND o.user_id = s.user_id
            WHERE s.street = 'preflop'
              AND h.excluded_from_stats = 0
              AND (? IS NULL OR h.game_mode = ?)
            GROUP BY s.hand_id, s.user_id, s.alias, s.position,
                     s.hole_cards_json, s.action_line
            """,
            (mode, mode),
        ).fetchall()
        result = []
        for row in rows:
            cards = json.loads(row[4] or "[]")
            hand_class = _canonical_starting_hand(cards)
            if hand_class is None:
                continue
            successful = set(filter(None, str(row[6] or "").split(",")))
            opportunities = set(filter(None, str(row[7] or "").split(",")))
            lines = _shown_preflop_lines(
                str(row[5] or ""),
                successful,
                opportunities,
            )
            if not lines:
                continue
            result.append(
                {
                    "hand_id": row[0],
                    "user_id": row[1],
                    "alias": row[2],
                    "position": normalize_preflop_position(row[3]),
                    "hand_class": hand_class,
                    "lines": lines,
                }
            )
        _REVEALED_PREFLOP_CACHE[cache_key] = result
        while len(_REVEALED_PREFLOP_CACHE) > 16:
            _REVEALED_PREFLOP_CACHE.pop(
                next(iter(_REVEALED_PREFLOP_CACHE))
            )
        return result


def _canonical_starting_hand(cards: Sequence[str]) -> Optional[str]:
    if len(cards) != 2:
        return None
    first, second = str(cards[0]), str(cards[1])
    if len(first) < 2 or len(second) < 2:
        return None
    ranks = (first[0].upper(), second[0].upper())
    if any(rank not in RANKS for rank in ranks):
        return None
    if ranks[0] == ranks[1]:
        return ranks[0] * 2
    high, low = sorted(ranks, key=RANKS.index)
    suited = first[-1].lower() == second[-1].lower()
    return f"{high}{low}{'s' if suited else 'o'}"


def _preflop_family(hand: str) -> str:
    first = RANKS.index(hand[0])
    second = RANKS.index(hand[1])
    if len(hand) == 2:
        if first <= 2:
            return "premium_pair"
        if first <= 7:
            return "middle_pair"
        return "small_pair"
    suited = hand.endswith("s")
    gap = max(first, second) - min(first, second) - 1
    high = min(first, second)
    low = max(first, second)
    if high == 0:
        return "suited_ace" if suited else "offsuit_ace"
    if high <= 3 and low <= 4:
        return "suited_broadway" if suited else "offsuit_broadway"
    if suited and gap <= 1:
        return "suited_connector"
    if suited and gap <= 3:
        return "suited_gapper"
    if suited:
        return "other_suited"
    if gap <= 1:
        return "offsuit_connector"
    return "other_offsuit"


def _shown_preflop_lines(
    action_line: str, successful: set, opportunities: set
) -> List[str]:
    actions = [action for action in action_line.split("-") if action]
    voluntary = [
        action for action in actions if action in {"call", "raise", "all_in", "bet"}
    ]
    if not voluntary:
        return []
    lines = ["vpip"]
    if "three_bet" in successful:
        lines.append("three_bet")
    elif "rfi" in successful:
        lines.append("open_raise")
    elif voluntary[0] == "call":
        lines.append("limp" if "rfi" in opportunities else "cold_call")
    elif voluntary[0] in {"raise", "all_in"}:
        lines.append("open_raise")
    return lines


def _preflop_line_frequency(
    connection: sqlite3.Connection,
    user_id: str,
    position: str,
    line: str,
    mode: Optional[str],
) -> Dict[str, Any]:
    records = _preflop_frequency_records(connection, line, mode)
    if position != "ALL":
        records = [record for record in records if record["position"] == position]
    population_successes = sum(record["success"] for record in records)
    population_trials = len(records)
    player_records = [record for record in records if record["user_id"] == user_id]
    successes = sum(record["success"] for record in player_records)
    trials = len(player_records)
    fallback = {
        "vpip": 0.30,
        "open_raise": 0.18,
        "three_bet": 0.07,
        "cold_call": 0.16,
        "limp": 0.12,
    }[line]
    population_mean = (
        (population_successes + 1) / (population_trials + 2)
        if population_trials
        else fallback
    )
    alpha = population_mean * 12 + successes
    beta = (1 - population_mean) * 12 + trials - successes
    mean = alpha / (alpha + beta)
    variance = alpha * beta / ((alpha + beta) ** 2 * (alpha + beta + 1))
    delta = 1.2816 * math.sqrt(variance)
    return {
        "successes": successes,
        "opportunities": trials,
        "observed_pct": round(100 * successes / trials, 1) if trials else None,
        "mean_pct": round(100 * mean, 1),
        "low_pct": round(100 * max(0.0, mean - delta), 1),
        "high_pct": round(100 * min(1.0, mean + delta), 1),
        "population_mean_pct": round(100 * population_mean, 1),
    }


def _preflop_frequency_records(
    connection: sqlite3.Connection, line: str, mode: Optional[str]
) -> List[Dict[str, Any]]:
    if line in {"vpip", "open_raise", "three_bet"}:
        metric = {
            "vpip": "vpip",
            "open_raise": "rfi",
            "three_bet": "three_bet",
        }[line]
        return [
            {
                "user_id": row[0],
                "position": normalize_preflop_position(row[1]),
                "success": int(row[2]),
            }
            for row in connection.execute(
                """
                SELECT o.user_id, o.position, o.success
                FROM opportunities o JOIN hands h ON h.hand_id = o.hand_id
                WHERE o.user_id IS NOT NULL AND o.metric = ?
                  AND h.excluded_from_stats = 0
                  AND (? IS NULL OR h.game_mode = ?)
                """,
                (metric, mode, mode),
            )
        ]
    if line == "limp":
        return [
            {
                "user_id": row[0],
                "position": normalize_preflop_position(row[1]),
                "success": int(row[2] == "call"),
            }
            for row in connection.execute(
                """
                SELECT o.user_id, o.position, d.chosen_action
                FROM opportunities o
                JOIN hands h ON h.hand_id = o.hand_id
                JOIN decision_snapshots d
                  ON d.hand_id = o.hand_id
                 AND d.action_sequence = o.action_sequence
                WHERE o.user_id IS NOT NULL AND o.metric = 'rfi'
                  AND h.excluded_from_stats = 0
                  AND (? IS NULL OR h.game_mode = ?)
                """,
                (mode, mode),
            )
        ]
    rows = connection.execute(
        """
        SELECT d.hand_id, d.user_id, d.position, d.chosen_action, d.to_call,
               d.action_sequence
        FROM decision_snapshots d JOIN hands h ON h.hand_id = d.hand_id
        WHERE d.user_id IS NOT NULL AND d.street = 'preflop'
          AND d.chosen_action NOT IN ('ante','small_blind','big_blind','straddle')
          AND h.excluded_from_stats = 0
          AND (? IS NULL OR h.game_mode = ?)
        ORDER BY d.hand_id, d.user_id, d.action_sequence
        """,
        (mode, mode),
    ).fetchall()
    first_decisions = {}
    for row in rows:
        first_decisions.setdefault((row[0], row[1]), row)
    return [
        {
            "user_id": row[1],
            "position": normalize_preflop_position(row[2]),
            "success": int(row[3] == "call"),
        }
        for row in first_decisions.values()
        if float(row[4] or 0) > 0
    ]


def _preflop_position_options(
    connection: sqlite3.Connection,
    user_id: str,
    shown: Sequence[Dict[str, Any]],
    mode: Optional[str],
) -> List[Dict[str, Any]]:
    revealed = Counter(
        record["position"] for record in shown if record["user_id"] == user_id
    )
    opportunities = Counter()
    for row in connection.execute(
        """
        SELECT o.position
        FROM opportunities o JOIN hands h ON h.hand_id = o.hand_id
        WHERE o.user_id = ? AND o.metric = 'vpip'
          AND h.excluded_from_stats = 0
          AND (? IS NULL OR h.game_mode = ?)
        """,
        (user_id, mode, mode),
    ):
        opportunities[normalize_preflop_position(row[0])] += 1
    names = sorted(
        (set(revealed) | set(opportunities)) - {"ALL", "UNKNOWN"},
        key=lambda name: (POSITION_ORDER.get(name, 99), name),
    )
    return [
        {
            "value": "ALL",
            "label": "全部位置",
            "opportunities": sum(opportunities.values()),
            "revealed_samples": sum(revealed.values()),
        },
        *[
            {
                "value": name,
                "label": name,
                "opportunities": opportunities[name],
                "revealed_samples": revealed[name],
            }
            for name in names
        ],
    ]


def _range_confidence(revealed_samples: int, opportunities: int) -> str:
    if revealed_samples < 3 or opportunities < 10:
        return "very_low"
    if revealed_samples < 8 or opportunities < 30:
        return "low"
    if revealed_samples < 20 or opportunities < 100:
        return "medium"
    return "high"


def contextual_tendencies(
    connection: sqlite3.Connection, user_id: str
) -> Dict[str, Any]:
    net_before = _cumulative_net_before(connection)
    rows = connection.execute(
        """
        SELECT d.hand_id, d.street, d.position, d.chosen_action,
               d.effective_stack_bb, d.bet_fraction, h.big_blind,
               COALESCE(s.squid_start, 0), hp.net
        FROM decision_snapshots d
        JOIN hands h ON h.hand_id = d.hand_id
        LEFT JOIN hand_squid_players s
          ON s.hand_id = d.hand_id AND s.user_id = d.user_id
        LEFT JOIN hand_players hp
          ON hp.hand_id = d.hand_id AND hp.user_id = d.user_id
        WHERE d.user_id = ? AND h.excluded_from_stats = 0
          AND d.chosen_action NOT IN ('ante', 'small_blind', 'big_blind', 'straddle')
        ORDER BY h.played_at, d.action_sequence
        """,
        (user_id,),
    ).fetchall()
    buckets: Dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        cumulative = net_before.get((row[0], user_id), 0.0)
        profit = _profit_state(cumulative / row[6] if row[6] else cumulative)
        contexts = (
            f"位置:{_clean_position(row[2])}",
            f"码深:{_stack_bucket(row[4])}",
            f"鱿鱼:{'有' if row[7] else '无'}",
            f"盈亏:{profit}",
        )
        for context in contexts:
            buckets[context][row[3]] += 1
    contexts = {
        context: {
            "samples": sum(actions.values()),
            "actions": dict(actions.most_common()),
        }
        for context, actions in buckets.items()
    }
    showdown_rows = connection.execute(
        """
        SELECT street, preflop_class, strength_category, equity_vs_random
        FROM showdown_observations WHERE user_id = ?
        """,
        (user_id,),
    ).fetchall()
    preflop_classes = Counter(
        row[1] for row in showdown_rows if row[0] == "preflop"
    )
    strength_by_street: Dict[str, Counter] = defaultdict(Counter)
    equity_by_street: Dict[str, List[float]] = defaultdict(list)
    for row in showdown_rows:
        strength_by_street[row[0]][row[2]] += 1
        equity_by_street[row[0]].append(float(row[3]))
    return {
        "contexts": contexts,
        "showdowns": {
            "samples": len(
                connection.execute(
                    "SELECT DISTINCT hand_id, user_id FROM showdown_observations WHERE user_id = ?",
                    (user_id,),
                ).fetchall()
            ),
            "preflop_classes": dict(preflop_classes.most_common()),
            "strength_by_street": {
                street: dict(counts.most_common())
                for street, counts in strength_by_street.items()
            },
            "average_equity_by_street": {
                street: round(sum(values) / len(values), 4)
                for street, values in equity_by_street.items()
            },
        },
    }


def _training_rows(connection: sqlite3.Connection) -> List[Dict[str, Any]]:
    columns = (
        "hand_id",
        "user_id",
        "alias",
        "street",
        "preflop_class",
        "strength_category",
        "draws_json",
        "equity_vs_random",
        "feature_tokens_json",
        "action_line",
    )
    return [
        {
            **dict(zip(columns, row)),
            "draws": json.loads(row[6]),
            "tokens": json.loads(row[8]),
        }
        for row in connection.execute(
            """
            SELECT hand_id, user_id, alias, street, preflop_class,
                   strength_category, draws_json, equity_vs_random,
                   feature_tokens_json, action_line
            FROM showdown_observations
            """
        )
    ]


def _hand_targets(
    connection: sqlite3.Connection, hand_id: str
) -> List[Dict[str, Any]]:
    net_before = _cumulative_net_before(connection)
    hand = connection.execute(
        "SELECT big_blind FROM hands WHERE hand_id = ?", (hand_id,)
    ).fetchone()
    big_blind = hand[0] if hand else None
    rows = connection.execute(
        """
        SELECT d.user_id, d.alias, d.street, d.position, d.chosen_action,
               d.bet_fraction, d.effective_stack_bb, d.is_ip,
               d.players_in_hand, d.board_texture_json, d.action_sequence,
               COALESCE(s.squid_start, 0)
        FROM decision_snapshots d
        LEFT JOIN hand_squid_players s
          ON s.hand_id = d.hand_id AND s.user_id = d.user_id
        WHERE d.hand_id = ? AND d.user_id IS NOT NULL
        ORDER BY d.user_id, d.action_sequence
        """,
        (hand_id,),
    ).fetchall()
    grouped: Dict[str, Dict[str, List[sqlite3.Row]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        if row[4] not in FORCED_ACTIONS:
            grouped[row[0]][row[2]].append(row)
    targets = []
    for user_id, by_street in grouped.items():
        lines: Dict[str, str] = {}
        for street in STREETS:
            decisions = by_street.get(street) or []
            if not decisions:
                continue
            last = decisions[-1]
            lines[street] = "-".join(row[4] for row in decisions)
            cumulative = net_before.get((hand_id, user_id), 0.0)
            profit = _profit_state(
                cumulative / big_blind if big_blind else cumulative
            )
            targets.append(
                {
                    "hand_id": hand_id,
                    "user_id": user_id,
                    "alias": last[1],
                    "street": street,
                    "action_line": lines[street],
                    "tokens": _feature_tokens(
                        street,
                        last[3],
                        last[4],
                        last[5],
                        last[6],
                        last[7],
                        last[8],
                        json.loads(last[9] or "{}"),
                        int(last[11]),
                        profit,
                        lines,
                    ),
                }
            )
    return targets


def _predict(
    records: Sequence[Dict[str, Any]],
    target: Dict[str, Any],
    exclude_hand: Optional[str] = None,
) -> Dict[str, Any]:
    candidates = [
        record
        for record in records
        if record["street"] == target["street"]
        and (exclude_hand is None or record["hand_id"] != exclude_hand)
    ]
    labels = sorted({_target_label(record) for record in candidates})
    if not labels:
        return {
            "user_id": target["user_id"],
            "alias": target.get("alias"),
            "street": target["street"],
            "action_line": target.get("action_line"),
            "distribution": {},
            "top_label": None,
            "expected_equity": None,
            "confidence": "none",
            "training_samples": 0,
            "player_samples": 0,
        }
    label_counts = Counter(_target_label(record) for record in candidates)
    token_counts: Dict[str, Counter] = defaultdict(Counter)
    player_counts = Counter()
    equity_by_label: Dict[str, List[float]] = defaultdict(list)
    for record in candidates:
        label = _target_label(record)
        equity_by_label[label].append(float(record["equity_vs_random"]))
        for token in set(record["tokens"]):
            token_counts[label][token] += 1
        if record["user_id"] == target["user_id"]:
            player_counts[label] += 1
    total = len(candidates)
    logs: Dict[str, float] = {}
    for label in labels:
        count = label_counts[label]
        score = math.log((count + 1) / (total + len(labels)))
        for token in set(target["tokens"]):
            score += math.log((token_counts[label][token] + 1) / (count + 2))
        if sum(player_counts.values()):
            score += 0.65 * math.log(
                (player_counts[label] + 1)
                / (sum(player_counts.values()) + len(labels))
            )
        logs[label] = score
    maximum = max(logs.values())
    weights = {label: math.exp(score - maximum) for label, score in logs.items()}
    normalizer = sum(weights.values())
    distribution = {
        label: round(weight / normalizer, 4) for label, weight in weights.items()
    }
    top_label = max(distribution, key=distribution.get)
    expected_equity = sum(
        probability
        * (
            sum(equity_by_label[label]) / len(equity_by_label[label])
            if equity_by_label[label]
            else 0
        )
        for label, probability in distribution.items()
    )
    exact_samples = sum(
        1
        for record in candidates
        if record.get("action_line") == target.get("action_line")
    )
    return {
        "user_id": target["user_id"],
        "alias": target.get("alias"),
        "street": target["street"],
        "action_line": target.get("action_line"),
        "distribution": dict(
            sorted(distribution.items(), key=lambda item: item[1], reverse=True)
        ),
        "top_label": top_label,
        "expected_equity": round(expected_equity, 4),
        "confidence": _prediction_confidence(total, exact_samples),
        "training_samples": total,
        "matching_line_samples": exact_samples,
        "player_samples": sum(player_counts.values()),
    }


def _target_label(record: Dict[str, Any]) -> str:
    if record["street"] == "preflop":
        return record["preflop_class"]
    category = record["strength_category"]
    draws = record.get("draws") or {}
    if category == "high_card" and any(draws.values()):
        return "draw"
    if category in {"two_pair", "trips"}:
        return "two_pair_plus"
    if category in {"straight", "flush", "full_house", "quads", "straight_flush"}:
        return "straight_plus"
    return category


def _feature_tokens(
    street: str,
    position: Optional[str],
    action: str,
    bet_fraction: Optional[float],
    stack_bb: Optional[float],
    is_ip: Optional[int],
    players: int,
    texture: Dict[str, Any],
    squid_count: int,
    profit_state: str,
    lines: Dict[str, str],
) -> List[str]:
    tokens = [
        f"street={street}",
        f"position={_clean_position(position)}",
        f"action={action}",
        f"size={_size_bucket(bet_fraction)}",
        f"stack={_stack_bucket(stack_bb)}",
        f"ip={is_ip}",
        f"pot={'heads_up' if players <= 2 else 'multiway'}",
        f"squid={'yes' if squid_count > 0 else 'no'}",
        f"squid_count={min(squid_count, 3)}",
        f"profit={profit_state}",
    ]
    for line_street, line in lines.items():
        tokens.append(f"line_{line_street}={line}")
    for key in ("paired", "monotone", "two_tone", "connected"):
        if texture.get(key):
            tokens.append(f"board_{key}=yes")
    return tokens


def _cumulative_net_before(
    connection: sqlite3.Connection,
) -> Dict[Tuple[str, str], float]:
    result: Dict[Tuple[str, str], float] = {}
    totals: Dict[str, float] = defaultdict(float)
    for row in connection.execute(
        """
        SELECT h.hand_id, hp.user_id, hp.net
        FROM hands h JOIN hand_players hp ON hp.hand_id = h.hand_id
        WHERE h.excluded_from_stats = 0 AND hp.user_id IS NOT NULL
        ORDER BY h.played_at, h.hand_number
        """
    ):
        result[(row[0], row[1])] = totals[row[1]]
        totals[row[1]] += float(row[2] or 0)
    return result


def _profit_state(net_bb: float) -> str:
    if net_bb <= -50:
        return "deep_loss"
    if net_bb <= -10:
        return "losing"
    if net_bb >= 50:
        return "big_win"
    if net_bb >= 10:
        return "winning"
    return "neutral"


def _stack_bucket(stack_bb: Optional[float]) -> str:
    return stack_bucket(stack_bb)


def _size_bucket(fraction: Optional[float]) -> str:
    if fraction is None:
        return "none"
    if fraction <= 0.33:
        return "small"
    if fraction <= 0.75:
        return "medium"
    if fraction <= 1.1:
        return "pot"
    return "overbet"


def _prediction_confidence(samples: int, exact_samples: int) -> str:
    if samples < 10 or exact_samples < 2:
        return "very_low"
    if samples < 30 or exact_samples < 5:
        return "low"
    if samples < 100:
        return "medium"
    return "high"


def _clean_position(value: Optional[str]) -> str:
    text = str(value or "").strip()
    return text or "Unknown"
