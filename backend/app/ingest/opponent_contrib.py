"""阶段⑤：逐手 → 逐对手「可加计数器」贡献（确定性引擎，无 LLM）。

设计要点（对应服务端权威聚合 Option A）：
- 每手每位玩家产出一份**可加计数器**贡献（全部为数值叶子），
  合并/回滚 = 逐字段加减，因此增量更新天然幂等、可交换、可回滚、可重建。
- 计数器均以 {n, k} 或 {…: 计数} 形式给出：n=观测到的机会数，k=命中数。
  **重要**：截图样本天然偏向摊牌/关键手，这里的频率是「导入样本内的观测频率」，
  不是真实总体 HUD（读数时需按样本量做置信收缩，见前端）。

分两层：
- Tier1 观测频率：从重建的逐街动作直接读出（vpip/pfr/开池/面对开池反应/翻后激进度/
  c-bet/面对 c-bet 弃牌/看翻牌/摊牌/摊牌获胜）。位置相关项仅在能定位翻前顺序时统计。
- Tier2 接地偏离：从 analyze_deviations 的**已接地**决策聚合漏洞倾向（翻前/翻后分开）。

铁律：只做确定性统计，不臆造顺序。多路/未知位置等无法确定的情形一律跳过对应计数，
宁可少统计也不给错误分母。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from app.ingest.deviation import (
    POSITION_ORDER,
    _AGGR,
    _norm_pos,
    _preflop_action,
)

_POST_STREETS = ("翻牌", "转牌", "河牌")
_MONEY_ACTS = {"bet", "raise", "allin", "call"}
_PRE_SPOTS = {"RFI", "vs_RFI"}


def _empty_counters() -> Dict:
    """一份全零计数器骨架（所有叶子都是数值，便于 DB 端 deep-add 合并）。"""
    return {
        "vpip": {"n": 0, "k": 0},
        "pfr": {"n": 0, "k": 0},
        "pf_open": {"n": 0, "k": 0},          # 首入池时开池
        "pf_vs_open": {"n": 0, "fold": 0, "call": 0, "raise": 0},  # 面对单次开池的反应
        "af_post": {"aggr": 0, "passive": 0}, # 翻后激进度分量（bet+raise vs call+check）
        "cbet_flop": {"n": 0, "k": 0},        # 作为翻前进攻方在翻牌下注（仅 SRP·单挑）
        "fold_vs_cbet_flop": {"n": 0, "k": 0},# 面对翻牌 c-bet 弃牌（仅 SRP·单挑）
        "saw_flop": {"k": 0},
        "wtsd": {"n": 0, "k": 0},             # 看到翻牌后是否走到摊牌
        "won_sd": {"n": 0, "k": 0},           # 摊牌是否获胜
        "graded_pre": {"n": 0, "mistakes": 0},
        "graded_post": {"n": 0, "mistakes": 0},
        "leaks_pre": {},                      # deviation_type -> 次数
        "leaks_post": {},
    }


def _post_actions(player: Dict) -> List[Dict]:
    return [a for a in (player.get("actions") or []) if a.get("street") in _POST_STREETS]


def _street_money(actions: List[Dict], street: str) -> float:
    return sum(
        float(a["amount"])
        for a in actions
        if a.get("street") == street and a.get("action") in _MONEY_ACTS and a.get("amount")
    )


def _has_action_on(actions: List[Dict], street: str) -> bool:
    return any(a.get("street") == street and a.get("action") for a in actions)


def _folded_on(actions: List[Dict], street: str) -> bool:
    return any(a.get("street") == street and a.get("action") == "fold" for a in actions)


def _is_showdown(player: Dict) -> bool:
    """摊牌可见底牌 ⟺ 走到摊牌（英雄底牌恒可见，故本函数只对非英雄可靠）。"""
    return len(player.get("hole_cards") or []) >= 2


def _preflop_walk(players: List[Dict]):
    """按位置顺序走一遍翻前，产出：
    - situ_by_idx: idx -> 'first_in' | 'vs_open' | 'other'（该玩家决策时的池状态）
    - is_srp: 是否单加注底池（恰好一次加注、无跛入）
    - aggressor_idx: SRP 下翻前进攻方（开池者）的 idx，否则 None
    仅收录「位置已知且有翻前动作」的玩家（与 deviation.py 口径一致）。
    """
    pre = []
    for idx, p in enumerate(players):
        pa = _preflop_action(p)
        pos = _norm_pos(p.get("position"))
        if pa and pos:
            pre.append((POSITION_ORDER.index(pos), idx, pa))
    pre.sort(key=lambda t: t[0])

    situ_by_idx: Dict[int, str] = {}
    raises_before = 0
    limped = False
    opener_idx: Optional[int] = None
    for _, idx, pa in pre:
        if raises_before == 0 and not limped:
            situ_by_idx[idx] = "first_in"
        elif raises_before == 1 and not limped:
            situ_by_idx[idx] = "vs_open"
        else:
            situ_by_idx[idx] = "other"
        # 推进池状态
        if pa["raw_action"] == "call" and raises_before == 0:
            limped = True
        if pa["raw_action"] in _AGGR:
            if raises_before == 0:
                opener_idx = idx
            raises_before += 1

    is_srp = (raises_before == 1) and not limped
    aggressor_idx = opener_idx if is_srp else None
    return situ_by_idx, is_srp, aggressor_idx


def _tier2_by_alias(analysis: Optional[Dict]) -> Dict[str, Dict]:
    """从 analyze_deviations 输出聚合每个 alias 的接地偏离（翻前/翻后分开）。"""
    out: Dict[str, Dict] = {}
    if not analysis or not analysis.get("players"):
        return out
    for pl in analysis["players"]:
        alias = (pl.get("alias") or "").strip()
        if not alias:
            continue
        acc = out.setdefault(
            alias,
            {"graded_pre": {"n": 0, "mistakes": 0}, "graded_post": {"n": 0, "mistakes": 0},
             "leaks_pre": {}, "leaks_post": {}},
        )
        for d in pl.get("deviations") or []:
            if not d.get("grounded"):
                continue
            spot = str(d.get("spot") or "")
            is_pre = spot in _PRE_SPOTS
            is_post = spot.startswith("postflop")
            if not (is_pre or is_post):
                continue
            bucket = "graded_pre" if is_pre else "graded_post"
            leaks = "leaks_pre" if is_pre else "leaks_post"
            acc[bucket]["n"] += 1
            if d.get("grade") == "mistake":
                acc[bucket]["mistakes"] += 1
                dt = d.get("deviation_type")
                if dt:
                    acc[leaks][dt] = acc[leaks].get(dt, 0) + 1
    return out


def _player_counters(
    idx: int, player: Dict, players: List[Dict], board_len: int,
    situ_by_idx: Dict[int, str], is_srp: bool, aggressor_idx: Optional[int],
    tier2: Optional[Dict],
) -> Dict:
    c = _empty_counters()
    pa = _preflop_action(player)
    pos = _norm_pos(player.get("position"))
    post = _post_actions(player)
    showdown = _is_showdown(player)
    # 看到翻牌：有翻后动作，或摊牌可见（能走到摊牌必然看过翻牌）
    saw_flop = bool(post) or showdown

    # ---- 位置相关（需要能定位翻前决策）----
    if pa and pos:
        c["vpip"]["n"] = 1
        c["pfr"]["n"] = 1
        if pa["raw_action"] in _MONEY_ACTS:  # 主动投钱（跟/加/全下）
            c["vpip"]["k"] = 1
        if pa["raw_action"] in _AGGR:
            c["pfr"]["k"] = 1
        situ = situ_by_idx.get(idx)
        if situ == "first_in":
            c["pf_open"]["n"] = 1
            if pa["raw_action"] in _AGGR:
                c["pf_open"]["k"] = 1
        elif situ == "vs_open":
            c["pf_vs_open"]["n"] = 1
            if pa["raw_action"] == "fold":
                c["pf_vs_open"]["fold"] = 1
            elif pa["raw_action"] == "call":
                c["pf_vs_open"]["call"] = 1
            elif pa["raw_action"] in _AGGR:
                c["pf_vs_open"]["raise"] = 1

    # ---- 翻后激进度（位置无关）----
    for a in post:
        act = a.get("action")
        if act in _AGGR:
            c["af_post"]["aggr"] += 1
        elif act in ("call", "check"):
            c["af_post"]["passive"] += 1

    # ---- 看翻牌 / 摊牌 ----
    if saw_flop:
        c["saw_flop"]["k"] = 1
        c["wtsd"]["n"] = 1
        if showdown:
            c["wtsd"]["k"] = 1
            c["won_sd"]["n"] = 1
            net = player.get("net")
            if isinstance(net, (int, float)) and net > 0:
                c["won_sd"]["k"] = 1

    # ---- c-bet / 面对 c-bet（仅 SRP·翻后单挑，能定位进攻方）----
    if is_srp and aggressor_idx is not None:
        postflop_idx = [
            i for i, pp in enumerate(players)
            if _post_actions(pp) or _is_showdown(pp)
        ]
        if len(postflop_idx) == 2 and aggressor_idx in postflop_idx and board_len >= 3:
            defender_idx = postflop_idx[0] if postflop_idx[1] == aggressor_idx else postflop_idx[1]
            aggr_flop_money = _street_money(_post_actions(players[aggressor_idx]), "翻牌")
            if idx == aggressor_idx:
                c["cbet_flop"]["n"] = 1
                if aggr_flop_money > 0:
                    c["cbet_flop"]["k"] = 1
            elif idx == defender_idx and aggr_flop_money > 0:
                # 防守方面对翻牌 c-bet：需其在翻牌有明确动作才计（否则顺序不明，跳过）
                dact = _post_actions(players[defender_idx])
                if _has_action_on(dact, "翻牌"):
                    c["fold_vs_cbet_flop"]["n"] = 1
                    if _folded_on(dact, "翻牌"):
                        c["fold_vs_cbet_flop"]["k"] = 1

    # ---- Tier2 接地偏离 ----
    if tier2:
        c["graded_pre"] = dict(tier2.get("graded_pre", c["graded_pre"]))
        c["graded_post"] = dict(tier2.get("graded_post", c["graded_post"]))
        c["leaks_pre"] = dict(tier2.get("leaks_pre", {}))
        c["leaks_post"] = dict(tier2.get("leaks_post", {}))

    return c


def hand_contributions(
    facts: Dict, reconstruction: Optional[Dict], analysis: Optional[Dict] = None
) -> Dict:
    """一手 → 逐对手可加计数器贡献。

    返回 {"hand_id", "players": [{alias, is_hero, net, counters}]}。
    counters 全为数值叶子，可在 DB 端逐字段相加合并（幂等由 hand_id 守卫保证）。
    """
    if not reconstruction:
        return {"hand_id": facts.get("hand_id"), "players": []}
    players = reconstruction.get("players") or []
    board = reconstruction.get("board") or facts.get("board") or []
    board_len = len(board)

    situ_by_idx, is_srp, aggressor_idx = _preflop_walk(players)
    tier2_map = _tier2_by_alias(analysis)

    out_players: List[Dict] = []
    for idx, p in enumerate(players):
        alias = (p.get("alias") or "").strip()
        if not alias:
            continue
        counters = _player_counters(
            idx, p, players, board_len, situ_by_idx, is_srp, aggressor_idx, tier2_map.get(alias)
        )
        net = p.get("net")
        out_players.append(
            {
                "alias": alias,
                "is_hero": bool(p.get("is_hero")),
                "net": float(net) if isinstance(net, (int, float)) else 0.0,
                "counters": counters,
            }
        )
    return {"hand_id": facts.get("hand_id"), "players": out_players}
