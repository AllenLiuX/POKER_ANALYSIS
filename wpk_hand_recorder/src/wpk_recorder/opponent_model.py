from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .features import stack_bucket


FORCED_ACTIONS = {"ante", "small_blind", "big_blind", "straddle"}
SIZE_BUCKETS = ("tiny", "small", "medium", "large", "overbet")
FACING_ACTIONS = ("fold", "call", "raise")
OPEN_ACTIONS = ("check", "bet")
_ACTION_RESPONSE_QUALITY_CACHE: Dict[
    Tuple[str, str, int], Dict[str, Any]
] = {}
_JOINT_FOLD_QUALITY_CACHE: Dict[
    Tuple[str, str, int], Dict[str, Any]
] = {}


FALLBACK_MEANS = {
    "vpip": 0.32,
    "pfr": 0.20,
    "rfi": 0.18,
    "three_bet": 0.08,
    "four_bet": 0.035,
    "fold_to_three_bet": 0.55,
    "flop_cbet": 0.62,
    "turn_cbet": 0.48,
    "river_cbet": 0.45,
    "fold_to_flop_cbet": 0.45,
    "fold_to_turn_cbet": 0.42,
    "fold_to_river_cbet": 0.48,
    "fold_to_flop_bet": 0.42,
    "fold_to_turn_bet": 0.40,
    "fold_to_river_bet": 0.45,
    "fold_to_flop_raise": 0.52,
    "fold_to_turn_raise": 0.55,
    "fold_to_river_raise": 0.58,
    "flop_donk": 0.12,
    "turn_donk": 0.18,
    "river_donk": 0.20,
    "turn_probe": 0.42,
    "river_probe": 0.45,
    "turn_delayed_cbet": 0.50,
    "turn_barrel": 0.50,
    "river_barrel": 0.45,
    "river_triple_barrel": 0.35,
    "fold_to_turn_delayed_cbet": 0.45,
    "fold_to_turn_barrel": 0.42,
    "fold_to_river_barrel": 0.48,
    "call_vs_flop_bet": 0.48,
    "call_vs_turn_bet": 0.50,
    "call_vs_river_bet": 0.47,
    "raise_vs_flop_bet": 0.10,
    "raise_vs_turn_bet": 0.09,
    "raise_vs_river_bet": 0.08,
}


def opponent_metrics(
    connection: sqlite3.Connection,
    user_id: str,
    game_mode: str,
    *,
    position: Optional[str] = None,
    effective_stack_bb: Optional[float] = None,
    minimum_trials: int = 2,
    limit: int = 16,
) -> List[Dict[str, Any]]:
    """Hierarchical Beta posteriors with context fallback and player shrinkage."""

    rows = connection.execute(
        """
        SELECT o.user_id, o.metric, o.success, o.position, o.effective_stack_bb
        FROM opportunities o JOIN hands h ON h.hand_id = o.hand_id
        WHERE h.excluded_from_stats = 0
          AND (? = '' OR o.game_mode = ?)
        """,
        (game_mode, game_mode),
    ).fetchall()
    by_metric = defaultdict(list)
    for row in rows:
        by_metric[str(row[1])].append(
            {
                "user_id": str(row[0]) if row[0] is not None else None,
                "success": int(row[2] or 0),
                "position": str(row[3] or ""),
                "stack_bucket": _stack_bucket(row[4]),
            }
        )

    target_position = str(position or "")
    target_stack = _stack_bucket(effective_stack_bb)
    results = []
    for metric, records in by_metric.items():
        player_all = [record for record in records if record["user_id"] == user_id]
        if len(player_all) < minimum_trials:
            continue
        contextual = [
            record
            for record in records
            if _context_matches(record, target_position, target_stack)
        ]
        population = contextual if len(contextual) >= 20 else records
        population_mean = (
            (sum(record["success"] for record in population) + 1)
            / (len(population) + 2)
            if population
            else FALLBACK_MEANS.get(metric, 0.5)
        )
        player_exact = [
            record
            for record in player_all
            if _context_matches(record, target_position, target_stack)
        ]
        other_weight = 0.25 if len(player_exact) < 5 else 0.0
        exact_successes = sum(record["success"] for record in player_exact)
        other = [record for record in player_all if record not in player_exact]
        weighted_successes = exact_successes + other_weight * sum(
            record["success"] for record in other
        )
        effective_trials = len(player_exact) + other_weight * len(other)
        prior_strength = 16.0
        alpha = population_mean * prior_strength + weighted_successes
        beta = (
            (1.0 - population_mean) * prior_strength
            + effective_trials
            - weighted_successes
        )
        mean = alpha / (alpha + beta)
        low, high = _normal_beta_interval(alpha, beta)
        successes = sum(record["success"] for record in player_all)
        results.append(
            {
                "metric": metric,
                "successes": successes,
                "opportunities": len(player_all),
                "context_opportunities": len(player_exact),
                "effective_context_samples": round(effective_trials, 2),
                "observed_pct": round(100 * successes / len(player_all), 1),
                "mean_pct": round(100 * mean, 1),
                "low_pct": round(100 * low, 1),
                "high_pct": round(100 * high, 1),
                "population_mean_pct": round(100 * population_mean, 1),
                "confidence": _confidence(effective_trials),
                "method": "hierarchical-beta-context-v1",
            }
        )
    results.sort(
        key=lambda item: (
            -item["context_opportunities"],
            -item["opportunities"],
            item["metric"],
        )
    )
    return results[: max(1, int(limit))]


def contextual_action_response(
    connection: sqlite3.Connection,
    user_id: str,
    game_mode: str,
    *,
    street: str,
    position: Optional[str] = None,
    players_in_hand: Optional[int] = None,
    spr: Optional[float] = None,
    is_ip: Optional[bool] = None,
    faced_bet: bool = True,
    bet_fraction: Optional[float] = None,
    facing_action: Optional[str] = None,
    board_texture: Optional[Dict[str, Any]] = None,
    quality: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Estimate a general player response with hierarchical context shrinkage.

    This model intentionally learns actions rather than player archetype labels.
    A calling station, nit, maniac, or balanced player is represented by the
    same posterior contract and can change behavior across nodes.
    """

    records = _decision_records(connection, street, faced_bet)
    return _contextual_action_response_from_records(
        records,
        user_id,
        game_mode,
        street=street,
        position=position,
        players_in_hand=players_in_hand,
        spr=spr,
        is_ip=is_ip,
        faced_bet=faced_bet,
        bet_fraction=bet_fraction,
        facing_action=facing_action,
        board_texture=board_texture,
        quality=quality,
    )


def contextual_action_responses(
    connection: sqlite3.Connection,
    players: Sequence[Dict[str, Any]],
    game_mode: str,
    *,
    street: str,
    players_in_hand: Optional[int] = None,
    spr: Optional[float] = None,
    faced_bet: bool = True,
    facing_action: Optional[str] = None,
    board_texture: Optional[Dict[str, Any]] = None,
    quality: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Batch variant that scans historical decisions once per live node."""

    records = _decision_records(connection, street, faced_bet)
    result = {}
    for player in players:
        user_id = str(player.get("user_id") or "")
        if not user_id:
            continue
        result[user_id] = _contextual_action_response_from_records(
            records,
            user_id,
            game_mode,
            street=street,
            position=player.get("position"),
            players_in_hand=players_in_hand,
            spr=spr,
            is_ip=player.get("is_ip"),
            faced_bet=faced_bet,
            facing_action=facing_action,
            board_texture=board_texture,
            quality=quality,
        )
    return result


def _contextual_action_response_from_records(
    records: List[Dict[str, Any]],
    user_id: str,
    game_mode: str,
    *,
    street: str,
    position: Optional[str],
    players_in_hand: Optional[int],
    spr: Optional[float],
    is_ip: Optional[bool],
    faced_bet: bool,
    bet_fraction: Optional[float] = None,
    facing_action: Optional[str],
    board_texture: Optional[Dict[str, Any]],
    quality: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    actions = FACING_ACTIONS if faced_bet else OPEN_ACTIONS
    target = {
        "game_mode": str(game_mode or ""),
        "position": str(position or ""),
        "players_bucket": _players_bucket(players_in_hand),
        "spr_bucket": _spr_bucket(spr),
        "is_ip": None if is_ip is None else bool(is_ip),
        "size_bucket": (
            _size_bucket(bet_fraction) if bet_fraction is not None else None
        ),
        "facing_action": _normalized_action(facing_action),
        "board_texture": dict(board_texture or {}),
    }
    personal = _response_posterior(records, user_id, actions, target)
    population = _response_posterior(
        records, "__population_only__", actions, target
    )
    group_quality = _response_group_quality(quality, street, faced_bet)
    player_quality = _player_response_quality(group_quality, user_id)
    group_quality_summary = (
        {
            key: value
            for key, value in group_quality.items()
            if key != "players"
        }
        if group_quality
        else None
    )
    group_residual_enabled = quality is None or bool(
        (group_quality or {}).get("enabled")
    )
    mature_player_gate = bool(
        player_quality
        and int(player_quality.get("test_observations") or 0) >= 20
    )
    residual_enabled = bool(
        group_residual_enabled
        and (
            not mature_player_gate
            or bool(player_quality.get("enabled"))
        )
    )
    aggregate = personal if residual_enabled else population
    size_curve: Dict[str, Dict[str, Any]] = {}
    if faced_bet:
        for size_bucket in SIZE_BUCKETS:
            sized_target = {**target, "size_bucket": size_bucket}
            personal_size = _response_posterior(
                records, user_id, actions, sized_target
            )
            population_size = _response_posterior(
                records,
                "__population_only__",
                actions,
                sized_target,
            )
            posterior = (
                personal_size if residual_enabled else population_size
            )
            size_curve[size_bucket] = {
                "probabilities_pct": posterior["probabilities_pct"],
                "population_probabilities_pct": posterior[
                    "population_probabilities_pct"
                ],
                "effective_samples": posterior["effective_samples"],
                "exact_samples": posterior["exact_samples"],
                "confidence": posterior["confidence"],
            }
    drift = _player_strategy_drift(
        records,
        user_id,
        actions,
        target,
        population_prior=population,
    )
    drift_enabled = bool(
        (group_quality or {}).get("drift_enabled")
        and drift.get("status") == "shifted"
    )
    drift_weight = 0.0
    if drift_enabled:
        aggregate, drift_weight = _blend_response_with_drift(
            aggregate,
            drift,
            actions,
        )
        population_probabilities = aggregate.get(
            "population_probabilities_pct"
        ) or {}
        aggregate["deviation_pp"] = {
            action: round(
                float(aggregate["probabilities_pct"].get(action) or 0.0)
                - float(population_probabilities.get(action) or 0.0),
                1,
            )
            for action in actions
        }
        for size_bucket, row in size_curve.items():
            adjusted, _ = _blend_response_with_drift(
                row,
                drift,
                actions,
            )
            size_curve[size_bucket] = adjusted
        drift = {
            **drift,
            "used_for_prediction": True,
            "blend_weight": round(drift_weight, 3),
            "quality": group_quality_summary,
            "reason": "近期漂移模型通过时间外门控，已有限混入响应后验",
        }
    personal_raise_size = _raise_size_posterior(
        records, user_id, target, street
    )
    population_raise_size = _raise_size_posterior(
        records, "__population_only__", target, street
    )
    player_raise_size_enabled = quality is None or bool(
        (group_quality or {}).get("raise_size_player_enabled")
    )
    population_raise_size_enabled = quality is None or bool(
        (group_quality or {}).get("raise_size_population_enabled")
    )
    selected_raise_size = (
        personal_raise_size
        if player_raise_size_enabled
        else population_raise_size
    )
    raise_size_enabled = bool(
        player_raise_size_enabled or population_raise_size_enabled
    )
    return {
        **aggregate,
        "street": street,
        "faced_bet": faced_bet,
        "context": {
            "game_mode": game_mode,
            "position": position,
            "players_bucket": target["players_bucket"],
            "spr_bucket": target["spr_bucket"],
            "is_ip": target["is_ip"],
            "size_bucket": target["size_bucket"],
            "facing_action": target["facing_action"],
            "board_texture": target["board_texture"],
        },
        "size_curve": size_curve,
        "player_residual_enabled": residual_enabled,
        "player_residual_gate": (
            {
                **player_quality,
                "used_for_prediction": mature_player_gate,
                "fallback": (
                    "player_residual"
                    if residual_enabled
                    else "population_context"
                ),
            }
            if player_quality
            else {
                "status": "insufficient_player_holdout",
                "used_for_prediction": False,
                "fallback": (
                    "group_validated_shrunk_residual"
                    if residual_enabled
                    else "population_context"
                ),
            }
        ),
        "player_evidence_samples": personal["effective_samples"],
        "ungated_player_deviation_pp": personal["deviation_pp"],
        "residual_quality": group_quality_summary,
        "strategy_drift": drift,
        "raise_size_model": {
            **selected_raise_size,
            "enabled": raise_size_enabled,
            "player_residual_enabled": player_raise_size_enabled,
            "population_enabled": population_raise_size_enabled,
            "personal_expected_multiple": personal_raise_size[
                "expected_multiple"
            ],
            "population_expected_multiple": population_raise_size[
                "expected_multiple"
            ],
            "quality": group_quality_summary,
            "method": "hierarchical-log-raise-size-v1",
        },
        "production_training": {
            "eligible_decision_observations": len(records),
            "scope": "all_completed_good_history",
            "validation_rows_reused_after_gate": True,
        },
        "method": "hierarchical-player-gated-action-and-size-v5",
    }


def response_probability(
    response_model: Dict[str, Any],
    action: str,
    bet_fraction: Optional[float] = None,
) -> Optional[float]:
    """Return a posterior action probability, preferring matched size evidence."""

    source = response_model
    if bet_fraction is not None:
        source = (response_model.get("size_curve") or {}).get(
            _size_bucket(bet_fraction),
            response_model,
        )
    value = (source.get("probabilities_pct") or {}).get(action)
    try:
        return max(0.0, min(1.0, float(value) / 100.0))
    except (TypeError, ValueError):
        return None


def exploit_directives(response_model: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Translate posterior residuals into auditable, type-agnostic exploits."""

    confidence = str(response_model.get("confidence") or "very_low")
    if confidence not in {"medium", "high"}:
        return []
    deviations = response_model.get("deviation_pp") or {}
    directives = []
    fold_deviation = float(deviations.get("fold") or 0.0)
    raise_deviation = float(deviations.get("raise") or 0.0)
    if fold_deviation <= -6:
        directives.append(
            {
                "leak": "continues_too_wide",
                "adjustment": "expand_value_reduce_bluffs",
                "magnitude_pp": round(abs(fold_deviation), 1),
                "confidence": confidence,
                "reason": "该节点弃牌率低于同类牌池后验",
            }
        )
    elif fold_deviation >= 6:
        directives.append(
            {
                "leak": "folds_too_often",
                "adjustment": "expand_blocker_bluffs",
                "magnitude_pp": round(fold_deviation, 1),
                "confidence": confidence,
                "reason": "该节点弃牌率高于同类牌池后验",
            }
        )
    if raise_deviation >= 5:
        directives.append(
            {
                "leak": "raises_too_often",
                "adjustment": "protect_checks_trap_and_defend_wider",
                "magnitude_pp": round(raise_deviation, 1),
                "confidence": confidence,
                "reason": "该节点加注率高于同类牌池后验",
            }
        )
    elif raise_deviation <= -5:
        directives.append(
            {
                "leak": "raises_too_tightly",
                "adjustment": "thin_value_bet_and_overfold_to_raises",
                "magnitude_pp": round(abs(raise_deviation), 1),
                "confidence": confidence,
                "reason": "该节点加注率低于同类牌池后验",
            }
        )
    size_curve = response_model.get("size_curve") or {}
    small = size_curve.get("small") or {}
    overbet = size_curve.get("overbet") or {}
    if (
        float(small.get("effective_samples") or 0) >= 5
        and float(overbet.get("effective_samples") or 0) >= 5
    ):
        small_fold = float(
            (small.get("probabilities_pct") or {}).get("fold") or 0
        )
        overbet_fold = float(
            (overbet.get("probabilities_pct") or {}).get("fold") or 0
        )
        elasticity = overbet_fold - small_fold
        if elasticity <= 5:
            directives.append(
                {
                    "leak": "size_insensitive_continue",
                    "adjustment": "increase_strong_value_size",
                    "magnitude_pp": round(abs(elasticity), 1),
                    "confidence": min(
                        confidence,
                        str(overbet.get("confidence") or confidence),
                        key=lambda value: {
                            "very_low": 0,
                            "low": 1,
                            "medium": 2,
                            "high": 3,
                        }.get(value, 0),
                    ),
                    "reason": "小注到超池下注的弃牌率变化很小",
                }
            )
        elif elasticity >= 15:
            directives.append(
                {
                    "leak": "overfolds_to_large_sizes",
                    "adjustment": "polarize_large_bets",
                    "magnitude_pp": round(elasticity, 1),
                    "confidence": confidence,
                    "reason": "超池下注显著提高其弃牌率",
                }
            )
    return directives


def action_response_backtest(
    connection: sqlite3.Connection,
    game_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Chronological player-residual vs context-only population backtest."""

    groups = []
    total_model_loss = 0.0
    total_population_loss = 0.0
    total_model_correct = 0
    total_population_correct = 0
    total_scored = 0
    total_selective_loss = 0.0
    total_group_policy_loss = 0.0
    total_selective_scored = 0
    for street in ("preflop", "flop", "turn", "river"):
        for faced_bet in (False, True):
            records = _decision_records(connection, street, faced_bet)
            if game_mode:
                records = [
                    record
                    for record in records
                    if record["game_mode"] == game_mode
                ]
            records.sort(
                key=lambda record: (
                    record.get("played_at") or "",
                    record.get("hand_id") or "",
                )
            )
            hand_order = list(
                dict.fromkeys(record["hand_id"] for record in records)
            )
            if len(hand_order) < 6:
                continue
            split = max(
                1, min(len(hand_order) - 1, int(len(hand_order) * 0.7))
            )
            train_hands = set(hand_order[:split])
            raw_training = [
                record
                for record in records
                if record["hand_id"] in train_hands
            ]
            tests_by_hand = {
                hand_id: [
                    record
                    for record in records
                    if record["hand_id"] == hand_id
                ]
                for hand_id in hand_order[split:]
            }
            actions = FACING_ACTIONS if faced_bet else OPEN_ACTIONS
            model_loss = 0.0
            population_loss = 0.0
            model_correct = 0
            population_correct = 0
            scored = 0
            drift_model_loss = 0.0
            drift_baseline_loss = 0.0
            drift_population_model_loss = 0.0
            drift_population_baseline_loss = 0.0
            drift_scored = 0
            raise_size_model_error = 0.0
            raise_size_population_error = 0.0
            raise_size_default_error = 0.0
            raise_size_scored = 0
            player_gate_scores: Dict[str, Dict[str, Any]] = defaultdict(
                lambda: {
                    "validation_count": 0,
                    "validation_model_loss": 0.0,
                    "validation_population_loss": 0.0,
                    "selected_after_validation": None,
                    "evaluation_count": 0,
                    "evaluation_model_loss": 0.0,
                    "evaluation_population_loss": 0.0,
                }
            )
            for hand_id in hand_order[split:]:
                training = _reindex_recency_weights(raw_training)
                hand_targets = tests_by_hand.get(hand_id) or []
                for target_record in hand_targets:
                    target = {
                        key: target_record.get(key)
                        for key in (
                            "game_mode",
                            "position",
                            "players_bucket",
                            "spr_bucket",
                            "is_ip",
                            "size_bucket",
                            "facing_action",
                            "board_texture",
                        )
                    }
                    model = _response_posterior(
                        training,
                        target_record["user_id"],
                        actions,
                        target,
                    )
                    population = _response_posterior(
                        training,
                        "__population_only__",
                        actions,
                        target,
                    )
                    actual = target_record["action"]
                    actual_raise_multiple = (
                        _valid_raise_multiple(
                            target_record.get("raise_multiple")
                        )
                        if actual == "raise"
                        else None
                    )
                    if actual_raise_multiple is not None:
                        personal_raise_size = _raise_size_posterior(
                            training,
                            target_record["user_id"],
                            target,
                            street,
                        )
                        population_raise_size = _raise_size_posterior(
                            training,
                            "__population_only__",
                            target,
                            street,
                        )
                        actual_log_size = math.log(actual_raise_multiple)
                        raise_size_model_error += abs(
                            math.log(
                                float(
                                    personal_raise_size[
                                        "expected_multiple"
                                    ]
                                )
                            )
                            - actual_log_size
                        )
                        raise_size_population_error += abs(
                            math.log(
                                float(
                                    population_raise_size[
                                        "expected_multiple"
                                    ]
                                )
                            )
                            - actual_log_size
                        )
                        raise_size_default_error += abs(
                            math.log(3.5 if street == "preflop" else 3.0)
                            - actual_log_size
                        )
                        raise_size_scored += 1
                    drift = _player_strategy_drift(
                        training,
                        target_record["user_id"],
                        actions,
                        target,
                        population_prior=population,
                    )
                    drift_model, drift_weight = (
                        _blend_response_with_drift(
                            model,
                            drift,
                            actions,
                        )
                    )
                    drift_population_model, _ = (
                        _blend_response_with_drift(
                            population,
                            drift,
                            actions,
                        )
                    )
                    model_probabilities = {
                        key: value / 100
                        for key, value in model[
                            "probabilities_pct"
                        ].items()
                    }
                    population_probabilities = {
                        key: value / 100
                        for key, value in population[
                            "probabilities_pct"
                        ].items()
                    }
                    model_observation_loss = -math.log(
                        max(
                            1e-9,
                            model_probabilities.get(actual, 0.0),
                        )
                    )
                    population_observation_loss = -math.log(
                        max(
                            1e-9,
                            population_probabilities.get(actual, 0.0),
                        )
                    )
                    player_gate = player_gate_scores[
                        str(target_record["user_id"])
                    ]
                    if player_gate["validation_count"] < 12:
                        player_gate["validation_count"] += 1
                        player_gate[
                            "validation_model_loss"
                        ] += model_observation_loss
                        player_gate[
                            "validation_population_loss"
                        ] += population_observation_loss
                        if player_gate["validation_count"] == 12:
                            player_gate["selected_after_validation"] = bool(
                                player_gate["validation_model_loss"]
                                + 0.002
                                * player_gate["validation_count"]
                                < player_gate["validation_population_loss"]
                            )
                    else:
                        player_gate["evaluation_count"] += 1
                        player_gate[
                            "evaluation_model_loss"
                        ] += model_observation_loss
                        player_gate[
                            "evaluation_population_loss"
                        ] += population_observation_loss
                    if drift_weight > 0:
                        drift_probabilities = {
                            key: value / 100
                            for key, value in drift_model[
                                "probabilities_pct"
                            ].items()
                        }
                        drift_model_loss -= math.log(
                            max(
                                1e-9,
                                drift_probabilities.get(actual, 0.0),
                            )
                        )
                        drift_baseline_loss -= math.log(
                            max(
                                1e-9,
                                model_probabilities.get(actual, 0.0),
                            )
                        )
                        drift_population_probabilities = {
                            key: value / 100
                            for key, value in drift_population_model[
                                "probabilities_pct"
                            ].items()
                        }
                        drift_population_model_loss -= math.log(
                            max(
                                1e-9,
                                drift_population_probabilities.get(
                                    actual, 0.0
                                ),
                            )
                        )
                        drift_population_baseline_loss -= math.log(
                            max(
                                1e-9,
                                population_probabilities.get(actual, 0.0),
                            )
                        )
                        drift_scored += 1
                    model_loss += model_observation_loss
                    population_loss += population_observation_loss
                    model_correct += int(
                        max(
                            model_probabilities,
                            key=model_probabilities.get,
                        )
                        == actual
                    )
                    population_correct += int(
                        max(
                            population_probabilities,
                            key=population_probabilities.get,
                        )
                        == actual
                    )
                    scored += 1
                raw_training.extend(hand_targets)
            if not scored:
                continue
            total_model_loss += model_loss
            total_population_loss += population_loss
            total_model_correct += model_correct
            total_population_correct += population_correct
            total_scored += scored
            model_mean = model_loss / scored
            population_mean = population_loss / scored
            group_enabled = (
                scored >= 30 and model_mean + 0.002 < population_mean
            )
            drift_model_mean = (
                drift_model_loss / drift_scored
                if drift_scored
                else None
            )
            drift_baseline_mean = (
                drift_baseline_loss / drift_scored
                if drift_scored
                else None
            )
            drift_population_model_mean = (
                drift_population_model_loss / drift_scored
                if drift_scored
                else None
            )
            drift_population_baseline_mean = (
                drift_population_baseline_loss / drift_scored
                if drift_scored
                else None
            )
            selected_drift_model_mean = (
                drift_model_mean
                if group_enabled
                else drift_population_model_mean
            )
            selected_drift_baseline_mean = (
                drift_baseline_mean
                if group_enabled
                else drift_population_baseline_mean
            )
            drift_enabled = bool(
                drift_scored >= 20
                and selected_drift_model_mean is not None
                and selected_drift_baseline_mean is not None
                and selected_drift_model_mean + 0.002
                < selected_drift_baseline_mean
            )
            raise_size_model_mean = (
                raise_size_model_error / raise_size_scored
                if raise_size_scored
                else None
            )
            raise_size_population_mean = (
                raise_size_population_error / raise_size_scored
                if raise_size_scored
                else None
            )
            raise_size_default_mean = (
                raise_size_default_error / raise_size_scored
                if raise_size_scored
                else None
            )
            raise_size_population_enabled = bool(
                raise_size_scored >= 12
                and raise_size_population_mean is not None
                and raise_size_default_mean is not None
                and raise_size_population_mean + 0.005
                < raise_size_default_mean
            )
            raise_size_player_enabled = bool(
                raise_size_scored >= 20
                and raise_size_model_mean is not None
                and raise_size_population_mean is not None
                and raise_size_default_mean is not None
                and raise_size_model_mean + 0.005
                < min(raise_size_population_mean, raise_size_default_mean)
            )
            player_quality_rows = []
            group_selective_loss = 0.0
            group_policy_loss = 0.0
            group_selective_scored = 0
            for player_id, player_score in sorted(
                player_gate_scores.items()
            ):
                validation_count = int(
                    player_score["validation_count"]
                )
                evaluation_count = int(
                    player_score["evaluation_count"]
                )
                test_observations = validation_count + evaluation_count
                evaluation_model_mean = (
                    player_score["evaluation_model_loss"] / evaluation_count
                    if evaluation_count
                    else None
                )
                evaluation_population_mean = (
                    player_score["evaluation_population_loss"]
                    / evaluation_count
                    if evaluation_count
                    else None
                )
                selected_after_validation = bool(
                    player_score["selected_after_validation"]
                )
                individually_validated = bool(
                    selected_after_validation
                    and evaluation_count >= 8
                    and evaluation_model_mean is not None
                    and evaluation_population_mean is not None
                    and evaluation_model_mean + 0.002
                    < evaluation_population_mean
                )
                mature = test_observations >= 20
                persistent_reject = bool(
                    not selected_after_validation
                    and evaluation_count >= 8
                    and evaluation_model_mean is not None
                    and evaluation_population_mean is not None
                    and evaluation_population_mean + 0.002
                    < evaluation_model_mean
                )
                player_enabled = bool(
                    group_enabled and mature and not persistent_reject
                )
                if evaluation_count:
                    if group_enabled:
                        group_policy_loss += player_score[
                            "evaluation_model_loss"
                        ]
                        selected_loss = (
                            player_score["evaluation_model_loss"]
                            if not mature or player_enabled
                            else player_score["evaluation_population_loss"]
                        )
                    else:
                        group_policy_loss += player_score[
                            "evaluation_population_loss"
                        ]
                        selected_loss = player_score[
                            "evaluation_population_loss"
                        ]
                    group_selective_loss += selected_loss
                    group_selective_scored += evaluation_count
                player_quality_rows.append(
                    {
                        "user_id": player_id,
                        "enabled": player_enabled,
                        "status": (
                            "validated"
                            if individually_validated
                            else (
                                "rejected"
                                if persistent_reject
                                else (
                                    "group_validated"
                                    if mature
                                    else "insufficient_holdout"
                                )
                            )
                        ),
                        "test_observations": test_observations,
                        "selection_observations": validation_count,
                        "evaluation_observations": evaluation_count,
                        "selected_after_validation": (
                            selected_after_validation
                            if validation_count >= 12
                            else None
                        ),
                        "evaluation_model_log_loss": (
                            round(evaluation_model_mean, 4)
                            if evaluation_model_mean is not None
                            else None
                        ),
                        "evaluation_population_log_loss": (
                            round(evaluation_population_mean, 4)
                            if evaluation_population_mean is not None
                            else None
                        ),
                        "evaluation_log_loss_improvement": (
                            round(
                                evaluation_population_mean
                                - evaluation_model_mean,
                                4,
                            )
                            if evaluation_model_mean is not None
                            and evaluation_population_mean is not None
                            else None
                        ),
                    }
                )
            total_selective_loss += group_selective_loss
            total_group_policy_loss += group_policy_loss
            total_selective_scored += group_selective_scored
            groups.append(
                {
                    "street": street,
                    "faced_bet": faced_bet,
                    "enabled": group_enabled,
                    "train_hands": len(train_hands),
                    "validation_policy": (
                        "score_each_test_hand_then_add_it_to_training"
                    ),
                    "test_observations": scored,
                    "model_log_loss": round(model_mean, 4),
                    "population_log_loss": round(population_mean, 4),
                    "log_loss_improvement": round(
                        population_mean - model_mean, 4
                    ),
                    "model_top1_accuracy": round(
                        model_correct / scored, 4
                    ),
                    "population_top1_accuracy": round(
                        population_correct / scored, 4
                    ),
                    "drift_enabled": drift_enabled,
                    "drift_test_observations": drift_scored,
                    "drift_model_log_loss": (
                        round(selected_drift_model_mean, 4)
                        if selected_drift_model_mean is not None
                        else None
                    ),
                    "long_term_model_log_loss": (
                        round(selected_drift_baseline_mean, 4)
                        if selected_drift_baseline_mean is not None
                        else None
                    ),
                    "drift_comparator": (
                        "long_term_player"
                        if group_enabled
                        else "population"
                    ),
                    "drift_log_loss_improvement": (
                        round(
                            selected_drift_baseline_mean
                            - selected_drift_model_mean,
                            4,
                        )
                        if selected_drift_model_mean is not None
                        and selected_drift_baseline_mean is not None
                        else None
                    ),
                    "raise_size_population_enabled": (
                        raise_size_population_enabled
                    ),
                    "raise_size_player_enabled": raise_size_player_enabled,
                    "raise_size_test_observations": raise_size_scored,
                    "raise_size_model_log_mae": (
                        round(raise_size_model_mean, 4)
                        if raise_size_model_mean is not None
                        else None
                    ),
                    "raise_size_population_log_mae": (
                        round(raise_size_population_mean, 4)
                        if raise_size_population_mean is not None
                        else None
                    ),
                    "raise_size_default_log_mae": (
                        round(raise_size_default_mean, 4)
                        if raise_size_default_mean is not None
                        else None
                    ),
                    "player_gate_enabled": sum(
                        bool(row["enabled"]) for row in player_quality_rows
                    ),
                    "player_gate_individually_validated": sum(
                        row["status"] == "validated"
                        for row in player_quality_rows
                    ),
                    "player_gate_rejected": sum(
                        row["status"] == "rejected"
                        for row in player_quality_rows
                    ),
                    "player_gate_evaluation_observations": (
                        group_selective_scored
                    ),
                    "selective_policy_log_loss": (
                        round(
                            group_selective_loss
                            / group_selective_scored,
                            4,
                        )
                        if group_selective_scored
                        else None
                    ),
                    "group_policy_log_loss_on_player_holdout": (
                        round(
                            group_policy_loss / group_selective_scored,
                            4,
                        )
                        if group_selective_scored
                        else None
                    ),
                    "players": player_quality_rows,
                }
            )
    if not total_scored:
        return {
            "enabled": False,
            "test_observations": 0,
            "reason": "没有足够的时间外行动观察",
            "groups": [],
            "method": "nested-player-gated-response-and-size-v5",
        }
    model_mean_loss = total_model_loss / total_scored
    population_mean_loss = total_population_loss / total_scored
    enabled_groups = sum(bool(group["enabled"]) for group in groups)
    drift_enabled_groups = sum(
        bool(group.get("drift_enabled")) for group in groups
    )
    drift_test_observations = sum(
        int(group.get("drift_test_observations") or 0)
        for group in groups
    )
    enabled_observations = sum(
        int(group["test_observations"])
        for group in groups
        if group["enabled"]
    )
    residual_enabled = bool(enabled_groups) and (
        model_mean_loss <= population_mean_loss
    )
    adaptive_observations = min(
        total_scored,
        enabled_observations
        + sum(
            int(group.get("drift_test_observations") or 0)
            for group in groups
            if group.get("drift_enabled") and not group.get("enabled")
        ),
    )
    enabled = residual_enabled or bool(drift_enabled_groups)
    return {
        "enabled": enabled,
        "test_observations": total_scored,
        "model_log_loss": round(model_mean_loss, 4),
        "population_log_loss": round(population_mean_loss, 4),
        "log_loss_improvement": round(
            population_mean_loss - model_mean_loss, 4
        ),
        "model_top1_accuracy": round(
            total_model_correct / total_scored, 4
        ),
        "population_top1_accuracy": round(
            total_population_correct / total_scored, 4
        ),
        "reason": (
            f"{enabled_groups} 个节点允许玩家 residual，"
            f"{drift_enabled_groups} 个节点允许近期漂移"
            if enabled
            else "玩家 residual 与近期漂移均未稳定优于回退模型"
        ),
        "residual_enabled": residual_enabled,
        "enabled_groups": enabled_groups,
        "drift_enabled_groups": drift_enabled_groups,
        "drift_test_observations": drift_test_observations,
        "enabled_test_observations": adaptive_observations,
        "enabled_coverage_pct": round(
            100 * adaptive_observations / total_scored, 1
        ),
        "player_gate_evaluation_observations": total_selective_scored,
        "selective_policy_log_loss": (
            round(total_selective_loss / total_selective_scored, 4)
            if total_selective_scored
            else None
        ),
        "group_policy_log_loss_on_player_holdout": (
            round(total_group_policy_loss / total_selective_scored, 4)
            if total_selective_scored
            else None
        ),
        "player_gate_log_loss_improvement": (
            round(
                (total_group_policy_loss - total_selective_loss)
                / total_selective_scored,
                4,
            )
            if total_selective_scored
            else None
        ),
        "player_gate_enabled": sum(
            int(group.get("player_gate_enabled") or 0)
            for group in groups
        ),
        "player_gate_individually_validated": sum(
            int(group.get("player_gate_individually_validated") or 0)
            for group in groups
        ),
        "player_gate_rejected": sum(
            int(group.get("player_gate_rejected") or 0)
            for group in groups
        ),
        "groups": groups,
        "method": "nested-player-gated-response-and-size-v5",
    }


def cached_action_response_backtest(
    connection: sqlite3.Connection,
    game_mode: Optional[str] = None,
    *,
    refresh_on_miss: bool = True,
) -> Dict[str, Any]:
    """Refresh model gates every 20 valid hands and reuse between decisions."""

    database_row = connection.execute("PRAGMA database_list").fetchone()
    database_path = str(database_row[2] or "") if database_row else ""
    if not database_path:
        database_path = f"memory:{id(connection)}"
    count_row = connection.execute(
        """
        SELECT COUNT(*) FROM hands
        WHERE excluded_from_stats = 0
          AND (? IS NULL OR game_mode = ?)
        """,
        (game_mode, game_mode),
    ).fetchone()
    valid_hands = int(count_row[0] or 0) if count_row else 0
    cache_key = (
        database_path,
        str(game_mode or "all"),
        valid_hands // 20,
    )
    cached = _ACTION_RESPONSE_QUALITY_CACHE.get(cache_key)
    if cached is not None:
        return cached
    if not refresh_on_miss:
        stale = next(
            (
                value
                for key, value in reversed(
                    list(_ACTION_RESPONSE_QUALITY_CACHE.items())
                )
                if key[:2] == cache_key[:2]
            ),
            None,
        )
        if stale is not None:
            return {
                **stale,
                "stale": True,
                "valid_hands": valid_hands,
                "refresh_bucket": valid_hands // 20,
                "reason": (
                    "实时决策复用最近一次行动响应门控；"
                    "完整回测留到非实时路径刷新"
                ),
            }
        return {
            "enabled": False,
            "test_observations": 0,
            "enabled_groups": 0,
            "groups": [],
            "valid_hands": valid_hands,
            "refresh_bucket": valid_hands // 20,
            "stale": True,
            "reason": "行动响应门控尚未预热；实时决策使用牌池回退",
            "method": "realtime-unwarmed-response-gate-v1",
        }
    result = action_response_backtest(connection, game_mode)
    result["valid_hands"] = valid_hands
    result["refresh_bucket"] = valid_hands // 20
    _ACTION_RESPONSE_QUALITY_CACHE[cache_key] = result
    while len(_ACTION_RESPONSE_QUALITY_CACHE) > 16:
        oldest = next(iter(_ACTION_RESPONSE_QUALITY_CACHE))
        del _ACTION_RESPONSE_QUALITY_CACHE[oldest]
    return result


def joint_fold_calibration(
    connection: sqlite3.Connection,
    game_mode: str,
    *,
    street: str,
    opponents: int,
    action_type: str,
    quality: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Calibrate all-fold probability without assuming independent opponents."""

    records = _joint_fold_records(connection, game_mode)
    target = {
        "street": street,
        "opponents": max(1, int(opponents)),
        "opponents_bucket": _opponents_bucket(opponents),
        "action_type": action_type,
        "size_bucket": "none",
    }
    base = _joint_fold_posterior(records, target)
    size_curve = {
        size_bucket: _joint_fold_posterior(
            records, {**target, "size_bucket": size_bucket}
        )
        for size_bucket in SIZE_BUCKETS
    }
    group = _joint_fold_quality_group(
        quality, street, target["opponents_bucket"]
    )
    enabled = quality is None or bool((group or {}).get("enabled"))
    return {
        **base,
        "enabled": enabled,
        "preview_logit_correction": base["logit_correction"],
        "logit_correction": base["logit_correction"] if enabled else 0.0,
        "size_curve": {
            bucket: {
                **posterior,
                "preview_logit_correction": posterior[
                    "logit_correction"
                ],
                "logit_correction": (
                    posterior["logit_correction"] if enabled else 0.0
                ),
            }
            for bucket, posterior in size_curve.items()
        },
        "quality": group,
        "production_training": {
            "eligible_joint_opportunities": len(records),
            "scope": "all_completed_good_history",
            "validation_rows_reused_after_gate": True,
        },
        "reason": (
            "时间外联合弃牌模型优于独立假设，校准进入 EV"
            if enabled
            else "联合弃牌模型尚未在时间外超过独立假设"
        ),
        "method": "joint-all-fold-logit-calibration-v1",
    }


def joint_fold_backtest(
    connection: sqlite3.Connection,
    game_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Chronologically compare joint all-fold calibration with independence."""

    records = _joint_fold_records(connection, game_mode)
    records.sort(
        key=lambda record: (
            record.get("played_at") or "",
            record.get("hand_id") or "",
            record.get("action_sequence") or 0,
        )
    )
    hands = list(dict.fromkeys(record["hand_id"] for record in records))
    if len(hands) < 12:
        return {
            "enabled": False,
            "train_hands": 0,
            "test_hands": len(hands),
            "test_observations": 0,
            "groups": [],
            "reason": "多人进攻样本不足 12 手牌",
            "method": "prequential-expanding-joint-fold-gate-v2",
        }
    split = max(1, min(len(hands) - 1, int(0.70 * len(hands))))
    training_hands = set(hands[:split])
    test_hands = set(hands[split:])
    training = [
        record for record in records if record["hand_id"] in training_hands
    ]
    tests_by_hand = {
        hand_id: [
            record for record in records if record["hand_id"] == hand_id
        ]
        for hand_id in hands[split:]
    }
    group_scores: Dict[Tuple[str, str], Dict[str, float]] = defaultdict(
        lambda: {
            "model_loss": 0.0,
            "baseline_loss": 0.0,
            "model_brier": 0.0,
            "baseline_brier": 0.0,
            "count": 0.0,
        }
    )
    for hand_id in hands[split:]:
        hand_targets = tests_by_hand.get(hand_id) or []
        for target in hand_targets:
            posterior = _joint_fold_posterior(training, target)
            model_probability = posterior[
                "empirical_all_fold_probability"
            ]
            baseline_probability = posterior[
                "historical_independent_probability"
            ]
            label = 1.0 if target["all_fold"] else 0.0
            key = (target["street"], target["opponents_bucket"])
            score = group_scores[key]
            score["model_loss"] += _binary_log_loss(
                model_probability, bool(label)
            )
            score["baseline_loss"] += _binary_log_loss(
                baseline_probability, bool(label)
            )
            score["model_brier"] += (model_probability - label) ** 2
            score["baseline_brier"] += (
                baseline_probability - label
            ) ** 2
            score["count"] += 1
        training.extend(hand_targets)
    groups = []
    for (street, opponents_bucket), score in sorted(group_scores.items()):
        count = int(score["count"])
        model_loss = score["model_loss"] / max(1, count)
        baseline_loss = score["baseline_loss"] / max(1, count)
        model_brier = score["model_brier"] / max(1, count)
        baseline_brier = score["baseline_brier"] / max(1, count)
        enabled = (
            count >= 20
            and model_loss < baseline_loss
            and model_brier <= baseline_brier
        )
        groups.append(
            {
                "street": street,
                "opponents_bucket": opponents_bucket,
                "enabled": enabled,
                "test_observations": count,
                "model_log_loss": round(model_loss, 4),
                "independent_log_loss": round(baseline_loss, 4),
                "log_loss_improvement": round(
                    baseline_loss - model_loss, 4
                ),
                "model_brier": round(model_brier, 4),
                "independent_brier": round(baseline_brier, 4),
            }
        )
    total = sum(int(group["test_observations"]) for group in groups)
    enabled_observations = sum(
        int(group["test_observations"])
        for group in groups
        if group["enabled"]
    )
    weighted_model = sum(
        float(group["model_log_loss"]) * int(group["test_observations"])
        for group in groups
    ) / max(1, total)
    weighted_baseline = sum(
        float(group["independent_log_loss"])
        * int(group["test_observations"])
        for group in groups
    ) / max(1, total)
    return {
        "enabled": any(group["enabled"] for group in groups),
        "train_hands": len(training_hands),
        "test_hands": len(test_hands),
        "test_observations": total,
        "validation_policy": (
            "score_each_test_hand_then_add_it_to_training"
        ),
        "model_log_loss": round(weighted_model, 4),
        "independent_log_loss": round(weighted_baseline, 4),
        "log_loss_improvement": round(
            weighted_baseline - weighted_model, 4
        ),
        "enabled_groups": sum(group["enabled"] for group in groups),
        "enabled_test_observations": enabled_observations,
        "enabled_coverage_pct": round(
            100 * enabled_observations / max(1, total), 1
        ),
        "groups": groups,
        "reason": (
            "至少一个街道与人数节点的联合模型通过时间外门控"
            if any(group["enabled"] for group in groups)
            else "联合模型尚未在足量时间外样本上优于独立假设"
        ),
        "method": "prequential-expanding-joint-fold-gate-v2",
    }


def cached_joint_fold_backtest(
    connection: sqlite3.Connection,
    game_mode: Optional[str] = None,
) -> Dict[str, Any]:
    database_row = connection.execute("PRAGMA database_list").fetchone()
    database_path = str(database_row[2] or "") if database_row else ""
    if not database_path:
        database_path = f"memory:{id(connection)}"
    count_row = connection.execute(
        """
        SELECT COUNT(*) FROM hands
        WHERE excluded_from_stats = 0
          AND (? IS NULL OR game_mode = ?)
        """,
        (game_mode, game_mode),
    ).fetchone()
    valid_hands = int(count_row[0] or 0) if count_row else 0
    cache_key = (
        database_path,
        str(game_mode or "all"),
        valid_hands // 20,
    )
    cached = _JOINT_FOLD_QUALITY_CACHE.get(cache_key)
    if cached is not None:
        return cached
    result = joint_fold_backtest(connection, game_mode)
    result["valid_hands"] = valid_hands
    result["refresh_bucket"] = valid_hands // 20
    _JOINT_FOLD_QUALITY_CACHE[cache_key] = result
    while len(_JOINT_FOLD_QUALITY_CACHE) > 16:
        oldest = next(iter(_JOINT_FOLD_QUALITY_CACHE))
        del _JOINT_FOLD_QUALITY_CACHE[oldest]
    return result


def _response_group_quality(
    quality: Optional[Dict[str, Any]],
    street: str,
    faced_bet: bool,
) -> Optional[Dict[str, Any]]:
    if quality is None:
        return None
    return next(
        (
            group
            for group in quality.get("groups") or []
            if group.get("street") == street
            and bool(group.get("faced_bet")) == faced_bet
        ),
        {
            "street": street,
            "faced_bet": faced_bet,
            "enabled": False,
            "reason": "该行动节点组没有足够时间外样本",
        },
    )


def _player_response_quality(
    group_quality: Optional[Dict[str, Any]],
    user_id: str,
) -> Optional[Dict[str, Any]]:
    if not group_quality:
        return None
    return next(
        (
            row
            for row in group_quality.get("players") or []
            if str(row.get("user_id") or "") == str(user_id)
        ),
        None,
    )


def _joint_fold_records(
    connection: sqlite3.Connection,
    game_mode: Optional[str] = None,
) -> List[Dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT d.hand_id, h.played_at, d.action_sequence, d.user_id,
               d.seat, d.street, d.game_mode, d.chosen_action,
               d.players_in_hand, d.bet_fraction, d.facing_action
        FROM decision_snapshots d
        JOIN hands h ON h.hand_id = d.hand_id
        WHERE h.excluded_from_stats = 0
          AND (? IS NULL OR d.game_mode = ?)
        ORDER BY h.played_at, d.hand_id, d.action_sequence
        """,
        (game_mode, game_mode),
    ).fetchall()
    by_hand: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_hand[str(row[0])].append(
            {
                "hand_id": str(row[0]),
                "played_at": str(row[1] or ""),
                "action_sequence": int(row[2] or 0),
                "actor": (
                    f"user:{row[3]}"
                    if row[3] is not None
                    else f"seat:{row[4]}"
                ),
                "street": str(row[5] or ""),
                "game_mode": str(row[6] or ""),
                "action": _normalized_action(row[7]),
                "raw_action": str(row[7] or ""),
                "players_in_hand": int(row[8] or 0),
                "size_bucket": _size_bucket(row[9]),
                "facing_action": str(row[10] or ""),
            }
        )
    records = []
    for hand_rows in by_hand.values():
        for index, trigger in enumerate(hand_rows):
            if not _aggressive_action(trigger["raw_action"]):
                continue
            opponents = max(0, trigger["players_in_hand"] - 1)
            if opponents < 2:
                continue
            responders: Dict[str, str] = {}
            for response in hand_rows[index + 1 :]:
                if response["street"] != trigger["street"]:
                    break
                if response["actor"] == trigger["actor"]:
                    break
                if response["raw_action"] in FORCED_ACTIONS:
                    continue
                responders.setdefault(response["actor"], response["action"])
                if _aggressive_action(response["raw_action"]):
                    break
            if not responders:
                continue
            all_fold = all(action == "fold" for action in responders.values())
            if all_fold and len(responders) < opponents:
                continue
            records.append(
                {
                    **trigger,
                    "opponents": opponents,
                    "opponents_bucket": _opponents_bucket(opponents),
                    "action_type": (
                        "raise"
                        if trigger["facing_action"] in {"bet", "raise"}
                        else "bet"
                    ),
                    "all_fold": all_fold,
                    "fold_responses": sum(
                        action == "fold" for action in responders.values()
                    ),
                    "observed_responses": len(responders),
                }
            )
    return records


def _joint_fold_posterior(
    records: Sequence[Dict[str, Any]],
    target: Dict[str, Any],
) -> Dict[str, Any]:
    exact = [
        record
        for record in records
        if record["street"] == target["street"]
        and record["opponents_bucket"] == target["opponents_bucket"]
        and record["action_type"] == target["action_type"]
    ]
    if len(exact) >= 24:
        candidates = exact
        feature_level = "street_opponents_action"
    else:
        grouped = [
            record
            for record in records
            if record["street"] == target["street"]
            and record["opponents_bucket"] == target["opponents_bucket"]
        ]
        if len(grouped) >= 24:
            candidates = grouped
            feature_level = "street_opponents"
        else:
            street = [
                record
                for record in records
                if record["street"] == target["street"]
            ]
            candidates = street if len(street) >= 24 else list(records)
            feature_level = (
                "street" if len(street) >= 24 else "all_contexts"
            )
    size_bucket = str(target.get("size_bucket") or "none")
    sized = [
        record
        for record in candidates
        if record["size_bucket"] == size_bucket
    ]
    if size_bucket != "none" and len(sized) >= 20:
        candidates = sized
        feature_level += "_size"
    fold_responses = sum(
        int(record["fold_responses"]) for record in candidates
    )
    observed_responses = sum(
        int(record["observed_responses"]) for record in candidates
    )
    marginal_fold = (fold_responses + 2.0) / (observed_responses + 4.0)
    independent = marginal_fold ** max(1, int(target["opponents"]))
    successes = sum(bool(record["all_fold"]) for record in candidates)
    sample_count = len(candidates)
    prior_strength = 16.0
    empirical = (
        successes + independent * prior_strength
    ) / max(1e-9, sample_count + prior_strength)
    correction = _logit(empirical) - _logit(independent)
    return {
        "sample_count": sample_count,
        "all_fold_successes": successes,
        "marginal_fold_probability": round(marginal_fold, 5),
        "historical_independent_probability": round(independent, 5),
        "empirical_all_fold_probability": round(empirical, 5),
        "logit_correction": round(max(-1.25, min(1.25, correction)), 5),
        "feature_level": feature_level,
        "confidence": _confidence(float(sample_count)),
    }


def _joint_fold_quality_group(
    quality: Optional[Dict[str, Any]],
    street: str,
    opponents_bucket: str,
) -> Optional[Dict[str, Any]]:
    if quality is None:
        return None
    return next(
        (
            group
            for group in quality.get("groups") or []
            if group.get("street") == street
            and group.get("opponents_bucket") == opponents_bucket
        ),
        None,
    )


def _opponents_bucket(opponents: int) -> str:
    if opponents <= 1:
        return "heads_up"
    if opponents == 2:
        return "two"
    if opponents <= 4:
        return "three_four"
    return "five_plus"


def _logit(probability: float) -> float:
    clipped = max(1e-6, min(1.0 - 1e-6, probability))
    return math.log(clipped / (1.0 - clipped))


def _binary_log_loss(probability: float, label: bool) -> float:
    clipped = max(1e-6, min(1.0 - 1e-6, probability))
    return -math.log(clipped if label else 1.0 - clipped)


def _decision_records(
    connection: sqlite3.Connection,
    street: str,
    faced_bet: bool,
) -> List[Dict[str, Any]]:
    decision_columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(decision_snapshots)"
        ).fetchall()
    }
    raise_multiple_column = (
        "d.raise_multiple" if "raise_multiple" in decision_columns else "NULL"
    )
    rows = connection.execute(
        f"""
        SELECT d.user_id, d.chosen_action, d.position, d.spr, d.to_call,
               d.pot_before, d.is_ip, d.players_in_hand, d.game_mode,
               h.played_at, d.action_sequence, d.facing_action,
               d.board_texture_json, d.hand_id, {raise_multiple_column}
        FROM decision_snapshots d
        JOIN hands h ON h.hand_id = d.hand_id
        WHERE h.excluded_from_stats = 0
          AND d.user_id IS NOT NULL
          AND d.street = ?
          AND d.chosen_action NOT IN ('ante','small_blind','big_blind','straddle')
        ORDER BY h.played_at DESC, d.action_sequence DESC
        """,
        (street,),
    ).fetchall()
    records = []
    player_recency: Dict[str, int] = defaultdict(int)
    for row in rows:
        record_faced_bet = float(row[4] or 0.0) > 0
        if record_faced_bet != faced_bet:
            continue
        action = _response_action(str(row[1] or ""), faced_bet)
        if action is None:
            continue
        player = str(row[0])
        recency_index = player_recency[player]
        player_recency[player] += 1
        faced_fraction = (
            float(row[4]) / float(row[5])
            if record_faced_bet and float(row[5] or 0.0) > 0
            else None
        )
        records.append(
            {
                "user_id": player,
                "action": action,
                "raw_action": str(row[1] or "").lower(),
                "position": str(row[2] or ""),
                "spr_bucket": _spr_bucket(row[3]),
                "is_ip": None if row[6] is None else bool(row[6]),
                "players_bucket": _players_bucket(row[7]),
                "game_mode": str(row[8] or ""),
                "size_bucket": _size_bucket(faced_fraction),
                "facing_action": _normalized_action(row[11]),
                "board_texture": json.loads(row[12] or "{}"),
                "hand_id": str(row[13]),
                "raise_multiple": row[14],
                "played_at": str(row[9] or ""),
                "recency_weight": 0.5 ** (recency_index / 200.0),
            }
        )
    return records


def _reindex_recency_weights(
    records: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = defaultdict(int)
    result = []
    for record in sorted(
        records,
        key=lambda item: (
            item.get("played_at") or "",
            item.get("hand_id") or "",
        ),
        reverse=True,
    ):
        player = str(record.get("user_id") or "")
        recency_index = counts[player]
        counts[player] += 1
        result.append(
            {
                **record,
                "recency_weight": 0.5 ** (recency_index / 200.0),
            }
        )
    return result


def _response_posterior(
    records: List[Dict[str, Any]],
    user_id: str,
    actions: Tuple[str, ...],
    target: Dict[str, Any],
) -> Dict[str, Any]:
    fallback = (
        {"fold": 0.43, "call": 0.49, "raise": 0.08}
        if actions == FACING_ACTIONS
        else {"check": 0.55, "bet": 0.45}
    )
    population_smoothing = 6.0
    population_counts = {
        action: population_smoothing * fallback[action] for action in actions
    }
    population_weight = population_smoothing
    player_counts = {action: 0.0 for action in actions}
    player_weight = 0.0
    raw_samples = 0
    exact_samples = 0
    for record in records:
        weight = _context_weight(record, target)
        if weight <= 0:
            continue
        action = record["action"]
        population_counts[action] += weight
        population_weight += weight
        if record["user_id"] != user_id:
            continue
        raw_samples += 1
        recency_weight = float(record.get("recency_weight") or 1.0)
        weighted = weight * recency_weight
        player_counts[action] += weighted
        player_weight += weighted
        if weight >= 0.75:
            exact_samples += 1
    population = {
        action: population_counts[action] / population_weight
        for action in actions
    }
    prior_strength = 18.0
    alphas = {
        action: prior_strength * population[action] + player_counts[action]
        for action in actions
    }
    total_alpha = sum(alphas.values())
    probabilities = {
        action: alphas[action] / total_alpha for action in actions
    }
    intervals = {}
    for action in actions:
        low, high = _normal_beta_interval(
            alphas[action], total_alpha - alphas[action]
        )
        intervals[action] = {
            "low_pct": round(100 * low, 1),
            "high_pct": round(100 * high, 1),
        }
    return {
        "probabilities_pct": {
            action: round(100 * probabilities[action], 1)
            for action in actions
        },
        "population_probabilities_pct": {
            action: round(100 * population[action], 1)
            for action in actions
        },
        "deviation_pp": {
            action: round(100 * (probabilities[action] - population[action]), 1)
            for action in actions
        },
        "intervals": intervals,
        "raw_samples": raw_samples,
        "exact_samples": exact_samples,
        "effective_samples": round(player_weight, 2),
        "population_effective_samples": round(
            population_weight - population_smoothing, 2
        ),
        "confidence": _confidence(player_weight),
    }


def _raise_size_posterior(
    records: Sequence[Dict[str, Any]],
    user_id: str,
    target: Dict[str, Any],
    street: str,
) -> Dict[str, Any]:
    """Estimate a robust raise-to multiple with player-to-pool shrinkage."""

    default_multiple = 3.5 if street == "preflop" else 3.0
    default_log = math.log(default_multiple)
    population_weight = 8.0
    population_log_sum = population_weight * default_log
    player_weight = 0.0
    player_log_sum = 0.0
    raw_samples = 0
    exact_samples = 0
    for record in records:
        if record.get("raw_action") != "raise":
            continue
        multiple = _valid_raise_multiple(record.get("raise_multiple"))
        if multiple is None:
            continue
        context_weight = _context_weight(record, target)
        if context_weight <= 0:
            continue
        log_multiple = math.log(multiple)
        population_log_sum += context_weight * log_multiple
        population_weight += context_weight
        if record.get("user_id") != user_id:
            continue
        raw_samples += 1
        weighted = context_weight * float(record.get("recency_weight") or 1.0)
        player_log_sum += weighted * log_multiple
        player_weight += weighted
        exact_samples += int(context_weight >= 0.75)
    population_log = population_log_sum / population_weight
    prior_strength = 12.0
    posterior_log = (
        prior_strength * population_log + player_log_sum
    ) / (prior_strength + player_weight)
    expected_multiple = math.exp(posterior_log)
    return {
        "expected_multiple": round(expected_multiple, 3),
        "raw_samples": raw_samples,
        "exact_samples": exact_samples,
        "effective_samples": round(player_weight, 2),
        "population_effective_samples": round(population_weight - 8.0, 2),
        "confidence": _confidence(player_weight),
    }


def _valid_raise_multiple(value: Any) -> Optional[float]:
    try:
        multiple = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(multiple) or not 1.5 <= multiple <= 6.0:
        return None
    return multiple


def _player_strategy_drift(
    records: Sequence[Dict[str, Any]],
    user_id: str,
    actions: Tuple[str, ...],
    target: Dict[str, Any],
    population_prior: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    contextual = [
        (record, _context_weight(record, target))
        for record in records
        if record.get("user_id") == user_id
    ]
    contextual = [
        (record, weight)
        for record, weight in contextual
        if weight >= 0.20
    ]
    contextual.sort(
        key=lambda item: (
            item[0].get("played_at") or "",
            item[0].get("hand_id") or "",
        ),
        reverse=True,
    )
    if len(contextual) < 20:
        return {
            "available": False,
            "recent_samples": min(len(contextual), 20),
            "prior_samples": max(0, len(contextual) - 20),
            "reason": "相似节点历史不足 20 次",
            "used_for_prediction": False,
            "method": "session-vs-long-term-dirichlet-v1",
        }
    latest_time = _parse_timestamp(contextual[0][0].get("played_at"))
    session_cutoff = (
        latest_time - timedelta(minutes=90) if latest_time is not None else None
    )
    session = [
        item
        for item in contextual
        if session_cutoff is not None
        and (
            timestamp := _parse_timestamp(item[0].get("played_at"))
        ) is not None
        and timestamp >= session_cutoff
    ]
    if len(session) >= 8:
        recent = session
        recent_source = "latest_90_minutes"
    else:
        recent = contextual[: min(20, max(8, len(contextual) // 3))]
        recent_source = "latest_actions"
    recent_ids = {id(record) for record, _ in recent}
    prior_rows = [
        item for item in contextual if id(item[0]) not in recent_ids
    ]
    if len(prior_rows) < 12:
        return {
            "available": False,
            "recent_samples": len(recent),
            "prior_samples": len(prior_rows),
            "reason": "近期窗口之外的长期样本不足 12 次",
            "used_for_prediction": False,
            "method": "session-vs-long-term-dirichlet-v1",
        }
    population = population_prior or _response_posterior(
        list(records), "__population_only__", actions, target
    )
    population_prior = {
        action: float(population["probabilities_pct"][action]) / 100
        for action in actions
    }
    long_term = _weighted_action_distribution(
        prior_rows,
        actions,
        population_prior,
        prior_strength=10.0,
    )
    recent_distribution = _weighted_action_distribution(
        recent,
        actions,
        long_term,
        prior_strength=6.0,
    )
    shifts = {
        action: recent_distribution[action] - long_term[action]
        for action in actions
    }
    dominant_action = max(shifts, key=lambda action: abs(shifts[action]))
    max_shift = abs(shifts[dominant_action])
    divergence = _jensen_shannon(recent_distribution, long_term, actions)
    effective_recent = sum(weight for _, weight in recent)
    if (
        effective_recent >= 10
        and len(prior_rows) >= 20
        and max_shift >= 0.15
        and divergence >= 0.015
    ):
        status = "shifted"
    elif max_shift >= 0.08 and divergence >= 0.005:
        status = "watch"
    else:
        status = "stable"
    return {
        "available": True,
        "status": status,
        "recent_source": recent_source,
        "recent_samples": len(recent),
        "recent_effective_samples": round(effective_recent, 2),
        "prior_samples": len(prior_rows),
        "recent_probabilities_pct": {
            action: round(100 * probability, 1)
            for action, probability in recent_distribution.items()
        },
        "long_term_probabilities_pct": {
            action: round(100 * probability, 1)
            for action, probability in long_term.items()
        },
        "shift_pp": {
            action: round(100 * shift, 1)
            for action, shift in shifts.items()
        },
        "dominant_shift": dominant_action,
        "max_shift_pp": round(100 * max_shift, 1),
        "jensen_shannon": round(divergence, 5),
        "used_for_prediction": False,
        "reason": (
            "检测到近期策略漂移；需单独时间外验证后才能影响 EV"
            if status == "shifted"
            else "近期与长期策略差异尚不足以改变预测"
        ),
        "method": "session-vs-long-term-dirichlet-v1",
    }


def _blend_response_with_drift(
    posterior: Dict[str, Any],
    drift: Dict[str, Any],
    actions: Tuple[str, ...],
) -> Tuple[Dict[str, Any], float]:
    recent = drift.get("recent_probabilities_pct") or {}
    if drift.get("status") != "shifted" or not recent:
        return dict(posterior), 0.0
    effective_recent = float(
        drift.get("recent_effective_samples") or 0.0
    )
    weight = min(
        0.30,
        max(0.10, effective_recent / (effective_recent + 50.0)),
    )
    current = posterior.get("probabilities_pct") or {}
    blended = {
        action: (
            (1.0 - weight) * float(current.get(action) or 0.0)
            + weight * float(recent.get(action) or 0.0)
        )
        for action in actions
    }
    total = sum(blended.values())
    if total <= 0:
        return dict(posterior), 0.0
    return {
        **posterior,
        "probabilities_pct": {
            action: round(100 * value / total, 1)
            for action, value in blended.items()
        },
    }, weight


def _weighted_action_distribution(
    rows: Sequence[Tuple[Dict[str, Any], float]],
    actions: Tuple[str, ...],
    prior: Dict[str, float],
    *,
    prior_strength: float,
) -> Dict[str, float]:
    counts = {
        action: prior_strength * max(0.0, prior.get(action, 0.0))
        for action in actions
    }
    total = sum(counts.values())
    for record, weight in rows:
        action = str(record.get("action") or "")
        if action not in counts:
            continue
        counts[action] += weight
        total += weight
    return {
        action: counts[action] / max(1e-9, total) for action in actions
    }


def _jensen_shannon(
    left: Dict[str, float],
    right: Dict[str, float],
    actions: Tuple[str, ...],
) -> float:
    midpoint = {
        action: 0.5 * (left[action] + right[action]) for action in actions
    }

    def divergence(
        distribution: Dict[str, float],
        reference: Dict[str, float],
    ) -> float:
        return sum(
            probability
            * math.log(
                max(1e-12, probability)
                / max(1e-12, reference[action])
            )
            for action, probability in distribution.items()
            if probability > 0
        )

    return 0.5 * (
        divergence(left, midpoint) + divergence(right, midpoint)
    )


def _parse_timestamp(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _context_weight(
    record: Dict[str, Any],
    target: Dict[str, Any],
) -> float:
    weight = 1.0
    game_mode = target.get("game_mode")
    if game_mode and record["game_mode"] != game_mode:
        weight *= 0.12
    position = target.get("position")
    if position:
        if record["position"] == position:
            pass
        elif _position_group(record["position"]) == _position_group(position):
            weight *= 0.72
        else:
            weight *= 0.38
    players_bucket = target.get("players_bucket")
    if players_bucket != "unknown" and record["players_bucket"] != players_bucket:
        weight *= 0.35
    spr_bucket = target.get("spr_bucket")
    if spr_bucket != "unknown" and record["spr_bucket"] != spr_bucket:
        weight *= (
            0.65
            if _adjacent_bucket(
                record["spr_bucket"],
                spr_bucket,
                ("low", "medium", "deep", "very_deep"),
            )
            else 0.35
        )
    is_ip = target.get("is_ip")
    if (
        is_ip is not None
        and record.get("is_ip") is not None
        and record["is_ip"] != is_ip
    ):
        weight *= 0.60
    size_bucket = target.get("size_bucket")
    if size_bucket and record["size_bucket"] != size_bucket:
        weight *= (
            0.50
            if _adjacent_bucket(
                record["size_bucket"], size_bucket, SIZE_BUCKETS
            )
            else 0.14
        )
    facing_action = target.get("facing_action")
    if facing_action and record.get("facing_action") != facing_action:
        weight *= (
            0.65
            if _aggressive_action(record.get("facing_action"))
            and _aggressive_action(facing_action)
            else 0.35
        )
    target_texture = target.get("board_texture") or {}
    record_texture = record.get("board_texture") or {}
    for key in ("paired", "monotone", "two_tone", "connected"):
        if key in target_texture and bool(record_texture.get(key)) != bool(
            target_texture.get(key)
        ):
            weight *= 0.82
    target_high = _high_card_bucket(target_texture.get("high_card"))
    record_high = _high_card_bucket(record_texture.get("high_card"))
    if target_high != "unknown" and record_high != target_high:
        weight *= 0.78
    try:
        broadway_gap = abs(
            int(target_texture.get("broadway_count"))
            - int(record_texture.get("broadway_count"))
        )
    except (TypeError, ValueError):
        broadway_gap = 0
    if broadway_gap >= 2:
        weight *= 0.82
    return weight


def _response_action(value: str, faced_bet: bool) -> Optional[str]:
    action = value.lower().replace("-", "_").replace(" ", "_")
    if faced_bet:
        if action == "fold":
            return "fold"
        if action in {"call", "check"}:
            return "call"
        if action in {"bet", "raise", "all_in", "allin"}:
            return "raise"
    else:
        if action in {"check", "call", "fold"}:
            return "check"
        if action in {"bet", "raise", "all_in", "allin"}:
            return "bet"
    return None


def _normalized_action(value: Any) -> str:
    action = str(value or "").lower().replace("-", "_").replace(" ", "_")
    return "all_in" if action in {"allin", "all_in"} else action


def _aggressive_action(value: Any) -> bool:
    return _normalized_action(value) in {"bet", "raise", "all_in"}


def _high_card_bucket(value: Any) -> str:
    try:
        rank = int(value)
    except (TypeError, ValueError):
        return "unknown"
    if rank >= 14:
        return "ace"
    if rank >= 12:
        return "broadway"
    if rank >= 9:
        return "middle"
    return "low"


def _size_bucket(value: Any) -> str:
    try:
        fraction = float(value)
    except (TypeError, ValueError):
        return "medium"
    if fraction <= 0.30:
        return "tiny"
    if fraction <= 0.55:
        return "small"
    if fraction <= 0.85:
        return "medium"
    if fraction <= 1.15:
        return "large"
    return "overbet"


def _spr_bucket(value: Any) -> str:
    try:
        spr = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if spr < 2:
        return "low"
    if spr < 5:
        return "medium"
    if spr < 12:
        return "deep"
    return "very_deep"


def _players_bucket(value: Any) -> str:
    try:
        players = int(value)
    except (TypeError, ValueError):
        return "unknown"
    if players <= 2:
        return "heads_up"
    if players <= 4:
        return "small_multiway"
    return "large_multiway"


def _position_group(position: str) -> str:
    value = str(position or "").upper()
    if value in {"SB", "BB", "BTN/SB"}:
        return "blind"
    if value in {"BTN", "CO"}:
        return "late"
    if value in {"HJ", "MP", "MP+1"}:
        return "middle"
    if value.startswith("UTG") or value.startswith("EP"):
        return "early"
    return value or "unknown"


def _adjacent_bucket(left: str, right: str, order: Tuple[str, ...]) -> bool:
    try:
        return abs(order.index(left) - order.index(right)) == 1
    except ValueError:
        return False


def _context_matches(
    record: Dict[str, Any], target_position: str, target_stack: str
) -> bool:
    position_match = not target_position or record["position"] == target_position
    stack_match = target_stack == "unknown" or record["stack_bucket"] == target_stack
    return position_match and stack_match


def _stack_bucket(value: Any) -> str:
    return stack_bucket(value)


def _normal_beta_interval(alpha: float, beta: float) -> Tuple[float, float]:
    mean = alpha / (alpha + beta)
    variance = alpha * beta / ((alpha + beta) ** 2 * (alpha + beta + 1))
    delta = 1.2816 * math.sqrt(variance)
    return max(0.0, mean - delta), min(1.0, mean + delta)


def _confidence(samples: float) -> str:
    if samples < 5:
        return "very_low"
    if samples < 15:
        return "low"
    if samples < 50:
        return "medium"
    return "high"
