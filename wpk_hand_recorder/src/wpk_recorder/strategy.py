from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .poker import (
    showdown_stats_vs_random_multiway,
    showdown_stats_vs_weighted_ranges,
)
from .squid_value import (
    hero_values_after_next_award,
    squid_state_from_dict,
)


def evaluate_money_strategy(
    context: Dict[str, Any], baseline: Dict[str, Any]
) -> Dict[str, Any]:
    """Evaluate a restricted action set without presenting heuristic EV as GTO."""

    started = time.perf_counter()
    decision = context.get("decision") or {}
    quality = decision.get("state_quality") or {}
    if not quality.get("valid", True):
        return {
            "status": "blocked",
            "engine_version": "money-ev-baseline-v1",
            "reasons": list(quality.get("blocking_reasons") or []),
            "latency_ms": _elapsed_ms(started),
        }
    hole_cards = list(decision.get("hero_cards") or [])
    if len(hole_cards) != 2:
        return {
            "status": "range_only",
            "engine_version": "money-ev-baseline-v1",
            "baseline": baseline,
            "reasons": ["本人底牌未知，不能计算当前手牌反事实 EV"],
            "latency_ms": _elapsed_ms(started),
        }

    pot = max(0.0, _number(decision.get("pot")) or 0.0)
    opponents = max(1, int(decision.get("players_in_hand") or 2) - 1)
    range_profiles = [
        profile.get("preflop_range") or {}
        for profile in context.get("active_opponent_profiles") or []
    ][:opponents]
    range_profiles.sort(
        key=lambda profile: _number(profile.get("estimated_range_pct"))
        or 100.0
    )
    range_models = [
        profile.get("weights") or {} for profile in range_profiles
    ]
    while len(range_models) < opponents:
        range_models.append({})
    uses_ranges = any(bool(model) for model in range_models)
    trials = max(
        180 if uses_ranges else 220,
        round((420 if uses_ranges else 600) / math.sqrt(opponents)),
    )
    board = list(decision.get("board") or [])
    showdown = _showdown_stats(
        hole_cards, board, range_models, opponents, trials, uses_ranges
    )
    equity = float(showdown["pot_share"])
    award_probability = float(showdown["award_probability"])
    if uses_ranges:
        equity_model = "hierarchical-preflop-ranges-multiway-v1"
    else:
        equity_model = "uniform-random-multiway-v1"
    fold_model = _fold_model(context, opponents)
    all_fold = float(fold_model["all_fold_probability"])
    conditional_callers = (
        float(fold_model["expected_callers"]) / max(1e-9, 1.0 - all_fold)
        if all_fold < 1.0
        else 1.0
    )
    caller_count = max(1, min(opponents, int(round(conditional_callers))))
    continued_showdown = _showdown_stats(
        hole_cards,
        board,
        range_models[:caller_count],
        caller_count,
        trials,
        uses_ranges,
    )
    fold_model["conditional_caller_count"] = caller_count
    fold_model["conditional_callers"] = round(conditional_callers, 4)
    candidates = _candidate_actions(decision)
    for candidate in candidates:
        candidate.update(
            _candidate_chip_ev(
                candidate,
                decision,
                equity,
                award_probability,
                float(continued_showdown["pot_share"]),
                float(continued_showdown["award_probability"]),
                fold_model,
            )
        )

    squid = _squid_edges(context, candidates, equity, fold_model)
    can_combine_squid = bool(squid.get("calibrated"))
    reference_round_ev = _reference_round_ev(candidates, squid)
    for candidate in candidates:
        round_ev = (squid.get("round_ev_by_action") or {}).get(
            candidate["id"]
        )
        squid_edge = (
            round_ev - reference_round_ev
            if round_ev is not None and reference_round_ev is not None
            else None
        )
        candidate["squid_ev"] = (
            round(squid_edge, 2) if squid_edge is not None else None
        )
        candidate["squid_ev_unit"] = (
            squid.get("unit") if squid_edge is not None else None
        )
        combined = candidate["chip_ev"]
        if can_combine_squid and squid_edge is not None:
            combined += squid_edge
        candidate["money_ev"] = round(combined, 2)
        uncertainty = _uncertainty_penalty(
            decision,
            candidate,
            trials,
            fold_model,
            can_combine_squid,
        )
        candidate["uncertainty_penalty"] = round(uncertainty, 2)
        candidate["robust_ev"] = round(combined - uncertainty, 2)

    confidence = _engine_confidence(decision, fold_model, squid)
    exploit_weight = {"low": 0.15, "medium": 0.30, "high": 0.45}[confidence]
    baseline_mix = _baseline_action_mix(baseline)
    baseline_fallback = False
    if not baseline_mix:
        reference_id = _reference_candidate_id(candidates)
        reference = next(
            (item for item in candidates if item["id"] == reference_id),
            None,
        )
        if reference is not None:
            baseline_mix = {str(reference["action"]): 1.0}
            baseline_fallback = True
    policy = _mixed_policy(
        candidates,
        baseline_mix,
        exploit_weight,
        pot,
        _number(decision.get("big_blind")) or 4.0,
    )
    primary = max(policy, key=lambda item: item["frequency_pct"]) if policy else None
    return {
        "status": "experimental",
        "engine_version": "money-ev-baseline-v1",
        "scope": "8/9-max · SB/BB 2/4 · ante 1 · deep-stack squid",
        "confidence": confidence,
        "equity": {
            "pot_share_pct": round(100 * equity, 1),
            "award_probability_pct": round(100 * award_probability, 1),
            "opponents": opponents,
            "raise_caller_count": caller_count,
            "raise_pot_share_pct": round(
                100 * float(continued_showdown["pot_share"]), 1
            ),
            "trials": trials,
            "model": equity_model,
            "range_models": [
                {
                    key: value
                    for key, value in (
                        profile.get("preflop_range") or {}
                    ).items()
                    if key != "weights"
                }
                for profile in context.get("active_opponent_profiles") or []
                if profile.get("preflop_range")
            ],
        },
        "fold_model": fold_model,
        "squid": {
            key: value
            for key, value in squid.items()
            if key != "round_ev_by_action"
        },
        "exploit_weight_pct": round(100 * exploit_weight),
        "baseline_fallback": baseline_fallback,
        "reference_action": _reference_candidate_id(candidates),
        "recommended": primary,
        "policy": policy,
        "candidates": sorted(
            candidates,
            key=lambda item: (-item["robust_ev"], item["id"]),
        ),
        "caveats": [
            (
                "equity 已按各对手翻前 169 类范围与阻断牌采样，但尚未逐街更新隐藏范围"
                if uses_ranges
                else "缺少可用对手范围，equity 退化为多方均匀随机手牌"
            ),
            "翻后价值使用深度受限近似，尚未接入 solver 蒸馏的叶节点价值",
            "只有校准出鱿鱼筹码价值后，squid_ev 才会并入 money_ev",
            "当前反事实尚未逐动作计入抽水、保险、基金和复杂多人边池现金流",
        ],
        "latency_ms": _elapsed_ms(started),
    }


def _showdown_stats(
    hole_cards: Sequence[str],
    board: Sequence[str],
    range_models: Sequence[Mapping[str, float]],
    opponents: int,
    trials: int,
    uses_ranges: bool,
) -> Dict[str, float]:
    if uses_ranges:
        models = list(range_models)
        while len(models) < opponents:
            models.append({})
        return showdown_stats_vs_weighted_ranges(
            hole_cards, board, models[:opponents], trials
        )
    return showdown_stats_vs_random_multiway(
        hole_cards, board, opponents, trials
    )


def _candidate_actions(decision: Mapping[str, Any]) -> List[Dict[str, Any]]:
    legal = list(dict.fromkeys(decision.get("legal_actions") or []))
    result = []
    for action in legal:
        if action != "raise":
            result.append({"id": action, "action": action, "raise_to": None})
            continue
        minimum = _number(decision.get("min_raise_to"))
        maximum = _number(decision.get("max_raise_to"))
        contribution = _number(decision.get("hero_street_contribution")) or 0.0
        pot = _number(decision.get("pot")) or 0.0
        targets = [
            ("min", minimum),
            ("half_pot", contribution + 0.5 * pot),
            ("three_quarter_pot", contribution + 0.75 * pot),
            ("pot", contribution + pot),
        ]
        seen = set()
        for label, target in targets:
            if target is None:
                continue
            if minimum is not None:
                target = max(minimum, target)
            if maximum is not None:
                target = min(maximum, target)
            rounded = round(target, 2)
            if rounded in seen:
                continue
            seen.add(rounded)
            result.append(
                {
                    "id": f"raise:{rounded:g}",
                    "action": "raise",
                    "raise_to": rounded,
                    "size": label,
                }
            )
    return result


def _candidate_chip_ev(
    candidate: Mapping[str, Any],
    decision: Mapping[str, Any],
    equity: float,
    award_probability: float,
    continued_equity: float,
    continued_award_probability: float,
    fold_model: Mapping[str, Any],
) -> Dict[str, float]:
    action = str(candidate["action"])
    pot = max(0.0, _number(decision.get("pot")) or 0.0)
    call = max(0.0, _number(decision.get("call_score")) or 0.0)
    if action == "fold":
        return {"chip_ev": 0.0, "award_probability": 0.0}
    if action == "check":
        return {
            "chip_ev": round(equity * pot, 2),
            "award_probability": round(award_probability, 5),
        }
    if action == "call":
        return {
            "chip_ev": round(equity * (pot + call) - call, 2),
            "award_probability": round(award_probability, 5),
        }

    contribution = _number(decision.get("hero_street_contribution")) or 0.0
    target = (
        _number(candidate.get("raise_to"))
        if action == "raise"
        else _number(decision.get("max_raise_to"))
    )
    if target is None:
        target = contribution + max(call, _number(decision.get("hero_stack")) or 0)
    cost = max(0.0, target - contribution)
    facing_total = contribution + call
    call_increment = max(0.0, target - facing_total)
    all_fold = float(fold_model["all_fold_probability"])
    expected_callers = float(fold_model["expected_callers"])
    conditional_callers = (
        expected_callers / max(1e-9, 1.0 - all_fold)
        if all_fold < 1
        else 0.0
    )
    contested = pot + cost + call_increment * conditional_callers
    continued_ev = continued_equity * contested - cost
    chip_ev = all_fold * pot + (1.0 - all_fold) * continued_ev
    aggressive_award_probability = all_fold + (
        1.0 - all_fold
    ) * continued_award_probability
    return {
        "chip_ev": round(chip_ev, 2),
        "award_probability": round(aggressive_award_probability, 5),
    }


def _fold_model(context: Mapping[str, Any], opponents: int) -> Dict[str, Any]:
    decision = context.get("decision") or {}
    street = str(decision.get("street") or "preflop")
    facing_bet = (_number(decision.get("call_score")) or 0.0) > 0
    if street == "preflop":
        metric = "fold_to_three_bet"
        prior = 0.55
    else:
        metric = (
            f"fold_to_{street}_raise"
            if facing_bet
            else f"fold_to_{street}_bet"
        )
        prior = 0.45
    probabilities = []
    evidence_trials = 0
    for profile in context.get("active_opponent_profiles") or []:
        observation = next(
            (
                item
                for item in profile.get("observations") or []
                if item.get("metric") == metric
            ),
            None,
        )
        if observation:
            trials = max(0, int(observation.get("opportunities") or 0))
            successes = max(0, int(observation.get("successes") or 0))
            posterior_mean = _number(observation.get("mean_pct"))
            probability = (
                posterior_mean / 100
                if posterior_mean is not None
                else (successes + prior * 12) / (trials + 12)
            )
            evidence_trials += trials
        else:
            probability = prior
        probabilities.append(max(0.05, min(0.95, probability)))
    while len(probabilities) < opponents:
        probabilities.append(prior)
    probabilities = probabilities[:opponents]
    all_fold = math.prod(probabilities)
    return {
        "metric": metric,
        "per_opponent_pct": [round(100 * value, 1) for value in probabilities],
        "all_fold_probability": round(all_fold, 5),
        "expected_callers": round(sum(1.0 - value for value in probabilities), 5),
        "evidence_trials": evidence_trials,
        "method": "beta-shrunk-independent-v1",
    }


def _squid_edges(
    context: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    equity: float,
    fold_model: Mapping[str, Any],
) -> Dict[str, Any]:
    decision = context.get("decision") or {}
    if str(decision.get("game_mode") or "") != "squid":
        return {"available": False, "reason": "当前不是鱿鱼模式"}
    state_value = context.get("squid_round")
    if not isinstance(state_value, dict):
        return {"available": False, "reason": "缺少当前鱿鱼轮次状态"}
    state = squid_state_from_dict(state_value)
    counts = dict(state.counts)
    if state.rule_status == "mismatch":
        return {
            "available": False,
            "hard_block": True,
            "rule_status": state.rule_status,
            "reason": state.calibration_error
            or "历史结算与当前鱿鱼公式不匹配",
        }
    if (
        state.participant_count <= 0
        or state.total_squids != state.participant_count + 4
        or state.awarded_squids != sum(counts.values())
        or state.awarded_squids > state.total_squids
    ):
        return {
            "available": False,
            "hard_block": True,
            "rule_status": "invalid_state",
            "reason": "鱿鱼数量、参与人数或 N+4 上限不守恒",
        }
    if "acting_player" not in counts or state.terminal:
        return {
            "available": False,
            "reason": "本人不在轮次状态中或本轮已经终局",
        }
    award_model = context.get("squid_award_model") or {}
    future = {
        player: float(
            (award_model.get("probabilities") or {}).get(player, 0.0)
        )
        for player in state.participants
    }
    if sum(future.values()) <= 0:
        future = {player: 1.0 for player in state.participants}
    values_after = hero_values_after_next_award(
        state,
        "acting_player",
        future,
        simulations_per_winner=350,
    )
    active_opponents = [
        str(profile.get("player"))
        for profile in context.get("active_opponent_profiles") or []
        if str(profile.get("player")) in counts
        and str(profile.get("player")) != "acting_player"
    ]
    if not active_opponents:
        active_opponents = [
            player for player in state.participants if player != "acting_player"
        ]
    if not active_opponents:
        return {
            "available": False,
            "hard_block": True,
            "rule_status": "invalid_state",
            "reason": "当前手没有可获得鱿鱼的有效对手",
        }
    opponent_weights = {
        player: max(0.0, future.get(player, 0.0))
        for player in active_opponents
    }
    opponent_weight_total = sum(opponent_weights.values())
    if opponent_weight_total <= 0:
        opponent_weights = {player: 1.0 for player in active_opponents}
        opponent_weight_total = float(len(active_opponents))
    round_ev = {}
    for candidate in candidates:
        raw_probability = candidate.get("award_probability")
        hero_probability = (
            float(raw_probability) if raw_probability is not None else equity
        )
        hero_probability = min(1.0, max(0.0, hero_probability))
        expected = hero_probability * values_after.get("acting_player", 0.0)
        expected += sum(
            (1.0 - hero_probability)
            * opponent_weights[player]
            / opponent_weight_total
            * values_after.get(player, 0.0)
            for player in active_opponents
        )
        round_ev[str(candidate["id"])] = round(expected, 4)
    return {
        "available": True,
        "calibrated": (
            state.squid_value is not None
            and state.rule_status
            in {
                "validated",
                "configured_basis",
                "configured_unverified",
            }
        ),
        "unit": "chips" if state.squid_value is not None else "squid_value_unit",
        "squid_value": state.squid_value,
        "payout_basis": state.payout_basis,
        "calibration_confidence": state.calibration_confidence,
        "calibration_rounds": state.calibration_rounds,
        "rule_status": state.rule_status,
        "award_probability_method": award_model.get("method")
        or "uniform-no-history",
        "award_probability_evidence_hands": int(
            award_model.get("evidence_hands") or 0
        ),
        "awarded_squids": state.awarded_squids,
        "total_squids": state.total_squids,
        "zero_squid_players": state.zero_squid_players,
        "hero_squid_count": counts["acting_player"],
        "round_ev_by_action": round_ev,
    }


def _reference_round_ev(
    candidates: Sequence[Mapping[str, Any]], squid: Mapping[str, Any]
) -> Optional[float]:
    values = squid.get("round_ev_by_action") or {}
    reference = _reference_candidate_id(candidates)
    return values.get(reference) if reference else None


def _reference_candidate_id(
    candidates: Sequence[Mapping[str, Any]]
) -> Optional[str]:
    for action in ("fold", "check", "call"):
        match = next(
            (item for item in candidates if item.get("action") == action),
            None,
        )
        if match:
            return str(match["id"])
    return str(candidates[0]["id"]) if candidates else None


def _baseline_action_mix(baseline: Mapping[str, Any]) -> Dict[str, float]:
    frequencies = {}
    if baseline.get("kind") == "preflop_matrix":
        hero_hand = baseline.get("hero_hand")
        cell = next(
            (
                item
                for item in baseline.get("cells") or []
                if item.get("hand") == hero_hand
            ),
            None,
        )
        frequencies = (cell or {}).get("frequencies") or {}
    elif baseline.get("kind") == "postflop_buckets":
        hero_bucket = baseline.get("hero_bucket")
        bucket = next(
            (
                item
                for item in baseline.get("buckets") or []
                if item.get("key") == hero_bucket
            ),
            None,
        )
        frequencies = (bucket or {}).get("frequencies") or {}
    total = sum(max(0.0, float(value)) for value in frequencies.values())
    return (
        {
            str(action): max(0.0, float(value)) / total
            for action, value in frequencies.items()
        }
        if total > 0
        else {}
    )


def _mixed_policy(
    candidates: Sequence[Mapping[str, Any]],
    baseline: Mapping[str, float],
    exploit_weight: float,
    pot: float,
    big_blind: float,
) -> List[Dict[str, Any]]:
    if not candidates:
        return []
    temperature = max(big_blind * 1.5, pot * 0.08, 1.0)
    maximum = max(float(item["robust_ev"]) for item in candidates)
    weights = [
        math.exp(max(-30.0, (float(item["robust_ev"]) - maximum) / temperature))
        for item in candidates
    ]
    normalizer = sum(weights) or 1.0
    best_raise = max(
        (item for item in candidates if item.get("action") == "raise"),
        key=lambda item: float(item["robust_ev"]),
        default=None,
    )
    rows = []
    for candidate, weight in zip(candidates, weights):
        action = str(candidate["action"])
        baseline_probability = float(baseline.get(action, 0.0))
        if action == "raise" and candidate is not best_raise:
            baseline_probability = 0.0
        probability = (
            (1.0 - exploit_weight) * baseline_probability
            + exploit_weight * weight / normalizer
        )
        rows.append(
            {
                "id": candidate["id"],
                "action": action,
                "raise_to": candidate.get("raise_to"),
                "_probability": probability,
                "robust_ev": candidate["robust_ev"],
            }
        )
    total = sum(item["_probability"] for item in rows)
    if total <= 0:
        best = max(rows, key=lambda item: item["robust_ev"])
        best["_probability"] = 1.0
        total = 1.0
    rounded = []
    for item in rows:
        rounded.append(
            {
                key: value for key, value in item.items() if key != "_probability"
            }
        )
        rounded[-1]["frequency_pct"] = round(
            100 * item["_probability"] / total, 1
        )
    difference = round(100.0 - sum(item["frequency_pct"] for item in rounded), 1)
    if rounded and difference:
        largest = max(rounded, key=lambda item: item["frequency_pct"])
        largest["frequency_pct"] = round(
            largest["frequency_pct"] + difference, 1
        )
    return sorted(
        rounded,
        key=lambda item: (-item["frequency_pct"], -item["robust_ev"], item["id"]),
    )


def _uncertainty_penalty(
    decision: Mapping[str, Any],
    candidate: Mapping[str, Any],
    trials: int,
    fold_model: Mapping[str, Any],
    squid_calibrated: bool,
) -> float:
    pot = _number(decision.get("pot")) or 0.0
    sample_penalty = pot / math.sqrt(max(1, trials))
    evidence_penalty = (
        0.08 * pot if int(fold_model.get("evidence_trials") or 0) < 20 else 0.03 * pot
    )
    squid_penalty = 0.04 * pot if not squid_calibrated else 0.0
    aggression_penalty = (
        0.04 * pot if candidate.get("action") in {"raise", "all_in"} else 0.0
    )
    return sample_penalty + evidence_penalty + squid_penalty + aggression_penalty


def _engine_confidence(
    decision: Mapping[str, Any],
    fold_model: Mapping[str, Any],
    squid: Mapping[str, Any],
) -> str:
    quality = decision.get("state_quality") or {}
    if not quality.get("target_game_supported", False):
        return "low"
    if quality.get("warnings"):
        return "low"
    if not squid.get("calibrated"):
        return "low"
    trials = int(fold_model.get("evidence_trials") or 0)
    if trials >= 100 and squid.get("calibration_confidence") in {"medium", "high"}:
        return "high"
    if trials >= 30:
        return "medium"
    return "low"


def _number(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)
