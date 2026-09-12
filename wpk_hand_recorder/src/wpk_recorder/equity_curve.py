from __future__ import annotations

import math
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .inference import selected_opponent_range
from .poker import (
    showdown_stats_vs_random_multiway,
    showdown_stats_vs_weighted_ranges,
    starting_hand_class,
    starting_hand_combos_by_class,
)

CURVE_VERSION = "range-equity-curve-v2"


class EquityCurveCancelled(RuntimeError):
    """Raised when a superseded curve calculation should stop."""


def build_range_equity_curve(
    context: Mapping[str, Any],
    hero_range: Mapping[str, Any],
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """Build an equity-ordered hero-range curve for the current board."""

    started = time.perf_counter()
    _raise_if_cancelled(cancel_check)
    decision = context.get("decision") or {}
    board = list(decision.get("board") or [])
    if len(board) < 3 or len(board) > 5:
        return {
            "status": "unavailable",
            "curve_version": CURVE_VERSION,
            "reason": "翻牌后才生成基于公共牌的范围权益曲线",
            "latency_ms": _elapsed_ms(started),
        }

    opponents = max(1, min(9, int(decision.get("players_in_hand") or 2) - 1))
    acting_label = (
        f"seat_{decision.get('subject_seat') or decision.get('acting_seat')}"
    )
    opponent_profiles = [
        profile
        for profile in context.get("active_opponent_profiles") or []
        if str(profile.get("player") or "") != acting_label
    ]
    selected_range_profiles = [
        selected_opponent_range(profile) for profile in opponent_profiles
    ][:opponents]
    range_models = [
        profile.get("weights") or {} for profile in selected_range_profiles
    ]
    while len(range_models) < opponents:
        range_models.append({})
    uses_ranges = any(bool(model) for model in range_models)
    action_line_opponents = sum(
        profile.get("source") == "action_line_posterior"
        for profile in selected_range_profiles
    )
    weights = {
        str(hand): max(0.0, float(weight))
        for hand, weight in (hero_range.get("weights") or {}).items()
        if float(weight) > 0
    }
    combos_by_class = starting_hand_combos_by_class(board)
    per_combo_trials = max(16, round(32 / math.sqrt(opponents)))
    rows = []
    total_simulations = 0
    for hand_class, range_weight in weights.items():
        _raise_if_cancelled(cancel_check)
        combos = combos_by_class.get(hand_class) or []
        if not combos:
            continue
        representatives = _representative_combos(combos, limit=2)
        equities = []
        for combo in representatives:
            _raise_if_cancelled(cancel_check)
            equities.append(
                _exact_equity(
                    combo,
                    board,
                    range_models,
                    opponents,
                    per_combo_trials,
                    uses_ranges,
                )
            )
        total_simulations += len(representatives) * per_combo_trials
        mass = len(combos) * range_weight / 100
        rows.append(
            {
                "hand": hand_class,
                "equity_pct": round(
                    100 * sum(equities) / max(1, len(equities)),
                    1,
                ),
                "range_weight_pct": round(range_weight, 1),
                "available_combos": len(combos),
                "mass": mass,
            }
        )
    if not rows:
        return {
            "status": "unavailable",
            "curve_version": CURVE_VERSION,
            "reason": "当前翻前行动线没有可用的英雄范围",
            "latency_ms": _elapsed_ms(started),
        }

    rows.sort(key=lambda item: (item["equity_pct"], item["hand"]))
    total_mass = sum(float(item["mass"]) for item in rows)
    cumulative = 0.0
    points = []
    for item in rows:
        mass = float(item["mass"])
        percentile = 100 * (cumulative + mass / 2) / total_mass
        cumulative += mass
        points.append(
            {
                key: value
                for key, value in item.items()
                if key != "mass"
            }
            | {"percentile": round(percentile, 1)}
        )

    range_equity = sum(
        float(item["equity_pct"]) * float(item["mass"]) for item in rows
    ) / total_mass
    hero = _hero_marker(
        decision,
        board,
        range_models,
        opponents,
        uses_ranges,
        rows,
        total_mass,
        weights,
        cancel_check,
    )
    confidence = _curve_confidence(
        selected_range_profiles,
        uses_ranges,
        len(points),
    )
    return {
        "status": "ok",
        "curve_version": CURVE_VERSION,
        "board": board,
        "street": str(decision.get("street") or ""),
        "decision_subject": str(
            decision.get("decision_subject") or "self"
        ),
        "opponents": opponents,
        "confidence": confidence,
        "range_equity_pct": round(range_equity, 1),
        "quantiles": {
            f"p{percentile}": _weighted_quantile(
                rows,
                total_mass,
                percentile / 100,
            )
            for percentile in (10, 25, 50, 75, 90)
        },
        "hero": hero,
        "points": points,
        "hero_range": {
            "source": hero_range.get("source"),
            "scenario": hero_range.get("scenario"),
            "action_line": hero_range.get("action_line"),
            "equivalent_combos": round(total_mass, 1),
            "classes": len(points),
        },
        "opponent_model": (
            "逐街行动线后验；未通过门控者回退翻前范围"
            if action_line_opponents
            else "逐对手贝叶斯翻前范围"
            if uses_ranges
            else "缺少范围时的均匀随机牌"
        ),
        "opponent_ranges": [
            {
                "player": profile.get("player"),
                "source": profile.get("source"),
                "confidence": profile.get("confidence"),
                "line": profile.get("line"),
                "applied_streets": profile.get("applied_streets") or [],
            }
            for profile in selected_range_profiles
        ],
        "action_line_conditioned_opponents": action_line_opponents,
        "computation": {
            "representative_simulations": total_simulations,
            "trials_per_combo": per_combo_trials,
        },
        "caveats": [
            "曲线按英雄翻前行动线范围加权，并按当前 board 重新计算阻断牌。",
            (
                "对手范围优先使用已通过时间外门控的逐街行动线后验；"
                "其余对手回退翻前范围。"
                if action_line_opponents
                else "对手范围尚未按翻后动作收紧，当前使用翻前范围回退。"
            ),
            "范围后验仍受公开摊牌选择偏差影响，这是实时近似而非 solver 精确节点。",
            "每个 169 类起手牌最多抽取 2 个花色组合，极端同花阻断会有采样误差。",
        ],
        "latency_ms": _elapsed_ms(started),
    }


def _hero_marker(
    decision: Mapping[str, Any],
    board: Sequence[str],
    range_models: Sequence[Mapping[str, float]],
    opponents: int,
    uses_ranges: bool,
    rows: Sequence[Mapping[str, Any]],
    total_mass: float,
    weights: Mapping[str, float],
    cancel_check: Optional[Callable[[], bool]],
) -> Optional[Dict[str, Any]]:
    _raise_if_cancelled(cancel_check)
    hole_cards = list(decision.get("hero_cards") or [])
    if len(hole_cards) != 2:
        return None
    trials = max(100, round(180 / math.sqrt(opponents)))
    equity = 100 * _exact_equity(
        (hole_cards[0], hole_cards[1]),
        board,
        range_models,
        opponents,
        trials,
        uses_ranges,
    )
    below = sum(
        float(item["mass"])
        for item in rows
        if float(item["equity_pct"]) < equity
    )
    equal = sum(
        float(item["mass"])
        for item in rows
        if float(item["equity_pct"]) == round(equity, 1)
    )
    hand_class = starting_hand_class(hole_cards)
    return {
        "cards": hole_cards,
        "hand": hand_class,
        "equity_pct": round(equity, 1),
        "percentile": round(100 * (below + equal / 2) / total_mass, 1),
        "range_weight_pct": round(float(weights.get(hand_class or "", 0.0)), 1),
        "trials": trials,
    }


def _exact_equity(
    hole_cards: Tuple[str, str],
    board: Sequence[str],
    range_models: Sequence[Mapping[str, float]],
    opponents: int,
    trials: int,
    uses_ranges: bool,
) -> float:
    if uses_ranges:
        return float(
            showdown_stats_vs_weighted_ranges(
                hole_cards,
                board,
                range_models,
                trials,
            )["pot_share"]
        )
    return float(
        showdown_stats_vs_random_multiway(
            hole_cards,
            board,
            opponents,
            trials,
        )["pot_share"]
    )


def _representative_combos(
    combos: Sequence[Tuple[str, str]],
    *,
    limit: int,
) -> List[Tuple[str, str]]:
    if len(combos) <= limit:
        return list(combos)
    indices = {
        round(index * (len(combos) - 1) / (limit - 1))
        for index in range(limit)
    }
    return [combos[index] for index in sorted(indices)]


def _weighted_quantile(
    rows: Sequence[Mapping[str, Any]],
    total_mass: float,
    fraction: float,
) -> float:
    target = total_mass * fraction
    cumulative = 0.0
    for item in rows:
        cumulative += float(item["mass"])
        if cumulative >= target:
            return round(float(item["equity_pct"]), 1)
    return round(float(rows[-1]["equity_pct"]), 1)


def _curve_confidence(
    profiles: Sequence[Mapping[str, Any]],
    uses_ranges: bool,
    class_count: int,
) -> str:
    if not uses_ranges or class_count < 15:
        return "low"
    if any(
        profile.get("confidence") in {"none", "very_low", "low"}
        for profile in profiles
    ):
        return "low"
    return "medium"


def _raise_if_cancelled(
    cancel_check: Optional[Callable[[], bool]],
) -> None:
    if cancel_check is not None and cancel_check():
        raise EquityCurveCancelled("权益曲线计算已被更新状态取代")


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
