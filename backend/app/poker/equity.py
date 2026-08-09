"""胜率（equity）与赔率计算。

- equity_hand_vs_range_enum: **精确加权枚举**（翻牌/转牌/河牌），确定性、按组合权重加权，
  这是各开源 solver 内部计算胜率的标准做法。河牌/转牌全枚举，翻牌在评估预算内全枚举、
  超预算则确定性抽样 runout（组合仍全枚举加权），去除蒙特卡洛方差与"忽略组合权重"的偏差。
- equity_hand_vs_range: 蒙特卡洛（保留用于翻前 0 公共牌等场景与回退）。
- hero_equity: 分派器——有 3~5 张公共牌走精确枚举，否则走蒙特卡洛。
- pot_odds / ev_call: 赔率与跟注 EV。
"""
from __future__ import annotations

import itertools
import random
from typing import Dict, List, Optional, Tuple

import eval7

from app.poker.cards import full_deck, normalize_cards
from app.poker.evaluate import to_eval7

# 翻牌精确枚举的评估预算（eval7≈1.2M/s）：成本≈runout数×(1+组合数)。
# 300k 对应最坏情形（宽范围翻牌）≈0.25s，且窄范围能落到近乎全枚举。
_ENUM_BUDGET = 300_000


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


def _weighted_combos(
    range_str: str, dead: set
) -> List[Tuple[List["eval7.Card"], Tuple[int, int], float]]:
    """范围字符串 → [(两张牌, (id0,id1), 权重)]，跳过与 dead(已知牌) 冲突或自冲突的组合。"""
    hr = eval7.HandRange(range_str)
    out: List[Tuple[List["eval7.Card"], Tuple[int, int], float]] = []
    for entry in hr.hands:
        cards = list(entry[0])
        weight = float(entry[1])
        i0, i1 = _cid(cards[0]), _cid(cards[1])
        if i0 == i1 or i0 in dead or i1 in dead:
            continue
        out.append((cards, (i0, i1), weight))
    return out


def equity_hand_vs_range_enum(
    hero: List[str],
    villain_range: str,
    board: List[str],
    *,
    budget: int = _ENUM_BUDGET,
    seed: Optional[int] = None,
) -> Dict[str, float]:
    """英雄两张手牌 vs 对手范围的**精确加权枚举**胜率（仅 3~5 张公共牌）。

    - 河牌(0 runout)/转牌(1 runout)：全枚举、确定性、按组合权重加权。
    - 翻牌(2 runout)：评估量在 budget 内则全枚举；超出则按 seed 确定性抽样 runout
      （对手组合仍全枚举加权），显著优于此前"均匀抽组合、忽略权重"的蒙特卡洛。

    Returns: {win, tie, lose, equity, samples}（win/tie/lose 为按权重归一后的占比）。
    """
    hero_c = to_eval7(hero)
    if len(hero_c) != 2:
        raise ValueError("hero 必须是 2 张手牌")
    board_c = to_eval7(board or [])
    if len(board_c) not in (3, 4, 5):
        raise ValueError("精确枚举只支持 3/4/5 张公共牌")
    dead = {_cid(c) for c in hero_c} | {_cid(c) for c in board_c}
    if len(dead) != len(hero_c) + len(board_c):
        raise ValueError("hero / board 存在重复牌")

    combos = _weighted_combos(villain_range, dead)
    if not combos:
        raise ValueError("对手范围与已知牌完全冲突，无有效组合")

    avail = [c for c in (eval7.Card(x) for x in full_deck()) if _cid(c) not in dead]
    need = 5 - len(board_c)

    if need == 0:
        runouts: List[Tuple["eval7.Card", ...]] = [()]
    else:
        all_runouts = list(itertools.combinations(avail, need))
        max_runouts = max(1, budget // (1 + len(combos)))
        if len(all_runouts) <= max_runouts:
            runouts = all_runouts
        else:
            runouts = random.Random(seed if seed is not None else 0).sample(
                all_runouts, max_runouts
            )

    win = tie = lose = 0.0
    wsum = 0.0
    evals = 0
    for ro in runouts:
        ro_ids = {_cid(c) for c in ro}
        final_board = board_c + list(ro)
        hero_score = eval7.evaluate(hero_c + final_board)
        for cards, (i0, i1), w in combos:
            if i0 in ro_ids or i1 in ro_ids:  # 该组合与本次 runout 冲突 → 此 runout 下不可能
                continue
            vill_score = eval7.evaluate(cards + final_board)
            if hero_score > vill_score:
                win += w
            elif hero_score < vill_score:
                lose += w
            else:
                tie += w
            wsum += w
            evals += 1

    if wsum == 0.0:
        raise ValueError("对手范围与已知牌完全冲突，无有效样本")
    return {
        "win": win / wsum,
        "tie": tie / wsum,
        "lose": lose / wsum,
        "equity": (win + tie / 2.0) / wsum,
        "samples": evals,
    }


def hero_equity(
    hero: List[str],
    villain_range: str,
    board: Optional[List[str]] = None,
    *,
    trials: int = 10000,
    seed: Optional[int] = None,
) -> Dict[str, float]:
    """胜率分派器：有 3~5 张公共牌 → 精确加权枚举；否则（翻前）→ 蒙特卡洛。"""
    if board and len(normalize_cards(board)) in (3, 4, 5):
        return equity_hand_vs_range_enum(hero, villain_range, board, seed=seed)
    return equity_hand_vs_range(hero, villain_range, board=board, trials=trials, seed=seed)


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
