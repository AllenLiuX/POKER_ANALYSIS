from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .poker import equity_vs_random, hand_features


STREETS = ("preflop", "flop", "turn", "river")
STREET_CARD_COUNT = {"preflop": 0, "flop": 3, "turn": 4, "river": 5}
FORCED_ACTIONS = {"ante", "small_blind", "big_blind", "straddle"}
RANKS = "AKQJT98765432"
PREFLOP_LINES = ("vpip", "open_raise", "three_bet", "cold_call", "limp")
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
    if hand_id:
        connection.execute(
            "DELETE FROM showdown_observations WHERE hand_id = ?", (hand_id,)
        )
    else:
        connection.execute("DELETE FROM showdown_observations")

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
            connection.execute(
                """
                INSERT INTO showdown_observations(
                    hand_id, user_id, alias, street, hole_cards_json, board_json,
                    preflop_class, strength_category, draws_json, equity_vs_random,
                    position, effective_stack_bb, squid_count,
                    cumulative_net_before, profit_state, action_line,
                    feature_tokens_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
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
    connection.commit()


def hand_range_predictions(
    connection: sqlite3.Connection, hand_id: str
) -> List[Dict[str, Any]]:
    training = _training_rows(connection)
    targets = _hand_targets(connection, hand_id)
    return [_predict(training, target, exclude_hand=hand_id) for target in targets]


def inference_backtest(connection: sqlite3.Connection) -> Dict[str, Any]:
    records = _training_rows(connection)
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
               s.profit_state, s.action_line
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
        hand_class = _canonical_starting_hand(json.loads(row[3] or "[]"))
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
            }
        action_rows = connection.execute(
            """
            SELECT street, chosen_action, facing_action, bet_fraction,
                   raise_multiple, players_in_hand, board_texture_json
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
                    "street": action[0],
                    "action": action[1],
                    "facing": action[2],
                    "size": size,
                    "raise_size": raise_size,
                    "players_in_hand": action[5],
                    "board_texture": texture_summary,
                }
            )
            information_score += 0.5
            if size in {"pot", "overbet"} or raise_size in {"large", "huge"}:
                information_score += 1
            if action[1] in {"raise", "all_in"}:
                information_score += 0.5
        if any(value in semantic_lines for value in ("three_bet", "open_raise")):
            information_score += 1
        candidates.append(
            {
                "_played_at": row[1] or "",
                "_recency": recency,
                "_score": information_score,
                "game_mode": row[2],
                "position": normalize_preflop_position(row[4]),
                "shown_hand": hand_class,
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
    return {
        "available_cases": len(candidates),
        "selected_cases": len(cases),
        "selection_method": "information_score_then_recency",
        "cases": cases,
        "limitations": [
            "案例只来自公开底牌，折叠且未公开的范围仍不可见",
            "为控制延迟，只发送信息量最高的匿名案例，不发送 hand_id 或时间戳",
        ],
    }


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
    rows = connection.execute(
        """
        SELECT s.hand_id, s.user_id, s.alias, s.position, s.hole_cards_json,
               s.action_line,
               GROUP_CONCAT(DISTINCT CASE WHEN o.success = 1 THEN o.metric END)
                   AS successful_metrics,
               GROUP_CONCAT(DISTINCT o.metric) AS opportunity_metrics
        FROM showdown_observations s
        JOIN hands h ON h.hand_id = s.hand_id
        LEFT JOIN opportunities o
          ON o.hand_id = s.hand_id AND o.user_id = s.user_id
        WHERE s.street = 'preflop'
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
        lines = _shown_preflop_lines(str(row[5] or ""), successful, opportunities)
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
    if stack_bb is None:
        return "unknown"
    if stack_bb < 20:
        return "short"
    if stack_bb < 60:
        return "medium"
    if stack_bb < 150:
        return "deep"
    return "very_deep"


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
