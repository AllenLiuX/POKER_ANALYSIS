from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from .models import HandHistory


SUITS = {"s": "♠", "h": "♥", "c": "♣", "d": "♦"}
ACTION_LABELS = {
    "small_blind": "小盲",
    "big_blind": "大盲",
    "ante": "前注",
    "straddle": "抓头",
    "raise": "加注",
    "bet": "下注",
    "call": "跟注",
    "check": "过牌",
    "fold": "弃牌",
    "allin": "全下",
    "all-in": "全下",
}
STREET_LABELS = {
    "preflop": "翻前",
    "flop": "翻牌",
    "turn": "转牌",
    "river": "河牌",
    "showdown": "摊牌",
}


def display_card(card: str) -> str:
    if len(card) < 2:
        return card
    return f"{card[:-1]}{SUITS.get(card[-1].lower(), card[-1])}"


def display_cards(cards: Iterable[str]) -> str:
    return " ".join(display_card(card) for card in cards)


def action_label(action: str) -> str:
    return ACTION_LABELS.get(action.lower(), action)


def render_event(event: Dict[str, Any]) -> Optional[str]:
    kind = event.get("event")
    if kind == "start":
        blinds = ""
        if event.get("small_blind") is not None and event.get("big_blind") is not None:
            blinds = f" 盲注 {event['small_blind']:g}/{event['big_blind']:g}"
        return f"\n牌局 #{event.get('hand_id', '未知')}{blinds}"
    if kind == "player":
        cards = display_cards(event.get("cards") or [])
        suffix = f" [{cards}]" if cards else ""
        return f"座位 {event.get('seat', '?')} · {event.get('alias') or '未知玩家'}{suffix}"
    if kind == "action":
        actor = event.get("alias") or (
            f"座位 {event['seat']}" if event.get("seat") is not None else "未知玩家"
        )
        amount = ""
        if event.get("amount") is not None:
            amount = f" {event['amount']:g}"
        if event.get("amount_to") is not None:
            amount += f"（到 {event['amount_to']:g}）"
        street = STREET_LABELS.get(str(event.get("street")), str(event.get("street") or ""))
        return f"{street} · {actor}：{action_label(str(event.get('action', '')))}{amount}"
    if kind == "board":
        cards = display_cards(event.get("append_cards") or event.get("board") or [])
        if cards:
            street = STREET_LABELS.get(str(event.get("street")), str(event.get("street") or "发牌"))
            return f"{street}：[ {cards} ]  底池 {event.get('pot', '?')}"
    if kind == "squid":
        return (
            f"鱿鱼 · 阶段 {event.get('scene')} · "
            f"参与 {len(event.get('users') or [])} 人 · 轮次 {event.get('round_id')}"
        )
    return None


def render_hand_text(hand: HandHistory) -> str:
    lines = [f"WPK 牌局 #{hand.hand_id}"]
    if hand.table_id:
        lines.append(f"牌桌：{hand.table_id}")
    if hand.small_blind is not None and hand.big_blind is not None:
        lines.append(f"盲注：{hand.small_blind:g}/{hand.big_blind:g}")
    for player in hand.players.values():
        stack = f"（{player.stack_start:g}）" if player.stack_start is not None else ""
        cards = f" [ {display_cards(player.hole_cards)} ]" if player.hole_cards else ""
        hero = "（我）" if player.is_hero else ""
        lines.append(f"座位 {player.seat}：{player.alias or '未知玩家'}{hero}{stack}{cards}")
    current_street = None
    for action in hand.actions:
        if action.street != current_street:
            current_street = action.street
            lines.append(f"\n--- {STREET_LABELS.get(current_street, current_street)} ---")
            if current_street != "preflop" and hand.board:
                shown = {"flop": 3, "turn": 4, "river": 5}.get(
                    current_street, len(hand.board)
                )
                lines.append(f"公共牌：[ {display_cards(hand.board[:shown])} ]")
        actor = action.player or (
            f"座位 {action.seat}" if action.seat is not None else "未知玩家"
        )
        amount = f" {action.amount:g}" if action.amount is not None else ""
        amount_to = f"（到 {action.amount_to:g}）" if action.amount_to is not None else ""
        lines.append(f"{actor}：{action_label(action.action)}{amount}{amount_to}")
    if hand.pot is not None:
        lines.append(f"\n总底池：{hand.pot:g}")
    for player in hand.players.values():
        if player.net is not None:
            lines.append(f"{player.alias or f'座位 {player.seat}'}：净额 {player.net:+g}")
    if hand.warnings:
        lines.extend(f"警告：{warning}" for warning in hand.warnings)
    return "\n".join(lines) + "\n"
