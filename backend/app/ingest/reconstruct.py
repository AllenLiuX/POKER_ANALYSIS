"""阶段②：把观测事实（尤其是 hand_replay 的逐街动作原文）重建为结构化下注序列，
并用引擎规则校验。

铁律：LLM 只在阶段①做视觉转写；这里的解析、推算与校验完全是**确定性引擎逻辑**。

关键设计——用「净额」推算投入，而非只信任逐街动作串：
截图里每位玩家右侧的净额（net）是单个数字，识别最可靠；而逐街动作串是多段文本，
多模态模型偶尔会漏读中间某一街。因此每位玩家的**总投入以 net 推算**（输家=|net|，
含盲注/前注；赢家=底池−net），再与「逐街动作金额之和」交叉核对：若两者对不上，
说明该行动作很可能没被完整识别，标记为待复核，避免展示"投入 32 却净输 1200"这类自相矛盾。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

# 需要计入投入的动作（会往池里放钱）
_MONEY_ACTIONS = {"bet", "raise", "call", "allin"}

_ACTION_LABELS = {
    "fold": "弃牌",
    "check": "过牌",
    "call": "跟注",
    "bet": "下注",
    "raise": "加注",
    "allin": "全下",
}

# 回放里每位玩家一行的动作按街从左到右排列：翻前 / 翻牌 / 转牌 / 河牌
_STREETS = ["翻前", "翻牌", "转牌", "河牌"]


def _street_for_index(i: int) -> str:
    return _STREETS[i] if i < len(_STREETS) else _STREETS[-1]


def _classify_action(piece: str) -> Optional[str]:
    low = piece.lower()
    if "allin" in low or "all-in" in low or "全下" in piece or "梭哈" in piece:
        return "allin"
    if "加注" in piece or "再加" in piece:
        return "raise"
    if "下注" in piece:
        return "bet"
    if "跟注" in piece or "跟" in piece or "call" in low:
        return "call"
    if "过牌" in piece or "让牌" in piece or "check" in low or piece.strip() in {"过", "看牌"}:
        return "check"
    if "弃牌" in piece or "fold" in low or piece.strip() in {"弃"}:
        return "fold"
    return None


def parse_actions(raw: Optional[str]) -> List[Dict[str, object]]:
    """'加注32 → 下注38 → 跟注188 → Allin941' → 逐个 {action, amount, label, raw}。"""
    if not raw:
        return []
    out: List[Dict[str, object]] = []
    for piece in re.split(r"[→>,/、;；]+|\s{2,}", str(raw)):
        piece = piece.strip()
        if not piece:
            continue
        action = _classify_action(piece)
        if not action:
            continue
        m = re.search(r"(\d+(?:\.\d+)?)", piece.replace(",", ""))
        amount = float(m.group(1)) if m else None
        out.append(
            {"action": action, "amount": amount, "label": _ACTION_LABELS[action], "raw": piece}
        )
    return out


def _round(x: float) -> float:
    return round(x, 2)


def reconstruct_hand(facts: Dict) -> Dict[str, object]:
    """把观测事实重建为近似下注序列 + 引擎校验结果。"""
    stype = str(facts.get("screenshot_type") or "unknown")
    players_in = facts.get("players") or []
    pot = facts.get("pot")
    pot_val = float(pot) if isinstance(pot, (int, float)) else None

    # 赢家 = net 最大且为正者（简化：不处理边池/多赢家平分）
    winner_idx: Optional[int] = None
    best_net = 0.0
    for i, p in enumerate(players_in):
        n = p.get("net")
        if isinstance(n, (int, float)) and n > best_net:
            best_net, winner_idx = float(n), i

    players: List[Dict[str, object]] = []
    for idx, p in enumerate(players_in):
        raw_actions = parse_actions(p.get("actions_raw"))
        actions: List[Dict[str, object]] = []
        for i, a in enumerate(raw_actions):
            actions.append({**a, "street": _street_for_index(i) if stype == "hand_replay" else None})

        money = [a["amount"] for a in raw_actions if a["action"] in _MONEY_ACTIONS and a["amount"]]  # type: ignore[misc]
        parsed_invested = _round(sum(money))  # type: ignore[arg-type]
        has_money = len(money) > 0

        net = p.get("net")
        net_val = float(net) if isinstance(net, (int, float)) else None

        # 用净额推算总投入（含盲注/前注）：赢家=底池−net；其余=|net|
        if net_val is not None and idx == winner_idx and pot_val is not None:
            contributed = _round(pot_val - net_val)
        elif net_val is not None and idx != winner_idx:
            contributed = _round(max(0.0, -net_val))
        else:
            contributed = parsed_invested

        # 交叉核对：仅当该玩家有下注类动作、且能从 net 得到期望投入时才校验
        uncertain = False
        if has_money and net_val is not None and (idx != winner_idx or pot_val is not None):
            tol = max(4.0, 0.1 * abs(contributed))  # 容许盲注/前注带来的小差
            if abs(parsed_invested - contributed) > tol:
                uncertain = True

        players.append(
            {
                "alias": p.get("alias"),
                "position": p.get("position"),
                "is_hero": bool(p.get("is_hero")),
                "is_winner": idx == winner_idx,
                "hole_cards": p.get("hole_cards") or [],
                "net": net,
                "invested": contributed,  # 以净额推算为准（权威、含盲注/前注）
                "parsed_invested": parsed_invested,  # 逐街动作金额之和（供核对）
                "actions": actions,
                "uncertain": uncertain,
            }
        )

    net_list = [float(p["net"]) for p in players if isinstance(p["net"], (int, float))]  # type: ignore[arg-type]
    net_sum = _round(sum(net_list)) if net_list else None
    abs_scale = sum(abs(n) for n in net_list) if net_list else 0.0
    net_ok = net_sum is not None and abs(net_sum) <= max(2.0, 0.02 * abs_scale)

    invested_sum = _round(sum(float(p["invested"]) for p in players))  # type: ignore[arg-type]
    uncertain_count = sum(1 for p in players if p["uncertain"])
    rows_consistent = uncertain_count == 0
    has_actions = any(p["actions"] for p in players)

    if stype == "hand_replay" and has_actions:
        if net_ok and rows_consistent:
            status, confidence = "validated", 0.9
        elif net_ok:
            status, confidence = "needs_review", 0.6
        else:
            status, confidence = "needs_review", 0.45
    elif has_actions:
        status, confidence = "needs_review", 0.4
    else:
        status, confidence = "needs_user", 0.25

    return {
        "status": status,  # validated | needs_review | needs_user
        "confidence": round(confidence, 2),
        "pot": pot,
        "board": facts.get("board") or [],
        "players": players,
        "checks": {
            "net_sum": net_sum,
            "net_ok": bool(net_ok),
            "invested_sum": invested_sum,
            "pot": pot,
            "uncertain_count": uncertain_count,
            "rows_consistent": bool(rows_consistent),
        },
        "note": (
            "每位玩家投入以净额推算（含盲注/前注），并与逐街动作交叉核对；"
            "标记「待复核」的行，其动作可能未被完整识别。"
        ),
    }
