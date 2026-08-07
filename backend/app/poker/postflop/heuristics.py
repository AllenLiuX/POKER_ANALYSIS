"""翻后决策启发式：c-bet 与防守。透明规则，非精确 solver。

关键概念：
- MDF（最低防守频率）= 1 - bet/(pot+bet)：面对下注至少防守这么多组合才不被无脑诈唬剥削。
- 底池赔率 required_equity = bet/(pot+bet)：跟注需要的最低胜率。
- Range advantage：翻前加注方在高张/干燥面通常占范围优势，可高频小注。
"""
from __future__ import annotations

from typing import Dict, List, Set


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

    if tier == "value":
        rec = "bet"
        size = "大注(约 2/3 底池)" if wet >= 0.5 else "小到中注(约 1/3~1/2)"
        accept = {"bet"}
        reasons.append(f"{hand['made_label']}属价值牌，下注取价值/保护")
        mix = False
    elif tier == "draw":
        rec = "bet"
        size = "半诈唬下注(约 1/2~2/3)"
        accept = {"bet", "check"}
        reasons.append(f"{hand['draw_label']}有 {hand['outs']} outs，半诈唬有弃牌率+成手潜力")
        mix = True
    elif tier == "medium":
        # 边缘成手：多控池，干面可小注薄价值
        if wet < 0.35:
            rec = "bet"
            size = "小注(约 1/3)薄价值/否则过牌"
            accept = {"bet", "check"}
            reasons.append("边缘成手在干面可小注薄价值，湿面更宜控池")
        else:
            rec = "check"
            size = "过牌控池"
            accept = {"check"}
            reasons.append("边缘成手在湿面控池，避免被加注为难")
        mix = True
    else:  # air / weak
        if wet < 0.35 and high_board:
            rec = "bet"
            size = "小注(约 1/3)范围下注"
            accept = {"bet", "check"}
            reasons.append("干燥高张面加注方占范围优势，可高频小注施压")
            mix = True
        else:
            rec = "check"
            size = "过牌放弃"
            accept = {"check"}
            reasons.append("湿/低面利于跟注方，空气牌下注 EV 低，过牌")
            mix = False

    return {
        "spot": "cbet",
        "recommended": rec,
        "accept": sorted(accept),
        "size_advice": size,
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
        "mix": mix,
        "equity": round(equity, 3),
        "required_equity": round(required, 3),
        "mdf": round(defend, 3),
        "pot_bb": pot_bb,
        "bet_bb": bet_bb,
        "reasons": reasons,
    }
