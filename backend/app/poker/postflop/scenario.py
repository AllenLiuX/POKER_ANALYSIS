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

OPEN_BB = 2.5
CBET_FRACTION = 0.5  # 加注方半池持续下注

# 盲注与翻后行动顺序（首个行动=最小序号；序号越大越靠后=有位置）。
BLIND_POST = {"SB": 0.5, "BB": 1.0}
POSTFLOP_ORDER = {"SB": 0, "BB": 1, "UTG": 2, "MP": 3, "CO": 4, "BTN": 5}
POS_CN = {"UTG": "枪口", "MP": "中位", "CO": "劫位", "BTN": "按钮", "SB": "小盲", "BB": "大盲"}

# 仓库现有的 vs_RFI 对位（键名 = "{防守方}_vs_{开池方}"）。
VS_SPOTS = [
    "BB_vs_UTG", "BB_vs_MP", "BB_vs_CO", "BB_vs_BTN", "BB_vs_SB",
    "SB_vs_UTG", "SB_vs_MP", "SB_vs_CO", "SB_vs_BTN",
    "BTN_vs_UTG", "BTN_vs_MP", "BTN_vs_CO",
]


def _has_flat_range(vs_spot: str) -> bool:
    """该防守方对位是否有跟注(flat)范围。无 flat（SB/BTN 多为 3bet-or-fold）就没有单加注底池。"""
    try:
        ps = load_spot(FMT, "vs_RFI", vs_spot)
    except FileNotFoundError:
        return False
    return any(f.get("call", 0.0) > 0.0 for f in ps.frequencies.values())


def _build_configs() -> List[Dict[str, object]]:
    """由 vs_RFI 对位推导单加注底池配置：开/跟各 2.5bb，未参与手牌的盲注为死钱。

    仅纳入有 flat 范围的对位（当前数据下为 BB 防守：UTG/MP/CO/BTN/SB 开池）。
    """
    out: List[Dict[str, object]] = []
    for key in VS_SPOTS:
        if not _has_flat_range(key):
            continue
        caller, pfr = key.split("_vs_")
        dead = sum(v for pos, v in BLIND_POST.items() if pos not in (pfr, caller))
        pot0 = round(OPEN_BB * 2 + dead, 2)
        out.append({"pfr": pfr, "caller": caller, "vs_spot": key, "pot0": pot0})
    return out


CONFIGS = _build_configs()


def _matchup_label(pfr: str, caller: str) -> str:
    return f"{POS_CN.get(pfr, pfr)} 开池 vs {POS_CN.get(caller, caller)} 防守"


def _find_config(matchup: str) -> Optional[Dict[str, object]]:
    for c in CONFIGS:
        if c["vs_spot"] == matchup:
            return c
    return None


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
    *,
    role: Optional[str] = None,
    config_idx: Optional[int] = None,
    matchup: Optional[str] = None,
    seed: Optional[int] = None,
) -> Dict[str, object]:
    rng = random.Random(seed)
    if matchup is not None:
        cfg = _find_config(matchup)
        if cfg is None:
            raise ValueError(f"对位不存在：{matchup}")
    elif config_idx is not None:
        cfg = CONFIGS[config_idx]
    else:
        cfg = rng.choice(CONFIGS)
    role = role or rng.choice(["pfr", "caller"])
    if role not in ("pfr", "caller"):
        raise ValueError("role 只能是 pfr / caller")

    pfr, caller, vs_spot = cfg["pfr"], cfg["caller"], cfg["vs_spot"]
    pot0 = float(cfg["pot0"])
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
    # 翻后是否有位置（行动顺序越靠后越有位置）。
    hero_ip = POSTFLOP_ORDER[hero_pos] > POSTFLOP_ORDER[villain_pos]

    bet_sizes: List[Dict[str, object]] = []
    raise_sizes: List[Dict[str, object]] = []
    if role == "pfr":
        pot_bb = round(pot0, 2)
        bet_bb = None
        actions = ["check", "bet"]
        bet_sizes = _bet_size_options(pot_bb)
        board_str = " ".join(card_glyph(c) for c in board)
        if hero_ip:
            flow = f"{villain_pos} 过牌到你"
        else:
            flow = f"轮到你先行动（{villain_pos} 在你身后，有位置）"
        prompt = (
            f"6-max 100bb 单加注底池。你在 {hero_pos} 翻前加注、{villain_pos} 跟注。"
            f"翻牌 {board_str}，{flow}。底池 {pot_bb}bb，是否持续下注？"
        )
    else:
        villain_bet = round(pot0 * CBET_FRACTION, 2)
        pot_bb = round(pot0 + villain_bet, 2)  # 含对手下注、待你行动的底池
        bet_bb = villain_bet
        actions = ["fold", "call", "raise"]
        raise_sizes = _raise_size_options(villain_bet)
        pos_note = "（你有位置）" if hero_ip else "（你无位置）"
        prompt = (
            f"6-max 100bb 单加注底池。你在 {hero_pos} 翻前跟注 {villain_pos} 的开池。"
            f"翻牌 {' '.join(card_glyph(c) for c in board)}，{villain_pos} 持续下注 {villain_bet}bb"
            f"（底池现 {pot_bb}bb）{pos_note}。该怎么防守？"
        )

    return {
        "id": uuid.uuid4().hex,
        "mode": "postflop",
        "street": "flop",
        "role": role,
        "format": FMT,
        "config": {"pfr": pfr, "caller": caller},
        "matchup": vs_spot,
        "matchup_label": _matchup_label(pfr, caller),
        "hero_position": hero_pos,
        "villain_position": villain_pos,
        "hero_ip": hero_ip,
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
