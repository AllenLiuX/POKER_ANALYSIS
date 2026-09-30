"""Decision-facing player portrait tags.

Tags are multi-label and opportunity-conditioned. They are derived from the
same additive opportunity counters as HUD metrics, so a schema-version bump
replays history and later hands accumulate naturally.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


CONFIDENCE_RANK = {
    "very_low": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
}


@dataclass(frozen=True)
class TagSpec:
    tag_id: str
    family: str
    label: str
    metric: str
    direction: str  # "high" or "low"
    exploit: str
    streets: Sequence[str]
    min_n: int = 5
    medium_n: int = 15
    high_n: int = 40
    delta_pp: float = 8.0
    showdown_only: bool = False


TAG_SPECS: Sequence[TagSpec] = (
    TagSpec(
        tag_id="big_pot_calldown",
        family="stick",
        label="大底池跟住",
        metric="big_pot_calldown",
        direction="high",
        exploit="少打无阻断诈唬，薄价值加频加厚",
        streets=("turn", "river"),
        min_n=5,
        medium_n=15,
        high_n=40,
        delta_pp=8.0,
    ),
    TagSpec(
        tag_id="river_station",
        family="stick",
        label="河牌大注黏",
        metric="river_station",
        direction="high",
        exploit="河牌几乎只打价值，尺寸可以极化",
        streets=("river",),
        min_n=4,
        medium_n=12,
        high_n=30,
        delta_pp=8.0,
    ),
    TagSpec(
        tag_id="missed_initiative",
        family="scared",
        label="有主动权不敢开火",
        metric="missed_initiative",
        direction="high",
        exploit="他过牌后放心 probe/stab，其下注更像价值",
        streets=("flop", "turn", "river"),
        min_n=8,
        medium_n=20,
        high_n=50,
        delta_pp=8.0,
    ),
    TagSpec(
        tag_id="ip_river_checkback",
        family="scared",
        label="IP 河牌过牌",
        metric="ip_river_checkback",
        direction="high",
        exploit="IP 过牌范围偏封顶，可薄价值或弃边缘抓诈",
        streets=("river",),
        min_n=5,
        medium_n=12,
        high_n=30,
        delta_pp=8.0,
    ),
    TagSpec(
        tag_id="call_then_lead",
        family="drawlead",
        label="买牌领打",
        metric="call_then_lead",
        direction="high",
        exploit="跟注后常抢主动权，其过牌更弱；这条只统计频率，不含牌力",
        streets=("flop", "turn", "river"),
        min_n=6,
        medium_n=15,
        high_n=35,
        delta_pp=8.0,
    ),
    TagSpec(
        tag_id="overfold_small_bet",
        family="fold",
        label="面对小注过弃",
        metric="overfold_small_bet",
        direction="high",
        exploit="提高小注诈唬频率，大注改为价值",
        streets=("flop", "turn", "river"),
        min_n=8,
        medium_n=20,
        high_n=45,
        delta_pp=8.0,
    ),
    TagSpec(
        tag_id="fold_to_barrel",
        family="fold",
        label="面对二枪易弃",
        metric="fold_to_barrel",
        direction="high",
        exploit="可多打合理二枪；他跟住则转价值",
        streets=("turn", "river"),
        min_n=6,
        medium_n=15,
        high_n=35,
        delta_pp=8.0,
    ),
    TagSpec(
        tag_id="fold_to_postflop_raise",
        family="fold",
        label="面对加注易弃",
        metric="fold_to_postflop_raise",
        direction="high",
        exploit="可更宽地加注惩罚 cbet",
        streets=("flop", "turn", "river"),
        min_n=6,
        medium_n=15,
        high_n=35,
        delta_pp=8.0,
    ),
    TagSpec(
        tag_id="folds_big_pots",
        family="fold",
        label="大底池易弃",
        metric="big_pot_calldown",
        direction="low",
        exploit="大池可增加合理阻断牌诈唬",
        streets=("turn", "river"),
        min_n=5,
        medium_n=15,
        high_n=40,
        delta_pp=8.0,
    ),
    TagSpec(
        tag_id="fires_initiative",
        family="aggro",
        label="有主动权爱开火",
        metric="missed_initiative",
        direction="low",
        exploit="其持续下注偏宽，多抓诈、少自动弃牌",
        streets=("flop", "turn", "river"),
        min_n=8,
        medium_n=20,
        high_n=50,
        delta_pp=8.0,
    ),
    TagSpec(
        tag_id="slowplay_two_pair_plus",
        family="trap",
        label="大牌蹲坑",
        metric="slowplay_two_pair_plus",
        direction="high",
        exploit="过牌/跟注含坚果，少空枪过牌加注，薄价值可以打",
        streets=("flop", "turn", "river"),
        min_n=4,
        medium_n=10,
        high_n=24,
        delta_pp=10.0,
        showdown_only=True,
    ),
    TagSpec(
        tag_id="checked_strong_pair",
        family="scared",
        label="一对不敢打",
        metric="checked_strong_pair",
        direction="high",
        exploit="过牌一对偏多，可薄价值；其主动下注更像两对+",
        streets=("flop", "turn", "river"),
        min_n=4,
        medium_n=10,
        high_n=24,
        delta_pp=10.0,
        showdown_only=True,
    ),
    TagSpec(
        tag_id="delayed_value",
        family="trap",
        label="翻牌过牌后延迟价值",
        metric="delayed_value",
        direction="high",
        exploit="转河开火更像价值，少当空气抓",
        streets=("turn", "river"),
        min_n=4,
        medium_n=10,
        high_n=22,
        delta_pp=10.0,
        showdown_only=True,
    ),
    TagSpec(
        tag_id="check_raise_nuts",
        family="trap",
        label="过牌加注拿坚果",
        metric="check_raise_nuts",
        direction="high",
        exploit="面对 CR 少跟空气，除非有阻断/坚果",
        streets=("flop", "turn", "river"),
        min_n=3,
        medium_n=8,
        high_n=18,
        delta_pp=12.0,
        showdown_only=True,
    ),
    TagSpec(
        tag_id="hit_then_lead",
        family="drawlead",
        label="命中后领打",
        metric="hit_then_lead",
        direction="high",
        exploit="成牌后爱领打，过牌更像没中；领打是否价值看亮牌「领打是命中」",
        streets=("flop", "turn", "river"),
        min_n=4,
        medium_n=10,
        high_n=22,
        delta_pp=10.0,
        showdown_only=True,
    ),
    TagSpec(
        tag_id="lead_was_hit",
        family="drawlead",
        label="领打亮牌是命中",
        metric="shown_lead_was_hit",
        direction="high",
        exploit="亮牌领打里成牌偏多，面对其领打少空枪反打、按价值防守",
        streets=("flop", "turn", "river"),
        min_n=4,
        medium_n=10,
        high_n=22,
        delta_pp=10.0,
        showdown_only=True,
    ),
    TagSpec(
        tag_id="lead_was_air",
        family="air",
        label="领打偏空气",
        metric="shown_lead_was_hit",
        direction="low",
        exploit="可加注惩罚领打，尤其在砖块公牌",
        streets=("flop", "turn", "river"),
        min_n=4,
        medium_n=10,
        high_n=22,
        delta_pp=10.0,
        showdown_only=True,
    ),
    TagSpec(
        tag_id="miss_then_give_up",
        family="fold",
        label="听牌没中就放弃",
        metric="miss_then_give_up",
        direction="high",
        exploit="他过牌/弃牌后别再给免费牌，可薄打",
        streets=("turn", "river"),
        min_n=4,
        medium_n=10,
        high_n=22,
        delta_pp=10.0,
        showdown_only=True,
    ),
    TagSpec(
        tag_id="shown_air_aggression",
        family="air",
        label="亮牌进攻偏空气",
        metric="shown_air_aggression",
        direction="high",
        exploit="多抓诈、轻跟；其过牌则更像放弃",
        streets=("flop", "turn", "river"),
        min_n=5,
        medium_n=12,
        high_n=28,
        delta_pp=8.0,
        showdown_only=True,
    ),
    TagSpec(
        tag_id="value_heavy_aggression",
        family="aggro",
        label="进攻很少空气",
        metric="shown_air_aggression",
        direction="low",
        exploit="其下注当价值，少抓空气",
        streets=("flop", "turn", "river"),
        min_n=5,
        medium_n=12,
        high_n=28,
        delta_pp=8.0,
        showdown_only=True,
    ),
    TagSpec(
        tag_id="river_air_bluff",
        family="air",
        label="河牌空气诈唬",
        metric="river_air_bluff",
        direction="high",
        exploit="河牌可多抓；其大注也不全是坚果",
        streets=("river",),
        min_n=4,
        medium_n=10,
        high_n=22,
        delta_pp=8.0,
        showdown_only=True,
    ),
    TagSpec(
        tag_id="river_value_only",
        family="aggro",
        label="河牌几乎不打空气",
        metric="river_air_bluff",
        direction="low",
        exploit="河牌只跟坚果/阻断，少 spew 抓诈",
        streets=("river",),
        min_n=4,
        medium_n=10,
        high_n=22,
        delta_pp=8.0,
        showdown_only=True,
    ),
    TagSpec(
        tag_id="call_vs_raise",
        family="stick",
        label="面对加注爱跟",
        metric="call_vs_raise",
        direction="high",
        exploit="加注多为价值，减少轻率再加；其跟注范围偏宽",
        streets=("flop", "turn", "river"),
        min_n=6,
        medium_n=18,
        high_n=40,
        delta_pp=8.0,
    ),
    TagSpec(
        tag_id="overcall_overbet",
        family="stick",
        label="面对超池爱跟",
        metric="overcall_overbet",
        direction="high",
        exploit="超池多为价值，少空枪超池",
        streets=("flop", "turn", "river"),
        min_n=4,
        medium_n=10,
        high_n=25,
        delta_pp=8.0,
    ),
    TagSpec(
        tag_id="bet_aversion",
        family="scared",
        label="有开火机会却过牌",
        metric="bet_aversion",
        direction="high",
        exploit="整体当紧弱：过牌就偷，其主动下注更像价值",
        streets=("flop", "turn", "river"),
        min_n=10,
        medium_n=25,
        high_n=60,
        delta_pp=8.0,
    ),
    TagSpec(
        tag_id="draw_complete_donk",
        family="drawlead",
        label="听牌完成就领打",
        metric="draw_complete_donk",
        direction="high",
        exploit="donk 当价值，check-raise 其领打要更紧",
        streets=("flop", "turn", "river"),
        min_n=3,
        medium_n=8,
        high_n=16,
        delta_pp=10.0,
        showdown_only=True,
    ),
    TagSpec(
        tag_id="bluff_size_split",
        family="air",
        label="空气爱打小注",
        metric="bluff_size_split",
        direction="high",
        exploit="小注多抓，大注当价值，不要用一个平均诈唬率",
        streets=("flop", "turn", "river"),
        min_n=6,
        medium_n=15,
        high_n=30,
        delta_pp=8.0,
        showdown_only=True,
    ),
    TagSpec(
        tag_id="bluff_overbet",
        family="air",
        label="空气爱打大注",
        metric="bluff_size_split",
        direction="low",
        exploit="大注/超池更像空气，小注更像薄价值",
        streets=("flop", "turn", "river"),
        min_n=6,
        medium_n=15,
        high_n=30,
        delta_pp=8.0,
        showdown_only=True,
    ),
    TagSpec(
        tag_id="low_wsd_large_pot",
        family="stick",
        label="大池摊牌常输",
        metric="low_wsd_large_pot",
        direction="high",
        exploit="确认跟太宽，继续加压价值",
        streets=("turn", "river"),
        min_n=4,
        medium_n=10,
        high_n=25,
        delta_pp=8.0,
        showdown_only=True,
    ),
    TagSpec(
        tag_id="thin_value_medium",
        family="aggro",
        label="中等牌力爱薄打",
        metric="thin_value_medium",
        direction="high",
        exploit="一对转河常开火，过牌更弱可多偷；其主动下注少空气",
        streets=("turn", "river"),
        min_n=4,
        medium_n=10,
        high_n=22,
        delta_pp=10.0,
        showdown_only=True,
    ),
    TagSpec(
        tag_id="checks_medium",
        family="scared",
        label="中等牌力不打薄价值",
        metric="thin_value_medium",
        direction="low",
        exploit="过牌含顶对/中间对，可薄打；其主动下注更像两对+",
        streets=("turn", "river"),
        min_n=4,
        medium_n=10,
        high_n=22,
        delta_pp=10.0,
        showdown_only=True,
    ),
    TagSpec(
        tag_id="weak_pays_big",
        family="stick",
        label="弱牌大池爱付钱",
        metric="weak_pays_big",
        direction="high",
        exploit="垃圾/底对也会跟大池，少诈唬、价值加厚",
        streets=("turn", "river"),
        min_n=4,
        medium_n=10,
        high_n=22,
        delta_pp=10.0,
        showdown_only=True,
    ),
    TagSpec(
        tag_id="folds_weak_big",
        family="fold",
        label="弱牌大池会弃",
        metric="weak_pays_big",
        direction="low",
        exploit="大池弱牌会放，可打合理阻断诈唬",
        streets=("turn", "river"),
        min_n=4,
        medium_n=10,
        high_n=22,
        delta_pp=10.0,
        showdown_only=True,
    ),
    TagSpec(
        tag_id="medium_calls_big",
        family="stick",
        label="一对大池爱跟",
        metric="medium_calls_big",
        direction="high",
        exploit="顶对/中间对在大池黏，继续加压价值、少空枪",
        streets=("turn", "river"),
        min_n=4,
        medium_n=10,
        high_n=22,
        delta_pp=10.0,
        showdown_only=True,
    ),
    TagSpec(
        tag_id="folds_medium_big",
        family="fold",
        label="一对大池易弃",
        metric="medium_calls_big",
        direction="low",
        exploit="大池一对会放，可把一对压弃",
        streets=("turn", "river"),
        min_n=4,
        medium_n=10,
        high_n=22,
        delta_pp=10.0,
        showdown_only=True,
    ),
    TagSpec(
        tag_id="river_weak_call",
        family="stick",
        label="河牌弱牌爱跟",
        metric="river_weak_call",
        direction="high",
        exploit="河牌空气/底对也跟，价值加厚、少打空气",
        streets=("river",),
        min_n=4,
        medium_n=10,
        high_n=22,
        delta_pp=8.0,
        showdown_only=True,
    ),
    TagSpec(
        tag_id="folds_river_weak",
        family="fold",
        label="河牌弱牌易弃",
        metric="river_weak_call",
        direction="low",
        exploit="河牌弱牌会放，可合理诈唬",
        streets=("river",),
        min_n=4,
        medium_n=10,
        high_n=22,
        delta_pp=8.0,
        showdown_only=True,
    ),
)


PORTRAIT_METRIC_KEYS = tuple(sorted({spec.metric for spec in TAG_SPECS}))


def player_portrait_tags(
    connection: sqlite3.Connection,
    user_id: str,
    game_mode: Optional[str] = None,
    *,
    street: Optional[str] = None,
    limit: Optional[int] = None,
    spot_limit: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Load opportunity posteriors for one player and return overall + spot tags."""

    metrics = portrait_metrics(connection, user_id, game_mode)
    tags = classify_portrait_tags(metrics, limit=limit)
    return tags, match_tags_to_spot(tags, street=street, limit=spot_limit)


def portrait_metrics(
    connection: sqlite3.Connection,
    user_id: str,
    game_mode: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    placeholders = ",".join("?" for _ in PORTRAIT_METRIC_KEYS)
    rows = connection.execute(
        f"""
        SELECT o.user_id, o.metric, o.success
        FROM opportunities o
        JOIN hands h ON h.hand_id = o.hand_id
        WHERE h.excluded_from_stats = 0
          AND o.metric IN ({placeholders})
          AND (? IS NULL OR o.game_mode = ?)
        """,
        (*PORTRAIT_METRIC_KEYS, game_mode, game_mode),
    ).fetchall()
    population: Dict[str, List[int]] = {
        metric: [0, 0] for metric in PORTRAIT_METRIC_KEYS
    }
    player: Dict[str, List[int]] = {
        metric: [0, 0] for metric in PORTRAIT_METRIC_KEYS
    }
    for row in rows:
        metric = str(row[1])
        success = int(row[2] or 0)
        population[metric][0] += success
        population[metric][1] += 1
        if str(row[0] or "") == user_id:
            player[metric][0] += success
            player[metric][1] += 1
    return {
        metric: _bayesian_metric(player[metric][0], player[metric][1], population[metric])
        for metric in PORTRAIT_METRIC_KEYS
        if player[metric][1] > 0
    }


def classify_portrait_tags(
    metrics: Mapping[str, Mapping[str, Any]],
    *,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return fired tags ranked by |delta| * sqrt(n)."""

    tags: List[Dict[str, Any]] = []
    for spec in TAG_SPECS:
        metric = metrics.get(spec.metric)
        tag = _evaluate_spec(spec, metric)
        if tag is not None:
            tags.append(tag)
    tags.sort(
        key=lambda item: (
            -CONFIDENCE_RANK.get(str(item["confidence"]), 0),
            -float(item["_score"]),
            item["id"],
        )
    )
    for item in tags:
        item.pop("_score", None)
    if limit is None:
        return tags
    return tags[: max(0, int(limit))]


def match_tags_to_spot(
    tags: Iterable[Mapping[str, Any]],
    *,
    street: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Keep tags that apply to the current street; preflop keeps overall leaks."""

    street_key = str(street or "").lower()
    matched: List[Dict[str, Any]] = []
    cap = None if limit is None else max(0, int(limit))
    for tag in tags:
        streets = {str(item) for item in (tag.get("streets") or [])}
        if not street_key or street_key == "preflop" or street_key in streets:
            matched.append(dict(tag))
        if cap is not None and len(matched) >= cap:
            break
    return matched


def _evaluate_spec(
    spec: TagSpec, metric: Optional[Mapping[str, Any]]
) -> Optional[Dict[str, Any]]:
    if not metric:
        return None
    trials = int(metric.get("opportunities") or 0)
    if trials < spec.min_n:
        return None
    mean = _number(metric.get("mean_pct"))
    pool = _number(metric.get("population_mean_pct"))
    if mean is None or pool is None:
        return None
    delta = mean - pool
    if spec.direction == "high" and delta < spec.delta_pp:
        return None
    if spec.direction == "low" and delta > -spec.delta_pp:
        return None
    low = _number(metric.get("low_pct"))
    high = _number(metric.get("high_pct"))
    separated = False
    if low is not None and high is not None:
        separated = low > pool or high < pool
    if trials >= spec.medium_n and not separated:
        return None
    if trials >= spec.high_n:
        confidence = "high"
    elif trials >= spec.medium_n:
        confidence = "medium"
    else:
        confidence = "low"
    score = abs(delta) * min(1.0, (trials / 30) ** 0.5)
    if separated:
        score += 8.0
    return {
        "id": spec.tag_id,
        "family": spec.family,
        "label": spec.label,
        "exploit": spec.exploit,
        "metric": spec.metric,
        "direction": spec.direction,
        "streets": list(spec.streets),
        "mean_pct": mean,
        "population_mean_pct": pool,
        "delta_pp": round(delta, 1),
        "opportunities": trials,
        "successes": int(metric.get("successes") or 0),
        "confidence": confidence,
        "interval_excludes_pool": separated,
        "evidence": spec.metric,
        "selection_bias": "showdown_only" if spec.showdown_only else "action",
        "_score": score,
    }


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
    delta = 1.2816 * math.sqrt(max(0.0, variance))
    if trials < 10:
        confidence = "very_low"
    elif trials < 30:
        confidence = "low"
    elif trials < 100:
        confidence = "medium"
    else:
        confidence = "high"
    return {
        "successes": successes,
        "opportunities": trials,
        "observed_pct": round(100 * successes / trials, 1) if trials else None,
        "mean_pct": round(100 * mean, 1),
        "population_mean_pct": round(100 * prior_mean, 1),
        "low_pct": round(100 * max(0.0, mean - delta), 1),
        "high_pct": round(100 * min(1.0, mean + delta), 1),
        "confidence": confidence,
    }


def _number(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
