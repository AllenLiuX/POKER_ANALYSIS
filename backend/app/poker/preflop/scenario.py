"""翻前训练场景生成器。

给定（或随机选取）一个可用 spot（format/spot/position），随机发一手 hero 手牌，
拼出可供前端渲染牌桌 + 出题的场景对象。场景本身是**无状态**的：评分所需的全部字段
（format/spot/position/hero）都放在场景里，前端答题时原样回传即可，无需服务端会话。

目前覆盖 RFI（开池）：前面玩家全部弃牌，轮到 hero 首先行动。
"""
from __future__ import annotations

import random
import uuid
from typing import Dict, List, Optional

from app.poker.cards import full_deck
from app.poker.preflop.handclass import hand_class
from app.poker.preflop.ranges import list_spots, load_spot

# 6-max 翻前行动顺序（UTG 最先，BB 最后）
POSITION_ORDER = ["UTG", "MP", "CO", "BTN", "SB", "BB"]

# 各动作的中文/展示名（用于出题文案与反馈）
ACTION_LABELS = {"fold": "弃牌", "call": "跟注", "raise": "加注", "allin": "全下"}

# 花色 -> Unicode，便于前端/日志友好展示
_SUIT_GLYPH = {"s": "♠", "h": "♥", "d": "♦", "c": "♣"}

# 期望的动作展示顺序
_ACTION_SORT = {"fold": 0, "call": 1, "raise": 2, "allin": 3}


def card_glyph(card: str) -> str:
    """'As' -> 'A♠'（仅用于展示）。"""
    return f"{card[0]}{_SUIT_GLYPH.get(card[1], card[1])}"


def _order_actions(actions: List[str]) -> List[str]:
    return sorted(actions, key=lambda a: _ACTION_SORT.get(a, 99))


def _deal_hero(rng: random.Random) -> List[str]:
    deck = full_deck()
    rng.shuffle(deck)
    return [deck[0], deck[1]]


def _build_seats(
    hero_position: str, spot: str, opener_position: Optional[str] = None
) -> List[Dict[str, object]]:
    """按行动顺序生成 6 个座位状态，供前端画牌桌。

    RFI：hero 之前的位置全部 folded，之后的位置 waiting。
    vs_RFI：opener 标记为 raiser，hero 之前的其它位置 folded，之后 waiting。
    SB/BB 恒标记 is_blind。
    """
    hero_idx = POSITION_ORDER.index(hero_position)
    opener_idx = POSITION_ORDER.index(opener_position) if opener_position else None
    seats: List[Dict[str, object]] = []
    for idx, pos in enumerate(POSITION_ORDER):
        if pos == hero_position:
            status = "hero"
        elif opener_idx is not None and idx == opener_idx:
            status = "raiser"
        elif idx < hero_idx:
            status = "folded"
        else:
            status = "waiting"
        seats.append(
            {
                "position": pos,
                "order": idx,
                "status": status,
                "is_hero": pos == hero_position,
                "is_blind": pos in ("SB", "BB"),
            }
        )
    return seats


def _prompt_text(
    spot: str,
    hero_position: str,
    hero: List[str],
    opener_position: Optional[str] = None,
    open_size_bb: float = 2.5,
) -> str:
    hand = " ".join(card_glyph(c) for c in hero)
    if spot == "RFI":
        if hero_position == "UTG":
            lead = "轮到你在 UTG 首先行动"
        else:
            lead = f"前面玩家全部弃牌，轮到你在 {hero_position} 首先行动"
        return f"6-max 100bb。{lead}。你的手牌是 {hand}。该怎么打？"
    if spot == "vs_RFI":
        size = f"{open_size_bb:g}bb"
        return (
            f"6-max 100bb。{opener_position} 加注开池到 {size}，"
            f"其余玩家弃牌，轮到你在 {hero_position} 防守。你的手牌是 {hand}。该怎么打？"
        )
    return f"6-max 100bb。位置 {hero_position}（{spot}）。你的手牌是 {hand}。该怎么打？"


def available_spots(fmt: Optional[str] = None) -> List[Dict[str, str]]:
    spots = list_spots()
    if fmt:
        spots = [s for s in spots if s["format"] == fmt]
    return spots


def generate_scenario(
    *,
    fmt: Optional[str] = None,
    spot: Optional[str] = None,
    position: Optional[str] = None,
    seed: Optional[int] = None,
) -> Dict[str, object]:
    """随机（或按指定条件）生成一个翻前训练场景。

    未指定的维度会从现有范围数据里随机挑；hero 手牌总是随机发。
    传入 seed 可复现（便于测试）。
    """
    rng = random.Random(seed)
    pool = available_spots(fmt)
    if not pool:
        raise ValueError("没有可用的范围数据（data/ranges 为空）")

    if spot:
        pool = [s for s in pool if s["spot"] == spot]
    if position:
        pool = [s for s in pool if s["position"] == position]
    if not pool:
        raise ValueError(
            f"没有匹配的 spot：format={fmt} spot={spot} position={position}"
        )

    chosen = rng.choice(pool)
    ps = load_spot(chosen["format"], chosen["spot"], chosen["position"])
    meta = ps.meta

    hero = _deal_hero(rng)
    cls = hand_class(hero[0], hero[1])
    actions = _order_actions(list(ps.actions) + ["fold"])

    spot = chosen["spot"]
    hero_position = str(meta.get("hero_position", chosen["position"]))
    opener_position = meta.get("opener_position")
    opener_position = str(opener_position) if opener_position else None
    open_size_bb = float(meta.get("open_size_bb", 2.5))

    if spot == "vs_RFI":
        pot_bb = round(0.5 + 1.0 + open_size_bb, 2)
        facing = {"opener_position": opener_position, "open_size_bb": open_size_bb}
    else:
        pot_bb = 1.5
        facing = None

    return {
        "id": uuid.uuid4().hex,
        "format": chosen["format"],
        "spot": spot,
        "position": chosen["position"],  # 评分/加载键（vs_RFI 为 'BB_vs_BTN' 复合键）
        "hero_position": hero_position,
        "opener_position": opener_position,
        "facing": facing,
        "hero": hero,
        "hero_glyphs": [card_glyph(c) for c in hero],
        "hero_class": cls,
        "effective_stack_bb": 100,
        "blinds": {"sb": 0.5, "bb": 1.0},
        "pot_bb": pot_bb,
        "seats": _build_seats(hero_position, spot, opener_position),
        "available_actions": actions,
        "action_labels": {a: ACTION_LABELS.get(a, a) for a in actions},
        "prompt": _prompt_text(spot, hero_position, hero, opener_position, open_size_bb),
    }
