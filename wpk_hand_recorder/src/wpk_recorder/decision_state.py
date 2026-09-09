from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Tuple

from .features import derive_positions, project_hand_state
from .models import DecisionRequest, HandHistory


TARGET_SMALL_BLIND = 2.0
TARGET_BIG_BLIND = 4.0
TARGET_ANTE = 1.0
TARGET_TABLE_SIZES = {8, 9}
LEGAL_ACTIONS = {"fold", "check", "call", "raise", "all_in"}
DEFAULT_DECISION_TTL_SECONDS = 15.0


@dataclass(frozen=True)
class DecisionPlayerState:
    seat: int
    user_id: Optional[str]
    position: Optional[str]
    stack: Optional[float]
    street_contribution: float
    total_contribution: float
    effective_stack_to_hero: Optional[float]
    active: bool
    folded: bool
    all_in: bool
    is_hero: bool


@dataclass(frozen=True)
class DecisionState:
    """Immutable, versioned contract shared by live and replay strategy paths."""

    contract_version: str
    decision_id: str
    sequence: int
    hand_id: str
    decision_source: str
    captured_at: Optional[str]
    street: str
    acting_seat: Optional[int]
    hero_cards: Tuple[str, ...]
    hero_position: Optional[str]
    legal_actions: Tuple[str, ...]
    board: Tuple[str, ...]
    pot: Optional[float]
    reconstructed_pot: float
    pot_delta: Optional[float]
    side_pots: Tuple[Tuple[float, Tuple[int, ...]], ...]
    players: Tuple[DecisionPlayerState, ...]
    action_order: Tuple[int, ...]
    call_score: float
    min_raise_to: Optional[float]
    max_raise_to: Optional[float]
    hero_street_contribution: Optional[float]
    hero_stack: Optional[float]
    hero_stack_bb: Optional[float]
    small_blind: Optional[float]
    big_blind: Optional[float]
    ante: Optional[float]
    players_in_hand: int
    table_players: int
    game_mode: str
    squid_round_id: Optional[str]
    rake: float
    insurance: float
    fund: float
    remaining_ms: int
    state_hash: str

    def as_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        for key in ("hero_cards", "legal_actions", "board"):
            data[key] = list(data[key])
        data["players"] = [asdict(player) for player in self.players]
        data["action_order"] = list(self.action_order)
        data["side_pots"] = [
            {"amount": amount, "eligible_seats": list(eligible)}
            for amount, eligible in self.side_pots
        ]
        return data


def decision_state_contract(decision: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the canonical state plus separate correctness/support gates."""

    canonical = _canonical_fields(decision)
    hash_fields = dict(canonical)
    hash_fields["players"] = [
        asdict(player) for player in canonical["players"]
    ]
    # Countdown changes while the poker state does not; keep the hash stable.
    hash_fields.pop("remaining_ms", None)
    hash_fields.pop("captured_at", None)
    hash_fields.pop("decision_source", None)
    encoded = json.dumps(
        hash_fields, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    state_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]
    state = DecisionState(
        contract_version="decision-state-v1",
        state_hash=state_hash,
        **canonical,
    )
    blocking, warnings = _quality_reasons(state)
    target_mismatches = _target_mismatches(state)
    quality = {
        "valid": not blocking,
        "exact_strategy_ready": not blocking and len(state.hero_cards) == 2,
        "target_game_supported": (
            not blocking
            and len(state.hero_cards) == 2
            and not target_mismatches
        ),
        "money_ev_ready": (
            not blocking
            and not warnings
            and len(state.hero_cards) == 2
            and not target_mismatches
        ),
        "baseline_only": not blocking and bool(warnings or target_mismatches),
        "blocking_reasons": blocking,
        "warnings": warnings,
        "target_mismatches": target_mismatches,
    }
    return {**state.as_dict(), "quality": quality}


def canonical_legal_actions(values: Any) -> Tuple[str, ...]:
    """Normalize server actions and remove fold when a free check exists."""

    actions = tuple(
        action
        for action in (
            str(value or "").strip().lower().replace("-", "_")
            for value in values or []
        )
        if action in LEGAL_ACTIONS
    )
    if "check" in actions:
        actions = tuple(action for action in actions if action != "fold")
    return actions


def decision_state_from_hand(
    hand: HandHistory,
    request: Optional[DecisionRequest] = None,
    *,
    source: str = "live",
    now: Optional[datetime] = None,
    remaining_ms: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Build the canonical decision contract from a replayed HandHistory."""

    request = request or hand.pending_decision
    if request is None:
        return None
    projected = project_hand_state(hand)
    positions = derive_positions(hand)
    acting_seat = request.seat
    if acting_seat is None:
        acting_seat = next(
            (
                seat
                for seat, player in hand.players.items()
                if player.is_hero
                or (request.user_id and player.user_id == request.user_id)
            ),
            None,
        )
    actor = hand.players.get(acting_seat) if acting_seat is not None else None
    actor_stack = request.current_score
    if actor_stack is None and acting_seat is not None:
        actor_stack = projected.stacks.get(acting_seat)
    if actor_stack is None and actor is not None:
        actor_stack = actor.stack_end
        if actor_stack is None:
            actor_stack = actor.stack_start
    actor_contribution = (
        request.seat_score
        if request.seat_score is not None
        else projected.street_contributions.get(acting_seat, 0.0)
    )
    players = []
    for seat in sorted(hand.players):
        player = hand.players[seat]
        stack = projected.stacks.get(seat)
        if stack is None:
            stack = player.stack_end
        if stack is None:
            stack = player.stack_start
        players.append(
            {
                "seat": seat,
                "user_id": player.user_id,
                "position": positions.get(seat) or player.position,
                "stack": stack,
                "street_contribution": projected.street_contributions.get(
                    seat, 0.0
                ),
                "total_contribution": projected.total_contributions.get(
                    seat, 0.0
                ),
                "effective_stack_to_hero": (
                    min(float(actor_stack), float(stack))
                    if actor_stack is not None and stack is not None
                    else None
                ),
                "active": seat in projected.active_seats,
                "folded": seat in projected.folded_seats,
                "all_in": seat in projected.active_seats
                and stack is not None
                and stack <= 0,
                "is_hero": seat == acting_seat,
            }
        )
    pot = float(hand.pot) if hand.pot is not None else projected.reconstructed_pot
    resolved_remaining_ms = (
        max(0, int(remaining_ms))
        if remaining_ms is not None
        else _request_remaining_ms(request, now)
    )
    state = {
        "decision_id": f"{hand.hand_id}:{request.event_sequence}",
        "sequence": request.event_sequence,
        "hand_id": hand.hand_id,
        "decision_source": source,
        "captured_at": request.captured_at,
        "street": projected.street,
        "acting_seat": acting_seat,
        "hero_cards": request.cards
        or (actor.hole_cards if actor is not None else []),
        "hero_position": (
            positions.get(acting_seat) or actor.position
            if actor is not None
            else positions.get(acting_seat)
        ),
        "legal_actions": request.legal_actions,
        "board": list(hand.board),
        "pot": pot,
        "reconstructed_pot": projected.reconstructed_pot,
        "pot_delta": pot - projected.reconstructed_pot,
        "side_pots": projected.side_pots,
        "players": players,
        "action_order": _action_order(
            projected.active_seats, positions, projected.street
        ),
        "call_score": request.call_score,
        "min_raise_to": request.min_raise_to,
        "max_raise_to": request.max_raise_to,
        "hero_street_contribution": actor_contribution,
        "hero_stack": actor_stack,
        "hero_stack_bb": (
            float(actor_stack) / float(hand.big_blind)
            if actor_stack is not None and hand.big_blind
            else None
        ),
        "small_blind": hand.small_blind,
        "big_blind": hand.big_blind,
        "ante": hand.ante,
        "players_in_hand": len(projected.active_seats),
        "table_players": len(hand.players),
        "game_mode": hand.game_mode,
        "squid_round_id": hand.squid_round_id,
        "rake": 0.0,
        "insurance": 0.0,
        "fund": 0.0,
        "remaining_ms": resolved_remaining_ms,
    }
    return decision_state_contract(state)


def _canonical_fields(decision: Mapping[str, Any]) -> Dict[str, Any]:
    legal = canonical_legal_actions(decision.get("legal_actions"))
    players = tuple(
        DecisionPlayerState(
            seat=_integer(item.get("seat")),
            user_id=_optional_text(item.get("user_id")),
            position=_optional_text(item.get("position")),
            stack=_number(item.get("stack")),
            street_contribution=_number(
                item.get("street_contribution")
            )
            or 0.0,
            total_contribution=_number(item.get("total_contribution")) or 0.0,
            effective_stack_to_hero=_number(
                item.get("effective_stack_to_hero")
            ),
            active=bool(item.get("active")),
            folded=bool(item.get("folded")),
            all_in=bool(item.get("all_in")),
            is_hero=bool(item.get("is_hero")),
        )
        for item in decision.get("players") or []
        if isinstance(item, Mapping) and _integer(item.get("seat")) > 0
    )
    pot = _number(decision.get("pot"))
    reconstructed_pot = _number(decision.get("reconstructed_pot")) or 0.0
    return {
        "decision_id": str(decision.get("decision_id") or ""),
        "sequence": _integer(decision.get("sequence")),
        "hand_id": str(decision.get("hand_id") or ""),
        "decision_source": str(decision.get("decision_source") or "live"),
        "captured_at": _optional_text(decision.get("captured_at")),
        "street": str(decision.get("street") or ""),
        "acting_seat": (
            _integer(decision.get("acting_seat"))
            if decision.get("acting_seat") is not None
            else None
        ),
        "hero_cards": tuple(str(card) for card in decision.get("hero_cards") or []),
        "hero_position": _optional_text(decision.get("hero_position")),
        "legal_actions": legal,
        "board": tuple(str(card) for card in decision.get("board") or []),
        "pot": pot,
        "reconstructed_pot": reconstructed_pot,
        "pot_delta": (
            _number(decision.get("pot_delta"))
            if decision.get("pot_delta") is not None
            else (
                pot - reconstructed_pot
                if pot is not None and reconstructed_pot > 0
                else None
            )
        ),
        "side_pots": tuple(
            (
                _number(item.get("amount")) or 0.0,
                tuple(
                    _integer(seat)
                    for seat in item.get("eligible_seats") or []
                ),
            )
            for item in decision.get("side_pots") or []
            if isinstance(item, dict)
        ),
        "players": players,
        "action_order": tuple(
            _integer(seat)
            for seat in decision.get("action_order") or []
            if _integer(seat) > 0
        ),
        "call_score": _number(decision.get("call_score")) or 0.0,
        "min_raise_to": _number(decision.get("min_raise_to")),
        "max_raise_to": _number(decision.get("max_raise_to")),
        "hero_street_contribution": _number(
            decision.get("hero_street_contribution")
        ),
        "hero_stack": _number(decision.get("hero_stack")),
        "hero_stack_bb": _number(decision.get("hero_stack_bb")),
        "small_blind": _number(decision.get("small_blind")),
        "big_blind": _number(decision.get("big_blind")),
        "ante": _number(decision.get("ante")),
        "players_in_hand": _integer(decision.get("players_in_hand")),
        "table_players": _integer(decision.get("table_players")),
        "game_mode": str(decision.get("game_mode") or ""),
        "squid_round_id": _optional_text(decision.get("squid_round_id")),
        "rake": _number(decision.get("rake")) or 0.0,
        "insurance": _number(decision.get("insurance")) or 0.0,
        "fund": _number(decision.get("fund")) or 0.0,
        "remaining_ms": max(0, _integer(decision.get("remaining_ms"))),
    }


def _quality_reasons(state: DecisionState) -> Tuple[list, list]:
    blocking = []
    warnings = []
    if not state.decision_id or state.sequence <= 0 or not state.hand_id:
        blocking.append("行动节点缺少稳定标识")
    if state.street not in {"preflop", "flop", "turn", "river"}:
        blocking.append("街道无法识别")
    expected_board = {"preflop": 0, "flop": 3, "turn": 4, "river": 5}.get(
        state.street
    )
    if expected_board is not None and len(state.board) != expected_board:
        blocking.append(
            f"{state.street} 公共牌应为 {expected_board} 张，实际 {len(state.board)} 张"
        )
    if not state.legal_actions:
        blocking.append("合法动作列表为空")
    if len(state.legal_actions) != len(set(state.legal_actions)):
        warnings.append("合法动作列表包含重复项")
    if len(set(state.hero_cards + state.board)) != len(
        state.hero_cards + state.board
    ):
        blocking.append("底牌与公共牌存在重复")
    if state.pot is None or state.pot < 0:
        blocking.append("底池缺失或为负数")
    side_pot_total = sum(amount for amount, _ in state.side_pots)
    if (
        state.pot is not None
        and side_pot_total > 0
        and abs(side_pot_total - state.pot) > max(0.02, state.big_blind or 0)
    ):
        warnings.append("主/边池重建值与服务端底池不完全一致")
    if state.call_score < 0:
        blocking.append("跟注额为负数")
    if (
        state.min_raise_to is not None
        and state.max_raise_to is not None
        and state.min_raise_to > state.max_raise_to
    ):
        if "raise" in state.legal_actions:
            blocking.append("最小加注额高于最大加注额")
        elif "all_in" in state.legal_actions:
            warnings.append("剩余筹码不足常规最小加注，仅允许不足额全下")
    if state.hero_stack is None or state.hero_stack < 0:
        blocking.append("本人剩余筹码缺失或为负数")
    if not state.players:
        warnings.append("缺少逐玩家筹码与投入快照")
    elif state.acting_seat is None:
        blocking.append("行动者座位缺失")
    else:
        actor = next(
            (player for player in state.players if player.seat == state.acting_seat),
            None,
        )
        if actor is None or not actor.active:
            blocking.append("行动者不在当前有效玩家中")
    if len(state.action_order) != len(set(state.action_order)):
        blocking.append("行动顺序包含重复座位")
    if any(
        amount < 0 or not eligible for amount, eligible in state.side_pots
    ):
        blocking.append("主/边池金额或可赢取玩家异常")
    if (
        state.pot_delta is not None
        and state.reconstructed_pot > 0
        and abs(state.pot_delta) > max(0.02, state.big_blind or 0)
    ):
        warnings.append("动作投入重建底池与服务端底池不守恒")
    if state.players_in_hand <= 0 or state.players_in_hand > state.table_players:
        blocking.append("在局人数与桌上人数不一致")
    if len(state.hero_cards) not in {0, 2}:
        blocking.append("本人底牌数量异常")
    if state.remaining_ms == 0:
        warnings.append("行动倒计时未知或已经结束")
    if state.rake < 0 or state.insurance < 0 or state.fund < 0:
        blocking.append("费用字段不能为负数")
    return blocking, warnings


def _target_mismatches(state: DecisionState) -> list:
    mismatches = []
    if state.game_mode != "squid":
        mismatches.append("当前不是鱿鱼模式")
    if state.table_players not in TARGET_TABLE_SIZES:
        mismatches.append("目标模型只校准 8/9 人桌")
    if state.small_blind is not None and state.small_blind != TARGET_SMALL_BLIND:
        mismatches.append("目标模型要求小盲为 2")
    if state.big_blind != TARGET_BIG_BLIND:
        mismatches.append("目标模型要求大盲为 4")
    if state.ante != TARGET_ANTE:
        mismatches.append("目标模型要求每人 ante 为 1")
    if state.hero_stack_bb is not None and state.hero_stack_bb < 80:
        mismatches.append("目标模型尚未校准 80bb 以下筹码")
    return mismatches


def _request_remaining_ms(
    request: DecisionRequest, now: Optional[datetime]
) -> int:
    countdown = max(0.0, float(request.countdown or 0.0))
    if countdown > 300:
        countdown /= 1000.0
    if countdown <= 0:
        countdown = DEFAULT_DECISION_TTL_SECONDS
    try:
        captured = datetime.fromisoformat(request.captured_at.replace("Z", "+00:00"))
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=timezone.utc)
    except (AttributeError, TypeError, ValueError):
        return 0
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(0, int((captured.timestamp() + countdown - current.timestamp()) * 1000))


def _action_order(
    active_seats: set, positions: Mapping[int, str], street: str
) -> list:
    preflop = {
        "UTG": 0,
        "UTG+1": 1,
        "MP": 2,
        "MP+1": 3,
        "HJ": 4,
        "CO": 5,
        "BTN": 6,
        "BTN/SB": 6,
        "SB": 7,
        "BB": 8,
    }
    postflop = {
        "SB": 0,
        "BB": 1,
        "UTG": 2,
        "UTG+1": 3,
        "MP": 4,
        "MP+1": 5,
        "HJ": 6,
        "CO": 7,
        "BTN": 8,
        "BTN/SB": 8,
    }
    ranks = preflop if street == "preflop" else postflop
    return sorted(
        active_seats,
        key=lambda seat: (ranks.get(positions.get(seat, ""), 99), seat),
    )


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _number(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None
