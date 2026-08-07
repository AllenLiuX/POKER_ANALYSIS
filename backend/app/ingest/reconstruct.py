"""阶段②：把观测事实（尤其是 hand_replay 的逐街动作原文）重建为结构化下注序列，
并用引擎规则校验（净额守恒 / 底池一致）。

铁律：LLM 只在阶段①做视觉转写；这里的解析与校验完全是**确定性引擎逻辑**，
真相（合法性/守恒）由引擎判定，不经过 LLM。回放类截图动作已显式标注，属高置信主路径；
结算类或缺动作的截图标为待用户确认（needs_user）。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

# 需要计入投入的动作（会往池里放钱）
_MONEY_ACTIONS = {"bet", "raise", "call", "allin"}

# 中文/英文动作词 → 规范化动作（顺序敏感：先匹配更具体的）
_ACTION_LABELS = {
    "fold": "弃牌",
    "check": "过牌",
    "call": "跟注",
    "bet": "下注",
    "raise": "加注",
    "allin": "全下",
}


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


def _sum(vals: List[float]) -> float:
    return round(sum(vals), 2)


def reconstruct_hand(facts: Dict) -> Dict[str, object]:
    """把观测事实重建为近似下注序列 + 引擎校验结果。"""
    stype = str(facts.get("screenshot_type") or "unknown")
    players_in = facts.get("players") or []
    pot = facts.get("pot")

    players: List[Dict[str, object]] = []
    for p in players_in:
        actions = parse_actions(p.get("actions_raw"))
        invested = _sum(
            [a["amount"] for a in actions if a["action"] in _MONEY_ACTIONS and a["amount"]]  # type: ignore[misc]
        )
        players.append(
            {
                "alias": p.get("alias"),
                "position": p.get("position"),
                "is_hero": bool(p.get("is_hero")),
                "hole_cards": p.get("hole_cards") or [],
                "net": p.get("net"),
                "invested": invested,
                "actions": actions,
                "street_count": len(actions),
            }
        )

    nets = [p["net"] for p in players if isinstance(p["net"], (int, float))]
    net_sum = _sum([float(n) for n in nets]) if nets else None  # type: ignore[arg-type]
    abs_scale = _sum([abs(float(n)) for n in nets]) if nets else 0.0  # type: ignore[arg-type]
    net_ok = net_sum is not None and abs(net_sum) <= max(2.0, 0.02 * abs_scale)

    invested_sum = _sum([float(p["invested"]) for p in players])  # type: ignore[arg-type]
    pot_consistent = (
        isinstance(pot, (int, float)) and abs(invested_sum - float(pot)) <= max(2.0, 0.05 * float(pot))
    )

    has_actions = any(p["actions"] for p in players)

    if stype == "hand_replay" and has_actions:
        if net_ok and pot_consistent:
            status = "validated"
            confidence = 0.9
        elif net_ok or pot_consistent:
            # 部分校验通过（常见于个别玩家动作被漏读）：可用但需人工核对。
            status = "needs_review"
            confidence = 0.65
        else:
            status = "needs_review"
            confidence = 0.45
    elif has_actions:
        status = "needs_review"
        confidence = 0.4
    else:
        status = "needs_user"
        confidence = 0.25

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
            "pot_consistent": bool(pot_consistent),
        },
        "note": "逐街动作重建为近似结果；净额守恒/底池一致由引擎校验，低置信项需人工确认后再进入分析。",
    }
