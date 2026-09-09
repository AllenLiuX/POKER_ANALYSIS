from __future__ import annotations

from typing import List, Tuple

from .models import HandHistory


RAKE_BALANCE_TOLERANCE = 10.0


def assess_hand(
    hand: HandHistory, stale_in_progress: bool = False
) -> Tuple[str, List[str], bool]:
    reasons: List[str] = []
    if hand.status == "in_progress":
        if stale_in_progress:
            return "partial", ["录制中断，牌局没有完整结算"], True
        return "live", [], True
    if hand.status in {"interrupted", "partial"}:
        return "partial", ["录制中断，牌局没有完整结算"], True

    if len(hand.board) not in {0, 3, 4, 5}:
        reasons.append(f"公共牌数量异常：{len(hand.board)}")
    if not hand.actions:
        reasons.append("没有识别到行动")
    sequences = [action.sequence for action in hand.actions]
    if sequences != list(range(1, len(sequences) + 1)):
        reasons.append("行动序号不连续")
    street_order = {"preflop": 0, "flop": 1, "turn": 2, "river": 3, "showdown": 4}
    streets = [street_order.get(action.street, 0) for action in hand.actions]
    if any(current < previous for previous, current in zip(streets, streets[1:])):
        reasons.append("行动街道顺序逆序，疑似混入其他手牌")
    action_ids = [action.action_id for action in hand.actions if action.action_id]
    if len(action_ids) != len(set(action_ids)):
        reasons.append("检测到重复 action ID")
    if any((action.amount or 0) < 0 for action in hand.actions):
        reasons.append("检测到负数下注额")
    required_board = max(
        (
            {"preflop": 0, "flop": 3, "turn": 4, "river": 5}.get(
                action.street, 0
            )
            for action in hand.actions
        ),
        default=0,
    )
    if len(hand.board) < required_board:
        reasons.append(
            f"行动已到后续街道，但公共牌只有 {len(hand.board)} 张"
        )
    nets = [player.net for player in hand.players.values() if player.net is not None]
    insurance = sum(player.insurance_result or 0 for player in hand.players.values())
    funds = sum(player.fund or 0 for player in hand.players.values())
    adjusted_balance = sum(nets) - insurance + funds
    if len(nets) >= 2 and abs(adjusted_balance) > RAKE_BALANCE_TOLERANCE:
        reasons.append(f"玩家净输赢不平衡：{adjusted_balance:.2f}")
    if reasons:
        return "bad", reasons, True
    return "good", [], False
