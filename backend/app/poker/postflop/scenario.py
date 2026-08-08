"""翻后训练场景生成：HU 单加注底池（SRP）翻牌。

两种角色：
- pfr    —— 英雄是翻前加注方，对手过牌，决定 c-bet（check/bet）。
- caller —— 英雄翻前跟注，面对加注方的持续下注，决定 fold/call/raise。

对手范围直接取自本仓库的翻前数据（加注方=开池范围，跟注方=对应防守跟注范围），
让翻后 equity 有据可依。场景无状态：判分所需字段（board/hero/对手范围/底池）都在
场景里，前端答题时原样回传。
"""
from __future__ import annotations

import random
import uuid
from typing import Dict, List, Optional

from app.poker.cards import full_deck
from app.poker.preflop.handclass import combos_for_class
from app.poker.preflop.ranges import load_spot
from app.poker.preflop.scenario import card_glyph, deal_combo_for_class
from app.poker.postflop.heuristics import BET_SIZE_BUCKETS, RAISE_SIZE_BUCKETS
from app.poker.postflop.texture import classify_board

FMT = "6max_100bb"

# HU 单加注底池配置：加注方开池 2.5bb，跟注方（BB）跟注，SB 死 0.5bb。
CONFIGS = [
    {"pfr": "BTN", "caller": "BB", "vs_spot": "BB_vs_BTN"},
    {"pfr": "CO", "caller": "BB", "vs_spot": "BB_vs_CO"},
]

OPEN_BB = 2.5
POT0 = OPEN_BB * 2 + 0.5  # 翻牌底池 ≈ 5.5bb
CBET_FRACTION = 0.5  # 加注方半池持续下注

ACTION_LABELS = {"fold": "弃牌", "call": "跟注", "raise": "加注", "check": "过牌", "bet": "下注"}


def _in_range_classes(fmt: str, spot: str, position: str, actions) -> List[str]:
    ps = load_spot(fmt, spot, position)
    return [c for c, f in ps.frequencies.items() if any(f.get(a, 0.0) > 0.0 for a in actions)]


def _range_string(classes: List[str]) -> str:
    return ", ".join(classes)


def _sample_class(classes: List[str], rng: random.Random) -> str:
    weights = [combos_for_class(c) for c in classes]
    return rng.choices(classes, weights=weights, k=1)[0]


def _bet_size_options(pot_bb: float) -> List[Dict[str, object]]:
    """c-bet 尺度选项：占底池比例 → 具体下注额（bb）。"""
    return [
        {
            "id": b["id"],
            "label": b["label"],
            "fraction": b["fraction"],
            "amount_bb": round(pot_bb * b["fraction"], 1),
        }
        for b in BET_SIZE_BUCKETS
    ]


def _raise_size_options(bet_bb: float) -> List[Dict[str, object]]:
    """加注尺度选项：相对对手下注的倍数 → 加注到多少（bb）。"""
    return [
        {
            "id": b["id"],
            "label": b["label"],
            "mult": b["mult"],
            "amount_bb": round(bet_bb * b["mult"], 1),
        }
        for b in RAISE_SIZE_BUCKETS
    ]


def generate_postflop_scenario(
    *, role: Optional[str] = None, config_idx: Optional[int] = None, seed: Optional[int] = None
) -> Dict[str, object]:
    rng = random.Random(seed)
    cfg = CONFIGS[config_idx] if config_idx is not None else rng.choice(CONFIGS)
    role = role or rng.choice(["pfr", "caller"])
    if role not in ("pfr", "caller"):
        raise ValueError("role 只能是 pfr / caller")

    pfr, caller, vs_spot = cfg["pfr"], cfg["caller"], cfg["vs_spot"]
    pfr_open_classes = _in_range_classes(FMT, "RFI", pfr, ("raise",))
    caller_call_classes = _in_range_classes(FMT, "vs_RFI", vs_spot, ("call",))

    if role == "pfr":
        hero_pos, villain_pos = pfr, caller
        hero_classes = pfr_open_classes
        villain_range = _range_string(caller_call_classes)
        villain_range_label = f"{caller} 跟注范围"
    else:
        hero_pos, villain_pos = caller, pfr
        hero_classes = caller_call_classes
        villain_range = _range_string(pfr_open_classes)
        villain_range_label = f"{pfr} 开池(≈持续下注)范围"

    # 发英雄手牌（取自其翻前范围），再从剩余牌堆发翻牌
    cls = _sample_class(hero_classes, rng)
    hero = deal_combo_for_class(cls, rng)
    deck = full_deck()
    remaining = [c for c in deck if c not in hero]
    rng.shuffle(remaining)
    board = remaining[:3]

    texture = classify_board(board)

    bet_sizes: List[Dict[str, object]] = []
    raise_sizes: List[Dict[str, object]] = []
    if role == "pfr":
        pot_bb = round(POT0, 2)
        bet_bb = None
        actions = ["check", "bet"]
        bet_sizes = _bet_size_options(pot_bb)
        board_str = " ".join(card_glyph(c) for c in board)
        prompt = (
            f"6-max 100bb 单加注底池。你在 {hero_pos} 翻前加注、{villain_pos} 跟注。"
            f"翻牌 {board_str}，{villain_pos} 过牌到你。底池 {pot_bb}bb，该 c-bet 吗？"
        )
    else:
        villain_bet = round(POT0 * CBET_FRACTION, 2)
        pot_bb = round(POT0 + villain_bet, 2)  # 含对手下注、待你行动的底池
        bet_bb = villain_bet
        actions = ["fold", "call", "raise"]
        raise_sizes = _raise_size_options(villain_bet)
        prompt = (
            f"6-max 100bb 单加注底池。你在 {hero_pos} 翻前跟注 {villain_pos} 的开池。"
            f"翻牌 {' '.join(card_glyph(c) for c in board)}，{villain_pos} 持续下注 {villain_bet}bb"
            f"（底池现 {pot_bb}bb）。该怎么防守？"
        )

    return {
        "id": uuid.uuid4().hex,
        "mode": "postflop",
        "street": "flop",
        "role": role,
        "format": FMT,
        "config": {"pfr": pfr, "caller": caller},
        "hero_position": hero_pos,
        "villain_position": villain_pos,
        "hero": hero,
        "hero_glyphs": [card_glyph(c) for c in hero],
        "hero_class": cls,
        "board": board,
        "board_glyphs": [card_glyph(c) for c in board],
        "villain_range": villain_range,
        "villain_range_label": villain_range_label,
        "hero_range": _range_string(hero_classes),
        "effective_stack_bb": 100,
        "blinds": {"sb": 0.5, "bb": 1.0},
        "pot_bb": pot_bb,
        "bet_bb": bet_bb,
        "texture": texture,
        "available_actions": actions,
        "action_labels": {a: ACTION_LABELS[a] for a in actions},
        "bet_sizes": bet_sizes,
        "raise_sizes": raise_sizes,
        "prompt": prompt,
    }
