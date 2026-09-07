from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple


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


def _context_matches(
    record: Dict[str, Any], target_position: str, target_stack: str
) -> bool:
    position_match = not target_position or record["position"] == target_position
    stack_match = target_stack == "unknown" or record["stack_bucket"] == target_stack
    return position_match and stack_match


def _stack_bucket(value: Any) -> str:
    try:
        stack = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if stack < 80:
        return "short"
    if stack < 150:
        return "100bb"
    if stack < 300:
        return "deep"
    return "very_deep"


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
