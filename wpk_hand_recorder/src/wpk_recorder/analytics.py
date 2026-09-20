from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from .inference import hand_range_predictions
from .reasoning import build_preflop_preview, live_decision, public_decision
from .storage import hand_from_dict

def connect_readonly(data_dir: Path) -> sqlite3.Connection:
    path = data_dir / "hands.sqlite3"
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA query_only=ON")
    connection.row_factory = sqlite3.Row
    return connection


def _preflop_preview_payload(hand: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(hand, dict):
        return None
    if hand.get("status") != "in_progress" or list(hand.get("board") or []):
        return None
    try:
        return build_preflop_preview(hand_from_dict(hand))
    except (TypeError, ValueError, KeyError, AttributeError):
        return None


def snapshot(
    data_dir: Path,
    hand_limit: int = 30,
    event_limit: int = 80,
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    connection = connect_readonly(data_dir)
    try:
        hands, _ = _hand_summaries(
            connection,
            limit=hand_limit,
            offset=0,
            mode=mode,
        )
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
        decision = public_decision(live_decision(connection, mode=mode))
        current_hand = next(
            (hand for hand in hands if hand["quality_status"] == "live"),
            None,
        )
        if current_hand is None:
            latest_hand_id = next(
                (
                    str(event["hand_id"])
                    for event in events
                    if event.get("hand_id")
                ),
                None,
            )
            current_hand = next(
                (
                    hand
                    for hand in hands
                    if str(hand.get("hand_id") or "") == latest_hand_id
                ),
                hands[0] if hands else None,
            )
        _apply_runtime_player_states(current_hand, events)
        return {
            "last_sequence": last_sequence,
            "current_hand": current_hand,
            "live_decision": decision,
            "preflop_preview": _preflop_preview_payload(current_hand),
            "hands": hands,
            "events": events,
            "opponents": opponent_stats(connection, mode),
            "hero": hero_stats(connection, mode),
            "squid": squid_snapshot(connection),
        }
    finally:
        connection.close()


def live_snapshot(
    data_dir: Path,
    hand_limit: int = 3,
    event_limit: int = 30,
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Load only latency-sensitive table state for the SSE update loop."""

    connection = connect_readonly(data_dir)
    try:
        hands, _ = _hand_summaries(
            connection,
            limit=hand_limit,
            offset=0,
            mode=mode,
        )
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
        current_hand = next(
            (hand for hand in hands if hand["quality_status"] == "live"),
            None,
        )
        if current_hand is None:
            latest_hand_id = next(
                (
                    str(event["hand_id"])
                    for event in events
                    if event.get("hand_id")
                ),
                None,
            )
            current_hand = next(
                (
                    hand
                    for hand in hands
                    if str(hand.get("hand_id") or "") == latest_hand_id
                ),
                hands[0] if hands else None,
            )
        _apply_runtime_player_states(current_hand, events)
        return {
            "last_sequence": last_sequence,
            "current_hand": current_hand,
            "live_decision": public_decision(
                live_decision(connection, mode=mode)
            ),
            "preflop_preview": _preflop_preview_payload(current_hand),
        }
    finally:
        connection.close()


def _apply_runtime_player_states(
    hand: Optional[Dict[str, Any]],
    events: List[Dict[str, Any]],
) -> None:
    """Overlay the latest client-rendered fold state on the live hand."""

    if not hand or hand.get("status") != "in_progress":
        return
    hand_id = str(hand.get("hand_id") or "")
    for event in events:
        if str(event.get("hand_id") or "") != hand_id:
            continue
        payload = event.get("payload")
        states = (
            payload.get("_recorderPlayerStates")
            if isinstance(payload, dict)
            else None
        )
        if not isinstance(states, list):
            continue
        by_user = {
            str(state.get("userId")): state
            for state in states
            if isinstance(state, dict) and state.get("userId") is not None
        }
        if not by_user:
            continue
        for player in hand.get("players") or []:
            state = by_user.get(str(player.get("user_id")))
            if state is None or not isinstance(state.get("isFold"), bool):
                continue
            player["folded"] = state["isFold"]
        hand["player_state_source"] = "client_runtime"
        hand["player_state_sequence"] = event.get("sequence")
        return


def hand_summaries(
    data_dir: Path,
    *,
    limit: int = 50,
    offset: int = 0,
    mode: Optional[str] = None,
    valid_only: bool = False,
    user_id: Optional[str] = None,
    revealed_only: bool = False,
    query: Optional[str] = None,
    result: Optional[str] = None,
    street: Optional[str] = None,
) -> Dict[str, Any]:
    connection = connect_readonly(data_dir)
    try:
        hands, total = _hand_summaries(
            connection,
            limit=limit,
            offset=offset,
            mode=mode,
            valid_only=valid_only,
            user_id=user_id,
            revealed_only=revealed_only,
            query=query,
            result=result,
            street=street,
        )
        return {
            "hands": hands,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(hands) < total,
        }
    finally:
        connection.close()


def _hand_summaries(
    connection: sqlite3.Connection,
    *,
    limit: int,
    offset: int,
    mode: Optional[str],
    valid_only: bool = False,
    user_id: Optional[str] = None,
    revealed_only: bool = False,
    query: Optional[str] = None,
    result: Optional[str] = None,
    street: Optional[str] = None,
) -> tuple[List[Dict[str, Any]], int]:
    clauses = ["(? IS NULL OR h.game_mode = ?)"]
    parameters: List[Any] = [mode, mode]
    if valid_only:
        clauses.append("h.quality_status = 'good'")
    if user_id:
        reveal_clause = (
            " AND COALESCE(filtered_hp.hole_cards_json, '[]') <> '[]'"
            if revealed_only
            else ""
        )
        clauses.append(
            "EXISTS ("
            "SELECT 1 FROM hand_players filtered_hp "
            "WHERE filtered_hp.hand_id = h.hand_id "
            f"AND filtered_hp.user_id = ?{reveal_clause}"
            ")"
        )
        parameters.append(user_id)
    elif revealed_only:
        clauses.append(
            "EXISTS ("
            "SELECT 1 FROM hand_players revealed_hp "
            "WHERE revealed_hp.hand_id = h.hand_id "
            "AND revealed_hp.is_hero = 0 "
            "AND COALESCE(revealed_hp.hole_cards_json, '[]') <> '[]'"
            ")"
        )
    if query and query.strip():
        pattern = f"%{query.strip()}%"
        clauses.append(
            "("
            "h.hand_id LIKE ? OR CAST(h.hand_number AS TEXT) LIKE ? OR "
            "EXISTS ("
            "SELECT 1 FROM hand_players search_hp "
            "WHERE search_hp.hand_id = h.hand_id "
            "AND (search_hp.alias LIKE ? OR search_hp.user_id LIKE ?)"
            ")"
            ")"
        )
        parameters.extend([pattern, pattern, pattern, pattern])
    if result in {"won", "lost", "even"}:
        comparison = {"won": "> 0", "lost": "< 0", "even": "= 0"}[result]
        clauses.append(
            "EXISTS ("
            "SELECT 1 FROM hand_players hero_hp "
            "WHERE hero_hp.hand_id = h.hand_id "
            "AND hero_hp.is_hero = 1 "
            f"AND hero_hp.net {comparison}"
            ")"
        )
    board_count = "json_array_length(COALESCE(h.board_json, '[]'))"
    if street == "preflop":
        clauses.append(f"{board_count} = 0")
    elif street == "flop":
        clauses.append(f"{board_count} = 3")
    elif street == "turn":
        clauses.append(f"{board_count} = 4")
    elif street == "river":
        clauses.append(f"{board_count} >= 5")
    where = " AND ".join(clauses)
    total = int(
        connection.execute(
            f"SELECT COUNT(*) FROM hands h WHERE {where}",
            parameters,
        ).fetchone()[0]
    )
    rows = connection.execute(
        f"""
        SELECT h.hand_json, h.game_mode, h.hand_number, h.played_at,
               h.quality_status, h.quality_reasons_json, h.excluded_from_stats
        FROM hands h
        WHERE {where}
        ORDER BY CASE WHEN h.quality_status = 'live' THEN 0 ELSE 1 END,
                 h.played_at DESC, h.hand_number DESC
        LIMIT ? OFFSET ?
        """,
        [*parameters, limit, offset],
    )
    hands = []
    for row in rows:
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
    return hands, total


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
        hand["range_predictions"] = hand_range_predictions(connection, hand_id)
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
    return _player_stats(connection, mode, is_hero=False)


def hero_stats(
    connection: sqlite3.Connection, mode: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    profiles = _player_stats(connection, mode, is_hero=True)
    return profiles[0] if profiles else None


def _player_stats(
    connection: sqlite3.Connection,
    mode: Optional[str] = None,
    *,
    is_hero: bool,
) -> List[Dict[str, Any]]:
    players: Dict[str, Dict[str, Any]] = {}
    for row in connection.execute(
        """
        SELECT hp.user_id, COALESCE(p.latest_alias, hp.alias) AS alias,
               hp.hand_id, hp.net, hp.hole_cards_json, h.board_json, h.game_mode
        FROM hand_players hp
        JOIN hands h ON h.hand_id = hp.hand_id
        LEFT JOIN players p ON p.user_id = hp.user_id
        WHERE hp.is_hero = ? AND hp.user_id IS NOT NULL
          AND h.excluded_from_stats = 0
          AND (? IS NULL OR h.game_mode = ?)
        """,
        (int(is_hero), mode, mode),
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

    if not is_hero and players:
        # Occasional identity glitches can mark the seat owner as a non-hero.
        # Keep those seats out of the opponent list when the same user is
        # primarily recorded as hero, so hero samples never pollute opponents.
        alternate_counts: Dict[str, int] = defaultdict(int)
        for row in connection.execute(
            """
            SELECT hp.user_id, COUNT(DISTINCT hp.hand_id) AS hands
            FROM hand_players hp
            JOIN hands h ON h.hand_id = hp.hand_id
            WHERE hp.is_hero = 1 AND hp.user_id IS NOT NULL
              AND h.excluded_from_stats = 0
              AND (? IS NULL OR h.game_mode = ?)
            GROUP BY hp.user_id
            """,
            (mode, mode),
        ):
            alternate_counts[row["user_id"]] = int(row["hands"])
        for user_id in list(players):
            if alternate_counts.get(user_id, 0) >= len(players[user_id]["hands"]):
                del players[user_id]

    population: Dict[str, List[int]] = defaultdict(lambda: [0, 0])
    for row in connection.execute(
        """
        SELECT o.user_id, o.metric, o.success, o.position, hp.is_hero
        FROM opportunities o
        JOIN hands h ON h.hand_id = o.hand_id
        JOIN hand_players hp
          ON hp.hand_id = o.hand_id AND hp.user_id = o.user_id
        WHERE o.user_id IS NOT NULL AND h.excluded_from_stats = 0
          AND (? IS NULL OR o.game_mode = ?)
        """,
        (mode, mode),
    ):
        population[row["metric"]][0] += int(row["success"])
        population[row["metric"]][1] += 1
        if row["user_id"] not in players:
            continue
        if int(row["is_hero"]) != int(is_hero):
            continue
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
        JOIN hand_players hp
          ON hp.hand_id = d.hand_id AND hp.user_id = d.user_id
        WHERE d.user_id IS NOT NULL AND h.excluded_from_stats = 0
          AND hp.is_hero = ?
          AND (? IS NULL OR d.game_mode = ?)
        """,
        (int(is_hero), mode, mode),
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
        style = _player_style(metrics, item["aggressive"], item["calls"], hand_count)
        profile_caveats = []
        if len(item["showdowns"]) < 20:
            profile_caveats.append(
                f"仅有 {len(item['showdowns'])} 手公开底牌，范围形状仍以先验为主"
            )
        if max(
            (
                value["opportunities"]
                for key, value in metrics.items()
                if key not in {"vpip", "pfr"}
            ),
            default=0,
        ) < 30:
            profile_caveats.append("细分翻后节点机会不足 30 次，不生成高置信度漏洞")
        result.append(
            {
                "user_id": item["user_id"],
                "alias": item["alias"],
                "hands": hand_count,
                "revealed_hands": len(item["showdowns"]),
                "modes": sorted(item["modes"]),
                "net": sum(value or 0 for value in item["net_by_hand"].values()),
                "metrics": metrics,
                "positions": positions,
                "sizing": sizing,
                "tendencies": tendencies,
                "style": style,
                "profile_caveats": profile_caveats,
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
        "population_mean_pct": round(100 * prior_mean, 1),
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


def _player_style(
    metrics: Dict[str, Dict[str, Any]],
    aggressive: int,
    calls: int,
    hands: int,
) -> Dict[str, str]:
    vpip = float((metrics.get("vpip") or {}).get("mean_pct") or 0)
    pfr = float((metrics.get("pfr") or {}).get("mean_pct") or 0)
    gap = vpip - pfr
    aggression = aggressive / max(1, calls)
    if vpip >= 55 and pfr >= 24:
        label = "宽松激进"
        summary = "翻前进入较多底池且主动加注频繁，范围通常宽但方差较高。"
    elif vpip >= 52 and gap >= 28:
        label = "宽松被动"
        summary = "翻前入池很宽，但多数牌通过跟注进入，价值隔离通常优于纯诈唬。"
    elif vpip <= 42 and pfr <= 13:
        label = "偏紧被动"
        summary = "翻前主动性偏低，主动加注线应比其普通入池线受到更多尊重。"
    elif aggression >= 2.5:
        label = "翻后激进"
        summary = "翻后下注加注相对跟注更多，但仍需结合具体街道机会数。"
    else:
        label = "混合型"
        summary = "现有核心频率没有形成单一、稳定且高置信度的风格标签。"
    confidence = "medium" if hands >= 30 else ("low" if hands >= 10 else "very_low")
    return {"label": label, "summary": summary, "confidence": confidence}


def _tendencies(
    metrics: Dict[str, Dict[str, Any]], aggressive: int, calls: int
) -> List[Dict[str, str]]:
    result: List[Dict[str, str]] = []
    vpip = metrics.get("vpip")
    pfr = metrics.get("pfr")
    three_bet = metrics.get("three_bet")
    if (
        vpip
        and pfr
        and min(vpip["opportunities"], pfr["opportunities"]) >= 30
        and vpip["mean_pct"] - pfr["mean_pct"] >= 25
    ):
        result.append(
            {
                "label": "翻前跟注占比明显偏高",
                "exploit": "扩大强牌隔离范围和价值尺度，减少无权益多街诈唬",
                "evidence": "vpip_pfr_gap",
                "confidence": "medium",
            }
        )
    if pfr and pfr["opportunities"] >= 30 and pfr["mean_pct"] >= 24:
        result.append(
            {
                "label": "翻前主动加注范围较宽",
                "exploit": "位置好时扩大继续范围，并用价值牌和精选阻断牌反击",
                "evidence": "pfr",
                "confidence": "medium",
            }
        )
    if (
        three_bet
        and three_bet["opportunities"] >= 20
        and three_bet["high_pct"] <= 8
    ):
        result.append(
            {
                "label": "3bet 范围偏窄",
                "exploit": "其 3bet 线给予更多信用，同时扩大无人反击时的开池范围",
                "evidence": "three_bet",
                "confidence": "low",
            }
        )
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
            result.append(
                {
                    "label": label,
                    "exploit": exploit,
                    "evidence": metric,
                    "confidence": "low",
                }
            )
    if aggressive >= 10 and aggressive / max(1, calls) >= 3:
        result.append(
            {
                "label": "翻后激进因子偏高",
                "exploit": "扩大强 bluff-catcher 继续范围，减少边缘偷鸡",
                "evidence": "aggression_factor",
                "confidence": "low",
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
