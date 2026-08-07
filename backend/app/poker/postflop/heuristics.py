"""翻后决策启发式：c-bet 与防守。透明规则，非精确 solver。

关键概念：
- MDF（最低防守频率）= 1 - bet/(pot+bet)：面对下注至少防守这么多组合才不被无脑诈唬剥削。
- 底池赔率 required_equity = bet/(pot+bet)：跟注需要的最低胜率。
- Range advantage：翻前加注方在高张/干燥面通常占范围优势，可高频小注。
"""
from __future__ import annotations

from typing import Dict, List, Set

# 下注尺度桶（占底池比例），用于 c-bet；判分时对比"建议桶 / 可接受桶"。
BET_SIZE_BUCKETS = [
    {"id": "small", "label": "⅓ 池", "fraction": 1.0 / 3.0},
    {"id": "half", "label": "½ 池", "fraction": 0.5},
    {"id": "big", "label": "¾ 池", "fraction": 0.75},
    {"id": "pot", "label": "满池", "fraction": 1.0},
]

# 加注尺度桶（相对对手下注额的倍数，raise-to）。
RAISE_SIZE_BUCKETS = [
    {"id": "small", "label": "小加注 (≈2.5×)", "mult": 2.5},
    {"id": "big", "label": "大加注 (≈3.5×)", "mult": 3.5},
]


def size_label(action: str, size_id: str) -> str:
    """把尺度桶 id 翻成中文标签（bet 用池比例桶，raise 用倍数桶）。"""
    buckets = (
        BET_SIZE_BUCKETS
        if action == "bet"
        else RAISE_SIZE_BUCKETS
        if action == "raise"
        else []
    )
    for b in buckets:
        if b["id"] == size_id:
            return str(b["label"])
    return size_id


def pot_odds_required(pot_bb: float, bet_bb: float) -> float:
    return bet_bb / (pot_bb + bet_bb)


def mdf(pot_bb: float, bet_bb: float) -> float:
    return 1.0 - pot_odds_required(pot_bb, bet_bb)


def recommend_cbet(texture: Dict, hand: Dict, equity: float) -> Dict[str, object]:
    """英雄为翻前加注方，对手过牌，决定 check / bet。

    actions: check / bet。size 为建议尺度（文字），不参与判分。
    """
    tier = hand["tier"]
    wet = float(texture["wetness"])
    high_board = texture["high_card"] >= 10
    reasons: List[str] = []
    accept: Set[str] = set()
    # rec_size / accept_sizes 表示"若选择下注，应下多大"（判分时对比）。
    rec_size = "half"
    accept_sizes: Set[str] = {"small", "half", "big"}

    if tier == "value":
        rec = "bet"
        if wet >= 0.5:
            size = "大注(约 2/3~满池)"
            rec_size, accept_sizes = "big", {"half", "big", "pot"}
        else:
            size = "小到中注(约 1/3~1/2)"
            rec_size, accept_sizes = "half", {"small", "half"}
        accept = {"bet"}
        reasons.append(f"{hand['made_label']}属价值牌，下注取价值/保护")
        mix = False
    elif tier == "draw":
        rec = "bet"
        size = "半诈唬下注(约 1/2~2/3)"
        rec_size, accept_sizes = "half", {"half", "big"}
        accept = {"bet", "check"}
        reasons.append(f"{hand['draw_label']}有 {hand['outs']} outs，半诈唬有弃牌率+成手潜力")
        mix = True
    elif tier == "medium":
        # 边缘成手：多控池，干面可小注薄价值
        if wet < 0.35:
            rec = "bet"
            size = "小注(约 1/3)薄价值/否则过牌"
            rec_size, accept_sizes = "small", {"small"}
            accept = {"bet", "check"}
            reasons.append("边缘成手在干面可小注薄价值，湿面更宜控池")
        else:
            rec = "check"
            size = "过牌控池"
            rec_size, accept_sizes = "small", {"small"}
            accept = {"check"}
            reasons.append("边缘成手在湿面控池，避免被加注为难")
        mix = True
    else:  # air / weak
        if wet < 0.35 and high_board:
            rec = "bet"
            size = "小注(约 1/3)范围下注"
            rec_size, accept_sizes = "small", {"small"}
            accept = {"bet", "check"}
            reasons.append("干燥高张面加注方占范围优势，可高频小注施压")
            mix = True
        else:
            rec = "check"
            size = "过牌放弃"
            rec_size, accept_sizes = "small", {"small"}
            accept = {"check"}
            reasons.append("湿/低面利于跟注方，空气牌下注 EV 低，过牌")
            mix = False

    return {
        "spot": "cbet",
        "recommended": rec,
        "accept": sorted(accept),
        "size_advice": size,
        "recommended_size": rec_size,
        "accept_sizes": sorted(accept_sizes),
        "mix": mix,
        "equity": round(equity, 3),
        "wetness": wet,
        "reasons": reasons,
    }


def recommend_defense(
    texture: Dict, hand: Dict, equity: float, pot_bb: float, bet_bb: float
) -> Dict[str, object]:
    """英雄面对翻前加注方的 c-bet，决定 fold / call / raise。"""
    required = pot_odds_required(pot_bb, bet_bb)
    defend = mdf(pot_bb, bet_bb)
    tier = hand["tier"]
    reasons: List[str] = []
    accept: Set[str] = set()

    strong_value = tier == "value" and hand["made"] in (
        "Straight Flush", "Quads", "Full House", "Flush", "Straight", "Trips", "Two Pair",
    )

    if strong_value:
        rec = "raise"
        accept = {"raise", "call"}
        reasons.append(f"{hand['made_label']}够强，加注取价值（也可平跟诱敌）")
        mix = True
    elif tier == "value":  # 超对/顶对好踢脚
        rec = "call"
        accept = {"call", "raise"}
        reasons.append("强顶对/超对领先多数持续下注范围，跟注为主，偶尔加注")
        mix = True
    elif tier == "draw":
        rec = "call"
        accept = {"call", "raise"}
        reasons.append(f"{hand['draw_label']}胜率 {equity:.0%} ≥ 底池赔率 {required:.0%}，跟注/半诈唬加注")
        mix = True
    elif tier in ("medium", "weak"):
        if equity >= required:
            rec = "call"
            accept = {"call"}
            reasons.append(f"胜率 {equity:.0%} ≥ 需要 {required:.0%}，达到跟注门槛")
        else:
            rec = "fold"
            accept = {"fold"}
            reasons.append(f"胜率 {equity:.0%} < 需要 {required:.0%}，不够跟注")
        mix = False
    else:  # air
        rec = "fold"
        accept = {"fold"}
        reasons.append(f"空气牌胜率 {equity:.0%} < 需要 {required:.0%}，弃牌")
        mix = False

    return {
        "spot": "defense",
        "recommended": rec,
        "accept": sorted(accept),
        # 若选择加注：价值/半诈唬都倾向偏大的加注，两档都可接受。
        "recommended_raise_size": "big",
        "accept_raise_sizes": ["small", "big"],
        "mix": mix,
        "equity": round(equity, 3),
        "required_equity": round(required, 3),
        "mdf": round(defend, 3),
        "pot_bb": pot_bb,
        "bet_bb": bet_bb,
        "reasons": reasons,
    }
