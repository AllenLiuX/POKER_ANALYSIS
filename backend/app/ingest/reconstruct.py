"""阶段②：把观测事实（尤其是 hand_replay 的逐街动作原文）重建为结构化下注序列，
并用引擎规则校验。

铁律：LLM 只在阶段①做视觉转写；这里的解析、推算与校验完全是**确定性引擎逻辑**。

关键设计——用「净额」推算投入，而非只信任逐街动作串：
截图里每位玩家右侧的净额（net）是单个数字，识别最可靠；而逐街动作串是多段文本，
多模态模型偶尔会漏读中间某一街。因此每位玩家的**总投入以 net 推算**（输家=|net|，
含盲注/前注；赢家=底池−net），再与「逐街动作金额之和」交叉核对：若两者对不上，
说明该行动作很可能没被完整识别，标记为待复核，避免展示"投入 32 却净输 1200"这类自相矛盾。

保险对账：微扑克里若有人买保险，其净额通常已含保险盈亏，导致全桌净额之和不为 0（保险的
钱来自/流向系统而非牌桌）。因此这里用「桌面净额」table_net = net − insurance 还原纯牌桌
结果：赢家判定、投入推算、全桌净额守恒都以 table_net 为准；展示仍保留玩家真实净额 net。
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
# actions_by_street 的英文键 → 中文街道标签（下游 deviation 以中文标签判街）
_STREET_KEY_TO_LABEL = {"preflop": "翻前", "flop": "翻牌", "turn": "转牌", "river": "河牌"}


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


def parse_actions_by_street(by_street: Dict) -> List[Dict[str, object]]:
    """{preflop:[...], flop:[...], ...} → 逐个动作 {action, amount, label, raw, street}（已带准确街道）。

    比按序号推断更可靠：每个动作的街道由模型明确归位，能正确处理"某街过牌 / 一街多动作"。
    """
    out: List[Dict[str, object]] = []
    for key in ("preflop", "flop", "turn", "river"):
        entries = by_street.get(key)
        if not isinstance(entries, list):
            continue
        label = _STREET_KEY_TO_LABEL[key]
        for piece in entries:
            for a in parse_actions(str(piece)):
                out.append({**a, "street": label})
    return out


def _round(x: float) -> float:
    return round(x, 2)


def reconstruct_hand(facts: Dict) -> Dict[str, object]:
    """把观测事实重建为近似下注序列 + 引擎校验结果。"""
    stype = str(facts.get("screenshot_type") or "unknown")
    players_in = facts.get("players") or []
    pot = facts.get("pot")
    pot_val = float(pot) if isinstance(pot, (int, float)) else None

    # 桌面净额 table_net = net − 保险净额：把保险盈亏从右侧净额里剥离，还原纯牌桌结果。
    # 没有保险（insurance 缺省）时 table_net == net，行为与旧逻辑完全一致。
    def _table_net(p: Dict) -> Optional[float]:
        n = p.get("net")
        if not isinstance(n, (int, float)):
            return None
        ins = p.get("insurance")
        ins_val = float(ins) if isinstance(ins, (int, float)) else 0.0
        return float(n) - ins_val

    # 赢家 = 桌面净额最大且为正者（简化：不处理边池/多赢家平分）
    winner_idx: Optional[int] = None
    best_net = 0.0
    for i, p in enumerate(players_in):
        tn = _table_net(p)
        if tn is not None and tn > best_net:
            best_net, winner_idx = tn, i

    players: List[Dict[str, object]] = []
    for idx, p in enumerate(players_in):
        by_street = p.get("actions_by_street")
        if isinstance(by_street, dict) and by_street:
            # 首选：模型已按街分组，直接采用准确街道
            actions: List[Dict[str, object]] = parse_actions_by_street(by_street)
        else:
            # 回退：只有扁平串时按出现序号推断街道（翻前/翻牌/转牌/河牌）
            raw_actions = parse_actions(p.get("actions_raw"))
            actions = [
                {**a, "street": _street_for_index(i) if stype == "hand_replay" else None}
                for i, a in enumerate(raw_actions)
            ]

        money = [a["amount"] for a in actions if a["action"] in _MONEY_ACTIONS and a["amount"]]  # type: ignore[misc]
        parsed_invested = _round(sum(money))  # type: ignore[arg-type]
        has_money = len(money) > 0

        net = p.get("net")
        net_val = float(net) if isinstance(net, (int, float)) else None
        ins = p.get("insurance")
        ins_val = float(ins) if isinstance(ins, (int, float)) else None
        table_net = _table_net(p)  # net − 保险（用于对账/投入推算）

        # 用桌面净额推算总投入（含盲注/前注）：赢家=底池−table_net；其余=|table_net|
        if table_net is not None and idx == winner_idx and pot_val is not None:
            contributed = _round(pot_val - table_net)
        elif table_net is not None and idx != winner_idx:
            contributed = _round(max(0.0, -table_net))
        else:
            contributed = parsed_invested

        # 交叉核对：仅当该玩家有下注类动作、且能从桌面净额得到期望投入时才校验
        uncertain = False
        if has_money and table_net is not None and (idx != winner_idx or pot_val is not None):
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
                "net": net,  # 玩家真实净额（含保险盈亏，用于展示）
                "insurance": ins_val,  # 保险净额（赔付为正/保费为负；无则 None）
                "table_net": table_net if table_net is None else _round(table_net),  # 纯桌面净额
                "invested": contributed,  # 以桌面净额推算为准（权威、含盲注/前注）
                "parsed_invested": parsed_invested,  # 逐街动作金额之和（供核对）
                "actions": actions,
                "uncertain": uncertain,
            }
        )

    # 全桌净额守恒以「桌面净额」判定：剥离保险后各家应约和为 0。
    tnet_list = [float(p["table_net"]) for p in players if isinstance(p["table_net"], (int, float))]  # type: ignore[arg-type]
    net_sum = _round(sum(tnet_list)) if tnet_list else None
    abs_scale = sum(abs(n) for n in tnet_list) if tnet_list else 0.0
    net_ok = net_sum is not None and abs(net_sum) <= max(2.0, 0.02 * abs_scale)
    ins_list = [float(p["insurance"]) for p in players if isinstance(p["insurance"], (int, float))]  # type: ignore[arg-type]
    insurance_total = _round(sum(ins_list)) if ins_list else None

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
            "net_sum": net_sum,  # 桌面净额（已剥离保险）之和，应约等于 0
            "net_ok": bool(net_ok),
            "invested_sum": invested_sum,
            "pot": pot,
            "uncertain_count": uncertain_count,
            "rows_consistent": bool(rows_consistent),
            "insurance_total": insurance_total,  # 全桌保险净额（None=本手无保险）
        },
        "note": (
            "每位玩家投入以桌面净额（净额−保险）推算（含盲注/前注），并与逐街动作交叉核对；"
            "标记「待复核」的行，其动作可能未被完整识别。若有人买保险，净额之和用剥离保险后的桌面净额对账。"
        ),
    }
