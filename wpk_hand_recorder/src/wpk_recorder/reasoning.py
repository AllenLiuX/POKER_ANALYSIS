from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urlsplit

from .decision_state import decision_state_contract, decision_state_from_hand
from .inference import preflop_range_profile
from .models import DecisionRequest
from .opponent_model import opponent_metrics
from .poker import equity_vs_random, hand_features
from .protocol import _card_list
from .squid_value import (
    anonymize_squid_state,
    current_squid_state,
    estimate_award_probabilities,
)
from .storage import hand_from_dict


DEFAULT_MODEL = "gpt-5.6-sol"
MODEL_ENDPOINT_TEMPLATE = (
    "https://aidp-i18ntt-sg.tiktok-row.net/api/modelhub/online/v2/crawl/"
    "openai/deployments/{model}/chat/completions?api-version=2024-02-01"
)
DEFAULT_ENDPOINT = MODEL_ENDPOINT_TEMPLATE.format(model=DEFAULT_MODEL)
SYSTEM_PROMPT = (
    "你是实时德州扑克决策复核助手。输入是本地记录器从当前行动点确定性提取的事实和派生量。"
    "你可以做扑克推理并给出一个建议，但不得编造未提供的底牌、历史频率、equity、EV 或 GTO 数字。"
    "先检查合法动作、成牌/听牌、跟注所需胜率，再参考对手画像；"
    "equity_vs_one_random_hand 只是中性基线，不能冒充对手范围胜率。"
    "对 8/9 人、深码、多人池、ante、鱿鱼模式和小样本要主动降低置信度。"
    "只输出严格 JSON，不要 Markdown，不要展示冗长思维链；仅给简洁、可核验的决策因素。"
)
EXPLOIT_SYSTEM_PROMPT = (
    "你是德州扑克实时范围剥削复核助手。当前可能处于观察模式，没有行动者底牌；"
    "此时只能调整整个范围，不能猜具体持牌或给单手牌动作。"
    "只使用输入里的 evidence_id 支撑对手判断，并区分当前牌池偏移与 GTO 偏移。"
    "样本少、多人池、深码、ante 或鱿鱼模式必须降低置信度。"
    "只输出严格 JSON，不要 Markdown，不展示思维链。"
)
PROFILE_SYSTEM_PROMPT = (
    "你是德州扑克对手画像复核助手。输入包含本地统计、相对牌池偏移、"
    "公开亮牌的收缩后范围摘要，以及匿名化亮牌与行动案例。"
    "不得把净输赢当作技术水平，不得编造未提供的牌、频率、GTO 或 EV 数字。"
    "亮牌存在摊牌选择偏差；低样本结论必须降置信度并明确保留意见。"
    "population_deviations 只是相对当前牌池，不是 GTO 偏移。"
    "你的任务是交叉检查统计与案例，找出有证据的行为漏洞并给出可执行但保守的对抗调整。"
    "只输出严格 JSON，不要 Markdown，不展示思维链。"
)
STRATEGY_RANKS = "AKQJT98765432"
POSITION_OPEN_FRACTIONS = {
    "UTG": 0.17,
    "UTG+1": 0.19,
    "MP": 0.21,
    "MP+1": 0.23,
    "HJ": 0.26,
    "CO": 0.32,
    "BTN": 0.46,
    "BTN/SB": 0.42,
    "SB": 0.40,
    "BB": 0.28,
}


class ReasoningError(RuntimeError):
    pass


def live_decision(
    connection: sqlite3.Connection,
    mode: Optional[str] = None,
    sequence: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    row = _decision_row(connection, sequence)
    if row is None:
        return None
    event_sequence, timestamp, hand_id, payload_json = row
    payload = json.loads(payload_json)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return None

    hand_row = _hand_row(connection, hand_id)
    if hand_row is None:
        return None
    resolved_hand_id, game_mode, encoded_hand = hand_row
    if mode is not None and game_mode != mode:
        return None
    hand_data = json.loads(encoded_hand)
    if hand_data.get("status") != "in_progress":
        return None
    if _has_newer_invalidation(connection, event_sequence, resolved_hand_id):
        return None
    hand = hand_from_dict(hand_data)
    actor_user_id = _text(data.get("userId"))
    runtime_user_id = _text(
        payload.get("_recorderCurrentUserId")
        or data.get("_recorderCurrentUserId")
    )
    players = hand_data.get("players") or []
    actor_player = next(
        (
            player
            for player in players
            if actor_user_id and _text(player.get("user_id")) == actor_user_id
        ),
        None,
    )
    known_hero = next(
        (player for player in players if player.get("is_hero") is True),
        None,
    )
    raw_hero_cards = _card_list(data.get("handCards"))
    is_hero_decision = bool(
        actor_player
        and (
            actor_player.get("is_hero") is True
            or (
                runtime_user_id
                and actor_user_id == runtime_user_id
            )
            or len(raw_hero_cards) == 2
        )
    )
    if known_hero is not None and not is_hero_decision:
        return None
    request = DecisionRequest(
        event_sequence=int(event_sequence),
        captured_at=_parse_time(timestamp).isoformat(),
        hand_id=resolved_hand_id,
        seat=(actor_player or {}).get("seat"),
        user_id=actor_user_id,
        cards=raw_hero_cards
        or (
            list((actor_player or {}).get("hole_cards") or [])
            if is_hero_decision
            else []
        ),
        legal_actions=[
            action
            for value in data.get("canActionList") or []
            if (action := _normalize_action(value))
        ],
        call_score=_number(data.get("callScore")) or 0.0,
        min_raise_to=_number(data.get("minRaiseScore")),
        max_raise_to=_number(data.get("maxRaiseScore")),
        countdown=_number(
            data.get("countDown")
            if data.get("countDown") is not None
            else data.get("totalCountDown")
        )
        or 0.0,
        last_bet=_number(data.get("lastBet")),
        seat_score=_number(data.get("seatScore")),
        current_score=_number(data.get("currentScore")),
    )
    if (
        hand.pending_decision is not None
        and hand.pending_decision.event_sequence == event_sequence
    ):
        request = hand.pending_decision
    contract = decision_state_from_hand(hand, request, source="live")
    if contract is None:
        return None
    if contract["remaining_ms"] <= 0:
        return None
    street = str(contract.get("street") or "")
    stack_bb = _number(contract.get("hero_stack_bb"))
    auto_reasoning, auto_reason = _reasoning_gate(
        legal_actions=list(contract.get("legal_actions") or []),
        hero_cards=list(contract.get("hero_cards") or []),
        street=street,
        game_mode=game_mode,
        players_in_hand=int(contract.get("players_in_hand") or 0),
        table_players=len(players),
        ante=_number(hand_data.get("ante")),
        stack_bb=stack_bb,
        remaining_ms=int(contract.get("remaining_ms") or 0),
    )
    decision = {
        **contract,
        "state_contract_version": contract["contract_version"],
        "state_quality": contract["quality"],
        "decision_subject": (
            "self" if is_hero_decision else "observer"
        ),
        "subject_seat": contract.get("acting_seat"),
        "last_bet": request.last_bet,
        "auto_reasoning": auto_reasoning,
        "auto_reason": auto_reason,
        "llm_confidence_ceiling": _confidence_ceiling(
            street,
            game_mode,
            int(contract.get("players_in_hand") or 0),
            len(players),
            stack_bb,
        ),
        "_hero_user_id": actor_user_id,
        "_hand": hand_data,
    }
    decision["local_fast_available"] = _local_fast_candidate(decision)
    return decision


def live_decision_from_hand(hand: Any) -> Optional[Dict[str, Any]]:
    """Fast live path using the recorder's in-memory HandHistory snapshot."""

    request = getattr(hand, "pending_decision", None)
    if request is None or getattr(hand, "status", None) != "in_progress":
        return None
    contract = decision_state_from_hand(hand, request, source="live")
    if contract is None:
        return None
    if contract["remaining_ms"] <= 0:
        return None
    actor = hand.players.get(contract.get("acting_seat"))
    actor_user_id = actor.user_id if actor is not None else request.user_id
    stack_bb = _number(contract.get("hero_stack_bb"))
    auto_reasoning, auto_reason = _reasoning_gate(
        legal_actions=list(contract.get("legal_actions") or []),
        hero_cards=list(contract.get("hero_cards") or []),
        street=str(contract.get("street") or ""),
        game_mode=hand.game_mode,
        players_in_hand=int(contract.get("players_in_hand") or 0),
        table_players=int(contract.get("table_players") or 0),
        ante=_number(contract.get("ante")),
        stack_bb=stack_bb,
        remaining_ms=int(contract.get("remaining_ms") or 0),
    )
    decision = {
        **contract,
        "state_contract_version": contract["contract_version"],
        "state_quality": contract["quality"],
        "decision_subject": "self",
        "subject_seat": contract.get("acting_seat"),
        "last_bet": request.last_bet,
        "auto_reasoning": auto_reasoning,
        "auto_reason": auto_reason,
        "llm_confidence_ceiling": _confidence_ceiling(
            str(contract.get("street") or ""),
            hand.game_mode,
            int(contract.get("players_in_hand") or 0),
            int(contract.get("table_players") or 0),
            stack_bb,
        ),
        "_hero_user_id": actor_user_id,
        "_hand": hand.as_dict(),
    }
    decision["local_fast_available"] = _local_fast_candidate(decision)
    return decision


def public_decision(decision: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if decision is None:
        return None
    public = {
        key: value for key, value in decision.items() if not key.startswith("_")
    }
    public["players"] = [
        {key: value for key, value in player.items() if key != "user_id"}
        for player in public.get("players") or []
        if isinstance(player, dict)
    ]
    return public


def reasoning_context(
    connection: sqlite3.Connection,
    sequence: int,
    decision: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    decision = decision or live_decision(connection, sequence=sequence)
    if decision is None:
        return None
    hand = decision["_hand"]
    hero_user_id = decision["_hero_user_id"]
    players = hand.get("players") or []
    actions = hand.get("actions") or []
    positions = {
        player.get("seat"): player.get("position")
        for player in players
        if player.get("seat") is not None
    }
    participant_ids = [
        user_id
        for player in players
        if (user_id := _text(player.get("user_id")))
    ]
    participant_labels = {
        user_id: (
            "acting_player"
            if user_id == hero_user_id
            else f"seat_{player.get('seat')}"
        )
        for player in players
        if (user_id := _text(player.get("user_id")))
    }
    squid_state = None
    squid_award_model = None
    if str(decision.get("game_mode") or "") == "squid":
        raw_squid_state = current_squid_state(
            connection,
            str(decision.get("hand_id") or ""),
            participant_ids,
        )
        for index, (user_id, _count) in enumerate(
            raw_squid_state.counts, 1
        ):
            participant_labels.setdefault(user_id, f"participant_{index}")
        raw_award_model = estimate_award_probabilities(
            connection,
            raw_squid_state.participants,
            dict(raw_squid_state.counts),
        )
        squid_award_model = {
            **raw_award_model,
            "probabilities": {
                participant_labels.get(user_id, "participant_unknown"): value
                for user_id, value in (
                    raw_award_model.get("probabilities") or {}
                ).items()
            },
            "players": {
                participant_labels.get(user_id, "participant_unknown"): value
                for user_id, value in (
                    raw_award_model.get("players") or {}
                ).items()
            },
        }
        squid_state = anonymize_squid_state(
            raw_squid_state,
            participant_labels,
        )
    folded = {
        action.get("seat")
        for action in actions
        if _normalize_action(action.get("action")) == "fold"
    }
    profiles = []
    for player in players:
        user_id = _text(player.get("user_id"))
        seat = player.get("seat")
        if not user_id or user_id == hero_user_id or seat in folded:
            continue
        metrics = opponent_metrics(
            connection,
            user_id,
            str(decision.get("game_mode") or ""),
            position=player.get("position"),
            effective_stack_bb=_number(decision.get("hero_stack_bb")),
            limit=12,
        )
        for metric in metrics:
            metric["evidence_id"] = (
                f"seat_{seat}:metric:{metric.get('metric')}"
            )
            metric["deviation_pp"] = round(
                float(metric.get("mean_pct") or 0)
                - float(metric.get("population_mean_pct") or 0),
                1,
            )
        preflop_line = _opponent_preflop_line(actions, seat)
        range_model = None
        try:
            range_profile = preflop_range_profile(
                connection,
                user_id,
                position=str(player.get("position") or "ALL"),
                line=preflop_line,
                mode=str(decision.get("game_mode") or "") or None,
            )
            range_model = {
                "line": range_profile["line"],
                "estimated_range_pct": range_profile["estimated_range_pct"],
                "confidence": range_profile["confidence"],
                "action_opportunities": range_profile["evidence"][
                    "action_opportunities"
                ],
                "weights": {
                    item["hand"]: item["weight_pct"]
                    for item in range_profile["matrix"]
                },
            }
        except (LookupError, ValueError, sqlite3.Error):
            pass
        profiles.append(
            {
                "player": f"seat_{seat}",
                "position": player.get("position"),
                "observations": metrics,
                "preflop_range": range_model,
            }
        )
    sanitized_actions = [
        {
            "street": action.get("street"),
            "seat": action.get("seat"),
            "position": positions.get(action.get("seat")),
            "action": _history_action(action.get("action")),
            "amount": _number(action.get("amount")),
            "amount_to": _number(action.get("amount_to")),
        }
        for action in actions[-30:]
    ]
    context_decision = public_decision(decision)
    if context_decision is not None and not any(
        profile["observations"] for profile in profiles
    ):
        context_decision["llm_confidence_ceiling"] = "low"
    return {
        "decision": context_decision,
        "action_history": sanitized_actions,
        "active_opponent_profiles": profiles,
        "squid_round": squid_state.as_dict() if squid_state else None,
        "squid_award_model": squid_award_model,
        "limitations": [
            "对手底牌不可见，范围只能根据行动与小样本统计推断",
            "当前没有匹配 8/9 人、ante、深码多人池的已验证 GTO 解集",
            *(
                [
                    (
                        "鱿鱼价值尚未由历史结算校准，只能显示鱿鱼单位 EV"
                        if squid_state.squid_value is None
                        else "鱿鱼价值来自历史结算校准，仍需持续检查规则漂移"
                    )
                ]
                if squid_state
                else []
            ),
        ],
    }


def enrich_reasoning_context(
    context: Dict[str, Any], include_equity: bool = True
) -> Dict[str, Any]:
    enriched = dict(context)
    decision = dict(context.get("decision") or {})
    enriched["decision"] = decision
    hole_cards = list(decision.get("hero_cards") or [])
    board = list(decision.get("board") or [])
    derived: Dict[str, Any] = {}
    try:
        derived["hand"] = hand_features(hole_cards, board)
    except (TypeError, ValueError):
        derived["hand"] = {"category": "unknown", "preflop_class": "unknown"}

    pot = _number(decision.get("pot"))
    call = _number(decision.get("call_score")) or 0.0
    stack = _number(decision.get("hero_stack"))
    if pot is not None and pot >= 0:
        derived["call_pot_ratio_pct"] = (
            round(100 * call / pot, 1) if pot > 0 else None
        )
        derived["required_equity_to_call_pct"] = (
            round(100 * call / (pot + call), 1) if call > 0 else 0.0
        )
        derived["spr_before_action"] = (
            round(stack / pot, 2) if stack is not None and pot > 0 else None
        )
    if (
        include_equity
        and len(hole_cards) == 2
        and len(board) in {0, 3, 4, 5}
        and int(decision.get("players_in_hand") or 0) == 2
    ):
        try:
            equity = equity_vs_random(hole_cards, board)
            derived["equity_vs_one_random_hand_pct"] = round(100 * equity, 1)
            derived["equity_baseline_scope"] = (
                "仅对一个均匀随机手牌，不代表对手行动范围"
            )
            if pot is not None and call > 0:
                derived["call_ev_vs_random_chips"] = round(
                    equity * (pot + call) - call, 2
                )
        except (TypeError, ValueError):
            pass
    enriched["derived_facts"] = derived
    return enriched


def local_fast_analysis(context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    started = time.perf_counter()
    enriched = enrich_reasoning_context(context, include_equity=False)
    decision = enriched["decision"]
    rule = _local_exploit_rule(enriched)
    raise_fraction = 0.5
    if rule is None:
        rule = _local_fast_rule(decision)
        raise_fraction = 0.75
    if rule is None:
        return None
    action, summary, factors = rule
    raise_to = (
        _local_raise_to(decision, raise_fraction) if action == "raise" else None
    )
    return {
        "recommended_action": action,
        "raise_to": raise_to,
        "confidence": "high",
        "summary": summary,
        "factors": factors[:4],
        "risks": ["本地快路径只覆盖高确定性节点"] if len(decision.get("legal_actions") or []) > 1 else [],
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "source": "local-rules-v1",
    }


def local_exploit_fallback(
    context: Dict[str, Any],
    reason: str,
    latency_ms: int,
    reasoning_depth: str = "light",
) -> Dict[str, Any]:
    candidates = [
        (profile, metric)
        for profile in context.get("active_opponent_profiles") or []
        for metric in profile.get("observations") or []
        if int(metric.get("opportunities") or 0) >= 15
        and str(metric.get("confidence") or "") != "very_low"
        and abs(float(metric.get("deviation_pp") or 0)) >= 7
    ]
    candidates.sort(
        key=lambda item: (
            -abs(float(item[1].get("deviation_pp") or 0)),
            -int(item[1].get("opportunities") or 0),
        )
    )
    reads = []
    adjustments = []
    for profile, metric in candidates:
        finding, adjustment = _fallback_exploit_rule(metric)
        evidence_id = str(metric.get("evidence_id") or "")
        if not finding or not adjustment or not evidence_id:
            continue
        reads.append(
            {
                "player": profile.get("player"),
                "finding": finding,
                "evidence": [evidence_id],
            }
        )
        adjustments.append(
            {
                "adjustment": adjustment,
                "evidence": [evidence_id],
            }
        )
        if len(reads) >= 2:
            break
    findings = [*reads, *adjustments]
    return {
        "summary": (
            "GPT‑5.6 未在实时预算内完成；以下仅为本地统计回退。"
        ),
        "opponent_reads": reads,
        "range_adjustments": adjustments,
        "evidence_details": _exploit_evidence_details(context, findings),
        "caveats": [
            reason,
            "本地回退只使用显著后验偏移，不是 LLM 或 GTO 结论",
        ],
        "confidence": "low",
        "latency_ms": latency_ms,
        "source": "local-exploit-fallback-v1",
        "fallback_reason": reason,
        "reasoning_depth": reasoning_depth,
    }


def _fallback_exploit_rule(
    metric: Dict[str, Any],
) -> Tuple[Optional[str], Optional[str]]:
    name = str(metric.get("metric") or "")
    deviation = float(metric.get("deviation_pp") or 0)
    if name == "vpip":
        if deviation > 0:
            return "入池范围相对牌池偏宽", "扩大强价值隔离，减少无权益多街诈唬"
        return "入池范围相对牌池偏紧", "增加后位偷盲，遭遇继续时收紧弱价值"
    if name in {"pfr", "rfi", "three_bet", "four_bet"}:
        if deviation > 0:
            return "翻前主动进攻频率偏高", "增加强牌反击与有阻断牌继续，减少边缘开池"
        return "翻前主动进攻频率偏低", "扩大无人反击时开池，其强行动给予更多信用"
    if name.startswith("fold_to_"):
        street = next(
            (
                label
                for key, label in (
                    ("flop", "翻牌"),
                    ("turn", "转牌"),
                    ("river", "河牌"),
                )
                if key in name
            ),
            "翻后",
        )
        if deviation > 0:
            return f"{street}弃牌频率相对牌池偏高", f"略增{street}小尺度半诈唬"
        return f"{street}弃牌频率相对牌池偏低", f"扩大{street}薄价值，减少纯空气诈唬"
    return None, None


def simplified_range_strategy(context: Dict[str, Any]) -> Dict[str, Any]:
    """Return a compact, deterministic range view for the current spot.

    This is intentionally a readable baseline rather than a solver output. It
    remains useful when the recorder cannot see the acting player's hole cards.
    """
    started = time.perf_counter()
    decision = context.get("decision") or {}
    street = str(decision.get("street") or "preflop")
    legal = [
        action
        for value in decision.get("legal_actions") or []
        if (action := _normalize_action(value))
    ]
    spot = _strategy_spot(context)
    common = {
        "street": street,
        "spot": spot,
        "legal_actions": legal,
        "confidence": "baseline",
        "source": "local-range-baseline-v1",
        "caveat": "简化行动基线，不是 GTO solver 解；未看到底牌时不猜具体持牌。",
    }
    if street == "preflop":
        cells, scenario = _preflop_strategy_cells(decision, context, legal)
        hero_hand = _canonical_preflop_hand(decision.get("hero_cards") or [])
        result = {
            **common,
            "kind": "preflop_matrix",
            "scenario": scenario,
            "summary": _preflop_strategy_summary(scenario, spot),
            "action_mix": _aggregate_strategy_mix(cells),
            "hero_hand": hero_hand,
            "cells": cells,
        }
    else:
        buckets = _postflop_strategy_buckets(decision, legal)
        hero_bucket = _postflop_hero_bucket(decision)
        result = {
            **common,
            "kind": "postflop_buckets",
            "summary": "按当前牌面把全范围压缩为五类牌力；每行频率表示该类牌的建议动作混合。",
            "hero_bucket": hero_bucket,
            "buckets": buckets,
        }
    result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return result


def _strategy_spot(context: Dict[str, Any]) -> Dict[str, Any]:
    decision = context.get("decision") or {}
    street = str(decision.get("street") or "preflop")
    actions = [
        action
        for action in context.get("action_history") or []
        if action.get("street") == street and action.get("action")
    ]
    visible_line = actions[-5:]
    line_parts = [
        f"{action.get('position') or '未知位置'} {_action_text(action.get('action'))}"
        for action in visible_line
    ]
    hero_position = str(decision.get("hero_position") or "未知位置")
    if line_parts:
        action_line = " → ".join([*line_parts, f"{hero_position} 决策"])
    else:
        action_line = f"{hero_position} 首个决策"
    aggressive = next(
        (
            action
            for action in reversed(actions)
            if action.get("action") in {"bet", "raise", "all_in"}
        ),
        None,
    )
    call = _number(decision.get("call_score")) or 0.0
    pot = _number(decision.get("pot"))
    required = (
        round(100 * call / (pot + call), 1)
        if call > 0 and pot is not None and pot >= 0
        else 0.0
    )
    return {
        "hero_position": hero_position,
        "facing_position": (aggressive or {}).get("position"),
        "facing_action": (aggressive or {}).get("action"),
        "action_line": action_line,
        "players_in_hand": int(decision.get("players_in_hand") or 0),
        "call_score": call,
        "required_equity_pct": required,
    }


def _preflop_strategy_cells(
    decision: Dict[str, Any],
    context: Dict[str, Any],
    legal: List[str],
) -> Tuple[List[Dict[str, Any]], str]:
    actions = [
        action
        for action in context.get("action_history") or []
        if action.get("street") == "preflop"
    ]
    raises = sum(
        action.get("action") in {"raise", "all_in"} for action in actions
    )
    call = _number(decision.get("call_score")) or 0.0
    if call <= 0 and "check" in legal:
        scenario = "free_option"
    elif call > 0 and raises:
        scenario = "facing_raise" if raises == 1 else "facing_reraise"
    elif call > 0:
        scenario = "facing_limp"
    else:
        scenario = "unopened"

    position = str(decision.get("hero_position") or "").upper()
    open_fraction = POSITION_OPEN_FRACTIONS.get(position, 0.25)
    if scenario == "facing_raise":
        continue_fraction = max(0.12, min(0.22, open_fraction * 0.55))
        aggressive_fraction = max(0.05, continue_fraction * 0.42)
    elif scenario == "facing_reraise":
        continue_fraction = 0.10
        aggressive_fraction = 0.035
    elif scenario == "facing_limp":
        continue_fraction = max(0.38, open_fraction)
        aggressive_fraction = open_fraction
    else:
        continue_fraction = open_fraction
        aggressive_fraction = open_fraction

    classes = _preflop_strategy_classes()
    return [
        {
            "hand": item["hand"],
            "combo_count": item["combo_count"],
            "primary_action": max(
                (
                    mix := _preflop_mix(
                        item["percentile"],
                        scenario,
                        continue_fraction,
                        aggressive_fraction,
                        legal,
                    )
                ),
                key=mix.get,
            ),
            "frequencies": mix,
        }
        for item in classes
    ], scenario


def _preflop_mix(
    percentile: float,
    scenario: str,
    continue_fraction: float,
    aggressive_fraction: float,
    legal: List[str],
) -> Dict[str, int]:
    aggressive, passive, decline = _strategy_role_actions(legal)
    if scenario in {"facing_raise", "facing_reraise"}:
        if percentile <= aggressive_fraction * 0.72:
            raw = [(aggressive, 75), (passive, 25)]
        elif percentile <= aggressive_fraction:
            raw = [(aggressive, 45), (passive, 55)]
        elif percentile <= continue_fraction * 0.78:
            raw = [(passive, 90), (decline, 10)]
        elif percentile <= continue_fraction:
            raw = [(passive, 55), (decline, 45)]
        else:
            raw = [(decline, 100)]
    elif scenario == "facing_limp":
        if percentile <= aggressive_fraction * 0.82:
            raw = [(aggressive, 90), (passive, 10)]
        elif percentile <= aggressive_fraction * 1.12:
            raw = [(aggressive, 55), (passive, 45)]
        elif percentile <= continue_fraction:
            raw = [(passive, 85), (decline, 15)]
        else:
            raw = [(decline, 100)]
    else:
        boundary = continue_fraction * (1.12 if scenario == "free_option" else 1.08)
        if percentile <= continue_fraction * 0.82:
            raw = [(aggressive, 100)]
        elif percentile <= boundary:
            raw = [(aggressive, 50), (decline, 50)]
        else:
            raw = [(decline, 100)]
    return _combine_strategy_mix(raw)


def _preflop_strategy_classes() -> List[Dict[str, Any]]:
    classes = []
    for row, first in enumerate(STRATEGY_RANKS):
        for column, second in enumerate(STRATEGY_RANKS):
            if row == column:
                hand = f"{first}{second}"
                combo_count = 6
            elif row < column:
                hand = f"{first}{second}s"
                combo_count = 4
            else:
                hand = f"{second}{first}o"
                combo_count = 12
            classes.append(
                {
                    "hand": hand,
                    "combo_count": combo_count,
                    "score": _preflop_strategy_score(hand),
                }
            )
    cumulative = 0
    percentiles = {}
    for item in sorted(
        classes, key=lambda value: (-value["score"], value["hand"])
    ):
        percentiles[item["hand"]] = (cumulative + item["combo_count"] / 2) / 1326
        cumulative += item["combo_count"]
    return [
        {
            "hand": item["hand"],
            "combo_count": item["combo_count"],
            "percentile": percentiles[item["hand"]],
        }
        for item in classes
    ]


def _preflop_strategy_score(hand: str) -> float:
    first = STRATEGY_RANKS.index(hand[0])
    second = STRATEGY_RANKS.index(hand[1])
    if len(hand) == 2:
        return 1.12 - 0.045 * first
    high, low = min(first, second), max(first, second)
    gap = max(0, low - high - 1)
    score = 0.78 - 0.043 * high - 0.025 * low - 0.026 * gap
    if hand.endswith("s"):
        score += 0.075
    if gap == 0:
        score += 0.045
    if high == 0:
        score += 0.055
    if high <= 3 and low <= 4:
        score += 0.055
    return score


def _aggregate_strategy_mix(
    cells: List[Dict[str, Any]],
) -> Dict[str, int]:
    totals: Dict[str, float] = {}
    combos = sum(int(cell["combo_count"]) for cell in cells) or 1
    for cell in cells:
        for action, frequency in cell["frequencies"].items():
            totals[action] = totals.get(action, 0.0) + (
                int(cell["combo_count"]) * frequency / combos
            )
    rounded = {action: round(value) for action, value in totals.items()}
    if rounded:
        largest = max(rounded, key=rounded.get)
        rounded[largest] += 100 - sum(rounded.values())
    return rounded


def _postflop_strategy_buckets(
    decision: Dict[str, Any], legal: List[str]
) -> List[Dict[str, Any]]:
    aggressive, passive, decline = _strategy_role_actions(legal)
    call = _number(decision.get("call_score")) or 0.0
    pot = _number(decision.get("pot"))
    required = call / (pot + call) if call > 0 and pot is not None else 0.0
    facing_bet = call > 0 and "call" in legal
    if not facing_bet:
        mixes = [
            [(aggressive, 80), (passive, 20)],
            [(aggressive, 35), (passive, 65)],
            [(aggressive, 12), (passive, 88)],
            [(aggressive, 38), (passive, 62)],
            [(aggressive, 10), (passive, 90)],
        ]
    else:
        medium_call = 75 if required <= 0.20 else 50 if required <= 0.33 else 25
        draw_call = 72 if required <= 0.25 else 45 if required <= 0.38 else 22
        mixes = [
            [(aggressive, 72), (passive, 28)],
            [(aggressive, 25), (passive, 70), (decline, 5)],
            [(passive, medium_call), (decline, 100 - medium_call)],
            [(aggressive, 18), (passive, draw_call), (decline, 82 - draw_call)],
            [(aggressive, 8), (decline, 92)],
        ]
    definitions = [
        ("strong_value", "强价值", "两对、三条及以上"),
        ("top_pair", "顶对 / 超对", "主要跟注，少量加注"),
        ("showdown", "中弱摊牌价值", "中对、底对与弱踢脚"),
        ("strong_draw", "强听牌", "同花听牌、开放顺听与组合听牌"),
        ("air", "空气 / 弱听牌", "高牌、后门牌与低权益组合"),
    ]
    return [
        {
            "key": key,
            "label": label,
            "description": description,
            "primary_action": max(
                (frequencies := _combine_strategy_mix(mix)),
                key=frequencies.get,
            ),
            "frequencies": frequencies,
        }
        for (key, label, description), mix in zip(definitions, mixes)
    ]


def _postflop_hero_bucket(decision: Dict[str, Any]) -> Optional[str]:
    hole_cards = list(decision.get("hero_cards") or [])
    board = list(decision.get("board") or [])
    if len(hole_cards) != 2:
        return None
    try:
        features = hand_features(hole_cards, board)
    except (TypeError, ValueError):
        return None
    rank = int(features.get("category_rank") or 0)
    if rank >= 2:
        return "strong_value"
    if (
        features.get("flush_draw")
        or features.get("open_ended_draw")
    ) and rank <= 1:
        return "strong_draw"
    if rank == 1:
        return (
            "top_pair"
            if _is_top_pair_or_overpair(hole_cards, board)
            else "showdown"
        )
    if features.get("gutshot"):
        return "strong_draw"
    return "air"


def _is_top_pair_or_overpair(
    hole_cards: List[str], board: List[str]
) -> bool:
    if not board:
        return False
    hole_ranks = [STRATEGY_RANKS.index(card[:-1].upper()) for card in hole_cards]
    board_ranks = [STRATEGY_RANKS.index(card[:-1].upper()) for card in board]
    top_board = min(board_ranks)
    if top_board in hole_ranks:
        return True
    return hole_ranks[0] == hole_ranks[1] and hole_ranks[0] < top_board


def _strategy_role_actions(
    legal: List[str],
) -> Tuple[str, str, str]:
    aggressive = (
        "raise"
        if "raise" in legal
        else "all_in"
        if "all_in" in legal
        else "call"
        if "call" in legal
        else "check"
    )
    passive = (
        "call"
        if "call" in legal
        else "check"
        if "check" in legal
        else aggressive
    )
    decline = (
        "fold"
        if "fold" in legal
        else "check"
        if "check" in legal
        else passive
    )
    return aggressive, passive, decline


def _combine_strategy_mix(
    items: List[Tuple[str, int]],
) -> Dict[str, int]:
    result: Dict[str, int] = {}
    for action, frequency in items:
        result[action] = result.get(action, 0) + frequency
    return {action: value for action, value in result.items() if value > 0}


def _canonical_preflop_hand(cards: List[str]) -> Optional[str]:
    if len(cards) != 2:
        return None
    first, second = cards
    try:
        first_rank = STRATEGY_RANKS.index(first[:-1].upper())
        second_rank = STRATEGY_RANKS.index(second[:-1].upper())
    except (ValueError, AttributeError):
        return None
    if first_rank == second_rank:
        return STRATEGY_RANKS[first_rank] * 2
    high, low = sorted((first_rank, second_rank))
    suited = first[-1].lower() == second[-1].lower()
    return (
        f"{STRATEGY_RANKS[high]}{STRATEGY_RANKS[low]}"
        f"{'s' if suited else 'o'}"
    )


def _preflop_strategy_summary(
    scenario: str, spot: Dict[str, Any]
) -> str:
    labels = {
        "unopened": "前面无人主动入池：按当前位置展示开池与放弃范围。",
        "free_option": "当前可以免费过牌：强牌主动加注，其余范围保留过牌。",
        "facing_limp": "面对跟入：强牌做隔离加注，中段范围低成本继续。",
        "facing_raise": "面对一次加注：范围压缩为再加注、跟注与弃牌三档。",
        "facing_reraise": "面对再加注：只保留较强继续范围，边缘牌以弃牌为主。",
    }
    return labels.get(scenario, str(spot.get("action_line") or "当前翻前节点"))


def _action_text(value: Any) -> str:
    return {
        "fold": "弃牌",
        "check": "过牌",
        "call": "跟注",
        "bet": "下注",
        "raise": "加注",
        "all_in": "全下",
    }.get(str(value), str(value or "行动"))


def _remote_opponent_profiles(
    context: Dict[str, Any],
    evidence_limit: int = 6,
) -> List[Dict[str, Any]]:
    confidence_rank = {
        "very_low": 0,
        "low": 1,
        "medium": 2,
        "high": 3,
    }
    profiles = context.get("active_opponent_profiles") or []
    candidates = [
        (profile, metric)
        for profile in profiles
        for metric in profile.get("observations") or []
    ]
    meaningful = [
        item
        for item in candidates
        if (
            abs(float(item[1].get("deviation_pp") or 0)) >= 3
            or confidence_rank.get(str(item[1].get("confidence")), 0) >= 2
        )
    ]
    selected = sorted(
        meaningful or candidates,
        key=lambda item: (
            -confidence_rank.get(str(item[1].get("confidence")), 0),
            -abs(float(item[1].get("deviation_pp") or 0)),
            -int(item[1].get("opportunities") or 0),
        ),
    )[:evidence_limit]
    selected_by_player: Dict[str, List[Dict[str, Any]]] = {}
    for profile, metric in selected:
        player = str(profile.get("player") or "")
        selected_by_player.setdefault(player, []).append(
            {
                key: metric.get(key)
                for key in (
                    "evidence_id",
                    "metric",
                    "mean_pct",
                    "population_mean_pct",
                    "deviation_pp",
                    "opportunities",
                    "context_opportunities",
                    "confidence",
                )
            }
        )
    compact = []
    for profile in profiles:
        player = str(profile.get("player") or "")
        observations = selected_by_player.get(player)
        if not observations:
            continue
        range_model = profile.get("preflop_range") or {}
        compact.append(
            {
                "player": player,
                "position": profile.get("position"),
                "observations": observations,
                "preflop_range": (
                    {
                        key: range_model.get(key)
                        for key in (
                            "line",
                            "estimated_range_pct",
                            "confidence",
                            "action_opportunities",
                        )
                    }
                    if range_model
                    else None
                ),
            }
        )
    return compact


def build_prompt(
    context: Dict[str, Any],
    reasoning_depth: str = "light",
) -> str:
    deep = reasoning_depth == "deep"
    enriched = enrich_reasoning_context(context)
    legal_actions = enriched["decision"].get("legal_actions") or []
    min_raise = enriched["decision"].get("min_raise_to")
    max_raise = enriched["decision"].get("max_raise_to")
    remote_context = dict(enriched)
    remote_context["decision"] = {
        key: value
        for key, value in enriched["decision"].items()
        if key
        not in {
            "decision_id",
            "sequence",
            "hand_id",
            "captured_at",
            "expires_at",
        }
    }
    remote_context["action_history"] = (
        remote_context.get("action_history") or []
    )[-30 if deep else -16:]
    remote_context["active_opponent_profiles"] = (
        _remote_opponent_profiles(
            enriched,
            evidence_limit=16 if deep else 6,
        )
    )
    detail_instruction = (
        "这是重推理：交叉检查牌型、赔率、SPR、行动线、对手范围和样本偏移；"
        "summary 最多两句，factors 最多8条，risks 最多4条，"
        "opponent_reads 最多5条，range_adjustments 最多6条。"
        if deep
        else "这是轻推理：回答必须极短并优先保证 JSON 闭合；"
        "两个剥削数组各最多2条，每条结论最多35字。"
    )
    return (
        "请复核下面这个实时行动点。recommended_action 必须严格取自 legal_actions；"
        "只有选择 raise 时才填写 raise_to，并且必须处于给定最小/最大范围。"
        "confidence 不得高于 llm_confidence_ceiling；信息不足时必须为 low。"
        "不要给输入中没有的精确胜率或 EV。对手判断和剥削调整必须引用输入里真实存在的 evidence_id。\n\n"
        f"合法动作：{json.dumps(legal_actions, ensure_ascii=False)}\n"
        f"加注到范围：{min_raise} .. {max_raise}\n"
        f"{detail_instruction}"
        "输出 JSON schema："
        '{"recommended_action":"fold|check|call|raise|all_in",'
        '"raise_to":null,"confidence":"low|medium|high",'
        '"summary":"一句可执行结论","factors":["最多4条简短理由"],'
        '"risks":["最多2条关键风险"],'
        '"opponent_reads":[{"player":"seat_*","finding":"对手判断","evidence":["evidence_id"]}],'
        '"range_adjustments":[{"adjustment":"剥削调整","evidence":["evidence_id"]}]}\n\n'
        "当前事实：\n"
        + json.dumps(remote_context, ensure_ascii=False, separators=(",", ":"))
    )


def build_exploit_prompt(
    context: Dict[str, Any],
    reasoning_depth: str = "light",
) -> str:
    deep = reasoning_depth == "deep"
    enriched = enrich_reasoning_context(context, include_equity=False)
    strategy = simplified_range_strategy(context)
    compact_strategy = {
        key: strategy.get(key)
        for key in (
            "kind",
            "street",
            "summary",
            "action_mix",
            "spot",
        )
        if strategy.get(key) is not None
    }
    if strategy.get("buckets"):
        compact_strategy["buckets"] = [
            {
                "key": bucket.get("key"),
                "frequencies": bucket.get("frequencies"),
            }
            for bucket in strategy["buckets"]
        ]
    decision = enriched.get("decision") or {}
    remote_context = {
        "decision": {
            key: decision.get(key)
            for key in (
                "street",
                "hero_position",
                "decision_subject",
                "subject_seat",
                "legal_actions",
                "call_score",
                "min_raise_to",
                "max_raise_to",
                "hero_stack",
                "hero_stack_bb",
                "pot",
                "small_blind",
                "big_blind",
                "ante",
                "board",
                "players_in_hand",
                "table_players",
                "game_mode",
                "llm_confidence_ceiling",
            )
        },
        "action_history": (
            enriched.get("action_history") or []
        )[-24 if deep else -10:],
        "active_opponent_profiles": _remote_opponent_profiles(
            enriched,
            evidence_limit=16 if deep else 6,
        ),
        "squid_round": enriched.get("squid_round") or {},
        "local_range_baseline": compact_strategy,
        "limitations": enriched.get("limitations") or [],
    }
    return (
        "请对当前行动点做范围层面的对手剥削复核。当前没有可靠底牌，"
        "不得输出某一手牌的 fold/call/raise 结论；只说明相对本地范围基线应如何调整。"
        "每个 opponent_read 和 range_adjustment 必须引用输入中存在的 evidence_id。"
        "没有足够证据时保留空数组，并明确样本限制。"
        + (
            "这是重推理：交叉检查最多16条证据和更长行动线，最多选5个结论；"
            "s 最多120字，r/a 各最多60字。"
            if deep
            else "这是轻推理：最多选2个结论；s 最多35字，r/a 各最多25字。"
        )
        + "若无证据则 i=[]。只输出以下短键 JSON："
        '{"s":"总结","i":[{"p":"seat_*","r":"判断",'
        '"a":"范围调整","e":["evidence_id"]}],"c":"low|medium|high"}\n\n'
        "当前事实：\n"
        + json.dumps(remote_context, ensure_ascii=False, separators=(",", ":"))
    )


def build_profile_prompt(context: Dict[str, Any]) -> str:
    return (
        "复核下面的选手画像。所有数字已经由本地算法计算，不要自行补数字。"
        "只保留有样本证据的漏洞；如果范围依赖先验超过 80%，必须在 caveats 中说明。"
        "每个 vulnerability 必须引用至少一个输入中存在的 metric:* 或 case_* evidence_id；"
        "统计与案例冲突时以假设表述，不得写成已证实。"
        "输出 JSON schema："
        '{"style_summary":"最多两句","vulnerabilities":['
        '{"finding":"漏洞","evidence":["metric:*或case_*"],'
        '"confidence":"low|medium|high"}],'
        '"counter_strategy":["最多4条"],"caveats":["最多3条"],'
        '"confidence":"low|medium|high"}\n\n'
        "本地画像事实：\n"
        + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    )


class LLMReasoner:
    def __init__(
        self,
        api_key: str,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout_seconds: float = 12.0,
        max_completion_tokens: int = 800,
        reasoning_effort: str = "low",
        model: Optional[str] = None,
        profile_timeout_seconds: Optional[float] = None,
        deep_timeout_seconds: float = 20.0,
        deep_max_completion_tokens: int = 1600,
        deep_reasoning_effort: str = "high",
    ):
        self.api_key = api_key
        self.endpoint = endpoint
        self.model = (
            str(model or "").strip()
            or _model_from_endpoint(endpoint)
            or DEFAULT_MODEL
        )
        self.timeout_seconds = min(20.0, max(0.5, timeout_seconds))
        self.deep_timeout_seconds = min(
            20.0,
            max(self.timeout_seconds, deep_timeout_seconds),
        )
        self.profile_timeout_seconds = max(
            self.timeout_seconds,
            profile_timeout_seconds
            if profile_timeout_seconds is not None
            else 15.0,
        )
        self.max_completion_tokens = max(256, min(max_completion_tokens, 6000))
        self.deep_max_completion_tokens = max(
            self.max_completion_tokens,
            min(deep_max_completion_tokens, 6000),
        )
        self.reasoning_effort = (
            reasoning_effort
            if reasoning_effort in {"low", "medium", "high"}
            else "low"
        )
        self.deep_reasoning_effort = (
            deep_reasoning_effort
            if deep_reasoning_effort in {"medium", "high"}
            else "high"
        )

    @classmethod
    def from_env(cls) -> "LLMReasoner":
        key = os.getenv("WPK_LLM_API_KEY") or os.getenv("MODEL_GATEWAY_KEY") or ""
        configured_model = os.getenv("WPK_LLM_MODEL", "").strip()
        configured_endpoint = os.getenv("WPK_LLM_ENDPOINT", "").strip()
        model = (
            configured_model
            or _model_from_endpoint(configured_endpoint)
            or DEFAULT_MODEL
        )
        endpoint = configured_endpoint or _endpoint_for_model(model)
        timeout = _env_float("WPK_LLM_TIMEOUT_SECONDS", 12.0)
        profile_timeout = _env_float("WPK_LLM_PROFILE_TIMEOUT_SECONDS", 20.0)
        tokens = _env_int("WPK_LLM_MAX_COMPLETION_TOKENS", 800)
        effort = os.getenv("WPK_LLM_REASONING_EFFORT", "low").strip().lower()
        deep_timeout = _env_float("WPK_LLM_DEEP_TIMEOUT_SECONDS", 20.0)
        deep_tokens = _env_int("WPK_LLM_DEEP_MAX_COMPLETION_TOKENS", 1600)
        deep_effort = os.getenv(
            "WPK_LLM_DEEP_REASONING_EFFORT", "high"
        ).strip().lower()
        return cls(
            key,
            endpoint,
            timeout,
            tokens,
            effort,
            model,
            profile_timeout,
            deep_timeout,
            deep_tokens,
            deep_effort,
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def status(self) -> Dict[str, Any]:
        return {
            "configured": self.configured,
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "deep_timeout_seconds": self.deep_timeout_seconds,
            "profile_timeout_seconds": self.profile_timeout_seconds,
            "max_completion_tokens": self.max_completion_tokens,
            "deep_max_completion_tokens": self.deep_max_completion_tokens,
            "reasoning_effort": self.reasoning_effort,
            "deep_reasoning_effort": self.deep_reasoning_effort,
        }

    def analyze(
        self,
        context: Dict[str, Any],
        timeout_seconds: Optional[float] = None,
        reasoning_depth: str = "light",
    ) -> Dict[str, Any]:
        if not self.configured:
            raise ReasoningError(
                "LLM 未配置：请设置 WPK_LLM_API_KEY 或 MODEL_GATEWAY_KEY"
            )
        deep = reasoning_depth == "deep"
        parsed, latency_ms = self._complete_json(
            SYSTEM_PROMPT,
            build_prompt(context, reasoning_depth),
            timeout_seconds,
            "wpk-reasoning",
            timeout_cap=(
                self.deep_timeout_seconds if deep else self.timeout_seconds
            ),
            max_completion_tokens=(
                self.deep_max_completion_tokens if deep else 700
            ),
            json_mode=True,
            reasoning_effort=(
                self.deep_reasoning_effort if deep else self.reasoning_effort
            ),
        )
        return _validate_result(
            parsed,
            context,
            latency_ms,
            self.model,
            reasoning_depth=reasoning_depth,
        )

    def analyze_exploit(
        self,
        context: Dict[str, Any],
        timeout_seconds: Optional[float] = None,
        reasoning_depth: str = "light",
    ) -> Dict[str, Any]:
        if not self.configured:
            raise ReasoningError(
                "LLM 未配置：请设置 WPK_LLM_API_KEY 或 MODEL_GATEWAY_KEY"
            )
        deep = reasoning_depth == "deep"
        parsed, latency_ms = self._complete_json(
            EXPLOIT_SYSTEM_PROMPT,
            build_exploit_prompt(context, reasoning_depth),
            timeout_seconds,
            "wpk-exploit",
            timeout_cap=(
                self.deep_timeout_seconds if deep else self.timeout_seconds
            ),
            max_completion_tokens=(
                self.deep_max_completion_tokens if deep else 600
            ),
            json_mode=True,
            reasoning_effort=(
                self.deep_reasoning_effort if deep else self.reasoning_effort
            ),
        )
        return _validate_exploit_result(
            parsed,
            context,
            latency_ms,
            self.model,
            reasoning_depth=reasoning_depth,
        )

    def analyze_profile(
        self, context: Dict[str, Any], timeout_seconds: Optional[float] = None
    ) -> Dict[str, Any]:
        if not self.configured:
            raise ReasoningError(
                "LLM 未配置：请设置 WPK_LLM_API_KEY 或 MODEL_GATEWAY_KEY"
            )
        parsed, latency_ms = self._complete_json(
            PROFILE_SYSTEM_PROMPT,
            build_profile_prompt(context),
            timeout_seconds,
            "wpk-profile",
            timeout_cap=self.profile_timeout_seconds,
        )
        return _validate_profile_result(
            parsed, context, latency_ms, self.model
        )

    def _complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: Optional[float],
        log_prefix: str,
        timeout_cap: Optional[float] = None,
        max_completion_tokens: Optional[int] = None,
        json_mode: bool = False,
        reasoning_effort: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], int]:
        request_payload = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_completion_tokens": max(
                256,
                min(
                    max_completion_tokens or self.max_completion_tokens,
                    6000,
                ),
            ),
            "reasoning_effort": (
                reasoning_effort or self.reasoning_effort
            ),
        }
        if json_mode:
            request_payload["response_format"] = {"type": "json_object"}
        payload = json.dumps(
            request_payload,
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "api-key": self.api_key,
                "X-TT-LOGID": f"{log_prefix}-{time.time_ns()}",
            },
            method="POST",
        )
        cap = timeout_cap or self.timeout_seconds
        timeout = min(cap, timeout_seconds or cap)
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as error:
            raise ReasoningError(f"LLM 网关返回 HTTP {error.code}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ReasoningError("LLM 网关连接失败或超时") from error
        latency_ms = round((time.perf_counter() - started) * 1000)
        try:
            envelope = json.loads(body)
            content = envelope["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as error:
            raise ReasoningError("LLM 网关响应格式无法识别") from error
        parsed = _parse_model_json(str(content))
        return parsed, latency_ms


def _decision_row(
    connection: sqlite3.Connection, sequence: Optional[int]
) -> Optional[Tuple[int, str, Optional[str], str]]:
    if sequence is None:
        return connection.execute(
            """
            SELECT sequence, timestamp, hand_id, payload_json
            FROM raw_events
            WHERE event_name = 'userOptNotify'
            ORDER BY sequence DESC LIMIT 1
            """
        ).fetchone()
    return connection.execute(
        """
        SELECT sequence, timestamp, hand_id, payload_json
        FROM raw_events
        WHERE event_name = 'userOptNotify' AND sequence = ?
        """,
        (sequence,),
    ).fetchone()


def _hand_row(
    connection: sqlite3.Connection, hand_id: Optional[str]
) -> Optional[Tuple[str, str, str]]:
    if hand_id:
        row = connection.execute(
            "SELECT hand_id, game_mode, hand_json FROM hands WHERE hand_id = ?",
            (hand_id,),
        ).fetchone()
        if row:
            return row
    return connection.execute(
        """
        SELECT hand_id, game_mode, hand_json FROM hands
        WHERE quality_status = 'live'
        ORDER BY played_at DESC, hand_number DESC LIMIT 1
        """
    ).fetchone()


def _has_newer_invalidation(
    connection: sqlite3.Connection, sequence: int, hand_id: str
) -> bool:
    row = connection.execute(
        """
        SELECT 1 FROM raw_events
        WHERE sequence > ? AND hand_id = ?
          AND event_name IN (
            'userOptNotify', 'actionNotify', 'playResultNotify',
            'roundChangeNotify', 'cleanNotify', 'cleanGameNotify'
          )
        LIMIT 1
        """,
        (sequence, hand_id),
    ).fetchone()
    return row is not None


def _opponent_preflop_line(
    actions: List[Dict[str, Any]], seat: Any
) -> str:
    raises = 0
    selected = "vpip"
    for action in actions:
        if str(action.get("street") or "") != "preflop":
            continue
        kind = _history_action(action.get("action"))
        if action.get("seat") == seat:
            if kind in {"raise", "all_in"}:
                selected = "three_bet" if raises else "open_raise"
            elif kind == "call":
                selected = "cold_call" if raises else "limp"
        if kind in {"raise", "all_in"}:
            raises += 1
    return selected


def _player_metrics(
    connection: sqlite3.Connection, user_id: str, game_mode: str
) -> List[Dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT o.metric, SUM(o.success), COUNT(*)
        FROM opportunities o
        JOIN hands h ON h.hand_id = o.hand_id
        WHERE o.user_id = ? AND h.excluded_from_stats = 0
          AND (? = '' OR o.game_mode = ?)
        GROUP BY o.metric
        ORDER BY COUNT(*) DESC
        """,
        (user_id, game_mode, game_mode),
    ).fetchall()
    population_rows = connection.execute(
        """
        SELECT o.metric, SUM(o.success), COUNT(*)
        FROM opportunities o
        JOIN hands h ON h.hand_id = o.hand_id
        WHERE h.excluded_from_stats = 0
          AND (? = '' OR o.game_mode = ?)
        GROUP BY o.metric
        """,
        (game_mode, game_mode),
    ).fetchall()
    population = {
        str(metric): (
            round(100 * successes / trials, 1)
            if trials
            else None
        )
        for metric, successes, trials in population_rows
    }
    return [
        {
            "metric": metric,
            "evidence_id": f"metric:{metric}",
            "successes": int(successes),
            "opportunities": int(trials),
            "observed_pct": round(100 * successes / trials, 1),
            "population_pct": population.get(str(metric)),
            "deviation_pp": (
                round(
                    100 * successes / trials
                    - float(population[str(metric)]),
                    1,
                )
                if population.get(str(metric)) is not None
                else None
            ),
        }
        for metric, successes, trials in rows
        if trials >= 2
    ][:10]


def _reasoning_gate(
    *,
    legal_actions: List[str],
    hero_cards: List[str],
    street: str,
    game_mode: str,
    players_in_hand: int,
    table_players: int,
    ante: Optional[float],
    stack_bb: Optional[float],
    remaining_ms: int,
) -> Tuple[bool, str]:
    if len(legal_actions) <= 1:
        return False, "只有一个合法动作，无需调用 LLM"
    if len(hero_cards) != 2:
        return False, "英雄底牌不完整，禁止远程推理"
    if remaining_ms and remaining_ms < 2500:
        return False, "剩余时间不足，使用本地快速路径"
    reasons = []
    if street != "preflop":
        reasons.append(f"{street} 翻后节点")
    if players_in_hand != 2:
        reasons.append(f"{players_in_hand} 人底池")
    if table_players != 6:
        reasons.append(f"{table_players} 人桌")
    if ante and ante > 0:
        reasons.append("带 ante")
    if stack_bb is not None and not 80 <= stack_bb <= 120:
        reasons.append(f"约 {stack_bb:.0f}bb 深码")
    if game_mode == "squid":
        reasons.append("鱿鱼模式")
    if reasons:
        return True, "、".join(reasons[:4])
    return False, "标准 6-max 100bb 简单节点，优先使用本地算法"


def _local_fast_candidate(decision: Dict[str, Any]) -> bool:
    return _local_fast_rule(decision) is not None


def _local_fast_rule(
    decision: Dict[str, Any],
) -> Optional[Tuple[str, str, List[str]]]:
    legal = set(decision.get("legal_actions") or [])
    if len(legal) == 1:
        action = next(iter(legal))
        return action, f"当前只有 {action} 一个合法动作。", ["无需范围或概率推断"]

    street = str(decision.get("street") or "preflop")
    hole_cards = list(decision.get("hero_cards") or [])
    board = list(decision.get("board") or [])
    if len(hole_cards) != 2:
        return None
    try:
        features = hand_features(hole_cards, board)
    except (TypeError, ValueError):
        return None
    pot = _number(decision.get("pot"))
    call = _number(decision.get("call_score")) or 0.0
    required = call / (pot + call) if pot is not None and pot >= 0 and call > 0 else 0.0

    if street == "preflop":
        ranks = [card[:-1].upper() for card in hole_cards]
        if ranks == ["A", "A"] and legal == {"fold", "call"}:
            return "call", "AA 面对全下或封顶下注直接跟注。", ["翻前最强起手牌", "合法动作仅为弃牌或跟注"]
        if (
            set(ranks) == {"A", "K"}
            and hole_cards[0][-1] == hole_cards[1][-1]
            and {"call", "raise"} <= legal
            and required <= 0.35
            and int(decision.get("table_players") or 0) == 6
            and str(decision.get("game_mode") or "") == "holdem"
            and not (_number(decision.get("ante")) or 0)
            and 80 <= (_number(decision.get("hero_stack_bb")) or 0) <= 120
        ):
            return "call", "标准六人桌约百盲深度，AKs 面对常规 3bet 继续。", [f"所需胜率约 {required:.1%}", "保留对手较宽 3bet 范围"]
        if (
            legal == {"check", "raise"}
            and call == 0
            and features.get("preflop_class") == "other"
        ):
            return "check", "弱牌有免费过牌选项，不主动扩大底池。", ["跟注成本为 0", "弱非同花/非高张起手牌"]
        if (
            legal == {"fold", "call"}
            and features.get("preflop_class") == "other"
            and required >= 0.42
        ):
            return "fold", "弱牌面对极差跟注赔率直接弃牌。", [f"所需胜率约 {required:.0%}", "缺少可支持宽跟注的对手证据"]

    if (
        street == "flop"
        and {"call", "raise"} <= legal
        and _is_flopped_set(hole_cards, board, features)
        and pot is not None
        and call / pot <= 0.4
    ):
        return "raise", "湿润翻牌的小额下注前，用顶级价值牌加注。", ["英雄持有翻牌暗三条", "跟注价格不高且牌面存在较多转牌变化"]
    if (
        street == "flop"
        and "call" in legal
        and _has_nut_flush_draw(hole_cards, board)
        and required <= 0.34
    ):
        return "call", "坚果同花听牌获得足够价格，至少继续一条街。", [f"所需胜率约 {required:.1%}", "两张后续牌提供充分改善机会"]

    board_category = "unknown"
    if len(board) == 5:
        try:
            board_category = hand_features([], board)["category"]
        except (TypeError, ValueError):
            pass
    private_monster = (
        features.get("category") in {"quads", "straight_flush"}
        and board_category != features.get("category")
    )
    if street == "river" and private_monster:
        if legal == {"fold", "call"}:
            return "call", "河牌超强成牌面对全下直接跟注。", [f"本地牌型：{features['category']}", "英雄底牌提升了公共牌牌型"]
        if legal == {"check", "raise"} and call == 0:
            return "raise", "河牌超强成牌主动价值下注。", [f"本地牌型：{features['category']}", "当前无人下注"]
    if (
        street == "river"
        and legal == {"fold", "call"}
        and features.get("category") == "high_card"
        and required >= 0.5
    ):
        return "fold", "无摊牌价值面对超大下注直接弃牌。", [f"所需胜率约 {required:.0%}", "本地牌型仅为高牌"]
    if (
        street == "river"
        and legal == {"fold", "call"}
        and int(features.get("category_rank") or 0) <= 2
        and required >= 0.65
    ):
        return "fold", "中弱摊牌牌力面对极端河牌价格直接弃牌。", [f"所需胜率约 {required:.0%}", f"本地牌型：{features['category']}"]
    if (
        street == "river"
        and legal == {"fold", "call"}
        and int(features.get("category_rank") or 0) >= 1
        and required <= 0.12
    ):
        return "call", "河牌下注价格极低，持有成牌直接跟注。", [f"所需胜率约 {required:.1%}", f"本地牌型：{features['category']}"]
    if (
        street == "turn"
        and legal == {"fold", "call"}
        and features.get("flush_draw")
        and required <= 0.12
    ):
        return "call", "强同花听牌获得足够直接赔率，选择跟注。", [f"所需胜率约 {required:.1%}", "尚有一张河牌可改善"]
    if (
        street == "turn"
        and legal == {"fold", "call"}
        and features.get("gutshot")
        and not features.get("flush_draw")
        and not features.get("open_ended_draw")
        and required >= 0.35
    ):
        return "fold", "单卡顺子听牌面对高价格直接弃牌。", [f"所需胜率约 {required:.0%}", "主要改善牌数量有限"]
    return None


def _local_exploit_rule(
    context: Dict[str, Any],
) -> Optional[Tuple[str, str, List[str]]]:
    decision = context["decision"]
    legal = set(decision.get("legal_actions") or [])
    if (
        str(decision.get("street")) != "flop"
        or legal != {"check", "raise"}
        or (_number(decision.get("call_score")) or 0) != 0
    ):
        return None
    features = (context.get("derived_facts") or {}).get("hand") or {}
    if (
        features.get("category") != "high_card"
        or features.get("flush_draw")
        or features.get("open_ended_draw")
        or features.get("gutshot")
    ):
        return None
    observations = [
        metric
        for profile in context.get("active_opponent_profiles") or []
        for metric in profile.get("observations") or []
        if metric.get("metric") == "fold_to_flop_cbet"
        and int(metric.get("opportunities") or 0) >= 30
    ]
    if not observations:
        return None
    weighted_trials = sum(int(item["opportunities"]) for item in observations)
    fold_rate = sum(
        float(item.get("observed_pct") or 0) * int(item["opportunities"])
        for item in observations
    ) / weighted_trials
    if fold_rate >= 70:
        return "raise", "对手有高样本翻牌过度弃牌，使用小尺度诈唬。", [f"弃对翻牌持续下注 {fold_rate:.0f}%", f"样本 {weighted_trials} 次"]
    if fold_rate <= 25:
        return "check", "对手很少弃对翻牌持续下注，空气牌选择过牌。", [f"弃对翻牌持续下注仅 {fold_rate:.0f}%", f"样本 {weighted_trials} 次"]
    return None


def _is_flopped_set(
    hole_cards: List[str], board: List[str], features: Dict[str, Any]
) -> bool:
    if len(hole_cards) != 2 or len(board) != 3 or features.get("category") != "trips":
        return False
    rank = hole_cards[0][:-1].upper()
    return hole_cards[1][:-1].upper() == rank and sum(
        card[:-1].upper() == rank for card in board
    ) == 1


def _has_nut_flush_draw(hole_cards: List[str], board: List[str]) -> bool:
    if len(hole_cards) != 2 or len(board) not in {3, 4}:
        return False
    for suit in "shcd":
        suited_cards = [card for card in [*hole_cards, *board] if card[-1] == suit]
        if len(suited_cards) == 4 and f"A{suit}" in hole_cards:
            return True
    return False


def _local_raise_to(
    decision: Dict[str, Any], pot_fraction: float = 0.75
) -> Optional[float]:
    minimum = _number(decision.get("min_raise_to"))
    maximum = _number(decision.get("max_raise_to"))
    pot = _number(decision.get("pot"))
    target = pot * pot_fraction if pot is not None and pot > 0 else minimum
    if target is None:
        return None
    if minimum is not None:
        target = max(target, minimum)
    if maximum is not None:
        target = min(target, maximum)
    return round(target, 2)


def _confidence_ceiling(
    street: str,
    game_mode: str,
    players_in_hand: int,
    table_players: int,
    stack_bb: Optional[float],
) -> str:
    if game_mode == "squid" or players_in_hand != 2 or table_players not in {6, 0}:
        return "low"
    if street != "preflop" or stack_bb is None or not 80 <= stack_bb <= 120:
        return "medium"
    return "high"


def _validate_result(
    result: Dict[str, Any],
    context: Dict[str, Any],
    latency_ms: int,
    source: str = DEFAULT_MODEL,
    reasoning_depth: str = "light",
) -> Dict[str, Any]:
    deep = reasoning_depth == "deep"
    decision = context["decision"]
    legal = set(decision.get("legal_actions") or [])
    action = _normalize_action(result.get("recommended_action"))
    if action not in legal:
        raise ReasoningError("LLM 返回了当前节点的非法动作")
    confidence = str(result.get("confidence") or "low").lower()
    if confidence not in {"low", "medium", "high"}:
        confidence = "low"
    ceiling = str(decision.get("llm_confidence_ceiling") or "low")
    rank = {"low": 0, "medium": 1, "high": 2}
    if rank[confidence] > rank.get(ceiling, 0):
        confidence = ceiling if ceiling in rank else "low"
    raise_to = _number(result.get("raise_to"))
    if action != "raise":
        raise_to = None
    elif raise_to is not None:
        minimum = _number(decision.get("min_raise_to"))
        maximum = _number(decision.get("max_raise_to"))
        if (minimum is not None and raise_to < minimum) or (
            maximum is not None and raise_to > maximum
        ):
            raise ReasoningError("LLM 返回的加注尺度超出合法范围")
    opponent_reads = _validate_exploit_items(
        result.get("opponent_reads"),
        "finding",
        context,
        5 if deep else 3,
        include_player=True,
    )
    range_adjustments = _validate_exploit_items(
        result.get("range_adjustments"),
        "adjustment",
        context,
        6 if deep else 4,
    )
    return {
        "recommended_action": action,
        "raise_to": raise_to,
        "confidence": confidence,
        "summary": str(result.get("summary") or "").strip()[
            :600 if deep else 300
        ],
        "factors": _string_list(result.get("factors"), 8 if deep else 4),
        "risks": _string_list(result.get("risks"), 4 if deep else 2),
        "opponent_reads": opponent_reads,
        "range_adjustments": range_adjustments,
        "evidence_details": _exploit_evidence_details(
            context,
            [*opponent_reads, *range_adjustments],
        ),
        "latency_ms": latency_ms,
        "source": source,
        "reasoning_depth": reasoning_depth,
    }


def _validate_exploit_result(
    result: Dict[str, Any],
    context: Dict[str, Any],
    latency_ms: int,
    source: str = DEFAULT_MODEL,
    reasoning_depth: str = "light",
) -> Dict[str, Any]:
    deep = reasoning_depth == "deep"
    compact_items = result.get("i")
    if isinstance(compact_items, list):
        result = {
            "summary": result.get("s"),
            "opponent_reads": [
                {
                    "player": item.get("p"),
                    "finding": item.get("r"),
                    "evidence": item.get("e"),
                }
                for item in compact_items
                if isinstance(item, dict) and item.get("r")
            ],
            "range_adjustments": [
                {
                    "adjustment": item.get("a"),
                    "evidence": item.get("e"),
                }
                for item in compact_items
                if isinstance(item, dict) and item.get("a")
            ],
            "confidence": result.get("c"),
        }
    confidence = str(result.get("confidence") or "low").lower()
    if confidence not in {"low", "medium", "high"}:
        confidence = "low"
    ceiling = str(
        (context.get("decision") or {}).get("llm_confidence_ceiling")
        or "low"
    )
    rank = {"low": 0, "medium": 1, "high": 2}
    if rank[confidence] > rank.get(ceiling, 0):
        confidence = ceiling if ceiling in rank else "low"
    opponent_reads = _validate_exploit_items(
        result.get("opponent_reads"),
        "finding",
        context,
        5 if deep else 2,
        include_player=True,
    )
    range_adjustments = _validate_exploit_items(
        result.get("range_adjustments"),
        "adjustment",
        context,
        5 if deep else 2,
    )
    return {
        "summary": str(result.get("summary") or "").strip()[
            :600 if deep else 400
        ],
        "opponent_reads": opponent_reads,
        "range_adjustments": range_adjustments,
        "evidence_details": _exploit_evidence_details(
            context,
            [*opponent_reads, *range_adjustments],
        ),
        "caveats": _string_list(result.get("caveats"), 3),
        "confidence": confidence,
        "latency_ms": latency_ms,
        "source": source,
        "reasoning_depth": reasoning_depth,
    }


def _validate_exploit_items(
    value: Any,
    text_key: str,
    context: Dict[str, Any],
    limit: int,
    *,
    include_player: bool = False,
) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    profiles = context.get("active_opponent_profiles") or []
    allowed_evidence = {
        str(metric.get("evidence_id"))
        for profile in profiles
        for metric in profile.get("observations") or []
        if metric.get("evidence_id")
    }
    allowed_players = {
        str(profile.get("player"))
        for profile in profiles
        if profile.get("player")
    }
    items = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get(text_key) or "").strip()[:300]
        evidence = [
            str(reference)
            for reference in raw.get("evidence") or []
            if str(reference) in allowed_evidence
        ][:4]
        if not text or not evidence:
            continue
        item: Dict[str, Any] = {
            text_key: text,
            "evidence": evidence,
        }
        if include_player:
            player = str(raw.get("player") or "")
            if player not in allowed_players:
                player = evidence[0].split(":metric:", 1)[0]
            item["player"] = player
        items.append(item)
        if len(items) >= limit:
            break
    return items


def _exploit_evidence_details(
    context: Dict[str, Any],
    findings: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    used = {
        str(reference)
        for finding in findings
        for reference in finding.get("evidence") or []
    }
    details = []
    for profile in context.get("active_opponent_profiles") or []:
        for metric in profile.get("observations") or []:
            evidence_id = str(metric.get("evidence_id") or "")
            if not evidence_id or evidence_id not in used:
                continue
            details.append(
                {
                    "evidence_id": evidence_id,
                    "player": profile.get("player"),
                    "metric": metric.get("metric"),
                    "mean_pct": metric.get("mean_pct"),
                    "observed_pct": metric.get("observed_pct"),
                    "population_mean_pct": metric.get(
                        "population_mean_pct"
                    ),
                    "deviation_pp": metric.get("deviation_pp"),
                    "opportunities": metric.get("opportunities"),
                    "context_opportunities": metric.get(
                        "context_opportunities"
                    ),
                    "confidence": metric.get("confidence"),
                }
            )
    return details


def _validate_profile_result(
    result: Dict[str, Any],
    context: Dict[str, Any],
    latency_ms: int,
    source: str = DEFAULT_MODEL,
) -> Dict[str, Any]:
    confidence = str(result.get("confidence") or "low").lower()
    if confidence not in {"low", "medium", "high"}:
        confidence = "low"
    ceiling = str(context.get("confidence_ceiling") or "low")
    rank = {"low": 0, "medium": 1, "high": 2}
    if rank[confidence] > rank.get(ceiling, 0):
        confidence = ceiling if ceiling in rank else "low"
    allowed_evidence = {
        str(item.get("evidence_id"))
        for item in context.get("population_deviations") or []
        if item.get("evidence_id")
    }
    allowed_evidence.update(
        str(item.get("case_id"))
        for item in (context.get("evidence_cases") or {}).get("cases") or []
        if item.get("case_id")
    )
    return {
        "style_summary": str(result.get("style_summary") or "").strip()[:400],
        "vulnerabilities": _profile_findings(
            result.get("vulnerabilities"),
            allowed_evidence,
            ceiling,
        ),
        "counter_strategy": _string_list(result.get("counter_strategy"), 4),
        "caveats": _string_list(result.get("caveats"), 3),
        "confidence": confidence,
        "latency_ms": latency_ms,
        "source": source,
    }


def _profile_findings(
    value: Any, allowed_evidence: set, confidence_ceiling: str
) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rank = {"low": 0, "medium": 1, "high": 2}
    findings = []
    for item in value:
        if not isinstance(item, dict):
            continue
        finding = str(item.get("finding") or "").strip()[:300]
        evidence = [
            str(reference)
            for reference in item.get("evidence") or []
            if str(reference) in allowed_evidence
        ][:4]
        if not finding or not evidence:
            continue
        confidence = str(item.get("confidence") or "low").lower()
        if confidence not in rank:
            confidence = "low"
        if rank[confidence] > rank.get(confidence_ceiling, 0):
            confidence = (
                confidence_ceiling if confidence_ceiling in rank else "low"
            )
        findings.append(
            {
                "finding": finding,
                "evidence": evidence,
                "confidence": confidence,
            }
        )
        if len(findings) >= 3:
            break
    return findings


def _parse_model_json(content: str) -> Dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        last_fence = text.rfind("```")
        if first_newline >= 0 and last_fence > first_newline:
            text = text[first_newline + 1 : last_fence].strip()
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise ReasoningError("LLM 没有返回合法 JSON") from error
    if not isinstance(parsed, dict):
        raise ReasoningError("LLM 返回结果不是 JSON 对象")
    return parsed


def _normalize_action(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {"allin": "all_in", "all_in": "all_in"}
    text = aliases.get(text, text)
    return text if text in {"fold", "check", "call", "raise", "all_in"} else None


def _history_action(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text == "bet":
        return "bet"
    return _normalize_action(text)


def _street(board: List[str], actions: List[Dict[str, Any]]) -> str:
    if actions:
        value = str(actions[-1].get("street") or "")
        if value in {"preflop", "flop", "turn", "river"}:
            return value
    return {0: "preflop", 3: "flop", 4: "turn", 5: "river"}.get(
        len(board), "preflop"
    )


def _side_pots(
    actions: List[Dict[str, Any]], active_seats: set
) -> List[Dict[str, Any]]:
    contributions: Dict[int, float] = {}
    for action in actions:
        seat = action.get("seat")
        amount = _number(action.get("amount")) or 0.0
        if seat is None or amount <= 0:
            continue
        try:
            seat_number = int(seat)
        except (TypeError, ValueError):
            continue
        contributions[seat_number] = contributions.get(seat_number, 0.0) + amount
    pots = []
    previous = 0.0
    for level in sorted(set(contributions.values())):
        contributors = [
            seat for seat, amount in contributions.items() if amount >= level
        ]
        amount = (level - previous) * len(contributors)
        eligible = sorted(
            seat
            for seat in contributors
            if seat in active_seats
        )
        if amount > 0 and eligible:
            pots.append(
                {
                    "amount": round(amount, 2),
                    "eligible_seats": eligible,
                }
            )
        previous = level
    return pots


def _parse_time(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def _text(value: Any) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    return text or None


def _number(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _string_list(value: Any, limit: int) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:300] for item in value if str(item).strip()][:limit]


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _endpoint_for_model(model: str) -> str:
    return MODEL_ENDPOINT_TEMPLATE.format(model=quote(model, safe="._-"))


def _model_from_endpoint(endpoint: str) -> str:
    if not endpoint:
        return ""
    parts = urlsplit(endpoint).path.split("/")
    try:
        index = parts.index("deployments")
        return unquote(parts[index + 1]).strip()
    except (ValueError, IndexError):
        return ""
