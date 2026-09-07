from __future__ import annotations

import hashlib
import json
import math
import os
import random
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass
from statistics import median
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


PAYOUT_BASES = ("awarded", "configured")


@dataclass(frozen=True)
class SquidRoundState:
    round_id: Optional[str]
    participants: Tuple[str, ...]
    counts: Tuple[Tuple[str, int], ...]
    participant_count: int
    total_squids: int
    awarded_squids: int
    remaining_squids: int
    zero_squid_players: int
    terminal: bool
    squid_value: Optional[float]
    payout_basis: str
    calibration_confidence: str
    calibration_rounds: int
    rule_status: str = "insufficient_data"
    calibration_error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["participants"] = list(self.participants)
        data["counts"] = dict(self.counts)
        return data


def anonymize_squid_state(
    state: SquidRoundState, labels: Mapping[str, str]
) -> SquidRoundState:
    used = set()
    mapped = {}
    for index, (user_id, count) in enumerate(state.counts, 1):
        preferred = str(labels.get(user_id) or f"participant_{index}")
        label = preferred
        suffix = 2
        while label in used:
            label = f"{preferred}_{suffix}"
            suffix += 1
        used.add(label)
        mapped[label] = count
    return SquidRoundState(
        round_id="active" if state.round_id else None,
        participants=tuple(sorted(mapped)),
        counts=tuple(sorted(mapped.items())),
        participant_count=state.participant_count,
        total_squids=state.total_squids,
        awarded_squids=state.awarded_squids,
        remaining_squids=state.remaining_squids,
        zero_squid_players=state.zero_squid_players,
        terminal=state.terminal,
        squid_value=state.squid_value,
        payout_basis=state.payout_basis,
        calibration_confidence=state.calibration_confidence,
        calibration_rounds=state.calibration_rounds,
        rule_status=state.rule_status,
        calibration_error=state.calibration_error,
    )


def squid_state_from_dict(value: Mapping[str, Any]) -> SquidRoundState:
    counts_value = value.get("counts") or {}
    counts = (
        {str(key): max(0, int(item)) for key, item in counts_value.items()}
        if isinstance(counts_value, dict)
        else {}
    )
    participants = tuple(
        str(item) for item in value.get("participants") or counts.keys()
    )
    return SquidRoundState(
        round_id=(
            str(value["round_id"]) if value.get("round_id") is not None else None
        ),
        participants=participants,
        counts=tuple(sorted(counts.items())),
        participant_count=_integer(value.get("participant_count"), len(participants)),
        total_squids=_integer(value.get("total_squids")),
        awarded_squids=_integer(value.get("awarded_squids")),
        remaining_squids=_integer(value.get("remaining_squids")),
        zero_squid_players=_integer(value.get("zero_squid_players")),
        terminal=bool(value.get("terminal")),
        squid_value=_optional_number(value.get("squid_value")),
        payout_basis=str(value.get("payout_basis") or "awarded"),
        calibration_confidence=str(
            value.get("calibration_confidence") or "none"
        ),
        calibration_rounds=_integer(value.get("calibration_rounds")),
        rule_status=str(value.get("rule_status") or "insufficient_data"),
        calibration_error=(
            str(value["calibration_error"])
            if value.get("calibration_error")
            else None
        ),
    )


def terminal_payoffs(
    counts: Mapping[str, int],
    squid_value: float,
    *,
    payout_basis: str = "awarded",
    total_squids: Optional[int] = None,
) -> Dict[str, float]:
    """Return a zero-sum settlement under the user-described squid rules."""

    coefficients = settlement_coefficients(
        counts,
        payout_basis=payout_basis,
        total_squids=total_squids,
    )
    return {
        user_id: round(coefficient * float(squid_value), 6)
        for user_id, coefficient in coefficients.items()
    }


def settlement_coefficients(
    counts: Mapping[str, int],
    *,
    payout_basis: str = "awarded",
    total_squids: Optional[int] = None,
) -> Dict[str, float]:
    clean = {str(user_id): max(0, int(value)) for user_id, value in counts.items()}
    awarded = sum(clean.values())
    losers = sum(value == 0 for value in clean.values())
    if not clean or awarded <= 0 or losers <= 0:
        return {user_id: 0.0 for user_id in clean}
    if payout_basis not in PAYOUT_BASES:
        raise ValueError(f"unsupported squid payout basis: {payout_basis}")
    basis = (
        float(total_squids or awarded)
        if payout_basis == "configured"
        else float(awarded)
    )
    winner_multiplier = losers * basis / awarded
    return {
        user_id: -basis if count == 0 else count * winner_multiplier
        for user_id, count in clean.items()
    }


def current_squid_state(
    connection: sqlite3.Connection,
    hand_id: str,
    participant_ids: Sequence[str],
) -> SquidRoundState:
    hand_round_row = connection.execute(
        "SELECT hand_json FROM hands WHERE hand_id = ?",
        (hand_id,),
    ).fetchone()
    hand_round_id = None
    if hand_round_row and hand_round_row[0]:
        try:
            hand_round_id = (
                str(value)
                if (
                    value := json.loads(hand_round_row[0]).get(
                        "squid_round_id"
                    )
                )
                is not None
                else None
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    round_row = connection.execute(
        """
        SELECT round_id, participant_count, latest_json
        FROM squid_rounds
        WHERE status IN ('running', 'waiting', 'restored')
          AND (? IS NULL OR round_id = ?)
        ORDER BY started_at DESC LIMIT 1
        """,
        (hand_round_id, hand_round_id),
    ).fetchone()
    round_id = str(round_row[0]) if round_row else None
    participants = {
        str(value) for value in participant_ids if value is not None and str(value)
    }
    recorded_count = int(round_row[1] or 0) if round_row else 0
    if round_row:
        try:
            participants.update(
                str(value)
                for value in json.loads(round_row[2]).get("users") or []
                if str(value)
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

    event_counts: Counter = Counter()
    if round_id:
        round_participants, event_counts = reconstruct_round_counts(
            connection, round_id
        )
        participants.update(round_participants)
    snapshot_counts = {
        str(row[0]): max(0, int(row[1] or 0))
        for row in connection.execute(
            """
            SELECT user_id, squid_end FROM hand_squid_players
            WHERE hand_id = ?
            """,
            (hand_id,),
        )
        if row[0] is not None
    }
    participants.update(snapshot_counts)
    participant_count = max(len(participants), recorded_count)
    while len(participants) < participant_count:
        participants.add(f"unknown_{len(participants) + 1}")
    counts = {
        user_id: max(snapshot_counts.get(user_id, 0), event_counts[user_id])
        for user_id in participants
    }
    calibration = calibrate_squid_rules(connection)
    env_value = _env_positive_float("WPK_SQUID_VALUE")
    env_basis = os.getenv("WPK_SQUID_PAYOUT_BASIS", "").strip().lower()
    payout_basis = (
        env_basis
        if env_basis in PAYOUT_BASES
        else str(calibration.get("payout_basis") or "awarded")
    )
    explicit_basis = env_basis in PAYOUT_BASES
    selected_summary = (
        (calibration.get("candidates") or {}).get(payout_basis) or {}
    )
    selected_rounds = int(selected_summary.get("accepted_rounds") or 0)
    squid_value = (
        env_value
        if env_value is not None
        else _optional_number(selected_summary.get("median_squid_value"))
        if explicit_basis
        else _optional_number(calibration.get("squid_value"))
    )
    confidence = (
        "configured"
        if env_value is not None and explicit_basis
        else (
            "high"
            if selected_rounds >= 10
            else "medium"
            if selected_rounds >= 3
            else "low"
        )
        if explicit_basis and selected_rounds
        else str(calibration.get("confidence") or "none")
    )
    rule_status = str(
        calibration.get("rule_status") or "insufficient_data"
    )
    if explicit_basis and selected_rounds:
        rule_status = "configured_basis"
    elif explicit_basis and env_value is not None and rule_status != "mismatch":
        rule_status = "configured_unverified"
    total_squids = participant_count + 4 if participant_count else 0
    awarded = sum(counts.values())
    zero_players = sum(value == 0 for value in counts.values())
    return SquidRoundState(
        round_id=round_id,
        participants=tuple(sorted(participants)),
        counts=tuple(sorted(counts.items())),
        participant_count=participant_count,
        total_squids=total_squids,
        awarded_squids=awarded,
        remaining_squids=max(0, total_squids - awarded),
        zero_squid_players=zero_players,
        terminal=bool(
            participant_count
            and (awarded >= total_squids or zero_players <= 1)
        ),
        squid_value=squid_value,
        payout_basis=payout_basis,
        calibration_confidence=confidence,
        calibration_rounds=int(calibration.get("accepted_rounds") or 0),
        rule_status=rule_status,
        calibration_error=(
            str(calibration["calibration_error"])
            if calibration.get("calibration_error")
            else None
        ),
    )


def reconstruct_round_counts(
    connection: sqlite3.Connection, round_id: str
) -> Tuple[set, Counter]:
    participants = set()
    counts: Counter = Counter()
    rows = connection.execute(
        """
        SELECT scene, users_json, ext_json, raw_json
        FROM squid_events WHERE round_id = ? ORDER BY sequence
        """,
        (round_id,),
    ).fetchall()
    for scene, users_json, ext_json, raw_json in rows:
        participants.update(_json_string_list(users_json))
        if int(scene or 0) != 3:
            continue
        raw = _json_object(raw_json)
        data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        winners = data.get("users") if isinstance(data, dict) else None
        if not isinstance(winners, list):
            winners = []
        ext = _json_object(data.get("ext")) if isinstance(data, dict) else {}
        if not ext:
            ext = _json_object(ext_json)
        award_count = max(1, _integer(ext.get("winSquidNum"), 1))
        for winner in winners:
            user_id = str(winner)
            if user_id:
                participants.add(user_id)
                counts[user_id] += award_count
    return participants, counts


def calibrate_squid_rules(
    connection: sqlite3.Connection, limit: int = 50
) -> Dict[str, Any]:
    round_ids = [
        str(row[0])
        for row in connection.execute(
            """
            SELECT round_id FROM squid_rounds
            WHERE status = 'completed'
            ORDER BY ended_at DESC LIMIT ?
            """,
            (max(1, int(limit)),),
        )
    ]
    candidates: Dict[str, List[Dict[str, Any]]] = {
        "awarded": [],
        "configured": [],
    }
    for round_id in round_ids:
        participants, counts = reconstruct_round_counts(connection, round_id)
        settlements = {
            str(row[0]): float(row[1])
            for row in connection.execute(
                """
                SELECT user_id, add_score FROM squid_settlements
                WHERE round_id = ? AND add_score IS NOT NULL
                """,
                (round_id,),
            )
        }
        participants.update(settlements)
        clean_counts = {user_id: counts[user_id] for user_id in participants}
        if len(settlements) < 2 or sum(clean_counts.values()) <= 0:
            continue
        total_squids = len(participants) + 4
        for basis in PAYOUT_BASES:
            fit = _fit_round(
                clean_counts,
                settlements,
                payout_basis=basis,
                total_squids=total_squids,
            )
            if fit:
                candidates[basis].append({"round_id": round_id, **fit})

    summaries = {
        basis: _calibration_summary(records)
        for basis, records in candidates.items()
    }
    viable = [
        (basis, summary)
        for basis, summary in summaries.items()
        if summary["accepted_rounds"] > 0
    ]
    if not viable:
        fitted_rounds = max(
            (int(summary["rounds"]) for summary in summaries.values()),
            default=0,
        )
        rule_status = "mismatch" if fitted_rounds > 0 else "insufficient_data"
        return {
            "payout_basis": "awarded",
            "squid_value": None,
            "confidence": "none",
            "accepted_rounds": 0,
            "examined_rounds": len(round_ids),
            "rule_status": rule_status,
            "calibration_error": (
                "历史 scene=3 授予与 scene=4 结算不符合零和鱿鱼公式"
                if rule_status == "mismatch"
                else None
            ),
            "candidates": summaries,
        }
    if len(viable) > 1:
        accepted = max(
            int(summary["accepted_rounds"]) for _, summary in viable
        )
        return {
            "payout_basis": None,
            "squid_value": None,
            "confidence": "none",
            "accepted_rounds": accepted,
            "examined_rounds": len(round_ids),
            "rule_status": "ambiguous_basis",
            "calibration_error": (
                "仅凭结算分数无法区分按已发数量或 N+4 总数结算；"
                "请显式配置 WPK_SQUID_PAYOUT_BASIS"
            ),
            "candidates": summaries,
        }
    basis, best = min(
        viable,
        key=lambda item: (
            item[1]["median_normalized_rmse"],
            -item[1]["accepted_rounds"],
            0 if item[0] == "awarded" else 1,
        ),
    )
    accepted = int(best["accepted_rounds"])
    confidence = "high" if accepted >= 10 else "medium" if accepted >= 3 else "low"
    return {
        "payout_basis": basis,
        "squid_value": best["median_squid_value"],
        "confidence": confidence,
        "accepted_rounds": accepted,
        "examined_rounds": len(round_ids),
        "median_normalized_rmse": best["median_normalized_rmse"],
        "rule_status": "validated",
        "calibration_error": None,
        "candidates": summaries,
    }


def estimate_award_probabilities(
    connection: sqlite3.Connection,
    participant_ids: Sequence[str],
    current_counts: Optional[Mapping[str, int]] = None,
    *,
    half_life_hands: float = 100.0,
    prior_strength: float = 12.0,
    limit: int = 5000,
) -> Dict[str, Any]:
    """Estimate each participant's next effective pot-win share.

    Completed squid hands provide one Bernoulli observation per seated player.
    Older hands decay and observations from the same zero/non-zero squid state
    receive full weight; the other state receives partial weight.
    """

    participants = tuple(
        dict.fromkeys(
            str(user_id)
            for user_id in participant_ids
            if user_id is not None and str(user_id)
        )
    )
    if not participants:
        return {
            "method": "decayed-beta-pot-win-v1",
            "probabilities": {},
            "population_rate": None,
            "evidence_hands": 0,
            "players": {},
        }
    rows = connection.execute(
        """
        SELECT hp.hand_id, hp.user_id, hp.squid_start, h.ended_at,
               COALESCE(sa.award_count, 0)
        FROM hand_squid_players hp
        JOIN hands h ON h.hand_id = hp.hand_id
        LEFT JOIN squid_awards sa
          ON sa.hand_id = hp.hand_id AND sa.user_id = hp.user_id
        WHERE h.game_mode = 'squid' AND h.status = 'completed'
        ORDER BY COALESCE(h.ended_at, h.started_at) DESC, hp.hand_id DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    hand_rank: Dict[str, int] = {}
    stats: Dict[str, Dict[str, float]] = {
        user_id: {
            "opportunities": 0.0,
            "effective_trials": 0.0,
            "weighted_wins": 0.0,
            "awards": 0.0,
        }
        for user_id in participants
    }
    population_trials = 0.0
    population_wins = 0.0
    for hand_id, user_id, squid_start, _ended_at, award_count in rows:
        hand_key = str(hand_id)
        if hand_key not in hand_rank:
            hand_rank[hand_key] = len(hand_rank)
        recency = 0.5 ** (
            hand_rank[hand_key] / max(1.0, float(half_life_hands))
        )
        won = 1.0 if int(award_count or 0) > 0 else 0.0
        population_trials += recency
        population_wins += recency * won
        player_id = str(user_id)
        if player_id not in stats:
            continue
        target_zero = int((current_counts or {}).get(player_id, 0)) == 0
        observed_zero = int(squid_start or 0) == 0
        context_weight = 1.0 if target_zero == observed_zero else 0.35
        weight = recency * context_weight
        item = stats[player_id]
        item["opportunities"] += 1.0
        item["effective_trials"] += weight
        item["weighted_wins"] += weight * won
        item["awards"] += won
    population_rate = (
        population_wins / population_trials
        if population_trials > 0
        else 1.0 / len(participants)
    )
    raw_probabilities = {}
    player_results = {}
    for user_id, item in stats.items():
        trials = item["effective_trials"]
        posterior = (
            item["weighted_wins"] + prior_strength * population_rate
        ) / (trials + prior_strength)
        raw_probabilities[user_id] = max(1e-9, posterior)
        player_results[user_id] = {
            "opportunities": int(item["opportunities"]),
            "awarded_hands": int(item["awards"]),
            "effective_trials": round(trials, 2),
            "posterior_pot_win_pct": round(100 * posterior, 2),
            "confidence": (
                "high" if trials >= 50 else "medium" if trials >= 15 else "low"
            ),
        }
    probabilities = _normalized_probabilities(
        participants, raw_probabilities
    )
    return {
        "method": "decayed-beta-pot-win-v1",
        "probabilities": {
            user_id: round(probability, 8)
            for user_id, probability in probabilities.items()
        },
        "population_rate": round(population_rate, 8),
        "evidence_hands": len(hand_rank),
        "half_life_hands": float(half_life_hands),
        "prior_strength": float(prior_strength),
        "players": player_results,
    }


def hero_values_after_next_award(
    state: SquidRoundState,
    hero_user_id: str,
    future_win_probabilities: Mapping[str, float],
    simulations_per_winner: int = 500,
) -> Dict[str, float]:
    """Estimate hero terminal value conditional on who wins the current squid."""

    counts = dict(state.counts)
    if hero_user_id not in counts or state.participant_count <= 0:
        return {}
    probabilities = _normalized_probabilities(
        state.participants, future_win_probabilities
    )
    unit_value = state.squid_value if state.squid_value is not None else 1.0
    result = {}
    for next_winner in state.participants:
        next_counts = dict(counts)
        next_counts[next_winner] += 1
        result[next_winner] = _simulate_hero_terminal_value(
            next_counts,
            hero_user_id,
            state.total_squids,
            unit_value,
            state.payout_basis,
            probabilities,
            simulations_per_winner,
            seed_text=f"{state.round_id}|{state.counts}|{next_winner}",
        )
    return result


def _simulate_hero_terminal_value(
    starting_counts: Dict[str, int],
    hero_user_id: str,
    total_squids: int,
    squid_value: float,
    payout_basis: str,
    probabilities: Mapping[str, float],
    simulations: int,
    seed_text: str,
) -> float:
    players = tuple(sorted(starting_counts))
    weights = [probabilities[player] for player in players]
    seed = int(hashlib.sha256(seed_text.encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    total = 0.0
    trials = max(1, int(simulations))
    for _ in range(trials):
        counts = dict(starting_counts)
        while (
            sum(counts.values()) < total_squids
            and sum(value == 0 for value in counts.values()) > 1
        ):
            winner = rng.choices(players, weights=weights, k=1)[0]
            counts[winner] += 1
        total += terminal_payoffs(
            counts,
            squid_value,
            payout_basis=payout_basis,
            total_squids=total_squids,
        ).get(hero_user_id, 0.0)
    return round(total / trials, 4)


def _fit_round(
    counts: Mapping[str, int],
    settlements: Mapping[str, float],
    *,
    payout_basis: str,
    total_squids: int,
) -> Optional[Dict[str, Any]]:
    coefficients = settlement_coefficients(
        counts,
        payout_basis=payout_basis,
        total_squids=total_squids,
    )
    pairs = [
        (coefficients[user_id], float(value))
        for user_id, value in settlements.items()
        if user_id in coefficients and abs(coefficients[user_id]) > 1e-9
    ]
    denominator = sum(coefficient * coefficient for coefficient, _ in pairs)
    if len(pairs) < 2 or denominator <= 0:
        return None
    squid_value = sum(coefficient * value for coefficient, value in pairs) / denominator
    if squid_value <= 0:
        return None
    errors = [value - coefficient * squid_value for coefficient, value in pairs]
    rmse = math.sqrt(sum(error * error for error in errors) / len(errors))
    scale = max(1.0, max(abs(value) for _, value in pairs))
    return {
        "squid_value": round(squid_value, 6),
        "normalized_rmse": round(rmse / scale, 6),
        "zero_sum_error": round(abs(sum(settlements.values())) / scale, 6),
    }


def _calibration_summary(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    records = list(records)
    accepted = [
        record
        for record in records
        if record["normalized_rmse"] <= 0.05
        and record["zero_sum_error"] <= 0.05
    ]
    return {
        "rounds": len(records),
        "accepted_rounds": len(accepted),
        "median_squid_value": (
            round(median(record["squid_value"] for record in accepted), 6)
            if accepted
            else None
        ),
        "median_normalized_rmse": (
            round(median(record["normalized_rmse"] for record in accepted), 6)
            if accepted
            else 1.0
        ),
    }


def _normalized_probabilities(
    participants: Sequence[str], values: Mapping[str, float]
) -> Dict[str, float]:
    clean = {
        user_id: max(0.0, float(values.get(user_id, 0.0)))
        for user_id in participants
    }
    total = sum(clean.values())
    if total <= 0:
        return {user_id: 1.0 / len(participants) for user_id in participants}
    return {user_id: value / total for user_id, value in clean.items()}


def _json_string_list(value: Any) -> List[str]:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _optional_number(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _env_positive_float(name: str) -> Optional[float]:
    value = _optional_number(os.getenv(name))
    return value if value is not None and value > 0 else None
