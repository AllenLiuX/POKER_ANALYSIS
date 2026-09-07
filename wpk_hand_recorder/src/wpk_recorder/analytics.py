from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo


def connect_readonly(data_dir: Path) -> sqlite3.Connection:
    path = data_dir / "hands.sqlite3"
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    return connection


def snapshot(
    data_dir: Path,
    hand_limit: int = 30,
    event_limit: int = 80,
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    connection = connect_readonly(data_dir)
    try:
        hands = []
        for row in connection.execute(
            """
            SELECT hand_json, game_mode, hand_number, played_at,
                   quality_status, quality_reasons_json, excluded_from_stats
            FROM hands
            WHERE (? IS NULL OR game_mode = ?)
            ORDER BY played_at DESC, hand_number DESC LIMIT ?
            """,
            (mode, mode, hand_limit),
        ):
            hand = json.loads(row["hand_json"])
            hand["game_mode"] = row["game_mode"]
            hand["hand_number"] = row["hand_number"]
            hand["played_at"] = row["played_at"]
            hand["played_at_cn"] = _cn_time(row["played_at"])
            hand["quality_status"] = row["quality_status"]
            hand["quality_reasons"] = json.loads(row["quality_reasons_json"])
            hand["excluded_from_stats"] = bool(row["excluded_from_stats"])
            hand["squid_hand"] = _hand_squid(connection, hand["hand_id"])
            hands.append(hand)
        events = [
            {
                "sequence": row["sequence"],
                "timestamp": row["timestamp"],
                "event_name": row["event_name"],
                "hand_id": row["hand_id"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in connection.execute(
                """
                SELECT sequence, timestamp, event_name, hand_id, payload_json
                FROM raw_events ORDER BY sequence DESC LIMIT ?
                """,
                (event_limit,),
            )
        ]
        last_sequence = events[0]["sequence"] if events else 0
        return {
            "last_sequence": last_sequence,
            "hands": hands,
            "events": events,
            "opponents": opponent_stats(connection, mode),
            "squid": squid_snapshot(connection),
        }
    finally:
        connection.close()


def hand_detail(data_dir: Path, hand_id: str) -> Optional[Dict[str, Any]]:
    connection = connect_readonly(data_dir)
    try:
        row = connection.execute(
            """
            SELECT hand_json, hand_number, played_at, quality_status,
                   quality_reasons_json, excluded_from_stats
            FROM hands WHERE hand_id = ?
            """,
            (hand_id,),
        ).fetchone()
        if not row:
            return None
        hand = json.loads(row["hand_json"])
        hand["hand_number"] = row["hand_number"]
        hand["played_at"] = row["played_at"]
        hand["played_at_cn"] = _cn_time(row["played_at"])
        hand["quality_status"] = row["quality_status"]
        hand["quality_reasons"] = json.loads(row["quality_reasons_json"])
        hand["excluded_from_stats"] = bool(row["excluded_from_stats"])
        hand["squid_hand"] = _hand_squid(connection, hand_id)
        hand["raw_events"] = [
            {
                "sequence": event["sequence"],
                "event_name": event["event_name"],
                "payload": json.loads(event["payload_json"]),
            }
            for event in connection.execute(
                """
                SELECT sequence, event_name, payload_json
                FROM raw_events WHERE hand_id = ? ORDER BY sequence
                """,
                (hand_id,),
            )
        ]
        return hand
    finally:
        connection.close()


def opponent_stats(
    connection: sqlite3.Connection, mode: Optional[str] = None
) -> List[Dict[str, Any]]:
    players: Dict[str, Dict[str, Any]] = {}
    for row in connection.execute(
        """
        SELECT hp.user_id, COALESCE(p.latest_alias, hp.alias) AS alias,
               hp.hand_id, hp.net, hp.hole_cards_json, h.board_json, h.game_mode
        FROM hand_players hp
        JOIN hands h ON h.hand_id = hp.hand_id
        LEFT JOIN players p ON p.user_id = hp.user_id
        WHERE hp.is_hero = 0 AND hp.user_id IS NOT NULL
          AND h.excluded_from_stats = 0
          AND (? IS NULL OR h.game_mode = ?)
        """,
        (mode, mode),
    ):
        item = players.setdefault(
            row["user_id"],
            {
                "user_id": row["user_id"],
                "alias": row["alias"] or "未知玩家",
                "hands": set(),
                "modes": set(),
                "net_by_hand": {},
                "showdowns": set(),
                "showdown_wins": set(),
                "saw_flop": set(),
                "won_after_flop": set(),
                "aggressive": 0,
                "calls": 0,
                "opportunities": defaultdict(lambda: [0, 0]),
                "positions": defaultdict(lambda: defaultdict(lambda: [0, 0])),
                "sizing": defaultdict(list),
            },
        )
        hand_id = row["hand_id"]
        item["hands"].add(hand_id)
        item["modes"].add(row["game_mode"])
        item["net_by_hand"][hand_id] = row["net"]
        if json.loads(row["hole_cards_json"] or "[]"):
            item["showdowns"].add(hand_id)
            if (row["net"] or 0) > 0:
                item["showdown_wins"].add(hand_id)
        if len(json.loads(row["board_json"] or "[]")) >= 3:
            item["saw_flop"].add(hand_id)
            if (row["net"] or 0) > 0:
                item["won_after_flop"].add(hand_id)

    population: Dict[str, List[int]] = defaultdict(lambda: [0, 0])
    for row in connection.execute(
        """
        SELECT o.user_id, o.metric, o.success, o.position
        FROM opportunities o
        JOIN hands h ON h.hand_id = o.hand_id
        WHERE o.user_id IS NOT NULL AND h.excluded_from_stats = 0
          AND (? IS NULL OR o.game_mode = ?)
        """,
        (mode, mode),
    ):
        population[row["metric"]][0] += int(row["success"])
        population[row["metric"]][1] += 1
        if row["user_id"] in players:
            metric = players[row["user_id"]]["opportunities"][row["metric"]]
            metric[0] += int(row["success"])
            metric[1] += 1
            position = row["position"] or "Unknown"
            bucket = players[row["user_id"]]["positions"][position][row["metric"]]
            bucket[0] += int(row["success"])
            bucket[1] += 1

    for row in connection.execute(
        """
        SELECT d.user_id, d.street, d.chosen_action, d.bet_fraction
        FROM decision_snapshots d
        JOIN hands h ON h.hand_id = d.hand_id
        WHERE d.user_id IS NOT NULL AND h.excluded_from_stats = 0
          AND (? IS NULL OR d.game_mode = ?)
        """,
        (mode, mode),
    ):
        if row["user_id"] not in players:
            continue
        action = row["chosen_action"]
        if row["street"] != "preflop":
            if action in {"bet", "raise", "all_in"}:
                players[row["user_id"]]["aggressive"] += 1
            elif action == "call":
                players[row["user_id"]]["calls"] += 1
        if row["bet_fraction"] is not None and action in {"bet", "raise", "all_in"}:
            players[row["user_id"]]["sizing"][row["street"]].append(
                float(row["bet_fraction"])
            )

    result = []
    for item in players.values():
        metrics = {
            metric: _bayesian_metric(successes, trials, population.get(metric))
            for metric, (successes, trials) in item["opportunities"].items()
        }
        positions = {
            position: {
                metric: _bayesian_metric(successes, trials, population.get(metric))
                for metric, (successes, trials) in values.items()
            }
            for position, values in item["positions"].items()
        }
        sizing = {
            street: {
                "count": len(values),
                "median_pot": round(_quantile(values, 0.5), 2),
                "p25_pot": round(_quantile(values, 0.25), 2),
                "p75_pot": round(_quantile(values, 0.75), 2),
            }
            for street, values in item["sizing"].items()
        }
        hand_count = len(item["hands"])
        vpip = metrics.get("vpip", _bayesian_metric(0, 0, population.get("vpip")))
        pfr = metrics.get("pfr", _bayesian_metric(0, 0, population.get("pfr")))
        three_bet = metrics.get(
            "three_bet", _bayesian_metric(0, 0, population.get("three_bet"))
        )
        tendencies = _tendencies(metrics, item["aggressive"], item["calls"])
        result.append(
            {
                "user_id": item["user_id"],
                "alias": item["alias"],
                "hands": hand_count,
                "modes": sorted(item["modes"]),
                "net": sum(value or 0 for value in item["net_by_hand"].values()),
                "metrics": metrics,
                "positions": positions,
                "sizing": sizing,
                "tendencies": tendencies,
                "vpip_pct": vpip["mean_pct"],
                "pfr_pct": pfr["mean_pct"],
                "three_bet_pct": three_bet["mean_pct"],
                "showdown_pct": _pct(len(item["showdowns"]), hand_count),
                "wtsd_pct": _pct(len(item["showdowns"]), len(item["saw_flop"])),
                "w_sd_pct": _pct(
                    len(item["showdown_wins"]), len(item["showdowns"])
                ),
                "wwsf_pct": _pct(
                    len(item["won_after_flop"]), len(item["saw_flop"])
                ),
                "aggression_factor": round(
                    item["aggressive"] / max(1, item["calls"]), 2
                ),
            }
        )
    return sorted(result, key=lambda item: (-item["hands"], item["alias"]))


def _bayesian_metric(
    successes: int, trials: int, population: Optional[List[int]], strength: float = 12.0
) -> Dict[str, Any]:
    population_successes, population_trials = population or [0, 0]
    prior_mean = (
        (population_successes + 1) / (population_trials + 2)
        if population_trials
        else 0.5
    )
    alpha = prior_mean * strength + successes
    beta = (1 - prior_mean) * strength + trials - successes
    mean = alpha / (alpha + beta)
    variance = alpha * beta / (
        (alpha + beta) ** 2 * (alpha + beta + 1)
    )
    delta = 1.2816 * math.sqrt(variance)
    return {
        "successes": successes,
        "opportunities": trials,
        "observed_pct": _pct(successes, trials),
        "mean_pct": round(100 * mean, 1),
        "low_pct": round(100 * max(0.0, mean - delta), 1),
        "high_pct": round(100 * min(1.0, mean + delta), 1),
        "confidence": _confidence(trials),
    }


def _confidence(trials: int) -> str:
    if trials < 10:
        return "very_low"
    if trials < 30:
        return "low"
    if trials < 100:
        return "medium"
    return "high"


def _quantile(values: List[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def _tendencies(
    metrics: Dict[str, Dict[str, Any]], aggressive: int, calls: int
) -> List[Dict[str, str]]:
    result: List[Dict[str, str]] = []
    rules = [
        ("fold_to_three_bet", 65, "面对 3bet 偏紧", "可测试更宽的阻断牌 3bet"),
        ("fold_to_flop_cbet", 60, "翻牌面对 cbet 偏紧", "适度扩大翻牌范围下注"),
        ("flop_cbet", 75, "翻牌持续下注偏高", "增加有后门权益的跟注与转牌反击"),
        ("river_cbet", 65, "河牌三枪偏高", "用阻断牌和摊牌数据校准 bluff-catch"),
    ]
    for metric, threshold, label, exploit in rules:
        value = metrics.get(metric)
        if (
            value
            and value["opportunities"] >= 10
            and value["low_pct"] >= threshold
        ):
            result.append({"label": label, "exploit": exploit, "evidence": metric})
    if aggressive >= 10 and aggressive / max(1, calls) >= 3:
        result.append(
            {
                "label": "翻后激进因子偏高",
                "exploit": "扩大强 bluff-catcher 继续范围，减少边缘偷鸡",
                "evidence": "aggression_factor",
            }
        )
    return result


def squid_snapshot(connection: sqlite3.Connection) -> Dict[str, Any]:
    rounds = [
        {
            **dict(row),
            "hand_refs": json.loads(row["hand_refs_json"]),
            "latest": json.loads(row["latest_json"]),
        }
        for row in connection.execute(
            """
            SELECT round_id, table_id, started_at, ended_at, status,
                   participant_count, hand_refs_json, latest_json
            FROM squid_rounds ORDER BY started_at DESC LIMIT 30
            """
        )
    ]
    settlements = [
        dict(row)
        for row in connection.execute(
            """
            SELECT round_id, user_id, alias, add_score, current_score
            FROM squid_settlements ORDER BY round_id DESC
            """
        )
    ]
    return {"rounds": rounds, "settlements": settlements}


def _pct(numerator: int, denominator: int) -> float:
    return round(100 * numerator / denominator, 1) if denominator else 0.0


def _cn_time(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(ZoneInfo("Asia/Shanghai")).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except ValueError:
        return None


def _hand_squid(
    connection: sqlite3.Connection, hand_id: str
) -> Dict[str, Any]:
    players = [
        {
            **dict(row),
            "gained_by_snapshot": row["squid_end"] - row["squid_start"],
        }
        for row in connection.execute(
            """
            SELECT user_id, alias, squid_start, squid_end
            FROM hand_squid_players
            WHERE hand_id = ?
            ORDER BY squid_start DESC, alias
            """,
            (hand_id,),
        )
    ]
    awards = [
        dict(row)
        for row in connection.execute(
            """
            SELECT user_id, alias, award_count, event_sequence
            FROM squid_awards WHERE hand_id = ?
            ORDER BY award_count DESC, alias
            """,
            (hand_id,),
        )
    ]
    return {"players": players, "awards": awards}
