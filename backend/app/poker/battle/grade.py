"""对战判分：复用翻前 score_action / 翻后 score_postflop，输出统一的决策评估。

每个英雄决策产出一条记录：grade / optimal / severity（0~1 严重度）/ grounded。
severity 用于挑出"大的有问题的手"——严重度高或造成较大损失的手会被前端入库、供 AI 复盘。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from app.poker.postflop.analyze import analyze_spot
from app.poker.postflop.heuristics import size_label
from app.poker.postflop.scoring import score_postflop
from app.poker.preflop.scoring import score_action

SPOT_LABELS = {
    "RFI": "翻前开池",
    "vs_RFI": "翻前防守",
    "vs_3bet": "翻前面对 3-bet",
    "postflop_cbet": "翻后主动（过牌/下注）",
    "postflop_defense": "翻后防守（面对下注）",
}


def grade_preflop(*, cls: str, freqs: Dict[str, float], spot: str, action: str) -> Dict[str, object]:
    """翻前判分：freqs 为该手牌类别在该 spot 的动作频率。"""
    score = score_action(freqs, action)
    ev = float(score["ev_loss_proxy"])
    grade = str(score["grade"])
    severity = 0.0 if grade != "mistake" else min(1.0, ev / 0.8)
    return {
        "street": "preflop",
        "spot": spot,
        "spot_label": SPOT_LABELS.get(spot, spot),
        "hand_class": cls,
        "action": action,
        "optimal_action": score["optimal_action"],
        "grade": grade,
        "correct": bool(score["correct"]),
        "ev_loss_proxy": ev,
        "severity": round(severity, 3),
        "frequencies": freqs,
        "grounded": True,
    }


def grade_preflop_ungrounded(*, cls: str, spot: str, action: str) -> Dict[str, object]:
    """无范围数据的翻前节点（如面对 3-bet）：只记录动作，不判分。"""
    return {
        "street": "preflop",
        "spot": spot,
        "spot_label": SPOT_LABELS.get(spot, spot),
        "hand_class": cls,
        "action": action,
        "optimal_action": None,
        "grade": "ungraded",
        "correct": None,
        "ev_loss_proxy": 0.0,
        "severity": 0.0,
        "grounded": False,
    }


def grade_postflop(
    *,
    street: str,
    hero: List[str],
    board: List[str],
    hero_range: str,
    villain_range: str,
    pot_bb: float,
    bet_bb: Optional[float],
    action: str,
    size: Optional[str],
) -> Dict[str, object]:
    """翻后判分：role 由是否面对下注决定（cbet vs defense）。"""
    role = "caller" if (bet_bb and bet_bb > 0) else "pfr"
    _texture, hand, equity, rec = analyze_spot(
        role=role,
        hero=hero,
        board=board,
        villain_range=villain_range,
        pot_bb=pot_bb,
        bet_bb=bet_bb,
        hero_range=hero_range,
        trials=1500,
        ra_trials=300,
    )
    score = score_postflop(rec, action, size)
    if score.get("recommended_size"):
        score["recommended_size_label"] = size_label(action, str(score["recommended_size"]))
    if score.get("size"):
        score["size_label"] = size_label(action, str(score["size"]))

    grade = str(score["grade"])
    spot = "postflop_defense" if role == "caller" else "postflop_cbet"
    # 翻后严重度：偏离越"离谱"（选了明显更差的动作）+ 底池越大，越严重。
    severity = 0.0
    if grade == "mistake":
        base = 0.5
        # 价值牌该下注却过牌/弃牌，或空气该弃却跟/加，都更严重
        tier = str(hand.get("tier"))
        if tier in ("value",) and action in ("check", "fold"):
            base = 0.8
        if action == "fold" and rec["recommended"] in ("call", "raise", "bet"):
            base = max(base, 0.7)
        # 底池放大（相对起始 5.5bb 底池）
        severity = min(1.0, base * (0.7 + 0.3 * min(2.0, pot_bb / 12.0)))
    return {
        "street": street,
        "spot": spot,
        "spot_label": SPOT_LABELS.get(spot, spot),
        "made_label": hand.get("made_label"),
        "draw_label": hand.get("draw_label"),
        "tier": hand.get("tier"),
        "equity": round(equity, 3),
        "action": action,
        "size": size,
        "optimal_action": rec["recommended"],
        "grade": grade,
        "correct": bool(score["correct"]),
        "ev_loss_proxy": 0.0,
        "severity": round(severity, 3),
        "reasons": rec.get("reasons", []),
        "recommendation": rec,
        "grounded": True,
    }
