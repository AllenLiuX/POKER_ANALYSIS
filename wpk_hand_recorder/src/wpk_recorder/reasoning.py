from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import time
import urllib.error
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urlsplit

import httpx

from .decision_state import (
    canonical_legal_actions,
    decision_state_contract,
    decision_state_from_hand,
)
from .features import board_texture, derive_positions, project_hand_state
from .inference import (
    action_line_range_profile,
    available_starting_hand_combos,
    board_action_range_profile,
    cached_action_line_range_quality,
    cached_continuation_range_quality,
    conditional_continuation_ranges,
    current_hand_action_contexts,
    preflop_range_profile,
    range_exploit_composition,
    range_strength_distribution,
)
from .inference_templates import (
    CONTEXT_SCHEMA_VERSION,
    active_template,
    get_template,
)
from .models import DecisionRequest
from .offline_gto import (
    FIT_VERSION,
    PUBLIC_STRATEGY_SOURCES,
    classify_postflop_hand,
    postflop_strategy,
    preflop_open_fraction,
)
from .opponent_model import (
    cached_action_response_backtest,
    cached_joint_fold_backtest,
    contextual_action_responses,
    exploit_directives,
    joint_fold_calibration,
    opponent_metrics,
)
from .poker import equity_vs_random, hand_features
from .protocol import _card_list
from .squid_value import (
    anonymize_squid_state,
    current_squid_state,
    estimate_award_probabilities,
)
from .storage import hand_from_dict


DEFAULT_MODEL = "gpt-5.6-sol"
OPPONENT_NODE_MODEL_VERSION = "opponent-node-range-v6"
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
    "公开亮牌的收缩后范围摘要、离线漏洞模式，以及带具体 hole cards、board、"
    "SPR、底池赔率和下注尺度的匿名行动案例。"
    "不得把净输赢当作技术水平，不得编造未提供的牌、频率、GTO 或 EV 数字。"
    "亮牌存在摊牌选择偏差；低样本结论必须降置信度并明确保留意见。"
    "population_deviations 只是相对当前牌池，不是 GTO 偏移。"
    "必须优先检查 donk lead、错过 cbet 后 probe、听牌继续/加注、不同牌力的尺度泄漏、"
    "面对不同尺度的过度弃牌或跟注，以及跨街范围是否封顶。"
    "严格区分真正的 donk 与 PFA 过牌后的 probe；pattern 只是候选信号，不是自动定罪。"
    "你的任务是交叉检查总体统计、模式聚合与具体案例，找出节点明确的行为漏洞，"
    "并给出可执行但保守的对抗调整。"
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


class _ModelJSONError(ReasoningError):
    def __init__(
        self,
        finish_reason: str,
        latency_ms: int,
    ):
        super().__init__("LLM 没有返回合法 JSON")
        self.finish_reason = finish_reason
        self.latency_ms = latency_ms


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
    if payload.get("event") == "roundChangeNotify":
        data = data.get("userAciton") or data.get("userAction")
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
    runtime_player = next(
        (
            player
            for player in players
            if runtime_user_id
            and _text(player.get("user_id")) == runtime_user_id
        ),
        None,
    )
    is_spectating = bool(
        runtime_user_id
        and runtime_player is None
        and known_hero is None
    )
    actor_matches_runtime = bool(
        runtime_user_id and actor_user_id == runtime_user_id
    )
    wire_hero_cards = _card_list(data.get("handCards"))
    raw_hero_cards = (
        _card_list(payload.get("_recorderHeroCards"))
        if actor_matches_runtime or not runtime_user_id
        else []
    )
    if not raw_hero_cards:
        raw_hero_cards = wire_hero_cards
    is_hero_decision = bool(
        actor_player
        and (
            actor_player.get("is_hero") is True
            or actor_matches_runtime
            or (not runtime_user_id and len(raw_hero_cards) == 2)
        )
    )
    if runtime_user_id and not actor_matches_runtime and not is_spectating:
        return None
    if known_hero is not None and not is_hero_decision and not is_spectating:
        return None
    contribution = _number(data.get("seatScore")) or 0.0
    min_raise_increment = _number(data.get("minRaiseScore"))
    max_raise_increment = _number(data.get("maxRaiseScore"))
    request = DecisionRequest(
        event_sequence=int(event_sequence),
        captured_at=_parse_time(timestamp).isoformat(),
        hand_id=resolved_hand_id,
        seat=(actor_player or {}).get("seat"),
        user_id=actor_user_id,
        cards=(
            raw_hero_cards
            or list((actor_player or {}).get("hole_cards") or [])
            if is_hero_decision
            else []
        ),
        legal_actions=[
            action
            for value in data.get("canActionList") or []
            if (action := _normalize_action(value))
        ],
        call_score=_number(data.get("callScore")) or 0.0,
        min_raise_to=(
            contribution + min_raise_increment
            if min_raise_increment is not None
            else None
        ),
        max_raise_to=(
            contribution + max_raise_increment
            if max_raise_increment is not None
            else None
        ),
        countdown=_number(
            data.get("countDown")
            if data.get("countDown") is not None
            else data.get("totalCountDown")
        )
        or 0.0,
        last_bet=_number(data.get("lastBet")),
        seat_score=contribution,
        current_score=(
            max_raise_increment
            if max_raise_increment is not None
            else _number(data.get("currentScore"))
        ),
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
        decision_subject=(
            "self" if is_hero_decision else "observer"
        ),
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
        "_hero_user_id": _text((known_hero or {}).get("user_id"))
        if isinstance(known_hero, dict)
        else actor_user_id
        if is_hero_decision
        else None,
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
    known_hero = next(
        (
            player
            for player in hand.players.values()
            if player.is_hero
        ),
        None,
    )
    actor_is_known_hero = bool(actor is not None and actor.is_hero)
    if known_hero is not None and not actor_is_known_hero:
        return None
    is_self_decision = actor_is_known_hero
    stack_bb = _number(contract.get("hero_stack_bb"))
    auto_reasoning, auto_reason = _reasoning_gate(
        legal_actions=list(contract.get("legal_actions") or []),
        hero_cards=list(contract.get("hero_cards") or []),
        decision_subject=(
            "self" if is_self_decision else "observer"
        ),
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
        "decision_subject": (
            "self" if is_self_decision else "observer"
        ),
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
        "_hero_user_id": (
            known_hero.user_id
            if known_hero is not None
            else actor_user_id
            if is_self_decision
            else None
        ),
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


def preflop_preview_context_from_hand(
    hand: Any,
) -> Optional[Dict[str, Any]]:
    """Build a range-only hero preview before action reaches their seat."""

    if (
        hand is None
        or getattr(hand, "status", None) != "in_progress"
        or list(getattr(hand, "board", None) or [])
    ):
        return None
    hero = next(
        (
            player
            for player in getattr(hand, "players", {}).values()
            if player.is_hero
        ),
        None,
    )
    if hero is None:
        return None
    projected = project_hand_state(hand)
    if (
        projected.street not in {"", "preflop"}
        or hero.seat not in projected.active_seats
    ):
        return None
    voluntary_actions = {
        "fold", "check", "call", "bet", "raise", "all_in"
    }
    if any(
        action.seat == hero.seat
        and _history_action(action.action) in voluntary_actions
        for action in hand.actions
    ):
        return None
    pending = getattr(hand, "pending_decision", None)
    if pending is not None and pending.seat == hero.seat:
        return None

    hero_contribution = float(
        projected.street_contributions.get(hero.seat, 0.0)
    )
    target_contribution = max(
        (
            float(projected.street_contributions.get(seat, 0.0))
            for seat in projected.active_seats
        ),
        default=0.0,
    )
    hero_position = derive_positions(hand).get(hero.seat) or hero.position
    if target_contribution <= 0 and str(hero_position or "").upper() != "BB":
        target_contribution = float(hand.big_blind or 0.0)
    call_score = max(0.0, target_contribution - hero_contribution)
    legal_actions = (
        ["fold", "call", "raise"]
        if call_score > 0
        else ["check", "raise"]
    )
    sequence = max(
        1,
        int(getattr(pending, "event_sequence", 0) or 0),
        *[
            int(action.event_sequence or action.sequence or 0)
            for action in hand.actions
        ],
    )
    request = DecisionRequest(
        event_sequence=sequence,
        captured_at=datetime.now(timezone.utc).isoformat(),
        hand_id=hand.hand_id,
        seat=hero.seat,
        user_id=hero.user_id,
        cards=list(hero.hole_cards or []),
        legal_actions=legal_actions,
        call_score=call_score,
        last_bet=target_contribution,
        seat_score=hero_contribution,
    )
    decision = decision_state_from_hand(
        hand,
        request,
        source="preflop_preview",
        remaining_ms=60_000,
    )
    if decision is None:
        return None
    decision = {
        **decision,
        "decision_subject": "self_preview",
        "subject_seat": hero.seat,
        "preview": True,
        "_hero_user_id": hero.user_id,
        "_hand": hand.as_dict(),
    }
    positions = {
        player.get("seat"): player.get("position")
        for player in decision.get("players") or []
    }
    action_history = [
        {
            "street": action.street,
            "seat": action.seat,
            "position": positions.get(action.seat),
            "action": _llm_history_action(action.action),
            "amount": _number(action.amount),
            "amount_to": _number(action.amount_to),
            "sequence": int(action.event_sequence or action.sequence or 0),
        }
        for action in hand.actions
    ]
    public = public_decision(decision)
    return {
        "decision": public,
        "action_history": action_history,
        "active_opponent_profiles": [],
        "context_integrity": _context_integrity(public, action_history),
        "preview": True,
    }


def fast_preflop_context(
    decision: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Build the minimum context needed for an immediate preflop EV result.

    Opponent profiling and validation backtests are intentionally excluded.
    They remain available to the slower inference-review path after the local
    action recommendation has already been shown.
    """

    if (
        decision is None
        or str(decision.get("street") or "preflop") != "preflop"
    ):
        return None
    hand = decision.get("_hand") or {}
    positions = {
        player.get("seat"): player.get("position")
        for player in decision.get("players") or []
        if player.get("seat") is not None
    }
    actions = hand.get("actions") or []
    action_history = [
        {
            "street": action.get("street"),
            "seat": action.get("seat"),
            "position": positions.get(action.get("seat")),
            "action": _llm_history_action(action.get("action")),
            "amount": _number(action.get("amount")),
            "amount_to": _number(action.get("amount_to")),
            "sequence": int(
                action.get("event_sequence")
                or action.get("sequence")
                or 0
            ),
        }
        for action in actions
    ]
    public = public_decision(decision)
    return {
        "decision": public,
        "action_history": action_history,
        "active_opponent_profiles": [],
        "range_model_quality": {},
        "continuation_range_quality": {},
        "action_response_quality": {},
        "joint_response_model": {},
        "joint_response_quality": {},
        "limitations": [
            "翻前实时路径优先使用牌池基线；对手画像在后台复核阶段加载",
        ],
        "context_integrity": _context_integrity(public, action_history),
        "fast_preflop": True,
    }


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
    decision_positions = {
        player.get("seat"): player.get("position")
        for player in decision.get("players") or []
        if player.get("seat") is not None
    }
    players = [
        {
            **player,
            "position": (
                decision_positions.get(player.get("seat"))
                or player.get("position")
            ),
        }
        for player in hand.get("players") or []
    ]
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
    decision_pot = _number(decision.get("pot"))
    decision_stack = _number(decision.get("hero_stack"))
    current_spr = (
        decision_stack / decision_pot
        if decision_stack is not None
        and decision_pot is not None
        and decision_pot > 0
        else None
    )
    hero_is_ip = decision.get("is_ip")
    heads_up = int(decision.get("players_in_hand") or 0) == 2
    current_action_order = list(decision.get("action_order") or [])
    current_board_texture = board_texture(
        list(decision.get("board") or []),
        str(decision.get("street") or "preflop"),
    )
    range_quality = cached_action_line_range_quality(
        connection,
        str(decision.get("game_mode") or "") or None,
    )
    continuation_quality = cached_continuation_range_quality(
        connection,
        str(decision.get("game_mode") or "") or None,
    )
    response_quality = cached_action_response_backtest(
        connection,
        str(decision.get("game_mode") or "") or None,
        refresh_on_miss=False,
    )
    joint_fold_quality = cached_joint_fold_backtest(
        connection,
        str(decision.get("game_mode") or "") or None,
    )
    joint_response_model = joint_fold_calibration(
        connection,
        str(decision.get("game_mode") or ""),
        street=str(decision.get("street") or "preflop"),
        opponents=max(1, int(decision.get("players_in_hand") or 2) - 1),
        action_type=(
            "raise"
            if (_number(decision.get("call_score")) or 0) > 0
            else "bet"
        ),
        quality=joint_fold_quality,
    )
    action_contexts = current_hand_action_contexts(
        connection,
        str(decision.get("hand_id") or ""),
    )
    response_players = []
    for player in players:
        user_id = _text(player.get("user_id"))
        seat = player.get("seat")
        if not user_id or user_id == hero_user_id or seat in folded:
            continue
        response_players.append(
            {
                "user_id": user_id,
                "position": player.get("position"),
                "is_ip": (
                    seat == current_action_order[-1]
                    if current_action_order and seat in current_action_order
                    else not bool(hero_is_ip)
                    if heads_up and hero_is_ip is not None
                    else None
                ),
            }
        )
    response_models = contextual_action_responses(
        connection,
        response_players,
        str(decision.get("game_mode") or ""),
        street=str(decision.get("street") or "preflop"),
        players_in_hand=int(decision.get("players_in_hand") or 0) or None,
        spr=current_spr,
        faced_bet=True,
        bet_fraction=_current_facing_bet_fraction(decision),
        facing_action=(
            "raise"
            if (_number(decision.get("call_score")) or 0) > 0
            or str(decision.get("street") or "") == "preflop"
            else "bet"
        ),
        board_texture=current_board_texture,
        quality=response_quality,
    )
    profiles = []
    for player in players:
        user_id = _text(player.get("user_id"))
        seat = player.get("seat")
        if not user_id or user_id == hero_user_id or seat in folded:
            continue
        profile = _build_opponent_node_profile(
            connection,
            decision,
            player,
            actions,
            response_model=response_models.get(user_id, {}),
            range_quality=range_quality,
            continuation_quality=continuation_quality,
            action_contexts=action_contexts,
            folded=False,
        )
        profiles.append(
            {
                key: value
                for key, value in profile.items()
                if not key.startswith("_")
            }
        )
    sanitized_actions = [
        {
            "street": action.get("street"),
            "seat": action.get("seat"),
            "position": positions.get(action.get("seat")),
            "action": _llm_history_action(action.get("action")),
            "amount": _number(action.get("amount")),
            "amount_to": _number(action.get("amount_to")),
            "sequence": int(
                action.get("event_sequence")
                or action.get("sequence")
                or 0
            ),
        }
        for action in actions
    ]
    context_decision = public_decision(decision)
    if (
        context_decision is not None
        and context_decision.get("decision_subject") == "observer"
    ):
        context_decision["hero_cards"] = []
    if context_decision is not None and not any(
        profile["observations"] for profile in profiles
    ):
        context_decision["llm_confidence_ceiling"] = "low"
    return {
        "decision": context_decision,
        "action_history": sanitized_actions,
        "active_opponent_profiles": profiles,
        "range_model_quality": range_quality,
        "continuation_range_quality": continuation_quality,
        "action_response_quality": response_quality,
        "joint_response_model": joint_response_model,
        "joint_response_quality": joint_fold_quality,
        "context_integrity": _context_integrity(
            context_decision,
            sanitized_actions,
        ),
        "model_training_policy": {
            "validation": (
                "prequential_expanding_after_70_percent_warmup"
            ),
            "production_fit": "all_completed_good_history",
            "validation_rows_reused_after_gate": True,
            "current_hand_excluded": True,
            "note": (
                "测试段只验证模型类别与特征层级；通过后线上用全部"
                "已完成且质量合格的历史重估后验"
            ),
        },
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


def _opponent_is_ip(
    decision: Dict[str, Any],
    seat: Any,
) -> Optional[bool]:
    order = list(decision.get("action_order") or [])
    if seat in order and order:
        return seat == order[-1]
    if (
        int(decision.get("players_in_hand") or 0) == 2
        and decision.get("is_ip") is not None
    ):
        return not bool(decision.get("is_ip"))
    return None


def _current_facing_bet_fraction(
    decision: Dict[str, Any],
) -> Optional[float]:
    if str(decision.get("street") or "preflop") == "preflop":
        return None
    call = max(0.0, _number(decision.get("call_score")) or 0.0)
    pot = max(0.0, _number(decision.get("pot")) or 0.0)
    return call / pot if call > 0 and pot > 0 else None


def _build_opponent_node_profile(
    connection: sqlite3.Connection,
    decision: Dict[str, Any],
    player: Dict[str, Any],
    actions: List[Dict[str, Any]],
    *,
    response_model: Optional[Dict[str, Any]],
    range_quality: Dict[str, Any],
    continuation_quality: Dict[str, Any],
    action_contexts: Dict[str, Dict[str, Dict[str, Any]]],
    folded: bool,
    include_metrics: bool = True,
) -> Dict[str, Any]:
    user_id = _text(player.get("user_id"))
    seat = player.get("seat")
    game_mode = str(decision.get("game_mode") or "")
    metrics = (
        opponent_metrics(
            connection,
            user_id,
            game_mode,
            position=player.get("position"),
            effective_stack_bb=_player_effective_stack_bb(decision, seat),
            limit=12,
        )
        if include_metrics
        else []
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
    range_posterior = None
    continuation_ranges = None
    range_profile = None
    try:
        range_profile = preflop_range_profile(
            connection,
            user_id,
            position=str(player.get("position") or "ALL"),
            line=preflop_line,
            mode=game_mode or None,
            _exclude_hand_id=str(decision.get("hand_id") or "") or None,
        )
        range_model = {
            "line": range_profile["line"],
            "line_label": range_profile["line_label"],
            "estimated_range_pct": range_profile[
                "estimated_range_pct"
            ],
            "confidence": range_profile["confidence"],
            "action_opportunities": range_profile["evidence"][
                "action_opportunities"
            ],
            "weights": {
                item["hand"]: item["weight_pct"]
                for item in range_profile["matrix"]
            },
        }
        range_posterior = action_line_range_profile(
            connection,
            user_id,
            range_model,
            actions,
            seat,
            list(decision.get("board") or []),
            mode=game_mode or None,
            blockers=list(decision.get("hero_cards") or []),
            quality=range_quality,
            exclude_hand_id=str(decision.get("hand_id") or "") or None,
            action_context_by_street=action_contexts.get(user_id),
        )
        selected_range_weights = (
            range_posterior.get("weights")
            if range_posterior.get("enabled")
            else range_model.get("weights")
        ) or {}
        continuation_ranges = conditional_continuation_ranges(
            connection,
            user_id,
            selected_range_weights,
            list(decision.get("board") or []),
            street=str(decision.get("street") or "preflop"),
            mode=game_mode or None,
            blockers=list(decision.get("hero_cards") or []),
            quality=continuation_quality,
            exclude_hand_id=str(decision.get("hand_id") or "") or None,
        )
    except (LookupError, ValueError, sqlite3.Error):
        pass
    response = response_model or {}
    return {
        "player": f"seat_{seat}",
        "seat": seat,
        "display_name": (
            _text(player.get("alias")) or f"座位 {seat}"
        ),
        "position": player.get("position"),
        "folded": folded,
        "observations": metrics,
        "response_model": response,
        "automatic_exploits": exploit_directives(response),
        "preflop_range": range_model,
        "range_posterior": range_posterior,
        "continuation_ranges": continuation_ranges,
        "_user_id": user_id,
        "_range_matrix": (
            list(range_profile.get("matrix") or [])
            if range_profile
            else []
        ),
        "_range_evidence": (
            dict(range_profile.get("evidence") or {})
            if range_profile
            else {}
        ),
        "_range_limitations": (
            list(range_profile.get("limitations") or [])
            if range_profile
            else []
        ),
    }


def opponent_node_profile(
    connection: sqlite3.Connection,
    decision: Dict[str, Any],
    user_id: str,
    *,
    preview: bool = False,
) -> Dict[str, Any]:
    """Build one auditable opponent posterior at a frozen hand node."""

    hand = decision.get("_hand") or {}
    players = list(hand.get("players") or [])
    target = next(
        (
            player
            for player in players
            if _text(player.get("user_id")) == _text(user_id)
        ),
        None,
    )
    if target is None:
        raise LookupError("player not found in selected hand")
    if _text(user_id) == _text(decision.get("_hero_user_id")):
        raise ValueError("selected player is the decision subject")
    actions = list(hand.get("actions") or [])
    as_of_sequence = int(
        decision.get("_as_of_sequence")
        or decision.get("sequence")
        or 0
    )
    if as_of_sequence > 0:
        actions = [
            action
            for action in actions
            if int(action.get("sequence") or 0) <= as_of_sequence
        ]
    target_seat = target.get("seat")
    folded = bool(target.get("folded")) or any(
        action.get("seat") == target_seat
        and _normalize_action(action.get("action")) == "fold"
        for action in actions
    )
    game_mode = str(decision.get("game_mode") or "")
    if preview:
        range_quality = {
            "enabled": True,
            "preview": True,
            "display_only": True,
            "feature_groups": [],
            "reason": (
                "快速预览使用个人与牌池亮牌行动频率做保守收缩；"
                "不作为 Money-EV 的生产门控"
            ),
            "method": "display-only-history-shrink-v1",
        }
        continuation_quality = {
            "enabled": False,
            "preview": True,
            "groups": [],
            "reason": "继续范围正在后台加载",
            "method": "progressive-node-preview-v1",
        }
        response_quality = {
            "enabled": False,
            "preview": True,
            "groups": [],
            "reason": "行动响应后验正在后台加载",
            "method": "progressive-node-preview-v1",
        }
    else:
        range_quality = cached_action_line_range_quality(
            connection,
            game_mode or None,
        )
        continuation_quality = cached_continuation_range_quality(
            connection,
            game_mode or None,
        )
        response_quality = cached_action_response_backtest(
            connection,
            game_mode or None,
            refresh_on_miss=False,
        )
    action_contexts = current_hand_action_contexts(
        connection,
        str(decision.get("hand_id") or ""),
        max_sequence=as_of_sequence or None,
    )
    response_model: Dict[str, Any] = {}
    if not folded and not preview:
        pot = _number(decision.get("pot"))
        stack = _number(decision.get("hero_stack"))
        current_spr = (
            stack / pot
            if stack is not None and pot is not None and pot > 0
            else None
        )
        response_model = contextual_action_responses(
            connection,
            [
                {
                    "user_id": _text(user_id),
                    "position": target.get("position"),
                    "is_ip": _opponent_is_ip(
                        decision,
                        target_seat,
                    ),
                }
            ],
            game_mode,
            street=str(decision.get("street") or "preflop"),
            players_in_hand=(
                int(decision.get("players_in_hand") or 0) or None
            ),
            spr=current_spr,
            faced_bet=True,
            bet_fraction=_current_facing_bet_fraction(decision),
            facing_action=(
                "raise"
                if (_number(decision.get("call_score")) or 0) > 0
                or str(decision.get("street") or "") == "preflop"
                else "bet"
            ),
            board_texture=board_texture(
                list(decision.get("board") or []),
                str(decision.get("street") or "preflop"),
            ),
            quality=response_quality,
        ).get(_text(user_id), {})
    profile = _build_opponent_node_profile(
        connection,
        decision,
        target,
        actions,
        response_model=response_model,
        range_quality=range_quality,
        continuation_quality=continuation_quality,
        action_contexts=action_contexts,
        folded=folded,
        include_metrics=not preview,
    )
    base_range = profile.get("preflop_range") or {}
    posterior = profile.get("range_posterior") or {}
    blockers = list(decision.get("hero_cards") or [])
    board = list(decision.get("board") or [])
    production_enabled = bool(
        posterior.get("enabled") and not preview
    )
    if (
        not preview
        and not production_enabled
        and base_range.get("weights")
    ):
        display_posterior = action_line_range_profile(
            connection,
            _text(user_id),
            base_range,
            actions,
            target_seat,
            board,
            mode=game_mode or None,
            blockers=blockers,
            quality={
                "enabled": True,
                "display_only": True,
                "feature_groups": [],
                "reason": (
                    "生产门控未通过；展示层使用个人与牌池行动频率的"
                    "保守收缩"
                ),
                "method": "display-only-history-shrink-v1",
            },
            exclude_hand_id=(
                str(decision.get("hand_id") or "") or None
            ),
            action_context_by_street=action_contexts.get(
                _text(user_id)
            ),
        )
        if display_posterior.get("enabled"):
            posterior = display_posterior
    display_history_enabled = bool(
        posterior.get("enabled") and not production_enabled
    )
    posterior_enabled = (
        production_enabled or display_history_enabled
    )
    baseline_weights = dict(base_range.get("weights") or {})
    structural_fallback = {}
    if not posterior_enabled and board:
        structural_fallback = board_action_range_profile(
            baseline_weights,
            actions,
            target_seat,
            board,
            blockers=blockers,
            action_context_by_street=action_contexts.get(
                _text(user_id)
            ),
        )
    display_conditioned = bool(structural_fallback.get("enabled"))
    selected_weights = dict(
        posterior.get("weights")
        if posterior_enabled
        else structural_fallback.get("weights")
        if display_conditioned
        else baseline_weights
        or {}
    )
    matrix_template = list(profile.get("_range_matrix") or [])
    blocked_cards = [*board, *blockers]
    available_combos = {
        str(item.get("hand") or ""): available_starting_hand_combos(
            str(item.get("hand") or ""),
            blocked_cards,
        )
        for item in matrix_template
    }
    baseline_mass = sum(
        available_combos.get(str(item.get("hand") or ""), 0)
        * float(baseline_weights.get(str(item.get("hand") or "")) or 0.0)
        for item in matrix_template
    )
    selected_mass = sum(
        available_combos.get(str(item.get("hand") or ""), 0)
        * float(selected_weights.get(str(item.get("hand") or "")) or 0.0)
        for item in matrix_template
    )
    max_selected_weight = max(
        (float(weight or 0.0) for weight in selected_weights.values()),
        default=0.0,
    )
    matrix = []
    for item in matrix_template:
        hand_class = str(item.get("hand") or "")
        baseline = float(baseline_weights.get(hand_class) or 0.0)
        selected = float(selected_weights.get(hand_class) or 0.0)
        combo_count = available_combos.get(hand_class, 0)
        baseline_probability = (
            100.0 * combo_count * baseline / baseline_mass
            if baseline_mass > 0
            else 0.0
        )
        selected_probability = (
            100.0 * combo_count * selected / selected_mass
            if selected_mass > 0
            else 0.0
        )
        matrix.append(
            {
                "hand": hand_class,
                "weight_pct": round(selected, 2),
                "baseline_pct": round(baseline, 2),
                "delta_pp": round(selected - baseline, 2),
                "probability_pct": round(
                    selected_probability, 2
                ),
                "baseline_probability_pct": round(
                    baseline_probability, 2
                ),
                "probability_shift_pp": round(
                    selected_probability - baseline_probability,
                    2,
                ),
                "relative_likelihood_pct": round(
                    100.0 * selected / max_selected_weight,
                    1,
                )
                if max_selected_weight > 0
                else 0.0,
                "available_combos": combo_count,
            }
        )
    cumulative_probability = 0.0
    core_hands = set()
    for item in sorted(
        matrix,
        key=lambda candidate: (
            -float(candidate["probability_pct"]),
            candidate["hand"],
        ),
    ):
        if cumulative_probability >= 80.0:
            break
        core_hands.add(item["hand"])
        cumulative_probability += float(item["probability_pct"])
    for item in matrix:
        item["in_core_80"] = item["hand"] in core_hands
    target_actions = []
    for action in actions:
        display_action = _history_action(action.get("action"))
        if (
            action.get("seat") != target_seat
            or not display_action
        ):
            continue
        target_actions.append(
            {
                "street": action.get("street"),
                "action": display_action,
                "amount": _number(action.get("amount")),
                "amount_to": _number(action.get("amount_to")),
                "sequence": int(action.get("sequence") or 0),
            }
        )
    last_action = (
        str(target_actions[-1].get("action") or "")
        if target_actions
        else ""
    )
    aggressive_action = last_action in {"bet", "raise", "all_in"}
    baseline_strength = range_strength_distribution(
        baseline_weights,
        board,
        blockers,
    )
    selected_strength = range_strength_distribution(
        selected_weights,
        board,
        blockers,
    )
    strength_labels = set(baseline_strength) | set(selected_strength)
    strength_shifts = {
        label: round(
            float(selected_strength.get(label) or 0.0)
            - float(baseline_strength.get(label) or 0.0),
            1,
        )
        for label in strength_labels
    }
    baseline_composition = range_exploit_composition(
        baseline_weights,
        board,
        blockers,
        aggressive_action=aggressive_action,
    )
    selected_composition = range_exploit_composition(
        selected_weights,
        board,
        blockers,
        aggressive_action=aggressive_action,
    )
    composition_shifts = {
        label: round(
            float(selected_composition.get(label) or 0.0)
            - float(baseline_composition.get(label) or 0.0),
            1,
        )
        for label in (
            "strong_value",
            "marginal_showdown",
            "draws",
            "air",
        )
    }
    limitations = [
        *(profile.get("_range_limitations") or []),
        "后验范围不是对确切底牌的识别",
        "未公开弃牌底牌会造成摊牌选择偏差",
        *(
            ["该玩家已弃牌，下一行动模型不适用"]
            if folded
            else []
        ),
        *(
            [
                "统计门控未通过时，面板使用保守牌面/行动结构回退；"
                "该回退不进入 Money-EV"
            ]
            if display_conditioned
            else []
        ),
    ]
    return {
        "model_version": OPPONENT_NODE_MODEL_VERSION,
        "player": {
            "user_id": _text(user_id),
            "alias": profile.get("display_name"),
            "seat": target_seat,
            "position": target.get("position"),
            "folded": folded,
            "last_action": (
                target_actions[-1] if target_actions else None
            ),
            "action_line": target_actions,
        },
        "range": {
            "source": (
                "action_line_posterior"
                if production_enabled
                else "history_shrunk_display"
                if display_history_enabled
                else "board_action_heuristic"
                if display_conditioned
                else "preflop_prior"
            ),
            "production_enabled": production_enabled,
            "history_conditioned": (
                production_enabled or display_history_enabled
            ),
            "display_only_history": display_history_enabled,
            "display_conditioned": (
                posterior_enabled or display_conditioned
            ),
            "confidence": (
                posterior.get("confidence")
                if posterior_enabled
                else structural_fallback.get("confidence")
                if display_conditioned
                else base_range.get("confidence")
            ),
            "line": base_range.get("line"),
            "line_label": base_range.get("line_label"),
            "estimated_range_pct": base_range.get(
                "estimated_range_pct"
            ),
            "action_opportunities": base_range.get(
                "action_opportunities"
            ),
            "matrix": matrix,
            "core_80_class_count": len(core_hands),
            "top_class_shifts": (
                posterior.get("top_class_shifts") or []
                if posterior_enabled
                else structural_fallback.get("top_class_shifts") or []
            ),
        },
        "strength": {
            "baseline": baseline_strength,
            "posterior": selected_strength,
            "shifts": dict(
                sorted(
                    strength_shifts.items(),
                    key=lambda item: (-abs(item[1]), item[0]),
                )
            ),
        },
        "composition": {
            "baseline": baseline_composition,
            "posterior": selected_composition,
            "shifts": composition_shifts,
            "aggressive_action": aggressive_action,
            "method": "private-card-relative-to-board-v2",
            "interpretation": (
                "按底牌相对公共牌带来的提升分类；"
                "下注或加注后的空气只计为 bluff 候选，不能证明主观意图"
                if aggressive_action
                else (
                    "按底牌相对公共牌带来的提升分类；"
                    "最近行动不是下注或加注，因此只显示空气而不标记 bluff"
                )
            ),
        },
        "response_model": profile.get("response_model") or {},
        "automatic_exploits": profile.get(
            "automatic_exploits"
        ) or [],
        "evidence": {
            "preflop": profile.get("_range_evidence") or {},
            "updates": [
                *(posterior.get("updates") or []),
                *(structural_fallback.get("updates") or []),
            ],
            "gates": {
                "range": range_quality,
                "continuation": continuation_quality,
                "response": response_quality,
            },
            "range_gate": range_quality,
            "continuation_gate": continuation_quality,
            "response_gate": response_quality,
            "current_hand_excluded": bool(
                decision.get("hand_id")
            ),
            "limitations": limitations,
        },
        "limitations": limitations,
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
                "display_player": (
                    profile.get("display_name")
                    or profile.get("player")
                ),
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
    legal = list(canonical_legal_actions(decision.get("legal_actions")))
    spot = _strategy_spot(context)
    common = {
        "street": street,
        "spot": spot,
        "legal_actions": legal,
        "confidence": "baseline",
        "source": FIT_VERSION,
        "strategy_sources": list(PUBLIC_STRATEGY_SOURCES),
        "caveat": "公开 solver 规律的离线拟合，不是当前牌局的精确 solver 解；未看到底牌时不猜具体持牌。",
    }
    if street == "preflop":
        cells, scenario, range_context = _preflop_strategy_cells(
            decision, context, legal
        )
        hero_hand = _canonical_preflop_hand(decision.get("hero_cards") or [])
        result = {
            **common,
            "kind": "preflop_matrix",
            "scenario": scenario,
            "summary": _preflop_strategy_summary(
                scenario, spot, range_context
            ),
            "action_mix": _aggregate_strategy_mix(cells),
            "hero_hand": hero_hand,
            "range_context": range_context,
            "cells": cells,
        }
    else:
        fitted = postflop_strategy(context, legal)
        result = {
            **common,
            "kind": "postflop_buckets",
            "summary": "按人数、位置、底池类型、牌面、SPR、踢脚与听牌拟合离线行动频率。",
            **fitted,
        }
    result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return result


def apply_exploit_frequency_shifts(
    strategy: Dict[str, Any],
    analysis: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Apply validated LLM proposals without delegating arithmetic to the LLM."""
    shifts = analysis.get("frequency_shifts") or []
    if not shifts:
        return None
    adjusted = deepcopy(strategy)
    applied = []
    for shift in shifts:
        scope = str(shift.get("scope") or "")
        if adjusted.get("kind") == "preflop_matrix":
            targets = [
                cell
                for cell in adjusted.get("cells") or []
                if scope == "overall" or cell.get("bucket") == scope
            ]
        else:
            targets = [
                bucket
                for bucket in adjusted.get("buckets") or []
                if scope == "overall" or bucket.get("key") == scope
            ]
        if not targets:
            continue
        from_action = str(shift.get("from_action") or "")
        to_action = str(shift.get("to_action") or "")
        requested_delta = float(shift.get("delta_pp") or 0)
        before_from = _average_target_frequency(targets, from_action)
        before_to = _average_target_frequency(targets, to_action)
        changed = 0
        for target in targets:
            mix = target.get("frequencies") or {}
            transfer = min(requested_delta, float(mix.get(from_action) or 0))
            if transfer <= 0:
                continue
            mix[from_action] = round(float(mix.get(from_action) or 0) - transfer, 1)
            mix[to_action] = round(float(mix.get(to_action) or 0) + transfer, 1)
            target["frequencies"] = {
                action: frequency
                for action, frequency in mix.items()
                if frequency > 0
            }
            target["primary_action"] = max(
                target["frequencies"],
                key=target["frequencies"].get,
            )
            changed += 1
        after_from = _average_target_frequency(targets, from_action)
        after_to = _average_target_frequency(targets, to_action)
        applied_delta = round(after_to - before_to, 1)
        if changed == 0 or applied_delta <= 0:
            continue
        applied.append(
            {
                **shift,
                "applied_delta_pp": applied_delta,
                "baseline_from_pct": round(before_from, 1),
                "adjusted_from_pct": round(after_from, 1),
                "baseline_to_pct": round(before_to, 1),
                "adjusted_to_pct": round(after_to, 1),
                "affected_groups": changed,
            }
        )
    if not applied:
        return None
    if adjusted.get("kind") == "preflop_matrix":
        adjusted["action_mix"] = _aggregate_strategy_mix(
            adjusted.get("cells") or []
        )
    adjusted["source"] = "llm-bounded-exploit-v1"
    adjusted["baseline_source"] = strategy.get("source")
    adjusted["applied_frequency_shifts"] = applied
    adjusted["summary"] = (
        "已将通过证据、样本量与置信度校验的 LLM 调整应用到本地范围基线。"
    )
    adjusted["caveat"] = (
        "仅改变显示的建议行动频率，不会自动操作牌桌；每项调整均受本地上限约束且总频率保持 100%。"
    )
    return adjusted


def _average_target_frequency(
    targets: List[Dict[str, Any]],
    action: str,
) -> float:
    weighted = 0.0
    total_weight = 0
    for target in targets:
        weight = int(target.get("combo_count") or 1)
        weighted += float((target.get("frequencies") or {}).get(action) or 0) * weight
        total_weight += weight
    return weighted / total_weight if total_weight else 0.0


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
        _strategy_action_text(action, decision)
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
    limper_count = sum(
        action.get("action") == "call" for action in actions
    )
    call = _number(decision.get("call_score")) or 0.0
    pot = _number(decision.get("pot"))
    required = (
        round(100 * call / (pot + call), 1)
        if call > 0 and pot is not None and pot >= 0
        else 0.0
    )
    facing_price = _preflop_facing_price(decision, actions)
    return {
        "hero_position": hero_position,
        "facing_position": (aggressive or {}).get("position"),
        "facing_action": (aggressive or {}).get("action"),
        "action_line": action_line,
        "players_in_hand": int(decision.get("players_in_hand") or 0),
        "limper_count": limper_count,
        "call_score": call,
        "required_equity_pct": required,
        "facing_amount_to": facing_price.get("facing_amount_to"),
        "facing_amount_to_bb": facing_price.get(
            "facing_amount_to_bb"
        ),
    }


def _strategy_action_text(
    action: Dict[str, Any],
    decision: Dict[str, Any],
) -> str:
    label = (
        f"{action.get('position') or '未知位置'} "
        f"{_action_text(action.get('action'))}"
    )
    if action.get("action") not in {"bet", "raise", "all_in"}:
        return label
    amount_to = _number(action.get("amount_to"))
    amount = _number(action.get("amount"))
    value = amount_to if amount_to is not None and amount_to > 0 else amount
    if value is None or value <= 0:
        return label
    big_blind = _number(decision.get("big_blind")) or 0.0
    bb_text = (
        f" / {value / big_blind:.1f}BB"
        if big_blind > 0
        else ""
    )
    prefix = "到 " if amount_to is not None and amount_to > 0 else ""
    return f"{label} {prefix}{value:g}{bb_text}"


def _preflop_strategy_cells(
    decision: Dict[str, Any],
    context: Dict[str, Any],
    legal: List[str],
) -> Tuple[List[Dict[str, Any]], str, Dict[str, Any]]:
    actions = [
        action
        for action in context.get("action_history") or []
        if action.get("street") == "preflop" and action.get("action")
    ]
    raises = sum(
        action.get("action") in {"raise", "all_in"} for action in actions
    )
    limpers = sum(action.get("action") == "call" for action in actions)
    call = _number(decision.get("call_score")) or 0.0
    table_players = int(decision.get("table_players") or 0)
    position = str(decision.get("hero_position") or "").upper()
    normalized_position = {
        "SMALL_BLIND": "SB",
        "BTN/SB": "SB" if table_players > 2 else "BTN/SB",
    }.get(position, position)
    big_blind = _number(decision.get("big_blind")) or 0.0
    if (
        table_players == 2
        and position in {"BTN", "BTN/SB", "DEALER", "SB"}
        and not actions
        and {"call", "raise"} <= set(legal)
    ):
        scenario = "heads_up_button"
    elif (
        normalized_position == "SB"
        and raises == 0
        and limpers == 0
        and call > 0
        and (big_blind <= 0 or call <= big_blind * 0.55)
        and {"call", "raise"} <= set(legal)
    ):
        scenario = "small_blind_first_in"
    elif not actions and "raise" in legal:
        scenario = "unopened"
    elif call <= 0 and "check" in legal:
        scenario = "free_option"
    elif call > 0 and raises:
        scenario = "facing_raise" if raises == 1 else "facing_reraise"
    elif call > 0 and limpers > 0:
        scenario = "facing_limp"
    elif call > 0:
        scenario = "unopened"
    else:
        scenario = "unopened"

    range_context = _preflop_range_context(decision, context, scenario)
    open_fraction = min(
        0.88,
        preflop_open_fraction(decision)
        * float(range_context["width_multiplier"]),
    )
    if scenario == "facing_raise":
        base_continue = max(0.12, min(0.22, open_fraction * 0.55))
        price_multiplier = float(
            range_context.get("facing_price_multiplier") or 1.0
        )
        continue_fraction = max(
            0.06,
            min(0.32, base_continue * price_multiplier),
        )
        aggressive_fraction = max(
            0.025,
            min(
                continue_fraction,
                max(0.05, base_continue * 0.42)
                * price_multiplier**0.65,
            ),
        )
    elif scenario == "facing_reraise":
        price_multiplier = float(
            range_context.get("facing_price_multiplier") or 1.0
        )
        continue_fraction = max(
            0.045,
            min(0.16, 0.10 * price_multiplier),
        )
        aggressive_fraction = max(
            0.018,
            min(
                continue_fraction,
                0.035 * price_multiplier**0.65,
            ),
        )
    elif scenario == "facing_limp":
        (
            continue_fraction,
            aggressive_fraction,
        ) = _facing_limp_fractions(
            decision,
            open_fraction,
            limpers,
        )
    elif scenario == "heads_up_button":
        continue_fraction = open_fraction
        aggressive_fraction = min(0.62, open_fraction * 0.68)
    elif scenario == "small_blind_first_in":
        total_ante_bb = float(range_context.get("total_ante_bb") or 0.0)
        if total_ante_bb > 0:
            continue_fraction = min(
                0.95,
                max(0.78, 0.72 + 0.08 * total_ante_bb),
            )
            aggressive_fraction = min(0.50, open_fraction * 0.75)
        else:
            continue_fraction = min(0.66, max(0.58, open_fraction + 0.20))
            aggressive_fraction = min(0.45, open_fraction)
    else:
        continue_fraction = open_fraction
        aggressive_fraction = open_fraction

    classes = _preflop_strategy_classes(
        decision,
        context,
        scenario,
        range_context=range_context,
    )
    return [
        {
            "hand": item["hand"],
            "combo_count": item["combo_count"],
            "bucket": _preflop_frequency_scope(item["percentile"]),
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
    ], scenario, {
        **range_context,
        "base_fraction_pct": round(
            100 * preflop_open_fraction(decision), 1
        ),
        "adjusted_fraction_pct": round(100 * open_fraction, 1),
    }


def _facing_limp_fractions(
    decision: Dict[str, Any],
    open_fraction: float,
    limpers: int,
) -> Tuple[float, float]:
    position = str(decision.get("hero_position") or "").upper()
    position = {
        "SMALL_BLIND": "SB",
        "BIG_BLIND": "BB",
        "DEALER": "BTN",
        "BUTTON": "BTN",
    }.get(position, position)
    isolation_factor = {
        "UTG": 0.46,
        "UTG+1": 0.48,
        "MP": 0.52,
        "MP+1": 0.54,
        "HJ": 0.58,
        "CO": 0.64,
        "BTN": 0.72,
        "BTN/SB": 0.62,
        "SB": 0.42,
        "BB": 0.52,
    }.get(position, 0.48)
    limper_penalty = 1.0 + 0.28 * max(0, limpers - 1)
    aggressive_fraction = max(
        0.07,
        min(0.30, open_fraction * isolation_factor / limper_penalty),
    )
    passive_allowance = {
        "BTN": 0.13,
        "BTN/SB": 0.17,
        "SB": 0.22,
        "BB": 0.18,
    }.get(position, 0.10)
    continue_fraction = min(
        0.46,
        max(
            aggressive_fraction,
            aggressive_fraction
            + passive_allowance
            + 0.02 * min(3, max(0, limpers - 1)),
        ),
    )
    return continue_fraction, aggressive_fraction


def hero_preflop_range(context: Dict[str, Any]) -> Dict[str, Any]:
    """Approximate the hero range conditioned on their observed preflop line."""

    decision = context.get("decision") or {}
    hero_seat = decision.get("acting_seat")
    hero_position = str(decision.get("hero_position") or "").upper()
    preflop_actions = [
        action
        for action in context.get("action_history") or []
        if action.get("street") == "preflop"
        and action.get("action")
        in {"fold", "check", "call", "bet", "raise", "all_in"}
    ]

    def is_hero_action(action: Dict[str, Any]) -> bool:
        action_seat = action.get("seat")
        if hero_seat is not None and action_seat is not None:
            return action_seat == hero_seat
        return (
            bool(hero_position)
            and str(action.get("position") or "").upper() == hero_position
        )

    hero_indices = [
        index
        for index, action in enumerate(preflop_actions)
        if is_hero_action(action)
    ]
    classes = _preflop_strategy_classes(decision, context, "unconditioned")
    subject_label = (
        "当前行动者"
        if decision.get("decision_subject") == "observer"
        else "本人"
    )
    if not hero_indices:
        return {
            "source": FIT_VERSION,
            "scenario": "unconditioned",
            "action_line": f"未捕获{subject_label}翻前行动，使用全范围",
            "weights": {item["hand"]: 100.0 for item in classes},
            "combo_mass": 1326.0,
        }

    last_index = hero_indices[-1]
    selected = str(preflop_actions[last_index].get("action") or "")
    if selected in {"bet", "all_in"}:
        selected = "raise"
    prior_actions = preflop_actions[:last_index]
    legal = (
        ["check", "raise"]
        if selected == "check"
        else ["fold", "call", "raise"]
    )
    synthetic_decision = {
        **decision,
        "street": "preflop",
        "call_score": (
            0
            if selected == "check" or not prior_actions
            else _number(decision.get("big_blind")) or 1
        ),
        "legal_actions": legal,
    }
    cells, scenario, range_context = _preflop_strategy_cells(
        synthetic_decision,
        {**context, "action_history": prior_actions},
        legal,
    )
    weights = {
        str(cell["hand"]): float(
            (cell.get("frequencies") or {}).get(selected) or 0
        )
        for cell in cells
    }
    if not any(weights.values()):
        weights = {item["hand"]: 100.0 for item in classes}
        scenario = "unconditioned"
    combo_counts = {
        str(item["hand"]): int(item["combo_count"]) for item in classes
    }
    combo_mass = sum(
        combo_counts.get(hand, 0) * frequency / 100
        for hand, frequency in weights.items()
    )
    hero_line = [
        str(preflop_actions[index].get("action") or "")
        for index in hero_indices
    ]
    return {
        "source": FIT_VERSION,
        "scenario": scenario,
        "action_line": " → ".join(hero_line),
        "weights": weights,
        "combo_mass": round(combo_mass, 1),
        "range_context": range_context,
    }


def _preflop_frequency_scope(percentile: float) -> str:
    if percentile <= 0.05:
        return "premium"
    if percentile <= 0.14:
        return "strong"
    if percentile <= 0.28:
        return "medium"
    if percentile <= 0.50:
        return "speculative"
    return "weak"


def _preflop_mix(
    percentile: float,
    scenario: str,
    continue_fraction: float,
    aggressive_fraction: float,
    legal: List[str],
) -> Dict[str, int]:
    aggressive, passive, decline = _strategy_role_actions(legal)
    if scenario in {"heads_up_button", "small_blind_first_in"}:
        if percentile <= 0.14:
            raw = [(aggressive, 100)]
        elif percentile <= aggressive_fraction:
            raw = [(aggressive, 75), (passive, 25)]
        elif percentile <= continue_fraction:
            raw = [(aggressive, 35), (passive, 65)]
        else:
            raw = [(decline, 100)]
    elif scenario in {"facing_raise", "facing_reraise"}:
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


def _preflop_range_context(
    decision: Dict[str, Any],
    context: Dict[str, Any],
    scenario: str,
) -> Dict[str, Any]:
    table_players = max(
        2, min(9, int(decision.get("table_players") or 6))
    )
    stack_bb = _number(decision.get("hero_stack_bb"))
    big_blind = _number(decision.get("big_blind")) or 0.0
    ante = _number(decision.get("ante")) or 0.0
    total_ante_bb = (
        ante * table_players / big_blind if big_blind > 0 else 0.0
    )
    position = str(decision.get("hero_position") or "").upper()
    early_position = position in {"UTG", "UTG+1", "MP", "MP+1"}
    pressure = _table_preflop_pressure(context)
    preflop_actions = [
        action
        for action in context.get("action_history") or []
        if action.get("street") == "preflop" and action.get("action")
    ]
    facing_price = _preflop_facing_price(decision, preflop_actions)
    squid_round = context.get("squid_round") or {}
    squid_count = None
    zero_squid_players = None
    squid_pressure = "none"
    if (
        str(decision.get("game_mode") or "") == "squid"
        and isinstance(squid_round, dict)
    ):
        counts = squid_round.get("counts") or {}
        if isinstance(counts, dict) and "acting_player" in counts:
            squid_count = max(0, int(counts.get("acting_player") or 0))
            zero_squid_players = max(
                0, int(squid_round.get("zero_squid_players") or 0)
            )
            if squid_count == 0:
                squid_pressure = (
                    "high" if 0 < zero_squid_players <= 3 else "medium"
                )

    width_multiplier = 1.0
    if scenario in {
        "unopened",
        "small_blind_first_in",
        "facing_limp",
        "facing_raise",
    }:
        width_multiplier *= 1.0 - 0.08 * pressure
    if squid_pressure == "medium":
        width_multiplier *= 1.07
    elif squid_pressure == "high":
        width_multiplier *= 1.15

    suited_bonus = 0.115
    if stack_bb is not None and stack_bb >= 150:
        suited_bonus += 0.015
    if scenario in {"facing_limp", "facing_raise", "facing_reraise"}:
        suited_bonus += 0.015
    if total_ante_bb > 0:
        # Ante expands the range, but the lower postflop SPR gives raw high-card
        # equity a little more weight than a blind-only deep-stack chart.
        suited_bonus -= min(0.02, 0.009 * total_ante_bb)

    notes = []
    if table_players >= 8:
        notes.append("8/9 人桌前位避免被支配的非同花牌")
    if stack_bb is not None and stack_bb >= 150:
        notes.append("深码提高同花 Ax、同花大牌与连张可玩性")
    if total_ante_bb >= 0.5:
        notes.append(f"总前注 {total_ante_bb:.2f}BB 扩大争夺范围")
    if pressure >= 0.25:
        notes.append("后方 3bet 压力高，削减边缘开池与低同花连张")
    if scenario in {"facing_raise", "facing_reraise"}:
        amount_to_bb = facing_price.get("amount_to_bb")
        required_equity_pct = facing_price.get("required_equity_pct")
        if amount_to_bb is not None:
            notes.append(
                f"面对加注到 {amount_to_bb:.1f}BB，"
                f"跟注需约 {required_equity_pct:.1f}% 胜率"
            )
    if squid_pressure == "medium":
        notes.append("本人尚无鱿鱼，温和扩大主动争池范围")
    elif squid_pressure == "high":
        notes.append("鱿鱼末段高压力，优先高牌/阻断牌并扩大主动范围")
    return {
        "scenario": scenario,
        "table_players": table_players,
        "table_format": "full_ring" if table_players >= 8 else "short_handed",
        "effective_stack_bb": (
            round(stack_bb, 1) if stack_bb is not None else None
        ),
        "total_ante_bb": round(total_ante_bb, 2),
        "aggressive_pressure": round(pressure, 3),
        "squid_count": squid_count,
        "zero_squid_players": zero_squid_players,
        "squid_pressure": squid_pressure,
        "early_position": early_position,
        "suited_bonus": round(max(0.08, suited_bonus), 3),
        "width_multiplier": round(
            max(0.82, min(1.20, width_multiplier)), 3
        ),
        **facing_price,
        "notes": notes,
    }


def _preflop_facing_price(
    decision: Dict[str, Any],
    actions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    call = max(0.0, _number(decision.get("call_score")) or 0.0)
    pot = max(0.0, _number(decision.get("pot")) or 0.0)
    big_blind = max(0.0, _number(decision.get("big_blind")) or 0.0)
    contribution = max(
        0.0,
        _number(decision.get("hero_street_contribution")) or 0.0,
    )
    aggressive_actions = [
        action
        for action in actions
        if action.get("action") in {"raise", "all_in"}
    ]
    latest = aggressive_actions[-1] if aggressive_actions else {}
    amount_to = _number(latest.get("amount_to"))
    if amount_to is None or amount_to <= 0:
        amount_to = contribution + call if call > 0 else None
    amount_to_bb = (
        amount_to / big_blind
        if amount_to is not None and big_blind > 0
        else None
    )
    required = call / (pot + call) if call > 0 and pot + call > 0 else 0.0
    raises = len(aggressive_actions)
    reference_required = 0.30 if raises <= 1 else 0.34
    if required > 0:
        price_multiplier = max(
            0.50,
            min(1.45, (reference_required / required) ** 0.85),
        )
    else:
        price_multiplier = 1.0
    if required <= 0.22:
        price_bucket = "cheap"
    elif required <= 0.32:
        price_bucket = "standard"
    elif required <= 0.42:
        price_bucket = "expensive"
    else:
        price_bucket = "very_expensive"
    return {
        "facing_amount_to": (
            round(amount_to, 2) if amount_to is not None else None
        ),
        "facing_amount_to_bb": (
            round(amount_to_bb, 2) if amount_to_bb is not None else None
        ),
        "call_amount": round(call, 2),
        "required_equity_pct": round(100 * required, 1),
        "facing_price_bucket": price_bucket,
        "facing_price_multiplier": round(price_multiplier, 3),
    }


def _table_preflop_pressure(context: Dict[str, Any]) -> float:
    weighted_rate = 0.0
    weighted_pool = 0.0
    total_weight = 0.0
    for profile in context.get("active_opponent_profiles") or []:
        observation = next(
            (
                item
                for item in profile.get("observations") or []
                if item.get("metric") == "three_bet"
                and int(item.get("opportunities") or 0) >= 5
            ),
            None,
        )
        if observation is None:
            continue
        weight = min(30.0, float(observation.get("opportunities") or 0))
        weighted_rate += weight * float(observation.get("mean_pct") or 0)
        weighted_pool += weight * float(
            observation.get("population_mean_pct") or 8.0
        )
        total_weight += weight
    if total_weight <= 0:
        return 0.0
    rate = weighted_rate / total_weight
    pool = weighted_pool / total_weight
    absolute_pressure = max(0.0, (rate - 9.0) / 9.0)
    relative_pressure = max(0.0, (rate - pool) / 8.0)
    return max(0.0, min(1.0, max(absolute_pressure, relative_pressure)))


def _preflop_strategy_classes(
    decision: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
    scenario: str = "unopened",
    *,
    range_context: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    shape = range_context or _preflop_range_context(
        decision or {}, context or {}, scenario
    )
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
                    "score": _preflop_strategy_score(hand, shape),
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


def _preflop_strategy_score(
    hand: str, range_context: Optional[Dict[str, Any]] = None
) -> float:
    shape = range_context or {}
    first = STRATEGY_RANKS.index(hand[0])
    second = STRATEGY_RANKS.index(hand[1])
    if len(hand) == 2:
        return 1.12 - 0.045 * first
    high, low = min(first, second), max(first, second)
    gap = max(0, low - high - 1)
    score = 0.78 - 0.043 * high - 0.025 * low - 0.026 * gap
    suited = hand.endswith("s")
    pressure = float(shape.get("aggressive_pressure") or 0.0)
    if suited:
        score += float(shape.get("suited_bonus") or 0.115)
        if high == 0:
            score += 0.035
            if low >= 9:
                # A5s-A2s retain wheel potential and premium-card blockers;
                # treating their rank gap literally pushes them below
                # dominated offsuit broadways in ante/full-ring ranges.
                score += 0.18 + 0.045 * (low - 9)
        if high <= 3 and low <= 4:
            score += 0.025
        if gap <= 1:
            score += 0.020
        if (
            shape.get("effective_stack_bb") is not None
            and float(shape["effective_stack_bb"]) >= 150
            and gap <= 2
        ):
            score += 0.012
        if pressure > 0 and high >= 4 and low >= 6:
            score -= 0.050 * pressure
    else:
        if high >= 1 and low >= 4:
            score -= 0.040
        if gap >= 2 and low >= 5:
            score -= 0.015
        if (
            shape.get("table_format") == "full_ring"
            and shape.get("early_position")
            and low >= 4
        ):
            score -= 0.020
        if shape.get("scenario") in {"facing_raise", "facing_reraise"}:
            score -= 0.025
    if gap == 0:
        score += 0.045
    if high == 0:
        score += 0.055
    if high <= 3 and low <= 4:
        score += 0.055
    if shape.get("squid_pressure") in {"medium", "high"}:
        if high <= 1:
            score += 0.018
        if suited and high == 0:
            score += 0.015
        if shape.get("squid_pressure") == "high" and high >= 5:
            score -= 0.012
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
    return postflop_strategy({"decision": decision}, legal)["buckets"]


def _postflop_hero_bucket(decision: Dict[str, Any]) -> Optional[str]:
    return classify_postflop_hand(decision)


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
    scenario: str,
    spot: Dict[str, Any],
    range_context: Optional[Dict[str, Any]] = None,
) -> str:
    labels = {
        "unopened": "前面无人主动入池：按当前位置展示开池与放弃范围。",
        "heads_up_button": "单挑按钮位首入池：使用宽范围加注，并保留部分跛入混合。",
        "small_blind_first_in": (
            "弃牌到小盲：仅需面对大盲，使用宽补齐/加注混合；"
            "有前注时显著扩大补齐范围。"
        ),
        "free_option": "当前可以免费过牌：强牌主动加注，其余范围保留过牌。",
        "facing_limp": (
            f"面对 {int(spot.get('limper_count') or 1)} 人跟入："
            "按位置与跟入人数收紧隔离加注，中段范围低成本继续。"
        ),
        "facing_raise": "面对一次加注：范围压缩为再加注、跟注与弃牌三档。",
        "facing_reraise": "面对再加注：只保留较强继续范围，边缘牌以弃牌为主。",
    }
    base = labels.get(
        scenario, str(spot.get("action_line") or "当前翻前节点")
    )
    notes = list((range_context or {}).get("notes") or [])
    return f"{base} {'；'.join(notes)}。" if notes else base


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


def _compact_action_response_quality(
    quality: Dict[str, Any],
    selected_players: Optional[set[str]] = None,
) -> Dict[str, Any]:
    compact = {
        key: value
        for key, value in quality.items()
        if key != "groups"
    }
    groups = []
    for group in quality.get("groups") or []:
        row = {
            key: value
            for key, value in group.items()
            if key != "players"
        }
        if selected_players:
            row["players"] = [
                player
                for player in group.get("players") or []
                if str(player.get("user_id") or "") in selected_players
            ]
        groups.append(row)
    compact["groups"] = groups
    return compact


def _compact_strategy_for_llm(
    strategy: Dict[str, Any],
) -> Dict[str, Any]:
    compact = {
        key: strategy.get(key)
        for key in (
            "kind",
            "street",
            "summary",
            "action_mix",
            "spot",
            "legal_actions",
        )
        if strategy.get(key) is not None
    }
    scope_frequencies: Dict[str, Dict[str, Any]] = {}
    if strategy.get("kind") == "preflop_matrix":
        cells = strategy.get("cells") or []
        for scope in ("premium", "strong", "medium", "speculative", "weak"):
            scoped = [cell for cell in cells if cell.get("bucket") == scope]
            if scoped:
                scope_frequencies[scope] = _aggregate_strategy_mix(scoped)
    else:
        scope_frequencies = {
            str(bucket.get("key")): dict(bucket.get("frequencies") or {})
            for bucket in strategy.get("buckets") or []
            if bucket.get("key")
        }
    compact["scope_frequencies"] = {
        "overall": dict(strategy.get("action_mix") or {}),
        **scope_frequencies,
    }
    compact["available_scopes"] = list(compact["scope_frequencies"])
    return compact


def build_prompt(
    context: Dict[str, Any],
    reasoning_depth: str = "light",
    template_id: Optional[str] = None,
) -> str:
    deep = reasoning_depth == "deep"
    template = (
        get_template(template_id, subject="exact_hand")
        if template_id
        else active_template("exact_hand")
    )
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
    )
    remote_context["active_opponent_profiles"] = (
        _remote_opponent_profiles(
            enriched,
            evidence_limit=16 if deep else 6,
        )
    )
    remote_context["local_range_baseline"] = _compact_strategy_for_llm(
        simplified_range_strategy(context)
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
        "若输入包含 local_money_baseline，它是本地确定性数值基线；除非牌型、行动线或真实对手证据"
        "明确支持偏离，否则 recommended_action 应与其 recommended.action 一致。"
        "如有可靠样本偏移，frequency_shifts 应输出可执行的频率转移：scope 必须取自 "
        "local_range_baseline.available_scopes；from_action/to_action 必须取自 legal_actions；"
        "delta_pp 是 1–12 的正数；player 和 evidence 必须对应同一对手。无可靠调整时返回空数组。"
        "若存在 opportunities>=20、confidence 为 medium/high 且 |deviation_pp|>=10 的证据，"
        "只要对应 scope 的 from_action 基线频率大于0，就必须给出至少一项 frequency_shifts；"
        "本地服务还会二次限幅，因此不要用模糊文字替代可执行字段。"
        f"合法动作：{json.dumps(legal_actions, ensure_ascii=False)}\n"
        f"加注到范围：{min_raise} .. {max_raise}\n"
        f"{detail_instruction}"
        f"模板附加约束：{template.instruction_suffix}"
        "输出 JSON schema："
        '{"recommended_action":"fold|check|call|raise|all_in",'
        '"raise_to":null,"confidence":"low|medium|high",'
        '"summary":"一句可执行结论","factors":["最多4条简短理由"],'
        '"risks":["最多2条关键风险"],'
        '"opponent_reads":[{"player":"seat_*","finding":"对手判断","evidence":["evidence_id"]}],'
        '"range_adjustments":[{"adjustment":"剥削调整","evidence":["evidence_id"]}],'
        '"frequency_shifts":[{"player":"seat_*","scope":"available_scope",'
        '"from_action":"fold|check|call|raise|all_in",'
        '"to_action":"fold|check|call|raise|all_in","delta_pp":6,'
        '"reason":"调整原因","evidence":["evidence_id"]}]}\n\n'
        "当前事实：\n"
        + json.dumps(remote_context, ensure_ascii=False, separators=(",", ":"))
    )


def build_exploit_prompt(
    context: Dict[str, Any],
    reasoning_depth: str = "light",
    template_id: Optional[str] = None,
) -> str:
    deep = reasoning_depth == "deep"
    template = (
        get_template(template_id, subject="full_range")
        if template_id
        else active_template("full_range")
    )
    enriched = enrich_reasoning_context(context, include_equity=False)
    strategy = simplified_range_strategy(context)
    compact_strategy = _compact_strategy_for_llm(strategy)
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
        ),
        "context_integrity": enriched.get("context_integrity") or {},
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
        "如建议改变频率，在对应 i 项增加 f={s,x,t,d,z}：s 是 local_range_baseline 的 scope，"
        "x/t 是合法的转出/转入动作，d 是 1–12 的正百分点，z 是简短原因；"
        "player 与 evidence 必须是同一对手。没有可靠数值调整则不要输出 f。"
        "若存在 opportunities>=20、confidence 为 medium/high 且 |deviation_pp|>=10 的证据，"
        "只要该 scope 的 x 基线频率大于0，就必须至少输出一个 f；本地还会二次限幅。"
        + (
            "这是重推理：交叉检查最多16条证据和更长行动线，最多选5个结论；"
            "s 最多120字，r/a 各最多60字。"
            if deep
            else "这是轻推理：最多选2个结论；s 最多35字，r/a 各最多25字。"
        )
        + "若无证据则 i=[]。只输出以下短键 JSON："
        + f"模板附加约束：{template.instruction_suffix}"
        '{"s":"总结","i":[{"p":"seat_*","r":"判断","a":"范围调整",'
        '"f":{"s":"scope","x":"from_action","t":"to_action","d":6,"z":"原因"},'
        '"e":["evidence_id"]}],"c":"low|medium|high"}\n\n'
        "当前事实：\n"
        + json.dumps(remote_context, ensure_ascii=False, separators=(",", ":"))
    )


def build_profile_prompt(context: Dict[str, Any]) -> str:
    return (
        "复核下面的选手画像。所有数字已经由本地算法计算，不要自行补数字。"
        "只保留有样本证据的漏洞；如果范围依赖先验超过 80%，必须在 caveats 中说明。"
        "每个 vulnerability 必须说明街道/位置/面对动作/尺度等适用节点，"
        "并引用至少一个输入中存在的 metric:*、pattern:* 或 case_* evidence_id；"
        "优先用 pattern 的样本率与牌池基线提出假设，再用具体 case 的 hole cards、board、"
        "牌力/听牌、pot odds、SPR 和跨街行动确认或反驳。"
        "不能把单个亮牌案例外推成稳定漏洞，也不能把 non_pfa_open_aggression 一律叫 donk。"
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
        deep_max_completion_tokens: int = 2000,
        deep_reasoning_effort: str = "medium",
        profile_max_completion_tokens: int = 2000,
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
            else 30.0,
        )
        self.max_completion_tokens = max(256, min(max_completion_tokens, 6000))
        self.deep_max_completion_tokens = max(
            self.max_completion_tokens,
            min(deep_max_completion_tokens, 6000),
        )
        self.profile_max_completion_tokens = max(
            self.max_completion_tokens,
            min(profile_max_completion_tokens, 6000),
        )
        self.reasoning_effort = (
            reasoning_effort
            if reasoning_effort in {"low", "medium", "high"}
            else "low"
        )
        self.deep_reasoning_effort = (
            deep_reasoning_effort
            if deep_reasoning_effort in {"medium", "high"}
            else "medium"
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
        profile_timeout = _env_float("WPK_LLM_PROFILE_TIMEOUT_SECONDS", 30.0)
        tokens = _env_int("WPK_LLM_MAX_COMPLETION_TOKENS", 800)
        effort = os.getenv("WPK_LLM_REASONING_EFFORT", "low").strip().lower()
        deep_timeout = _env_float("WPK_LLM_DEEP_TIMEOUT_SECONDS", 20.0)
        deep_tokens = _env_int("WPK_LLM_DEEP_MAX_COMPLETION_TOKENS", 2000)
        profile_tokens = _env_int(
            "WPK_LLM_PROFILE_MAX_COMPLETION_TOKENS",
            2000,
        )
        deep_effort = os.getenv(
            "WPK_LLM_DEEP_REASONING_EFFORT", "medium"
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
            profile_tokens,
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
            "profile_max_completion_tokens": (
                self.profile_max_completion_tokens
            ),
            "deep_max_completion_tokens": self.deep_max_completion_tokens,
            "reasoning_effort": self.reasoning_effort,
            "deep_reasoning_effort": self.deep_reasoning_effort,
        }

    def analyze(
        self,
        context: Dict[str, Any],
        timeout_seconds: Optional[float] = None,
        reasoning_depth: str = "light",
        template_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.configured:
            raise ReasoningError(
                "LLM 未配置：请设置 WPK_LLM_API_KEY 或 MODEL_GATEWAY_KEY"
            )
        deep = reasoning_depth == "deep"
        template = (
            get_template(template_id, subject="exact_hand")
            if template_id
            else active_template("exact_hand")
        )
        user_prompt = build_prompt(
            context,
            reasoning_depth,
            template.template_id,
        )
        parsed, latency_ms = self._complete_json(
            SYSTEM_PROMPT,
            user_prompt,
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
        validated = _validate_result(
            parsed,
            context,
            latency_ms,
            self.model,
            reasoning_depth=reasoning_depth,
        )
        validated.update(
            {
                "template_id": template.template_id,
                "template_hash": template.template_hash,
                "prompt_hash": _prompt_hash(SYSTEM_PROMPT, user_prompt),
                "context_version": CONTEXT_SCHEMA_VERSION,
            }
        )
        return validated

    async def analyze_async(
        self,
        context: Dict[str, Any],
        timeout_seconds: Optional[float] = None,
        reasoning_depth: str = "light",
        template_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.configured:
            raise ReasoningError(
                "LLM 未配置：请设置 WPK_LLM_API_KEY 或 MODEL_GATEWAY_KEY"
            )
        deep = reasoning_depth == "deep"
        template = (
            get_template(template_id, subject="exact_hand")
            if template_id
            else active_template("exact_hand")
        )
        user_prompt = build_prompt(
            context,
            reasoning_depth,
            template.template_id,
        )
        parsed, latency_ms = await self._complete_json_async(
            SYSTEM_PROMPT,
            user_prompt,
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
        validated = _validate_result(
            parsed,
            context,
            latency_ms,
            self.model,
            reasoning_depth=reasoning_depth,
        )
        validated.update(
            {
                "template_id": template.template_id,
                "template_hash": template.template_hash,
                "prompt_hash": _prompt_hash(SYSTEM_PROMPT, user_prompt),
                "context_version": CONTEXT_SCHEMA_VERSION,
            }
        )
        return validated

    def analyze_exploit(
        self,
        context: Dict[str, Any],
        timeout_seconds: Optional[float] = None,
        reasoning_depth: str = "light",
        template_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.configured:
            raise ReasoningError(
                "LLM 未配置：请设置 WPK_LLM_API_KEY 或 MODEL_GATEWAY_KEY"
            )
        deep = reasoning_depth == "deep"
        template = (
            get_template(template_id, subject="full_range")
            if template_id
            else active_template("full_range")
        )
        user_prompt = build_exploit_prompt(
            context,
            reasoning_depth,
            template.template_id,
        )
        parsed, latency_ms = self._complete_json(
            EXPLOIT_SYSTEM_PROMPT,
            user_prompt,
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
        validated = _validate_exploit_result(
            parsed,
            context,
            latency_ms,
            self.model,
            reasoning_depth=reasoning_depth,
        )
        validated.update(
            {
                "template_id": template.template_id,
                "template_hash": template.template_hash,
                "prompt_hash": _prompt_hash(
                    EXPLOIT_SYSTEM_PROMPT,
                    user_prompt,
                ),
                "context_version": CONTEXT_SCHEMA_VERSION,
            }
        )
        return validated

    async def analyze_exploit_async(
        self,
        context: Dict[str, Any],
        timeout_seconds: Optional[float] = None,
        reasoning_depth: str = "light",
        template_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.configured:
            raise ReasoningError(
                "LLM 未配置：请设置 WPK_LLM_API_KEY 或 MODEL_GATEWAY_KEY"
            )
        deep = reasoning_depth == "deep"
        template = (
            get_template(template_id, subject="full_range")
            if template_id
            else active_template("full_range")
        )
        user_prompt = build_exploit_prompt(
            context,
            reasoning_depth,
            template.template_id,
        )
        parsed, latency_ms = await self._complete_json_async(
            EXPLOIT_SYSTEM_PROMPT,
            user_prompt,
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
        validated = _validate_exploit_result(
            parsed,
            context,
            latency_ms,
            self.model,
            reasoning_depth=reasoning_depth,
        )
        validated.update(
            {
                "template_id": template.template_id,
                "template_hash": template.template_hash,
                "prompt_hash": _prompt_hash(
                    EXPLOIT_SYSTEM_PROMPT,
                    user_prompt,
                ),
                "context_version": CONTEXT_SCHEMA_VERSION,
            }
        )
        return validated

    def analyze_profile(
        self, context: Dict[str, Any], timeout_seconds: Optional[float] = None
    ) -> Dict[str, Any]:
        if not self.configured:
            raise ReasoningError(
                "LLM 未配置：请设置 WPK_LLM_API_KEY 或 MODEL_GATEWAY_KEY"
            )
        profile_prompt = build_profile_prompt(context)
        total_timeout = min(
            self.profile_timeout_seconds,
            timeout_seconds or self.profile_timeout_seconds,
        )
        started = time.perf_counter()
        first_timeout = min(
            22.0,
            max(0.5, total_timeout * 0.7),
        )
        try:
            parsed, latency_ms = self._complete_json(
                PROFILE_SYSTEM_PROMPT,
                profile_prompt,
                first_timeout,
                "wpk-profile",
                timeout_cap=first_timeout,
                max_completion_tokens=self.profile_max_completion_tokens,
                json_mode=True,
                reasoning_effort=self.reasoning_effort,
            )
        except _ModelJSONError as first_error:
            elapsed = time.perf_counter() - started
            remaining = total_timeout - elapsed
            if remaining < 1.0:
                raise ReasoningError(
                    _profile_json_failure_message(first_error)
                ) from first_error
            retry_prompt = (
                profile_prompt
                + "\n\n上次输出未形成完整 JSON。请缩短文字，只返回 schema 中的字段；"
                "vulnerabilities 最多 3 条，每条 finding 最多一句。"
            )
            try:
                parsed, retry_latency_ms = self._complete_json(
                    PROFILE_SYSTEM_PROMPT,
                    retry_prompt,
                    remaining,
                    "wpk-profile-retry",
                    timeout_cap=remaining,
                    max_completion_tokens=self.profile_max_completion_tokens,
                    json_mode=True,
                    reasoning_effort="low",
                )
            except _ModelJSONError as retry_error:
                raise ReasoningError(
                    _profile_json_failure_message(retry_error, retried=True)
                ) from retry_error
            latency_ms = first_error.latency_ms + retry_latency_ms
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
            choice = envelope["choices"][0]
            content = choice["message"]["content"]
            finish_reason = str(choice.get("finish_reason") or "unknown")
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as error:
            raise ReasoningError("LLM 网关响应格式无法识别") from error
        try:
            parsed = _parse_model_json(str(content or ""))
        except ReasoningError as error:
            raise _ModelJSONError(
                finish_reason,
                latency_ms,
            ) from error
        return parsed, latency_ms

    async def _complete_json_async(
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
        cap = timeout_cap or self.timeout_seconds
        timeout = min(cap, timeout_seconds or cap)
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    self.endpoint,
                    json=request_payload,
                    headers={
                        "Content-Type": "application/json",
                        "api-key": self.api_key,
                        "X-TT-LOGID": f"{log_prefix}-{time.time_ns()}",
                    },
                )
                response.raise_for_status()
                body = response.content
        except httpx.HTTPStatusError as error:
            raise ReasoningError(
                f"LLM 网关返回 HTTP {error.response.status_code}"
            ) from error
        except (httpx.RequestError, TimeoutError, OSError) as error:
            raise ReasoningError("LLM 网关连接失败或超时") from error
        latency_ms = round((time.perf_counter() - started) * 1000)
        try:
            envelope = json.loads(body)
            choice = envelope["choices"][0]
            content = choice["message"]["content"]
            finish_reason = str(choice.get("finish_reason") or "unknown")
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as error:
            raise ReasoningError("LLM 网关响应格式无法识别") from error
        try:
            parsed = _parse_model_json(str(content or ""))
        except ReasoningError as error:
            raise _ModelJSONError(
                finish_reason,
                latency_ms,
            ) from error
        return parsed, latency_ms


def _decision_row(
    connection: sqlite3.Connection, sequence: Optional[int]
) -> Optional[Tuple[int, str, Optional[str], str]]:
    if sequence is None:
        return connection.execute(
            """
            SELECT sequence, timestamp, hand_id, payload_json
            FROM raw_events
            WHERE event_name IN ('userOptNotify', 'roundChangeNotify')
            ORDER BY sequence DESC LIMIT 1
            """
        ).fetchone()
    return connection.execute(
        """
        SELECT sequence, timestamp, hand_id, payload_json
        FROM raw_events
        WHERE event_name IN ('userOptNotify', 'roundChangeNotify')
          AND sequence = ?
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
    decision_subject: str,
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
        if decision_subject == "observer":
            return False, "观战节点默认显示本地全范围，可手动调用 LLM 复核"
        return False, "本人底牌不完整，只能提供全范围建议"
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
    frequency_shifts = _validate_frequency_shifts(
        result.get("frequency_shifts"),
        context,
        confidence,
        6 if deep else 3,
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
        "frequency_shifts": frequency_shifts,
        "evidence_details": _exploit_evidence_details(
            context,
            [*opponent_reads, *range_adjustments, *frequency_shifts],
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
            "frequency_shifts": [
                {
                    "player": item.get("p"),
                    "scope": frequency.get("s"),
                    "from_action": frequency.get("x"),
                    "to_action": frequency.get("t"),
                    "delta_pp": frequency.get("d"),
                    "reason": frequency.get("z"),
                    "evidence": item.get("e"),
                }
                for item in compact_items
                if isinstance(item, dict)
                and isinstance(
                    (frequency := item.get("f")),
                    dict,
                )
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
    frequency_shifts = _validate_frequency_shifts(
        result.get("frequency_shifts"),
        context,
        confidence,
        5 if deep else 2,
    )
    return {
        "summary": str(result.get("summary") or "").strip()[
            :600 if deep else 400
        ],
        "opponent_reads": opponent_reads,
        "range_adjustments": range_adjustments,
        "frequency_shifts": frequency_shifts,
        "evidence_details": _exploit_evidence_details(
            context,
            [*opponent_reads, *range_adjustments, *frequency_shifts],
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
            item["display_player"] = _opponent_display_names(context).get(
                player,
                player,
            )
        items.append(item)
        if len(items) >= limit:
            break
    return items


def _opponent_display_names(
    context: Dict[str, Any],
) -> Dict[str, str]:
    return {
        str(profile.get("player")): str(
            profile.get("display_name") or profile.get("player")
        )
        for profile in context.get("active_opponent_profiles") or []
        if profile.get("player")
    }


def _validate_frequency_shifts(
    value: Any,
    context: Dict[str, Any],
    result_confidence: str,
    limit: int,
) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    profiles = context.get("active_opponent_profiles") or []
    profile_by_player = {
        str(profile.get("player")): profile
        for profile in profiles
        if profile.get("player")
    }
    metric_by_evidence = {
        str(metric.get("evidence_id")): (str(profile.get("player")), metric)
        for profile in profiles
        for metric in profile.get("observations") or []
        if metric.get("evidence_id")
    }
    strategy = simplified_range_strategy(context)
    allowed_scopes = set(
        _compact_strategy_for_llm(strategy).get("available_scopes") or []
    )
    legal_actions = set(strategy.get("legal_actions") or [])
    display_names = _opponent_display_names(context)
    result_cap = {"low": 4.0, "medium": 8.0, "high": 12.0}.get(
        result_confidence,
        4.0,
    )
    confidence_cap = {
        "very_low": 0.0,
        "low": 4.0,
        "medium": 8.0,
        "high": 12.0,
    }
    shifts: List[Dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        evidence = [
            str(reference)
            for reference in raw.get("evidence") or []
            if str(reference) in metric_by_evidence
        ][:4]
        if not evidence:
            continue
        player = str(raw.get("player") or "")
        if player not in profile_by_player:
            player = metric_by_evidence[evidence[0]][0]
        evidence = [
            reference
            for reference in evidence
            if metric_by_evidence[reference][0] == player
        ]
        if not evidence:
            continue
        scope = str(raw.get("scope") or "")
        from_action = _normalize_action(raw.get("from_action"))
        to_action = _normalize_action(raw.get("to_action"))
        requested = abs(_number(raw.get("delta_pp")) or 0.0)
        reason = str(raw.get("reason") or "").strip()[:240]
        if (
            scope not in allowed_scopes
            or from_action not in legal_actions
            or to_action not in legal_actions
            or from_action == to_action
            or requested < 1
            or not reason
        ):
            continue
        evidence_caps = []
        for reference in evidence:
            metric = metric_by_evidence[reference][1]
            opportunities = int(metric.get("opportunities") or 0)
            sample_cap = (
                0.0
                if opportunities < 10
                else 4.0
                if opportunities < 20
                else 8.0
                if opportunities < 40
                else 12.0
            )
            evidence_caps.append(
                min(
                    sample_cap,
                    confidence_cap.get(
                        str(metric.get("confidence") or "very_low"),
                        0.0,
                    ),
                )
            )
        delta = min([requested, result_cap, *evidence_caps])
        if delta < 1:
            continue
        shifts.append(
            {
                "player": player,
                "display_player": display_names.get(player, player),
                "scope": scope,
                "from_action": from_action,
                "to_action": to_action,
                "requested_delta_pp": round(requested, 1),
                "delta_pp": round(delta, 1),
                "reason": reason,
                "evidence": evidence,
            }
        )
        if len(shifts) >= limit:
            break
    return shifts


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
                    "display_player": (
                        profile.get("display_name")
                        or profile.get("player")
                    ),
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
    for key in ("postflop_patterns", "revealed_pattern_summary"):
        allowed_evidence.update(
            str(item.get("evidence_id"))
            for item in (context.get("evidence_cases") or {}).get(key) or []
            if item.get("evidence_id")
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


def _profile_json_failure_message(
    error: _ModelJSONError,
    *,
    retried: bool = False,
) -> str:
    if error.finish_reason == "length":
        return (
            "LLM 画像输出被 token 上限截断"
            + ("，自动重试后仍不完整" if retried else "")
        )
    if error.finish_reason == "content_filter":
        return "LLM 画像输出被内容过滤器终止"
    return (
        "LLM 自动重试后仍没有返回合法 JSON"
        if retried
        else "LLM 没有返回合法 JSON"
    )


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


def _prompt_hash(system_prompt: str, user_prompt: str) -> str:
    return hashlib.sha256(
        f"{system_prompt}\n\n{user_prompt}".encode("utf-8")
    ).hexdigest()


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


def _llm_history_action(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    text = {"allin": "all_in"}.get(text, text)
    return (
        text
        if text
        in {
            "ante",
            "small_blind",
            "big_blind",
            "straddle",
            "fold",
            "check",
            "call",
            "bet",
            "raise",
            "all_in",
        }
        else None
    )


def _context_integrity(
    decision: Optional[Dict[str, Any]],
    action_history: List[Dict[str, Any]],
) -> Dict[str, Any]:
    decision = decision or {}
    is_observer = decision.get("decision_subject") == "observer"
    cards = list(decision.get("hero_cards") or [])
    board = list(decision.get("board") or [])
    return {
        "complete_action_history": True,
        "action_count": len(action_history),
        "board_complete_through_node": True,
        "board_card_count": len(board),
        "hole_cards_required": not is_observer,
        "hole_cards_included": len(cards) == 2,
        "observer_cards_omitted": is_observer and not cards,
    }


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


def _player_effective_stack_bb(
    decision: Dict[str, Any],
    seat: Any,
) -> Optional[float]:
    player = next(
        (
            item
            for item in decision.get("players") or []
            if isinstance(item, dict) and item.get("seat") == seat
        ),
        {},
    )
    effective_stack = _number(player.get("effective_stack_to_hero"))
    big_blind = _number(decision.get("big_blind"))
    if (
        effective_stack is not None
        and big_blind is not None
        and big_blind > 0
    ):
        return effective_stack / big_blind
    return _number(decision.get("hero_stack_bb"))


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
