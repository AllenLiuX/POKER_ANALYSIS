"""阶段③：GTO 偏离标注（确定性引擎，无 LLM）。

把重建后的近似牌谱里**每个能接地的决策点**对照 GTO 基线，标注偏离：
- **翻前**：接现有范围表打分——
    * 首入池（folded-to）→ RFI（UTG/MP/CO/BTN/SB）；
    * 面对单次开池 → vs_RFI 防守（BB/BTN/SB vs 开池者，含 3bet=raise）。
  用 `score_action` 判级（optimal/acceptable/mistake）+ ev_loss 近似 + 倾向分类（过紧/过松/太被动/太激进）。
- **翻后**：暂用启发式——按最终成手强度（`classify_hand`）给出线路性质说明（价值/薄/诈唬倾向），
  明确标 `grounded=false, approximate=true`，不产出 EV/频率（无翻后解，绝不编数字）。

铁律：只有**底牌可见**的玩家（英雄 + 摊牌者）才打分；数字/判定全来自引擎，置信度继承重建。
未覆盖的 spot（3bet 后、跛入池、多次加注、位置未知）一律标 `grounded=false`，不臆造结论。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from app.poker.preflop.handclass import hand_class
from app.poker.preflop.ranges import load_spot
from app.poker.preflop.scoring import MIN_MIX, score_action

logger = logging.getLogger(__name__)

POSITION_ORDER = ["UTG", "MP", "CO", "BTN", "SB", "BB"]
_RFI_POSITIONS = {"UTG", "MP", "CO", "BTN", "SB"}  # data/ranges/*/RFI/*.json

# 位置别名归一（微扑克/常见缩写 → 我们范围表用的 6max 位置）
_POS_ALIAS = {
    "UTG": "UTG", "EP": "UTG", "UTG1": "UTG", "UTG+1": "UTG",
    "MP": "MP", "MP1": "MP", "LJ": "MP", "HJ": "MP", "UTG2": "MP",
    "CO": "CO",
    "BTN": "BTN", "BU": "BTN", "BTN(D)": "BTN", "D": "BTN", "DEALER": "BTN", "BUTTON": "BTN",
    "SB": "SB",
    "BB": "BB",
}

_AGGR = {"raise", "bet", "allin"}
_ACTION_LABELS = {"fold": "弃牌", "call": "跟注", "check": "过牌", "raise": "加注", "bet": "下注", "allin": "全下"}
_LEAK_LABELS = {
    "too_tight": "过紧",
    "too_loose": "过松",
    "too_passive": "太被动",
    "too_aggressive": "太激进",
    "line_error": "线路偏差",
}
_GRADE_LABELS = {"optimal": "最优", "acceptable": "可接受", "mistake": "偏离"}


def _norm_pos(pos: Optional[str]) -> Optional[str]:
    if not pos:
        return None
    return _POS_ALIAS.get(str(pos).strip().upper())


def _classify_leak(action: str, optimal: str) -> Optional[str]:
    """把偏离归为一种倾向（与 review.classify_leak 一致）。action==optimal → None。"""
    if not action or not optimal or action == optimal:
        return None
    o_aggr = optimal in _AGGR
    if optimal == "fold" and action != "fold":
        return "too_loose"
    if o_aggr and action == "fold":
        return "too_tight"
    if optimal in {"call", "check"} and action == "fold":
        return "too_tight"
    if o_aggr and action in {"call", "check"}:
        return "too_passive"
    if optimal in {"call", "check"} and action in _AGGR:
        return "too_aggressive"
    return "line_error"


def _preflop_action(player: Dict) -> Optional[Dict]:
    """取该玩家翻前的（首个）动作。返回 {chosen, raw_action, amount} 或 None。"""
    for a in player.get("actions") or []:
        if a.get("street") in (None, "翻前"):
            act = a.get("action")
            if not act:
                continue
            chosen = "raise" if act in _AGGR else act  # bet/allin 翻前视作 raise
            return {"chosen": chosen, "raw_action": act, "amount": a.get("amount")}
    return None


def _grade_preflop(fmt: str, spot: str, position: str, cls: str, chosen: str) -> Optional[Dict]:
    """对照范围表打分。spot 文件不存在 → None（不接地）。

    若所选动作不在该 spot 的 GTO 动作集里（如 SB 面对开池的 GTO 基线是 3bet/弃牌、无平跟，
    玩家却平跟），视为**离树偏离**：chosen_freq=0、判为 mistake，并标 off_tree 供 UI 说明。
    """
    try:
        ps = load_spot(fmt, spot, position)
    except FileNotFoundError:
        return None
    try:
        freqs = ps.hand_freqs(cls)
    except ValueError:
        return None
    if chosen in freqs:
        return score_action(freqs, chosen)
    optimal = max(freqs, key=lambda a: freqs[a])
    acceptable = {a for a, f in freqs.items() if f >= MIN_MIX}
    return {
        "correct": False,
        "grade": "mistake",
        "chosen": chosen,
        "chosen_freq": 0.0,
        "optimal_action": optimal,
        "optimal_freq": freqs[optimal],
        "frequencies": freqs,
        "is_mixed": len(acceptable) > 1,
        "ev_loss_proxy": round(float(freqs[optimal]), 6),
        "off_tree": True,
    }


def _spot_label(spot: str, position: str, opener: Optional[str]) -> str:
    if spot == "RFI":
        return f"翻前开池 · {position}"
    if spot == "vs_RFI" and opener:
        return f"翻前防守 · {position} vs {opener}"
    return spot


def _deviation_from_score(
    *, alias, is_hero, street, spot, position, opener, cls, score, base_conf
) -> Dict:
    chosen = str(score["chosen"])
    optimal = str(score["optimal_action"])
    leak = _classify_leak(chosen, optimal) if score["grade"] == "mistake" else None
    off_tree = bool(score.get("off_tree"))
    note = None
    if off_tree:
        note = f"该位置 GTO 基线为加注/弃牌（无{_ACTION_LABELS.get(chosen, chosen)}），按偏离处理。"
    return {
        "alias": alias,
        "is_hero": bool(is_hero),
        "street": street,
        "spot": spot,
        "spot_label": _spot_label(spot, position, opener),
        "position": position,
        "opener": opener,
        "hand_class": cls,
        "actual": chosen,
        "actual_label": _ACTION_LABELS.get(chosen, chosen),
        "grade": score["grade"],
        "grade_label": _GRADE_LABELS.get(str(score["grade"]), str(score["grade"])),
        "optimal_action": optimal,
        "optimal_label": _ACTION_LABELS.get(optimal, optimal),
        "chosen_freq": round(float(score["chosen_freq"]), 4),
        "optimal_freq": round(float(score["optimal_freq"]), 4),
        "frequencies": score["frequencies"],
        "is_mixed": bool(score["is_mixed"]),
        "deviation_type": leak,
        "deviation_label": _LEAK_LABELS.get(leak) if leak else None,
        "ev_loss_proxy": round(float(score["ev_loss_proxy"]), 4),
        "off_tree": off_tree,
        "grounded": True,
        "approximate": False,
        "confidence": round(base_conf, 2),
        "note": note,
    }


def _postflop_note(player: Dict, board: List[str], base_conf: float) -> Optional[Dict]:
    """翻后启发式线路说明（不打分、不给 EV，明确近似）。仅底牌可见 + 有翻后动作时产出。"""
    hole = player.get("hole_cards") or []
    if len(hole) < 2 or len(board) < 3:
        return None
    postflop_actions = [a for a in (player.get("actions") or []) if a.get("street") in ("翻牌", "转牌", "河牌")]
    if not postflop_actions:
        return None
    try:
        from app.poker.postflop.handstrength import classify_hand

        hc = classify_hand(hole[:2], board)
    except Exception:  # noqa: BLE001 — 牌面异常不致命
        return None

    aggressive = any(a.get("action") in _AGGR for a in postflop_actions)
    tier = str(hc.get("tier"))
    made_label = str(hc.get("made_label"))
    draw_label = str(hc.get("draw_label") or "")

    if aggressive:
        if tier == "value":
            nature = f"以 {made_label} 主动下注/加注——价值线，合理。"
        elif tier == "draw":
            nature = f"持 {draw_label or '强听牌'} 主动进攻——半诈唬线。"
        elif tier in ("medium",):
            nature = f"以 {made_label} 主动下注——偏薄价值/保护，视对手而定。"
        else:
            nature = f"成手仅 {made_label} 却主动进攻——诈唬倾向（注意频率）。"
    else:
        if tier == "value":
            nature = f"持 {made_label} 走被动线（跟注/过牌）——可能漏价值。"
        elif tier in ("medium",):
            nature = f"以 {made_label} 控池/摊牌——被动线，通常可接受。"
        else:
            nature = f"成手 {made_label} 被动跟注/过牌。"

    return {
        "alias": player.get("alias"),
        "is_hero": bool(player.get("is_hero")),
        "street": "翻后",
        "spot": "postflop",
        "spot_label": "翻后线路（启发式）",
        "made_label": made_label,
        "draw_label": draw_label,
        "tier": tier,
        "grounded": False,
        "approximate": True,
        "confidence": round(base_conf * 0.8, 2),
        "note": nature,
    }


def analyze_deviations(facts: Dict, reconstruction: Optional[Dict]) -> Dict:
    """重建结果 → 逐人偏离标注（翻前接地 + 翻后启发式）。"""
    fmt = "6max_100bb"
    empty = {
        "supported": False,
        "format": fmt,
        "players": [],
        "counts": {"graded": 0, "grounded": 0, "mistakes": 0},
        "note": "",
    }
    if not reconstruction:
        return {**empty, "note": "无重建结果，无法标注偏离。"}

    players = reconstruction.get("players") or []
    board = reconstruction.get("board") or facts.get("board") or []
    base_conf = float(reconstruction.get("confidence") or 0.5)

    # ---- 翻前：按位置顺序走一遍，判定 RFI / vs_RFI ----
    # 收集有翻前动作且位置已知的玩家；按位置顺序排。
    pre = []
    total_aggr = 0
    for idx, p in enumerate(players):
        pa = _preflop_action(p)
        if pa and pa["raw_action"] in _AGGR:
            total_aggr += 1
        pos = _norm_pos(p.get("position"))
        if pa and pos:
            pre.append((POSITION_ORDER.index(pos), idx, p, pos, pa))
    pre.sort(key=lambda t: t[0])

    known_aggr = sum(1 for _, _, _, _, pa in pre if pa["raw_action"] in _AGGR)
    order_uncertain = known_aggr != total_aggr  # 有位置未知的进攻者 → 顺序可能不准

    # 每个玩家 idx -> 翻前偏离
    preflop_dev: Dict[int, Dict] = {}
    raises_before = 0
    limped = False
    opener_pos: Optional[str] = None
    for _, idx, p, pos, pa in pre:
        chosen = pa["chosen"]
        hole = p.get("hole_cards") or []
        cls = None
        if len(hole) >= 2:
            try:
                cls = hand_class(hole[0], hole[1])
            except Exception:  # noqa: BLE001
                cls = None

        spot = opener = None
        if raises_before == 0 and not limped:
            spot = "RFI"  # 首入池决策（folded-to）
        elif raises_before == 1 and not limped:
            spot, opener = "vs_RFI", opener_pos  # 面对单次开池

        if cls and spot == "RFI" and pos in _RFI_POSITIONS:
            score = _grade_preflop(fmt, "RFI", pos, cls, chosen)
            if score:
                conf = base_conf * (0.85 if p.get("uncertain") else 1.0) * (0.8 if order_uncertain else 1.0)
                dev = _deviation_from_score(
                    alias=p.get("alias"), is_hero=p.get("is_hero"), street="翻前",
                    spot="RFI", position=pos, opener=None, cls=cls, score=score, base_conf=conf,
                )
                preflop_dev[idx] = dev
        elif cls and spot == "vs_RFI" and opener:
            key = f"{pos}_vs_{opener}"
            score = _grade_preflop(fmt, "vs_RFI", key, cls, chosen)
            if score:
                conf = base_conf * (0.85 if p.get("uncertain") else 1.0) * (0.8 if order_uncertain else 1.0)
                dev = _deviation_from_score(
                    alias=p.get("alias"), is_hero=p.get("is_hero"), street="翻前",
                    spot="vs_RFI", position=pos, opener=opener, cls=cls, score=score, base_conf=conf,
                )
                preflop_dev[idx] = dev

        # 推进池状态
        if pa["raw_action"] == "call" and raises_before == 0:
            limped = True
        if pa["raw_action"] in _AGGR:
            if raises_before == 0:
                opener_pos = pos
            raises_before += 1

    # ---- 组装逐人结果（翻前偏离 + 翻后启发式）----
    out_players: List[Dict] = []
    graded = grounded = mistakes = 0
    for idx, p in enumerate(players):
        devs: List[Dict] = []
        if idx in preflop_dev:
            d = preflop_dev[idx]
            devs.append(d)
            graded += 1
            grounded += 1
            if d["grade"] == "mistake":
                mistakes += 1
        note = _postflop_note(p, board, base_conf)
        if note:
            devs.append(note)
        if not devs:
            continue
        out_players.append(
            {
                "alias": p.get("alias"),
                "is_hero": bool(p.get("is_hero")),
                "position": _norm_pos(p.get("position")),
                "hole_cards": p.get("hole_cards") or [],
                "net": p.get("net"),
                "deviations": devs,
            }
        )

    notes = []
    if order_uncertain:
        notes.append("部分玩家位置未识别，翻前接地可能不准，置信度已下调。")
    if not out_players:
        notes.append("本手没有可接地的决策点（多为未摊牌/位置缺失/3bet 后等未覆盖 spot）。")

    return {
        "supported": bool(out_players),
        "format": fmt,
        "players": out_players,
        "counts": {"graded": graded, "grounded": grounded, "mistakes": mistakes},
        "note": (
            "翻前对照开源近 GTO 范围表（RFI / vs 单次开池）判级；翻后为启发式线路说明（近似，不含 EV）。"
            + ("" if not notes else " " + " ".join(notes))
        ),
    }
