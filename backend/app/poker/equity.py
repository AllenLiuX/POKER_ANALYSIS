"""胜率（equity）与赔率计算。

- equity_hand_vs_range: 蒙特卡洛，返回 win/tie/lose 明细与 equity。
- pot_odds / ev_call: 赔率与跟注 EV。
"""
from __future__ import annotations

import random
from typing import Dict, List, Optional, Tuple

import eval7

from app.poker.cards import full_deck, normalize_cards
from app.poker.evaluate import to_eval7


def _cid(card: "eval7.Card") -> int:
    """eval7.Card 的唯一编号 0..51（eval7.Card 不支持 int()）。"""
    return card.rank * 4 + card.suit


def _range_combos(range_str: str) -> List[Tuple[List["eval7.Card"], float]]:
    """把范围字符串（如 'QQ+, AKs'）解析成 [(2 张牌, 权重), ...]。"""
    hr = eval7.HandRange(range_str)
    combos: List[Tuple[List["eval7.Card"], float]] = []
    for entry in hr.hands:
        cards, weight = entry[0], entry[1]
        combos.append((list(cards), float(weight)))
    return combos


def equity_hand_vs_range(
    hero: List[str],
    villain_range: str,
    board: Optional[List[str]] = None,
    trials: int = 10000,
    seed: Optional[int] = None,
) -> Dict[str, float]:
    """英雄两张手牌 vs 对手范围的蒙特卡洛胜率。

    Returns: {win, tie, lose, equity, samples}
    """
    rng = random.Random(seed)
    hero_c = to_eval7(hero)
    if len(hero_c) != 2:
        raise ValueError("hero 必须是 2 张手牌")
    board_c = to_eval7(board or [])
    if len(board_c) not in (0, 3, 4, 5):
        raise ValueError("board 只能是 0/3/4/5 张")

    dead = {_cid(c) for c in hero_c} | {_cid(c) for c in board_c}
    if len(dead) != len(hero_c) + len(board_c):
        raise ValueError("hero / board 存在重复牌")

    combos = _range_combos(villain_range)
    if not combos:
        raise ValueError("对手范围为空")

    full = [eval7.Card(c) for c in full_deck()]
    need = 5 - len(board_c)

    wins = ties = losses = 0
    count = 0
    attempts = 0
    max_attempts = max(trials * 20, 1000)
    while count < trials and attempts < max_attempts:
        attempts += 1
        combo_cards, _weight = rng.choice(combos)
        vids = [_cid(c) for c in combo_cards]
        if vids[0] == vids[1] or any(v in dead for v in vids):
            continue
        used = dead | set(vids)
        avail = [c for c in full if _cid(c) not in used]
        drawn = rng.sample(avail, need) if need else []
        final_board = board_c + drawn
        hero_score = eval7.evaluate(hero_c + final_board)
        vill_score = eval7.evaluate(list(combo_cards) + final_board)
        if hero_score > vill_score:
            wins += 1
        elif hero_score < vill_score:
            losses += 1
        else:
            ties += 1
        count += 1

    if count == 0:
        raise ValueError("对手范围与已知牌完全冲突，无有效样本")

    return {
        "win": wins / count,
        "tie": ties / count,
        "lose": losses / count,
        "equity": (wins + ties / 2) / count,
        "samples": count,
    }


def pot_odds(pot: float, call: float) -> Dict[str, float]:
    """赔率：pot 为跟注前底池（已含对手下注），call 为需跟注额。

    required_equity = call / (pot + call)
    """
    if pot < 0 or call < 0:
        raise ValueError("pot / call 不能为负")
    if pot + call == 0:
        raise ValueError("pot + call 不能为 0")
    required = call / (pot + call)
    return {"required_equity": required, "pot": pot, "call": call}


def ev_call(equity: float, pot: float, call: float) -> float:
    """跟注 EV：pot 为跟注前底池（已含对手下注）。

    赢时净赚 pot（自己的跟注额会归还，不计为收益），输时净亏 call：
        EV = equity * pot - (1 - equity) * call
    与 pot_odds 的 required_equity = call/(pot+call) 一致（EV=0 的临界点）。
    """
    if not 0.0 <= equity <= 1.0:
        raise ValueError("equity 必须在 [0,1]")
    return equity * pot - (1 - equity) * call
