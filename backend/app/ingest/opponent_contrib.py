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

import re
from typing import Dict, List, Optional

from app.ingest.deviation import (
    POSITION_ORDER,
    _AGGR,
    _norm_pos,
    _preflop_action,
)
from app.poker.postflop.handstrength import classify_hand

_POST_STREETS = ("翻牌", "转牌", "河牌")
_PREV_STREET = {"翻牌": "翻前", "转牌": "翻牌", "河牌": "转牌"}
_STREET_BOARD = {"翻牌": 3, "转牌": 4, "河牌": 5}
_MONEY_ACTS = {"bet", "raise", "allin", "call"}
_AGGR_ACTS = {"bet", "raise", "allin"}
_OPEN_ACTS = {"check", "bet", "raise", "allin"}
_PRE_SPOTS = {"RFI", "vs_RFI"}
_TWO_PAIR_PLUS = {
    "Straight Flush", "Quads", "Full House", "Flush", "Straight", "Trips", "Two Pair",
}
_MADE_RANK = {
    "High Card": 0,
    "Pair": 1,
    "Two Pair": 2,
    "Trips": 3,
    "Straight": 4,
    "Flush": 5,
    "Full House": 6,
    "Quads": 7,
    "Straight Flush": 8,
}
_COMPLETED_DRAW = {"Straight", "Flush", "Straight Flush", "Full House", "Quads"}


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
        "big_pot_calldown": {"n": 0, "k": 0},
        "river_station": {"n": 0, "k": 0},
        "missed_initiative": {"n": 0, "k": 0},
        "ip_river_checkback": {"n": 0, "k": 0},
        "call_then_lead": {"n": 0, "k": 0},
        "overfold_small_bet": {"n": 0, "k": 0},
        "fold_to_barrel": {"n": 0, "k": 0},
        "fold_to_postflop_raise": {"n": 0, "k": 0},
        "slowplay_two_pair_plus": {"n": 0, "k": 0},
        "checked_strong_pair": {"n": 0, "k": 0},
        "delayed_value": {"n": 0, "k": 0},
        "check_raise_nuts": {"n": 0, "k": 0},
        "hit_then_lead": {"n": 0, "k": 0},
        "shown_lead_was_hit": {"n": 0, "k": 0},
        "miss_then_give_up": {"n": 0, "k": 0},
        "shown_air_aggression": {"n": 0, "k": 0},
        "river_air_bluff": {"n": 0, "k": 0},
        "bet_aversion": {"n": 0, "k": 0},
        "call_vs_raise": {"n": 0, "k": 0},
        "overcall_overbet": {"n": 0, "k": 0},
        "draw_complete_donk": {"n": 0, "k": 0},
        "bluff_size_split": {"n": 0, "k": 0},
        "low_wsd_large_pot": {"n": 0, "k": 0},
        "thin_value_medium": {"n": 0, "k": 0},
        "weak_pays_big": {"n": 0, "k": 0},
        "medium_calls_big": {"n": 0, "k": 0},
        "river_weak_call": {"n": 0, "k": 0},
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
    idx: int, player: Dict, players: List[Dict], board: List,
    situ_by_idx: Dict[int, str], is_srp: bool, aggressor_idx: Optional[int],
    tier2: Optional[Dict],
    facts: Optional[Dict] = None,
) -> Dict:
    c = _empty_counters()
    pa = _preflop_action(player)
    pos = _norm_pos(player.get("position"))
    post = _post_actions(player)
    showdown = _is_showdown(player)
    board = list(board or [])
    board_len = len(board)
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

    _apply_portrait_counters(
        c, idx, players, is_srp, aggressor_idx, board, facts or {},
    )

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

    situ_by_idx, is_srp, aggressor_idx = _preflop_walk(players)
    tier2_map = _tier2_by_alias(analysis)

    out_players: List[Dict] = []
    for idx, p in enumerate(players):
        alias = (p.get("alias") or "").strip()
        if not alias:
            continue
        counters = _player_counters(
            idx, p, players, board, situ_by_idx, is_srp, aggressor_idx,
            tier2_map.get(alias), facts,
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


def _apply_portrait_counters(
    counters: Dict,
    idx: int,
    players: List[Dict],
    is_srp: bool,
    aggressor_idx: Optional[int],
    board: List,
    facts: Dict,
) -> None:
    """HU SRP 条件下的动作层画像计数。顺序不明或多人池一律跳过。"""

    board = list(board or [])
    board_len = len(board)
    if not is_srp or aggressor_idx is None or board_len < 3:
        return
    postflop_idx = [
        i for i, player in enumerate(players)
        if _post_actions(player) or _is_showdown(player)
    ]
    if len(postflop_idx) != 2 or aggressor_idx not in postflop_idx:
        return
    defender_idx = postflop_idx[0] if postflop_idx[1] == aggressor_idx else postflop_idx[1]
    if idx not in {aggressor_idx, defender_idx}:
        return
    big_blind = _parse_big_blind(facts)
    pot = _street_money_all(players, "翻前")
    pfa_bet_prev = False
    for street in _POST_STREETS:
        street_start = pot
        oop_act = _first_action(players[defender_idx], street)
        ip_act = _first_action(players[aggressor_idx], street)
        oop_led = bool(oop_act and oop_act.get("action") in _AGGR_ACTS)
        ip_can_open = not oop_led
        open_act = oop_act if idx == defender_idx else (ip_act if ip_can_open else None)
        if open_act and open_act.get("action") in _OPEN_ACTS:
            counters["bet_aversion"]["n"] += 1
            if open_act.get("action") == "check":
                counters["bet_aversion"]["k"] += 1
        if idx == aggressor_idx and ip_can_open and ip_act and ip_act.get("action") in _OPEN_ACTS:
            counters["missed_initiative"]["n"] += 1
            if ip_act.get("action") == "check":
                counters["missed_initiative"]["k"] += 1
            if street == "河牌":
                counters["ip_river_checkback"]["n"] += 1
                if ip_act.get("action") == "check":
                    counters["ip_river_checkback"]["k"] += 1
        if idx == defender_idx and _called_street(players[defender_idx], _PREV_STREET[street]):
            if oop_act and oop_act.get("action") in _OPEN_ACTS:
                counters["call_then_lead"]["n"] += 1
                if oop_act.get("action") in _AGGR_ACTS:
                    counters["call_then_lead"]["k"] += 1
        facing = None
        facing_amount = 0.0
        if idx == defender_idx and not oop_led and ip_act and ip_act.get("action") in _AGGR_ACTS:
            facing = _response_after_lead(players[defender_idx], street)
            facing_amount = _action_amount(ip_act)
        elif idx == aggressor_idx and oop_led:
            facing = _response_after_lead(players[aggressor_idx], street)
            facing_amount = _action_amount(oop_act)
        if facing in {"fold", "call"}:
            frac = facing_amount / street_start if street_start > 0 and facing_amount > 0 else None
            pot_bb = street_start / big_blind if big_blind and big_blind > 0 else None
            if street in {"转牌", "河牌"} and (
                (pot_bb is not None and pot_bb >= 20)
                or (frac is not None and frac >= 0.40)
            ):
                counters["big_pot_calldown"]["n"] += 1
                if facing == "call":
                    counters["big_pot_calldown"]["k"] += 1
            if street == "河牌" and frac is not None and frac >= 0.50:
                counters["river_station"]["n"] += 1
                if facing == "call":
                    counters["river_station"]["k"] += 1
            if frac is not None and frac <= 0.33:
                counters["overfold_small_bet"]["n"] += 1
                if facing == "fold":
                    counters["overfold_small_bet"]["k"] += 1
            if frac is not None and frac > 1.0:
                counters["overcall_overbet"]["n"] += 1
                if facing == "call":
                    counters["overcall_overbet"]["k"] += 1
        facing_raise = _response_after_own_bet(players[idx], street)
        if facing_raise in {"fold", "call", "raise", "allin"}:
            counters["fold_to_postflop_raise"]["n"] += 1
            if facing_raise == "fold":
                counters["fold_to_postflop_raise"]["k"] += 1
            counters["call_vs_raise"]["n"] += 1
            if facing_raise == "call":
                counters["call_vs_raise"]["k"] += 1
        if (
            idx == defender_idx
            and street in {"转牌", "河牌"}
            and pfa_bet_prev
            and not oop_led
            and ip_act
            and ip_act.get("action") in _AGGR_ACTS
            and facing in {"fold", "call", "raise", "allin"}
        ):
            counters["fold_to_barrel"]["n"] += 1
            if facing == "fold":
                counters["fold_to_barrel"]["k"] += 1
        pfa_bet_prev = bool(ip_act and ip_act.get("action") in _AGGR_ACTS)
        _apply_showdown_portrait(
            counters,
            players[idx],
            street,
            oop_act if idx == defender_idx else ip_act,
            (idx == defender_idx) or ip_can_open,
            board,
            idx == defender_idx,
            street_start,
            facing=facing,
            facing_amount=facing_amount,
            big_blind=big_blind,
        )
        pot += _street_money_all(players, street)

    if _is_showdown(players[idx]):
        net = players[idx].get("net")
        pot_bb = pot / big_blind if big_blind and big_blind > 0 else None
        if isinstance(net, (int, float)) and pot_bb is not None and pot_bb >= 20:
            counters["low_wsd_large_pot"]["n"] += 1
            if float(net) <= 0:
                counters["low_wsd_large_pot"]["k"] += 1


def _apply_showdown_portrait(
    counters: Dict,
    player: Dict,
    street: str,
    first_act: Optional[Dict],
    can_open: bool,
    board: List,
    is_oop: bool,
    street_start: float,
    facing: Optional[str] = None,
    facing_amount: float = 0.0,
    big_blind: Optional[float] = None,
) -> None:
    hole = player.get("hole_cards") or []
    if len(hole) < 2 or not first_act:
        return
    kind = str(first_act.get("action") or "")
    if kind not in {"check", "call", "bet", "raise", "allin", "fold"}:
        return
    current = _shown_strength(hole, board, street)
    if current is None:
        return
    prev_street = _PREV_STREET.get(street)
    previous = (
        _shown_strength(hole, board, prev_street)
        if prev_street and prev_street != "翻前"
        else None
    )
    two_pair_plus = _shown_two_pair_plus(current)
    pair_kind = current.get("pair_kind")
    strong_pair = pair_kind in {"overpair", "top_pair", "top_pair_weak"}
    air = _shown_air(current, street)
    hit = _shown_hit(previous, current)
    aggressive = kind in _AGGR_ACTS
    called_previous = bool(prev_street and _called_street(player, prev_street))
    flop_first = _first_action(player, "翻牌")
    flop_was_check = bool(flop_first and flop_first.get("action") == "check")

    if two_pair_plus and kind in {"check", "call", "bet", "raise", "allin"}:
        counters["slowplay_two_pair_plus"]["n"] += 1
        if kind in {"check", "call"}:
            counters["slowplay_two_pair_plus"]["k"] += 1
    if strong_pair and can_open and kind in _OPEN_ACTS:
        counters["checked_strong_pair"]["n"] += 1
        if kind == "check":
            counters["checked_strong_pair"]["k"] += 1
    if (
        two_pair_plus
        and street in {"转牌", "河牌"}
        and flop_was_check
        and can_open
        and kind in _OPEN_ACTS
    ):
        counters["delayed_value"]["n"] += 1
        if aggressive:
            counters["delayed_value"]["k"] += 1
    if two_pair_plus:
        after_check = _actions_after_check(player, street)
        if after_check:
            counters["check_raise_nuts"]["n"] += 1
            if any(item in _AGGR_ACTS for item in after_check):
                counters["check_raise_nuts"]["k"] += 1
    if can_open and called_previous and hit and kind in _OPEN_ACTS:
        counters["hit_then_lead"]["n"] += 1
        if aggressive:
            counters["hit_then_lead"]["k"] += 1
    if can_open and called_previous and aggressive:
        counters["shown_lead_was_hit"]["n"] += 1
        if hit or two_pair_plus:
            counters["shown_lead_was_hit"]["k"] += 1
    if (
        called_previous
        and previous is not None
        and _shown_made_draw(previous)
        and not hit
        and not two_pair_plus
        and not _shown_made_draw(current)
        and kind in {"check", "fold", "bet", "raise", "allin", "call"}
    ):
        counters["miss_then_give_up"]["n"] += 1
        if kind in {"check", "fold"}:
            counters["miss_then_give_up"]["k"] += 1
    if aggressive:
        counters["shown_air_aggression"]["n"] += 1
        if air:
            counters["shown_air_aggression"]["k"] += 1
        if street == "河牌":
            counters["river_air_bluff"]["n"] += 1
            if current.get("made") == "High Card" or current.get("board_dominated"):
                counters["river_air_bluff"]["k"] += 1
        if air and street_start > 0:
            frac = _action_amount(first_act) / street_start
            counters["bluff_size_split"]["n"] += 1
            if frac <= 0.40:
                counters["bluff_size_split"]["k"] += 1
    if (
        can_open
        and is_oop
        and previous is not None
        and _shown_made_draw(previous)
        and current.get("made") in _COMPLETED_DRAW
        and not current.get("board_dominated")
        and kind in _OPEN_ACTS
    ):
        counters["draw_complete_donk"]["n"] += 1
        if aggressive:
            counters["draw_complete_donk"]["k"] += 1
    band = _shown_made_band(current)
    if (
        band == "medium"
        and can_open
        and street in {"转牌", "河牌"}
        and kind in _OPEN_ACTS
    ):
        counters["thin_value_medium"]["n"] += 1
        if aggressive:
            counters["thin_value_medium"]["k"] += 1
    pot_bb = (
        street_start / big_blind
        if big_blind and big_blind > 0
        else None
    )
    frac = (
        facing_amount / street_start
        if street_start > 0 and facing_amount > 0
        else None
    )
    big = (pot_bb is not None and pot_bb >= 20) or (
        frac is not None and frac >= 0.40
    )
    if facing in {"fold", "call"} and street in {"转牌", "河牌"} and big:
        if band == "medium":
            counters["medium_calls_big"]["n"] += 1
            if facing == "call":
                counters["medium_calls_big"]["k"] += 1
        if band == "weak":
            counters["weak_pays_big"]["n"] += 1
            if facing == "call":
                counters["weak_pays_big"]["k"] += 1
    if band == "weak" and street == "河牌" and facing in {"fold", "call"}:
        counters["river_weak_call"]["n"] += 1
        if facing == "call":
            counters["river_weak_call"]["k"] += 1


def _shown_strength(hole: List, board: List, street: str) -> Optional[Dict]:
    count = _STREET_BOARD.get(street)
    if count is None or len(hole) < 2 or len(board) < 3:
        return None
    visible = list(board[:count])
    if len(visible) < 3:
        return None
    try:
        return classify_hand(list(hole), visible)
    except (TypeError, ValueError, IndexError, KeyError):
        return None


def _shown_two_pair_plus(info: Optional[Dict]) -> bool:
    if not info or info.get("board_dominated"):
        return False
    return info.get("made") in _TWO_PAIR_PLUS


def _shown_made_band(info: Optional[Dict]) -> str:
    """Exploit bands: strong=two pair+, medium=top/over/second pair, weak=else."""

    if _shown_two_pair_plus(info):
        return "strong"
    kind = (info or {}).get("pair_kind")
    if kind in {"overpair", "top_pair", "top_pair_weak", "second_pair"}:
        return "medium"
    if kind in {"underpair", "low_pair"}:
        return "weak"
    if (info or {}).get("made") == "Pair" and not (info or {}).get("board_dominated"):
        return "medium"
    return "weak"


def _shown_made_draw(info: Optional[Dict]) -> bool:
    draws = (info or {}).get("draws") or []
    return "flush_draw" in draws or "oesd" in draws


def _shown_air(info: Optional[Dict], street: str) -> bool:
    if not info:
        return False
    playing_board = bool(info.get("board_dominated")) or info.get("made") == "High Card"
    if not playing_board:
        return False
    if street == "河牌":
        return True
    draws = info.get("draws") or []
    return not draws


def _shown_hit(previous: Optional[Dict], current: Dict) -> bool:
    if current.get("board_dominated"):
        return False
    if previous is None:
        return _shown_two_pair_plus(current)
    prev_rank = _MADE_RANK.get(str(previous.get("made") or ""), 0)
    curr_rank = _MADE_RANK.get(str(current.get("made") or ""), 0)
    if curr_rank > prev_rank:
        return True
    if _shown_made_draw(previous) and current.get("made") in _COMPLETED_DRAW:
        return True
    return _shown_two_pair_plus(current) and not _shown_two_pair_plus(previous)


def _actions_after_check(player: Dict, street: str) -> List[str]:
    kinds = [
        str(action.get("action"))
        for action in player.get("actions") or []
        if action.get("street") == street and action.get("action")
    ]
    if "check" not in kinds:
        return []
    return kinds[kinds.index("check") + 1 :]


def _parse_big_blind(facts: Dict) -> Optional[float]:
    blinds = str(facts.get("blinds") or "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", blinds)
    if not match:
        return None
    try:
        return float(match.group(2))
    except (TypeError, ValueError):
        return None


def _street_money_all(players: List[Dict], street: str) -> float:
    return sum(_street_money(_player_street_actions(player), street) for player in players)


def _player_street_actions(player: Dict) -> List[Dict]:
    return [action for action in (player.get("actions") or []) if action.get("street")]


def _response_after_own_bet(player: Dict, street: str) -> Optional[str]:
    saw_aggr = False
    for action in player.get("actions") or []:
        if action.get("street") != street or not action.get("action"):
            continue
        kind = str(action.get("action"))
        if not saw_aggr:
            if kind in _AGGR_ACTS:
                saw_aggr = True
            continue
        if kind in {"fold", "call", "raise", "allin"}:
            return kind
    return None


def _first_action(player: Dict, street: str) -> Optional[Dict]:
    for action in player.get("actions") or []:
        if action.get("street") == street and action.get("action"):
            return action
    return None


def _called_street(player: Dict, street: str) -> bool:
    return any(
        action.get("street") == street and action.get("action") == "call"
        for action in player.get("actions") or []
    )


def _action_amount(action: Optional[Dict]) -> float:
    if not action:
        return 0.0
    try:
        return float(action.get("amount") or 0)
    except (TypeError, ValueError):
        return 0.0


def _response_after_lead(player: Dict, street: str) -> Optional[str]:
    seen_first = False
    for action in player.get("actions") or []:
        if action.get("street") != street or not action.get("action"):
            continue
        if not seen_first:
            seen_first = True
            continue
        return str(action.get("action"))
    first = _first_action(player, street)
    if first and first.get("action") in {"fold", "call", "raise", "allin"}:
        return str(first.get("action"))
    return None
