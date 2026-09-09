from __future__ import annotations

import math
import secrets
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .decision_state import canonical_legal_actions
from .inference import selected_opponent_range
from .poker import (
    showdown_stats_vs_random_multiway,
    showdown_stats_vs_weighted_ranges,
)
from .offline_gto import (
    PUBLIC_STRATEGY_SOURCES,
    allow_postflop_all_in,
    classify_postflop_hand,
    postflop_profile,
    preferred_bet_fraction,
)
from .opponent_model import response_probability
from .squid_value import (
    hero_values_after_next_award,
    squid_state_from_dict,
)

ENGINE_VERSION = "money-ev-dynamic-sizing-v13"


def evaluate_money_strategy(
    context: Dict[str, Any],
    baseline: Dict[str, Any],
    *,
    random_draw_pct: Optional[float] = None,
    trials_cap: Optional[int] = None,
) -> Dict[str, Any]:
    """Evaluate a restricted action set without presenting heuristic EV as GTO."""

    started = time.perf_counter()
    decision = context.get("decision") or {}
    quality = decision.get("state_quality") or {}
    if not quality.get("valid", True):
        return {
            "status": "blocked",
            "engine_version": ENGINE_VERSION,
            "reasons": list(quality.get("blocking_reasons") or []),
            "latency_ms": _elapsed_ms(started),
        }
    hole_cards = list(decision.get("hero_cards") or [])
    if len(hole_cards) != 2:
        return {
            "status": "range_only",
            "engine_version": ENGINE_VERSION,
            "baseline": baseline,
            "reasons": ["本人底牌未知，不能计算当前手牌反事实 EV"],
            "latency_ms": _elapsed_ms(started),
        }

    pot = max(0.0, _number(decision.get("pot")) or 0.0)
    opponents = max(1, int(decision.get("players_in_hand") or 2) - 1)
    opponent_profiles = list(
        context.get("active_opponent_profiles") or []
    )[:opponents]
    range_profiles = []
    range_models = []
    for profile in opponent_profiles:
        selected = selected_opponent_range(profile)
        range_profiles.append(selected)
        range_models.append(selected.get("weights") or {})
    while len(range_models) < opponents:
        range_models.append({})
    uses_ranges = any(bool(model) for model in range_models)
    trials = max(
        180 if uses_ranges else 220,
        round((420 if uses_ranges else 600) / math.sqrt(opponents)),
    )
    if trials_cap is not None:
        trials = min(trials, max(24, int(trials_cap)))
    board = list(decision.get("board") or [])
    street = str(decision.get("street") or "preflop")
    fit_profile = (
        postflop_profile(context) if street != "preflop" else None
    )
    hero_bucket = (
        classify_postflop_hand(decision) if street != "preflop" else None
    )
    preferred_size = (
        preferred_bet_fraction(context, hero_bucket)
        if street != "preflop"
        else None
    )
    preferred_preflop_raise_to = _preferred_preflop_open_to(context)
    fold_model = _fold_model(context, opponents)
    showdown = _showdown_stats(
        hole_cards, board, range_models, opponents, trials, uses_ranges
    )
    equity = float(showdown["pot_share"])
    award_probability = float(showdown["award_probability"])
    if uses_ranges:
        equity_model = (
            "action-line-posterior-ranges-multiway-v1"
            if any(
                profile.get("source") == "action_line_posterior"
                for profile in range_profiles
            )
            else "hierarchical-preflop-ranges-multiway-v1"
        )
    else:
        equity_model = "uniform-random-multiway-v1"
    all_fold = float(fold_model["all_fold_probability"])
    conditional_callers = (
        float(fold_model["expected_callers"]) / max(1e-9, 1.0 - all_fold)
        if all_fold < 1.0
        else 1.0
    )
    caller_count = max(1, min(opponents, int(round(conditional_callers))))
    fold_by_player = {
        str(item.get("player")): float(item.get("fold_probability") or 0.5)
        for item in fold_model.get("opponents") or []
    }
    likely_callers = sorted(
        zip(opponent_profiles, range_models),
        key=lambda item: (
            fold_by_player.get(str(item[0].get("player")), 0.5),
            str(item[0].get("player") or ""),
        ),
    )[:caller_count]
    caller_range_models = [model for _, model in likely_callers]
    while len(caller_range_models) < caller_count:
        caller_range_models.append({})
    continued_showdown = _showdown_stats(
        hole_cards,
        board,
        caller_range_models,
        caller_count,
        trials,
        uses_ranges,
    )
    fold_model["conditional_caller_count"] = caller_count
    fold_model["conditional_callers"] = round(conditional_callers, 4)
    candidates = _candidate_actions(decision, context)
    for candidate in candidates:
        candidate_showdown, continuation_audit = (
            _candidate_continuation_showdown(
                candidate,
                decision,
                hole_cards,
                board,
                opponent_profiles,
                range_models,
                fold_model,
                opponents,
                trials,
                continued_showdown,
            )
        )
        candidate.update(
            _candidate_chip_ev(
                candidate,
                decision,
                equity,
                award_probability,
                float(candidate_showdown["pot_share"]),
                float(candidate_showdown["award_probability"]),
                fold_model,
                opponents,
                conditional_range_enabled=bool(
                    continuation_audit.get("enabled")
                ),
                continuation_audit=continuation_audit,
            )
        )
        if candidate["action"] in {"raise", "bet", "all_in"}:
            candidate["continuation_range_model"] = continuation_audit
    uses_conditional_ranges = any(
        bool(candidate.get("conditional_range_enabled"))
        for candidate in candidates
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
        sizing_penalty = _sizing_penalty(
            candidate,
            decision,
            preferred_size,
        )
        sizing_penalty += _preflop_open_sizing_penalty(
            candidate,
            decision,
            preferred_preflop_raise_to,
        )
        raise_risk_penalty = _raise_risk_penalty(decision, candidate)
        candidate["uncertainty_penalty"] = round(uncertainty, 2)
        candidate["sizing_penalty"] = round(sizing_penalty, 2)
        candidate["raise_risk_penalty"] = round(
            raise_risk_penalty, 2
        )
        candidate["robust_ev"] = round(
            combined - uncertainty - sizing_penalty - raise_risk_penalty,
            2,
        )

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
        allow_all_in=(
            street == "preflop"
            or allow_postflop_all_in(context, hero_bucket)
        ),
    )
    most_frequent = (
        max(policy, key=lambda item: item["frequency_pct"]) if policy else None
    )
    random_selection = _weighted_random_selection(policy, random_draw_pct)
    selected = (
        random_selection.get("selected")
        if random_selection
        else most_frequent
    )
    sizing_recommendation = _dynamic_sizing_recommendation(
        candidates,
        decision,
        fold_model,
        selected,
    )
    reasons = _strategy_reasons(
        decision,
        fit_profile,
        hero_bucket,
        selected,
        preferred_size,
    )
    reasons.extend(_opponent_model_reasons(fold_model))
    if (
        selected
        and selected.get("action") == "raise"
        and sizing_recommendation.get("enabled")
    ):
        reasons.append(str(sizing_recommendation["reason"]))
    return {
        "status": "experimental",
        "engine_version": ENGINE_VERSION,
        "scope": (
            f"{int(decision.get('table_players') or opponents + 1)}-max · "
            f"{str(decision.get('game_mode') or 'holdem')} · "
            f"{(fit_profile or {}).get('profile_id') or 'preflop'}"
        ),
        "confidence": confidence,
        "reasons": reasons,
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
            "candidate_continuation_ranges_enabled": uses_conditional_ranges,
            "range_models": [
                {
                    key: value
                    for key, value in profile.items()
                    if key != "weights"
                }
                for profile in range_profiles
                if profile
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
        "fit_profile": fit_profile,
        "hero_bucket": hero_bucket,
        "preferred_bet_fraction": preferred_size,
        "preferred_preflop_raise_to": preferred_preflop_raise_to,
        "sizing_recommendation": sizing_recommendation,
        "strategy_sources": list(PUBLIC_STRATEGY_SOURCES),
        "recommended": selected,
        "most_frequent": most_frequent,
        "random_selection": random_selection,
        "policy": policy,
        "candidates": sorted(
            candidates,
            key=lambda item: (-item["robust_ev"], item["id"]),
        ),
        "caveats": [
            (
                "equity 已按行动线更新后的范围与阻断牌采样"
                if any(profile.get("enabled") for profile in range_profiles)
                else "equity 已按各对手翻前 169 类范围与阻断牌采样，但行动线范围模型尚未通过启用门"
                if uses_ranges
                else "缺少可用对手范围，equity 退化为多方均匀随机手牌"
            ),
            (
                "下注候选已使用通过时间外门控的 call/raise 条件范围"
                if uses_conditional_ranges
                else "下注后继续范围仍使用尺度相关启发式收紧"
            ),
            "只有校准出鱿鱼筹码价值后，squid_ev 才会并入 money_ev",
            "当前反事实尚未逐动作计入抽水、保险、基金和复杂多人边池现金流",
        ],
        "latency_ms": _elapsed_ms(started),
    }


def _strategy_reasons(
    decision: Mapping[str, Any],
    profile: Optional[Mapping[str, Any]],
    hero_bucket: Optional[str],
    primary: Optional[Mapping[str, Any]],
    preferred_size: Optional[float],
) -> List[str]:
    if profile is None:
        return ["翻前频率按桌上人数、位置、ante 与有效筹码拟合"]
    labels = {
        "strong_value": "强价值",
        "top_pair_strong": "强顶对/超对",
        "top_pair_weak": "弱踢脚顶对",
        "showdown": "中弱摊牌价值",
        "strong_draw": "强听牌",
        "weak_draw": "弱听牌",
        "air": "空气",
    }
    position = {"ip": "有位置", "oop": "无位置", "middle": "夹在中间"}.get(
        str(profile.get("position")),
        "位置不完整",
    )
    players = int(profile.get("players_in_hand") or 0)
    reasons = [
        f"{labels.get(str(hero_bucket), str(hero_bucket or '未知牌力'))} · "
        f"{players} 人底池 · {position} · {profile.get('pot_type')}",
    ]
    if primary and primary.get("action") == "raise" and preferred_size:
        reasons.append(f"拟合下注尺度约 {preferred_size:.0%} 底池")
    texture = profile.get("texture") or {}
    reasons.append(
        f"牌面湿度 {float(texture.get('wetness') or 0):.0%}，"
        f"SPR {profile.get('spr') if profile.get('spr') is not None else '未知'}"
    )
    return reasons


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


def _candidate_continuation_showdown(
    candidate: Mapping[str, Any],
    decision: Mapping[str, Any],
    hole_cards: Sequence[str],
    board: Sequence[str],
    opponent_profiles: Sequence[Mapping[str, Any]],
    base_range_models: Sequence[Mapping[str, float]],
    fold_model: Mapping[str, Any],
    opponents: int,
    trials: int,
    fallback: Mapping[str, float],
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    if str(candidate.get("action") or "") not in {"raise", "bet", "all_in"}:
        return dict(fallback), {"enabled": False}
    size_ratio = _candidate_size_ratio(candidate, decision)
    probabilities = _candidate_fold_probabilities(
        fold_model, size_ratio, opponents
    )
    response_rows = list(fold_model.get("opponents") or [])
    raise_probabilities = []
    call_probabilities = []
    for index in range(opponents):
        row = response_rows[index] if index < len(response_rows) else {}
        sized_raise = _size_curve_action_probability(
            row.get("size_curve"), size_ratio, "raise"
        )
        base_raise = float(row.get("raise_probability") or 0.0)
        raise_probabilities.append(
            max(
                0.0,
                min(
                    0.95,
                    sized_raise
                    if sized_raise is not None
                    else base_raise,
                ),
            )
        )
        sized_call = _size_curve_action_probability(
            row.get("size_curve"), size_ratio, "call"
        )
        base_call = row.get("call_probability")
        call_probabilities.append(
            max(
                0.0,
                min(
                    0.95,
                    sized_call
                    if sized_call is not None
                    else (
                        float(base_call)
                        if base_call is not None
                        else max(
                            0.0,
                            1.0
                            - probabilities[index]
                            - raise_probabilities[index],
                        )
                    ),
                ),
            )
        )
    all_fold = _joint_corrected_all_fold(
        math.prod(probabilities),
        fold_model.get("joint_response_model"),
        size_ratio,
    )
    expected_callers = sum(1.0 - probability for probability in probabilities)
    conditional_callers = (
        expected_callers / max(1e-9, 1.0 - all_fold)
        if all_fold < 1.0
        else 0.0
    )
    caller_count = max(
        1, min(opponents, int(round(conditional_callers)))
    )
    likely_indices = sorted(
        range(opponents),
        key=lambda index: (
            probabilities[index] if index < len(probabilities) else 0.5,
            index,
        ),
    )[:caller_count]
    size_bucket = _continuation_size_bucket(size_ratio)
    selected_ranges = []
    audits = []
    conditioned_count = 0
    for index in likely_indices:
        profile = (
            opponent_profiles[index]
            if index < len(opponent_profiles)
            else {}
        )
        base_range = (
            base_range_models[index]
            if index < len(base_range_models)
            else {}
        )
        continuation = profile.get("continuation_ranges") or {}
        bucket = (continuation.get("buckets") or {}).get(size_bucket) or {}
        conditioned = bool(
            continuation.get("enabled") and bucket.get("weights")
        )
        selected_ranges.append(
            bucket.get("weights") if conditioned else base_range
        )
        conditioned_count += int(conditioned)
        audits.append(
            {
                "player": profile.get("player") or f"opponent_{index + 1}",
                "fold_probability_pct": round(
                    100
                    * (
                        probabilities[index]
                        if index < len(probabilities)
                        else 0.5
                    ),
                    1,
                ),
                "conditioned": conditioned,
                "continuation_model_weight": bucket.get("model_weight"),
                "call_probability_pct": round(
                    100
                    * (
                        call_probabilities[index]
                        if index < len(call_probabilities)
                        else 0.0
                    ),
                    1,
                ),
                "raise_probability_pct": round(
                    100
                    * (
                        raise_probabilities[index]
                        if index < len(raise_probabilities)
                        else 0.0
                    ),
                    1,
                ),
                "baseline_strength_distribution_pct": bucket.get(
                    "baseline_strength_distribution_pct"
                ),
                "strength_distribution_pct": bucket.get(
                    "strength_distribution_pct"
                ),
                "call_strength_distribution_pct": bucket.get(
                    "call_strength_distribution_pct"
                ),
                "raise_strength_distribution_pct": bucket.get(
                    "raise_strength_distribution_pct"
                ),
                "raise_size_model": row.get("raise_size_model") or {},
                "top_weight_shifts": list(
                    bucket.get("top_weight_shifts") or []
                )[:5],
            }
        )
    fully_conditioned = bool(
        selected_ranges
        and conditioned_count == len(selected_ranges)
        and all(bool(model) for model in selected_ranges)
    )
    showdown = (
        _showdown_stats(
            hole_cards,
            board,
            selected_ranges,
            caller_count,
            trials,
            uses_ranges=True,
        )
        if fully_conditioned
        else dict(fallback)
    )
    any_raise_probability = 1.0 - math.prod(
        1.0 - probability for probability in raise_probabilities
    )
    pure_call_branch_probability = max(
        0.0, 1.0 - all_fold - min(any_raise_probability, 1.0 - all_fold)
    )
    expected_pure_callers = sum(call_probabilities)
    conditional_pure_callers = (
        expected_pure_callers / max(1e-9, pure_call_branch_probability)
        if pure_call_branch_probability > 0
        else 0.0
    )
    call_caller_count = max(
        1,
        min(opponents, int(round(max(1.0, conditional_pure_callers)))),
    )
    likely_call_indices = sorted(
        range(opponents),
        key=lambda index: (-call_probabilities[index], index),
    )[:call_caller_count]
    selected_call_ranges = []
    call_conditioned_count = 0
    for index in likely_call_indices:
        profile = (
            opponent_profiles[index]
            if index < len(opponent_profiles)
            else {}
        )
        base_range = (
            base_range_models[index]
            if index < len(base_range_models)
            else {}
        )
        continuation = profile.get("continuation_ranges") or {}
        bucket = (continuation.get("buckets") or {}).get(size_bucket) or {}
        call_conditioned = bool(
            continuation.get("enabled")
            and bucket.get("call_enabled")
            and bucket.get("call_weights")
        )
        selected_call_ranges.append(
            bucket.get("call_weights") if call_conditioned else base_range
        )
        call_conditioned_count += int(call_conditioned)
    fully_call_conditioned = bool(
        selected_call_ranges
        and call_conditioned_count == len(selected_call_ranges)
        and all(bool(model) for model in selected_call_ranges)
    )
    call_showdown = (
        _showdown_stats(
            hole_cards,
            board,
            selected_call_ranges,
            call_caller_count,
            trials,
            uses_ranges=True,
        )
        if fully_call_conditioned
        else dict(showdown)
    )
    likely_raiser_index = (
        max(
            range(opponents),
            key=lambda index: (
                raise_probabilities[index],
                -index,
            ),
        )
        if opponents and raise_probabilities
        else None
    )
    raise_range: Mapping[str, float] = {}
    raise_size_model: Mapping[str, Any] = {}
    raise_range_conditioned = False
    if likely_raiser_index is not None:
        raise_profile = (
            opponent_profiles[likely_raiser_index]
            if likely_raiser_index < len(opponent_profiles)
            else {}
        )
        raise_base_range = (
            base_range_models[likely_raiser_index]
            if likely_raiser_index < len(base_range_models)
            else {}
        )
        raise_continuation = raise_profile.get("continuation_ranges") or {}
        raise_bucket = (
            (raise_continuation.get("buckets") or {}).get(size_bucket) or {}
        )
        if (
            raise_continuation.get("enabled")
            and raise_bucket.get("raise_enabled")
            and raise_bucket.get("raise_weights")
        ):
            raise_range = raise_bucket["raise_weights"]
            raise_range_conditioned = True
        else:
            raise_range = raise_base_range
        raise_row = (
            response_rows[likely_raiser_index]
            if likely_raiser_index < len(response_rows)
            else {}
        )
        raise_size_model = raise_row.get("raise_size_model") or {}
    raise_showdown = (
        _showdown_stats(
            hole_cards,
            board,
            [raise_range],
            1,
            trials,
            uses_ranges=True,
        )
        if raise_range_conditioned
        else dict(showdown)
    )
    explicit_raise_branch_enabled = bool(
        fully_call_conditioned
        and raise_range_conditioned
        and raise_size_model.get("enabled")
        and any_raise_probability > 0
    )
    return showdown, {
        "enabled": fully_conditioned,
        "size_bucket": size_bucket,
        "candidate_size_ratio": round(size_ratio, 4),
        "conditional_caller_count": caller_count,
        "conditional_callers": round(conditional_callers, 4),
        "conditional_pure_caller_count": call_caller_count,
        "conditional_pure_callers": round(conditional_pure_callers, 4),
        "expected_pure_callers": round(expected_pure_callers, 4),
        "conditioned_callers": conditioned_count,
        "expected_raisers": round(sum(raise_probabilities), 4),
        "any_raise_probability": round(any_raise_probability, 5),
        "explicit_raise_branch_enabled": explicit_raise_branch_enabled,
        "call_range_conditioned": fully_call_conditioned,
        "raise_range_conditioned": raise_range_conditioned,
        "likely_raiser_index": likely_raiser_index,
        "expected_raise_multiple": (
            raise_size_model.get("expected_multiple")
            if raise_size_model.get("enabled")
            else None
        ),
        "call_showdown": call_showdown,
        "raise_showdown": raise_showdown,
        "callers": audits,
        "method": (
            "action-conditioned-caller-ranges-v1"
            if fully_conditioned
            else "heuristic-continued-equity-fallback"
        ),
    }


def _candidate_size_ratio(
    candidate: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> float:
    pot = max(0.0, _number(decision.get("pot")) or 0.0)
    call = max(0.0, _number(decision.get("call_score")) or 0.0)
    contribution = _number(decision.get("hero_street_contribution")) or 0.0
    target = (
        _number(candidate.get("raise_to"))
        if str(candidate.get("action") or "") == "raise"
        else _number(decision.get("max_raise_to"))
    )
    if target is None:
        target = contribution + max(
            call, _number(decision.get("hero_stack")) or 0.0
        )
    return max(0.0, target - contribution) / max(pot, 1e-9)


def _continuation_size_bucket(size_ratio: float) -> str:
    if size_ratio <= 0.33:
        return "small"
    if size_ratio <= 0.75:
        return "medium"
    if size_ratio <= 1.10:
        return "pot"
    return "overbet"


def _preferred_preflop_open_to(
    context: Mapping[str, Any],
) -> Optional[float]:
    decision = context.get("decision") or {}
    if (
        str(decision.get("street") or "preflop") != "preflop"
        or "raise" not in canonical_legal_actions(
            decision.get("legal_actions")
        )
    ):
        return None
    voluntary_actions = {
        "call",
        "raise",
        "bet",
        "all_in",
        "all-in",
        "allin",
    }
    if any(
        str(action.get("action") or "").lower() in voluntary_actions
        for action in context.get("action_history") or []
        if str(action.get("street") or "") == "preflop"
    ):
        return None
    big_blind = _number(decision.get("big_blind")) or 0.0
    if big_blind <= 0:
        return None
    position = str(decision.get("hero_position") or "").upper()
    position = {
        "SMALL_BLIND": "SB",
        "BTN/SB": "SB",
        "BIG_BLIND": "BB",
    }.get(position, position)
    if position == "BB":
        return None
    table_players = max(
        2,
        min(9, int(decision.get("table_players") or 6)),
    )
    ante = max(0.0, _number(decision.get("ante")) or 0.0)
    total_ante_bb = ante * table_players / big_blind
    if position == "SB":
        multiple = 3.5 if total_ante_bb > 0 else 3.0
    else:
        multiple = 3.0 if total_ante_bb > 0 else 2.5
    target = multiple * big_blind
    minimum = _number(decision.get("min_raise_to"))
    maximum = _number(decision.get("max_raise_to"))
    if minimum is not None:
        target = max(target, minimum)
    if maximum is not None:
        target = min(target, maximum)
    return round(target, 2)


def _candidate_actions(
    decision: Mapping[str, Any],
    context: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    legal = list(dict.fromkeys(canonical_legal_actions(decision.get("legal_actions"))))
    result = []
    preferred_open_to = _preferred_preflop_open_to(
        context or {"decision": decision}
    )
    for action in legal:
        if action != "raise":
            result.append({"id": action, "action": action, "raise_to": None})
            continue
        minimum = _number(decision.get("min_raise_to"))
        maximum = _number(decision.get("max_raise_to"))
        contribution = _number(decision.get("hero_street_contribution")) or 0.0
        pot = _number(decision.get("pot")) or 0.0
        targets = [
            ("preflop_standard", preferred_open_to),
            ("min", minimum),
            ("third_pot", contribution + 0.3 * pot),
            ("half_pot", contribution + 0.5 * pot),
            ("three_quarter_pot", contribution + 0.75 * pot),
            ("pot", contribution + pot),
            ("overbet", contribution + 1.25 * pot),
        ]
        open_floor = None
        if preferred_open_to is not None:
            big_blind = _number(decision.get("big_blind")) or 0.0
            position = str(decision.get("hero_position") or "").upper()
            is_small_blind = position in {"SB", "SMALL_BLIND", "BTN/SB"}
            if big_blind > 0:
                open_floor = big_blind * (3.0 if is_small_blind else 2.5)
                if maximum is not None and maximum < open_floor:
                    open_floor = None
        seen = set()
        for label, target in targets:
            if target is None:
                continue
            if minimum is not None:
                target = max(minimum, target)
            if maximum is not None:
                target = min(maximum, target)
            rounded = round(target, 2)
            if open_floor is not None and rounded < open_floor:
                continue
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


def _dynamic_sizing_recommendation(
    candidates: Sequence[Mapping[str, Any]],
    decision: Mapping[str, Any],
    fold_model: Mapping[str, Any],
    selected: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    raise_candidates = [
        candidate
        for candidate in candidates
        if candidate.get("action") == "raise"
        and candidate.get("raise_to") is not None
    ]
    if not raise_candidates:
        return {
            "enabled": False,
            "reason": "当前没有合法的非全下加注尺度",
        }
    ev_best = max(
        raise_candidates,
        key=lambda item: (
            float(item.get("robust_ev") or 0.0),
            -float(item.get("raise_to") or 0.0),
        ),
    )
    selected_raise = (
        next(
            (
                candidate
                for candidate in raise_candidates
                if candidate.get("id") == selected.get("id")
            ),
            None,
        )
        if selected and selected.get("action") == "raise"
        else None
    )
    recommended = selected_raise or ev_best
    opponent_responses = _sizing_opponent_responses(
        recommended,
        decision,
        fold_model,
    )
    if any(
        row.get("source") == "player_history"
        for row in opponent_responses
    ):
        source = "active_opponent_history"
        reason = "已按在场选手在相似节点面对各尺度的历史反应调整"
    elif any(
        row.get("source") == "population_context"
        for row in opponent_responses
    ):
        source = "population_context"
        reason = "在场选手个人尺度样本不足，使用相似牌池响应后验"
    else:
        source = "size_adjusted_heuristic"
        reason = "缺少可用尺度响应样本，使用牌面与底池尺度基线"
    rows = []
    for candidate in sorted(
        raise_candidates,
        key=lambda item: float(item.get("raise_to") or 0.0),
    ):
        ratio = _candidate_size_ratio(candidate, decision)
        rows.append(
            {
                "id": candidate.get("id"),
                "raise_to": candidate.get("raise_to"),
                "pot_fraction_pct": round(100 * ratio, 1),
                "chip_ev": candidate.get("chip_ev"),
                "robust_ev": candidate.get("robust_ev"),
                "all_fold_pct": round(
                    100
                    * float(
                        candidate.get("effective_fold_probability") or 0.0
                    ),
                    1,
                ),
                "expected_callers": candidate.get("expected_callers"),
                "selected": candidate.get("id") == recommended.get("id"),
                "ev_best": candidate.get("id") == ev_best.get("id"),
            }
        )
    return {
        "enabled": True,
        "source": source,
        "reason": reason,
        "recommended_raise_to": recommended.get("raise_to"),
        "recommended_pot_fraction_pct": round(
            100 * _candidate_size_ratio(recommended, decision),
            1,
        ),
        "ev_best_raise_to": ev_best.get("raise_to"),
        "selected_by_policy": selected_raise is not None,
        "candidates": rows,
        "opponent_responses": opponent_responses,
        "method": "active-opponent-size-response-ev-v1",
    }


def _sizing_opponent_responses(
    candidate: Mapping[str, Any],
    decision: Mapping[str, Any],
    fold_model: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    ratio = _candidate_size_ratio(candidate, decision)
    bucket = _bet_size_bucket(ratio)
    result = []
    for row in fold_model.get("opponents") or []:
        size_row = (row.get("size_curve") or {}).get(bucket) or {}
        probabilities = size_row.get("probabilities_pct") or {}
        effective_samples = float(size_row.get("effective_samples") or 0.0)
        fold_pct = probabilities.get("fold")
        call_pct = probabilities.get("call")
        raise_pct = probabilities.get("raise")
        if fold_pct is None:
            fold_pct = 100 * float(row.get("fold_probability") or 0.0)
        if call_pct is None and row.get("call_probability") is not None:
            call_pct = 100 * float(row["call_probability"])
        if raise_pct is None and row.get("raise_probability") is not None:
            raise_pct = 100 * float(row["raise_probability"])
        personal = bool(
            size_row
            and row.get("player_residual_enabled")
            and effective_samples > 0
        )
        result.append(
            {
                "player": row.get("player"),
                "display_name": row.get("display_name"),
                "size_bucket": bucket,
                "fold_pct": round(float(fold_pct or 0.0), 1),
                "call_pct": (
                    round(float(call_pct), 1)
                    if call_pct is not None
                    else None
                ),
                "raise_pct": (
                    round(float(raise_pct), 1)
                    if raise_pct is not None
                    else None
                ),
                "effective_samples": round(effective_samples, 1),
                "confidence": size_row.get("confidence")
                or row.get("confidence"),
                "source": (
                    "player_history"
                    if personal
                    else "population_context"
                    if size_row
                    else "heuristic"
                ),
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
    opponents: int,
    *,
    conditional_range_enabled: bool = False,
    continuation_audit: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
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
    size_ratio = _candidate_size_ratio(candidate, decision)
    all_fold, expected_callers = _candidate_response_totals(
        fold_model, size_ratio, opponents
    )
    independent_all_fold, _ = _candidate_response_totals(
        {**fold_model, "joint_response_model": None},
        size_ratio,
        opponents,
    )
    called_equity = (
        continued_equity
        if conditional_range_enabled
        else _continued_range_equity(
            continued_equity,
            size_ratio,
            opponents,
        )
    )
    called_award_probability = (
        continued_award_probability
        if conditional_range_enabled
        else _continued_range_equity(
            continued_award_probability,
            size_ratio,
            opponents,
        )
    )
    conditional_callers = (
        expected_callers / max(1e-9, 1.0 - all_fold)
        if all_fold < 1
        else 0.0
    )
    contested = pot + cost + call_increment * conditional_callers
    continued_ev = called_equity * contested - cost
    chip_ev = all_fold * pot + (1.0 - all_fold) * continued_ev
    aggressive_award_probability = all_fold + (
        1.0 - all_fold
    ) * called_award_probability
    explicit_branch = _explicit_raise_branch_ev(
        decision,
        candidate,
        all_fold=all_fold,
        cost=cost,
        target=target,
        facing_total=facing_total,
        call_increment=call_increment,
        continuation_audit=continuation_audit,
        fallback_call_equity=called_equity,
        fallback_call_award_probability=called_award_probability,
        opponents=opponents,
    )
    if explicit_branch.get("enabled"):
        chip_ev = float(explicit_branch["chip_ev"])
        aggressive_award_probability = float(
            explicit_branch["award_probability"]
        )
    return {
        "chip_ev": round(chip_ev, 2),
        "award_probability": round(aggressive_award_probability, 5),
        "called_equity": round(called_equity, 5),
        "effective_fold_probability": round(all_fold, 5),
        "independent_fold_probability": round(
            independent_all_fold, 5
        ),
        "joint_fold_adjustment_pp": round(
            100 * (all_fold - independent_all_fold), 2
        ),
        "expected_callers": round(expected_callers, 5),
        "conditional_range_enabled": conditional_range_enabled,
        "explicit_raise_branch": explicit_branch,
    }


def _explicit_raise_branch_ev(
    decision: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    all_fold: float,
    cost: float,
    target: float,
    facing_total: float,
    call_increment: float,
    continuation_audit: Optional[Mapping[str, Any]],
    fallback_call_equity: float,
    fallback_call_award_probability: float,
    opponents: int,
) -> Dict[str, Any]:
    audit = continuation_audit or {}
    action = str(candidate.get("action") or "")
    maximum = _number(decision.get("max_raise_to"))
    enabled = bool(
        action in {"bet", "raise"}
        and audit.get("explicit_raise_branch_enabled")
        and (maximum is None or target < maximum - 1e-9)
    )
    if not enabled:
        return {
            "enabled": False,
            "reason": (
                "raise size / call range / raise range 未同时通过门控"
            ),
        }
    any_raise = max(
        0.0, min(1.0 - all_fold, float(audit.get("any_raise_probability") or 0))
    )
    call_branch_probability = max(0.0, 1.0 - all_fold - any_raise)
    call_showdown = audit.get("call_showdown") or {}
    call_equity = max(
        0.0,
        min(
            1.0,
            float(call_showdown.get("pot_share") or fallback_call_equity),
        ),
    )
    call_award = max(
        0.0,
        min(
            1.0,
            float(
                call_showdown.get("award_probability")
                or fallback_call_award_probability
            ),
        ),
    )
    expected_pure_callers = max(
        0.0, float(audit.get("expected_pure_callers") or 0.0)
    )
    conditional_pure_callers = (
        min(
            float(max(1, opponents)),
            max(
                1.0,
                expected_pure_callers / max(1e-9, call_branch_probability),
            ),
        )
        if call_branch_probability > 0
        else 0.0
    )
    call_pot = pot = max(0.0, _number(decision.get("pot")) or 0.0)
    call_pot += cost + call_increment * conditional_pure_callers
    call_ev = call_equity * call_pot - cost

    expected_multiple = max(
        1.5, min(6.0, float(audit.get("expected_raise_multiple") or 3.0))
    )
    raise_to = target * expected_multiple
    if maximum is not None:
        raise_to = min(raise_to, maximum)
    raise_to = max(target, raise_to)
    opponent_raise_increment = max(0.0, raise_to - facing_total)
    hero_call_increment = max(0.0, raise_to - target)
    raise_showdown = audit.get("raise_showdown") or {}
    raise_equity = max(
        0.0, min(1.0, float(raise_showdown.get("pot_share") or 0.0))
    )
    raise_award = max(
        0.0,
        min(1.0, float(raise_showdown.get("award_probability") or 0.0)),
    )
    raised_pot_after_call = (
        pot + cost + opponent_raise_increment + hero_call_increment
    )
    fold_to_raise_ev = -cost
    call_raise_ev = (
        raise_equity * raised_pot_after_call - cost - hero_call_increment
    )
    continue_vs_raise = call_raise_ev > fold_to_raise_ev
    raise_branch_ev = (
        call_raise_ev if continue_vs_raise else fold_to_raise_ev
    )
    chip_ev = (
        all_fold * pot
        + call_branch_probability * call_ev
        + any_raise * raise_branch_ev
    )
    award_probability = (
        all_fold
        + call_branch_probability * call_award
        + any_raise * (raise_award if continue_vs_raise else 0.0)
    )
    return {
        "enabled": True,
        "fold_probability": round(all_fold, 5),
        "call_probability": round(call_branch_probability, 5),
        "raise_probability": round(any_raise, 5),
        "conditional_callers": round(conditional_pure_callers, 4),
        "call_equity": round(call_equity, 5),
        "call_branch_ev": round(call_ev, 2),
        "expected_raise_multiple": round(expected_multiple, 3),
        "expected_raise_to": round(raise_to, 2),
        "raise_equity": round(raise_equity, 5),
        "hero_response_to_raise": (
            "call" if continue_vs_raise else "fold"
        ),
        "fold_to_raise_ev": round(fold_to_raise_ev, 2),
        "call_raise_ev": round(call_raise_ev, 2),
        "raise_branch_ev": round(raise_branch_ev, 2),
        "chip_ev": round(chip_ev, 2),
        "award_probability": round(award_probability, 5),
        "method": "fold-call-reraise-branch-v1",
    }


def _candidate_response_totals(
    fold_model: Mapping[str, Any],
    size_ratio: float,
    opponents: int,
) -> Tuple[float, float]:
    probabilities = _candidate_fold_probabilities(
        fold_model, size_ratio, opponents
    )
    independent_all_fold = math.prod(probabilities)
    all_fold = _joint_corrected_all_fold(
        independent_all_fold,
        fold_model.get("joint_response_model"),
        size_ratio,
    )
    return all_fold, sum(1.0 - value for value in probabilities)


def _candidate_fold_probabilities(
    fold_model: Mapping[str, Any],
    size_ratio: float,
    opponents: int,
) -> List[float]:
    response_rows = list(fold_model.get("opponents") or [])
    probabilities = []
    for row in response_rows[:opponents]:
        base = max(
            0.02,
            min(0.95, float(row.get("fold_probability") or 0.0)),
        )
        sized = _size_curve_probability(row.get("size_curve"), size_ratio)
        probabilities.append(
            sized
            if sized is not None
            else _size_adjusted_single_fold_probability(base, size_ratio)
        )
    fallback = list(fold_model.get("per_opponent_pct") or [])
    while len(probabilities) < opponents:
        index = len(probabilities)
        base = (
            float(fallback[index]) / 100
            if index < len(fallback)
            else 0.45
        )
        probabilities.append(
            _size_adjusted_single_fold_probability(base, size_ratio)
        )
    return probabilities[:opponents]


def _size_curve_probability(
    size_curve: Any,
    size_ratio: float,
) -> Optional[float]:
    value = _size_curve_action_probability(
        size_curve, size_ratio, "fold"
    )
    return max(0.02, value) if value is not None else None


def _size_curve_action_probability(
    size_curve: Any,
    size_ratio: float,
    action: str,
) -> Optional[float]:
    if not isinstance(size_curve, Mapping):
        return None
    bucket = _bet_size_bucket(size_ratio)
    row = size_curve.get(bucket)
    if not isinstance(row, Mapping):
        return None
    value = (row.get("probabilities_pct") or {}).get(action)
    if value is None:
        return None
    return max(0.0, min(0.95, float(value) / 100))


def _bet_size_bucket(value: float) -> str:
    if value <= 0.30:
        return "tiny"
    if value <= 0.55:
        return "small"
    if value <= 0.85:
        return "medium"
    if value <= 1.15:
        return "large"
    return "overbet"


def _size_adjusted_single_fold_probability(
    base_probability: float,
    size_ratio: float,
) -> float:
    if size_ratio <= 0:
        return max(0.02, min(0.95, base_probability))
    shift = 0.10 * math.log2(max(0.25, size_ratio) / 0.5)
    return max(0.03, min(0.90, base_probability + shift))


def _joint_corrected_all_fold(
    independent_probability: float,
    joint_model: Any,
    size_ratio: Optional[float] = None,
) -> float:
    if not isinstance(joint_model, Mapping) or not joint_model.get("enabled"):
        return independent_probability
    source = joint_model
    if size_ratio is not None:
        sized = (joint_model.get("size_curve") or {}).get(
            _bet_size_bucket(size_ratio)
        )
        if isinstance(sized, Mapping):
            source = sized
    correction = float(source.get("logit_correction") or 0.0)
    clipped = max(1e-6, min(1.0 - 1e-6, independent_probability))
    log_odds = math.log(clipped / (1.0 - clipped)) + correction
    return max(0.001, min(0.999, 1.0 / (1.0 + math.exp(-log_odds))))


def _size_adjusted_fold_probability(
    base_probability: float,
    size_ratio: float,
    opponents: int,
) -> float:
    """Approximate how sizing changes folds without treating all sizes alike."""

    if size_ratio <= 0:
        return max(0.02, min(0.95, base_probability))
    shift = 0.10 * math.log2(max(0.25, size_ratio) / 0.5)
    if opponents > 1:
        shift -= 0.04 * min(3, opponents - 1)
    return max(0.03, min(0.78, base_probability + shift))


def _continued_range_equity(
    raw_equity: float,
    size_ratio: float,
    opponents: int,
) -> float:
    """Conservatively tighten the unseen range that continues to aggression."""

    selection = min(
        0.90,
        0.10 + 0.22 * math.log2(1.0 + max(0.0, size_ratio)),
    )
    selection += min(0.12, 0.04 * max(0, opponents - 1))
    selection = min(0.94, selection)
    equity_drop = selection * (0.20 + 0.40 * (1.0 - raw_equity))
    return max(0.02, min(raw_equity, raw_equity - equity_drop))


def _fold_model(context: Mapping[str, Any], opponents: int) -> Dict[str, Any]:
    decision = context.get("decision") or {}
    street = str(decision.get("street") or "preflop")
    facing_bet = (_number(decision.get("call_score")) or 0.0) > 0
    if street == "preflop":
        metric = "fold_to_three_bet"
        prior = 0.55
    elif not facing_bet and postflop_profile(context)["hero_is_preflop_aggressor"]:
        metric = f"fold_to_{street}_cbet"
        prior = {"flop": 0.45, "turn": 0.42, "river": 0.48}.get(street, 0.45)
    else:
        metric = (
            f"fold_to_{street}_raise"
            if facing_bet
            else f"fold_to_{street}_bet"
        )
        prior = (
            {"flop": 0.52, "turn": 0.55, "river": 0.58}.get(street, 0.55)
            if facing_bet
            else {"flop": 0.42, "turn": 0.40, "river": 0.45}.get(street, 0.42)
        )
    probabilities = []
    personal_evidence_trials = 0
    population_evidence_trials = 0
    opponent_rows = []
    uses_contextual_response = False
    for profile in context.get("active_opponent_profiles") or []:
        response_model = profile.get("response_model") or {}
        response_fold = response_probability(response_model, "fold")
        observation = next(
            (
                item
                for item in profile.get("observations") or []
                if item.get("metric") == metric
            ),
            None,
        )
        if response_fold is not None:
            uses_contextual_response = True
            probability = response_fold
            residual_enabled = bool(
                response_model.get("player_residual_enabled", True)
            )
            trial_value = (
                response_model.get("effective_samples")
                if residual_enabled
                else response_model.get("population_effective_samples")
            )
            trials = float(trial_value or 0.0)
            if residual_enabled:
                personal_evidence_trials += round(trials)
            else:
                population_evidence_trials = max(
                    population_evidence_trials,
                    round(trials),
                )
        elif observation:
            trials = max(0, int(observation.get("opportunities") or 0))
            successes = max(0, int(observation.get("successes") or 0))
            posterior_mean = _number(observation.get("mean_pct"))
            probability = (
                posterior_mean / 100
                if posterior_mean is not None
                else (successes + prior * 12) / (trials + 12)
            )
            personal_evidence_trials += trials
        else:
            probability = prior
            trials = 0
        probability = max(0.05, min(0.95, probability))
        probabilities.append(probability)
        opponent_rows.append(
            {
                "player": profile.get("player"),
                "display_name": profile.get("display_name"),
                "fold_probability": round(probability, 5),
                "raise_probability": response_probability(
                    response_model, "raise"
                ),
                "call_probability": response_probability(
                    response_model, "call"
                ),
                "population_fold_probability": (
                    float(
                        (
                            response_model.get(
                                "population_probabilities_pct"
                            )
                            or {}
                        ).get("fold")
                    )
                    / 100
                    if (
                        response_model.get("population_probabilities_pct") or {}
                    ).get("fold")
                    is not None
                    else None
                ),
                "effective_samples": round(float(trials), 2),
                "confidence": response_model.get("confidence"),
                "player_residual_enabled": response_model.get(
                    "player_residual_enabled"
                ),
                "player_residual_gate": response_model.get(
                    "player_residual_gate"
                ),
                "residual_quality": response_model.get("residual_quality"),
                "strategy_drift": response_model.get("strategy_drift"),
                "raise_size_model": response_model.get("raise_size_model")
                or {},
                "size_curve": response_model.get("size_curve") or {},
            }
        )
    while len(probabilities) < opponents:
        probabilities.append(prior)
    probabilities = probabilities[:opponents]
    independent_all_fold = math.prod(probabilities)
    joint_response_model = context.get("joint_response_model")
    all_fold = _joint_corrected_all_fold(
        independent_all_fold,
        joint_response_model,
    )
    return {
        "metric": metric,
        "per_opponent_pct": [round(100 * value, 1) for value in probabilities],
        "all_fold_probability": round(all_fold, 5),
        "independent_all_fold_probability": round(
            independent_all_fold, 5
        ),
        "expected_callers": round(sum(1.0 - value for value in probabilities), 5),
        "evidence_trials": (
            personal_evidence_trials + population_evidence_trials
        ),
        "opponents": opponent_rows,
        "joint_response_model": joint_response_model,
        "method": (
            "hierarchical-dirichlet-context-independent-v1"
            if uses_contextual_response
            else "beta-shrunk-independent-v1"
        ),
    }


def _opponent_model_reasons(fold_model: Mapping[str, Any]) -> List[str]:
    reasons = []
    joint_model = fold_model.get("joint_response_model") or {}
    if joint_model.get("enabled"):
        direction = (
            "高于"
            if float(joint_model.get("logit_correction") or 0.0) > 0
            else "低于"
        )
        reasons.append(
            f"多人联合历史显示全员弃牌率{direction}独立相乘假设，"
            "已用通过时间外门控的相关性校准"
        )
    shifted = [
        row
        for row in fold_model.get("opponents") or []
        if (row.get("strategy_drift") or {}).get("status") == "shifted"
    ]
    if shifted:
        applied = sum(
            bool((row.get("strategy_drift") or {}).get("used_for_prediction"))
            for row in shifted
        )
        if applied:
            reasons.append(
                f"{applied} 名相关对手的近期漂移已通过时间外门控，"
                "短期后验以受限权重进入响应曲线"
            )
        if len(shifted) > applied:
            reasons.append(
                f"{len(shifted) - applied} 名相关对手出现未经验证的"
                "近期漂移，当前仅提示"
            )
    rows = [
        row
        for row in fold_model.get("opponents") or []
        if float(row.get("effective_samples") or 0) >= 15
        and row.get("population_fold_probability") is not None
    ]
    if not rows:
        return reasons
    player_fold = sum(float(row["fold_probability"]) for row in rows) / len(rows)
    population_fold = sum(
        float(row["population_fold_probability"]) for row in rows
    ) / len(rows)
    deviation = player_fold - population_fold
    if deviation <= -0.05:
        reasons.append(
            f"相关对手在该节点比牌池少弃约 {abs(deviation):.0%}，"
            "价值下注受益，纯诈唬应收缩"
        )
    elif deviation >= 0.05:
        reasons.append(
            f"相关对手在该节点比牌池多弃约 {deviation:.0%}，"
            "增加经后验下界验证的进攻"
        )
    else:
        reasons.append("相关对手该节点响应接近牌池先验，未实施大幅个体偏移")
    return reasons


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
    *,
    allow_all_in: bool = True,
) -> List[Dict[str, Any]]:
    if not candidates:
        return []
    temperature = max(big_blind * 1.5, pot * 0.08, 1.0)
    legal_non_all_in = any(
        item.get("action") != "all_in" for item in candidates
    )
    eligible = [
        item
        for item in candidates
        if not (
            item.get("action") == "all_in"
            and not allow_all_in
            and legal_non_all_in
        )
    ]
    maximum = max(float(item["robust_ev"]) for item in eligible or candidates)
    weights = []
    for item in candidates:
        if (
            item.get("action") == "all_in"
            and not allow_all_in
            and legal_non_all_in
        ):
            weights.append(0.0)
            continue
        weights.append(
            math.exp(
                max(
                    -30.0,
                    (float(item["robust_ev"]) - maximum) / temperature,
                )
            )
        )
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


def _weighted_random_selection(
    policy: Sequence[Mapping[str, Any]],
    draw_pct: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    if not policy:
        return None
    if draw_pct is None:
        draw = secrets.randbelow(1_000_000) / 10_000
    else:
        draw = max(0.0, min(99.9999, float(draw_pct)))
    intervals = []
    cumulative = 0.0
    selected: Optional[Dict[str, Any]] = None
    selected_id: Optional[str] = None
    for index, item in enumerate(policy):
        start = cumulative
        cumulative = (
            100.0
            if index == len(policy) - 1
            else cumulative + float(item.get("frequency_pct") or 0.0)
        )
        hit = selected is None and draw < cumulative
        if hit:
            selected = dict(item)
            selected_id = str(item.get("id") or "")
        intervals.append(
            {
                "id": item.get("id"),
                "action": item.get("action"),
                "raise_to": item.get("raise_to"),
                "frequency_pct": item.get("frequency_pct"),
                "start_pct": round(start, 1),
                "end_pct": round(cumulative, 1),
                "selected": hit,
            }
        )
    if selected is None:
        selected = dict(policy[-1])
        selected_id = str(policy[-1].get("id") or "")
        intervals[-1]["selected"] = True
    return {
        "method": "server-weighted-random-v1",
        "draw_pct": round(draw, 4),
        "selected_id": selected_id,
        "selected": selected,
        "intervals": intervals,
    }


def _sizing_penalty(
    candidate: Mapping[str, Any],
    decision: Mapping[str, Any],
    preferred_fraction: Optional[float],
) -> float:
    if candidate.get("action") != "raise" or not preferred_fraction:
        return 0.0
    pot = max(0.0, _number(decision.get("pot")) or 0.0)
    if pot <= 0:
        return 0.0
    contribution = _number(decision.get("hero_street_contribution")) or 0.0
    target = _number(candidate.get("raise_to"))
    if target is None:
        return 0.0
    fraction = max(0.05, (target - contribution) / pot)
    distance = abs(math.log2(fraction / max(0.05, preferred_fraction)))
    return 0.35 * pot * distance


def _preflop_open_sizing_penalty(
    candidate: Mapping[str, Any],
    decision: Mapping[str, Any],
    preferred_raise_to: Optional[float],
) -> float:
    if (
        preferred_raise_to is None
        or candidate.get("action") != "raise"
        or preferred_raise_to <= 0
    ):
        return 0.0
    target = _number(candidate.get("raise_to"))
    if target is None or target <= 0:
        return 0.0
    pot = max(0.0, _number(decision.get("pot")) or 0.0)
    distance = abs(math.log2(target / preferred_raise_to))
    return 0.9 * pot * distance


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
    squid_penalty = (
        0.04 * pot
        if str(decision.get("game_mode") or "") == "squid"
        and not squid_calibrated
        else 0.0
    )
    aggression_penalty = (
        0.04 * pot if candidate.get("action") in {"raise", "all_in"} else 0.0
    )
    return sample_penalty + evidence_penalty + squid_penalty + aggression_penalty


def _raise_risk_penalty(
    decision: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> float:
    if candidate.get("action") not in {"raise", "bet", "all_in"}:
        return 0.0
    if (candidate.get("explicit_raise_branch") or {}).get("enabled"):
        return 0.0
    audit = candidate.get("continuation_range_model") or {}
    raise_probability = max(
        0.0, min(1.0, float(audit.get("any_raise_probability") or 0.0))
    )
    if raise_probability <= 0:
        return 0.0
    pot = max(0.0, _number(decision.get("pot")) or 0.0)
    contribution = _number(decision.get("hero_street_contribution")) or 0.0
    target = (
        _number(candidate.get("raise_to"))
        if candidate.get("action") == "raise"
        else _number(decision.get("max_raise_to"))
    )
    if target is None:
        target = contribution + (
            _number(decision.get("hero_stack")) or 0.0
        )
    cost = max(0.0, target - contribution)
    called_equity = max(
        0.0, min(1.0, float(candidate.get("called_equity") or 0.0))
    )
    vulnerability = max(0.15, 1.0 - 1.5 * called_equity)
    return min(
        0.15 * pot,
        raise_probability * cost * 0.25 * vulnerability,
    )


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
    if (
        str(decision.get("game_mode") or "") == "squid"
        and not squid.get("calibrated")
    ):
        return "low"
    trials = int(fold_model.get("evidence_trials") or 0)
    squid_ready = (
        str(decision.get("game_mode") or "") != "squid"
        or squid.get("calibration_confidence") in {"medium", "high"}
    )
    if trials >= 100 and squid_ready:
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
