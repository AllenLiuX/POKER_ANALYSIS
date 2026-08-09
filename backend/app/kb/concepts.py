"""德州策略「概念」taxonomy + 从引擎统计到概念的确定性映射。

概念是知识库的检索单元：每个概念有一条**英文检索 query**（英文策略内容更全、更权威）
和一个中文标签。分析时把对手的统计倾向映射到少数几个最相关概念，据此检索/复用知识库片段。
"""
from __future__ import annotations

from typing import Dict, List, Optional

# key -> {label(中文), query(英文检索词)}
CONCEPTS: Dict[str, Dict[str, str]] = {
    "calling_station_exploit": {
        "label": "剥削跟注站",
        "query": "how to exploit a calling station in poker thin value betting strategy",
    },
    "tight_player_exploit": {
        "label": "剥削过紧玩家",
        "query": "how to exploit tight nitty poker players steal blinds attack",
    },
    "overfold_exploit": {
        "label": "利用过度弃牌",
        "query": "exploiting players who fold too much poker overfold cbet bluff",
    },
    "aggressive_bluffer_exploit": {
        "label": "剥削激进诈唬型",
        "query": "how to exploit overly aggressive bluffer poker bluff catching strategy",
    },
    "fold_to_cbet_exploit": {
        "label": "对手高弃 c-bet",
        "query": "opponent folds too much to cbet exploit continuation bet bluff frequency",
    },
    "cbet_strategy": {
        "label": "持续下注策略",
        "query": "continuation bet cbet strategy flop frequency sizing by board texture",
    },
    "range_advantage_cbet": {
        "label": "范围优势与下注",
        "query": "range advantage nut advantage poker cbet polarization strategy",
    },
    "three_bet_pot_strategy": {
        "label": "3bet 底池打法",
        "query": "3bet pot postflop strategy out of position defense poker",
    },
    "blind_defense": {
        "label": "盲注防守",
        "query": "big blind defense poker ranges vs button open MDF pot odds",
    },
    "mdf_pot_odds": {
        "label": "MDF 与底池赔率",
        "query": "minimum defense frequency MDF pot odds poker bluff catching math",
    },
    "board_texture_strategy": {
        "label": "牌面结构打法",
        "query": "poker board texture dry wet flop strategy betting adjustments",
    },
    "thin_value_river": {
        "label": "河牌薄价值",
        "query": "thin value betting river poker vs calling station strategy",
    },
    "bluff_catching": {
        "label": "抓诈唬",
        "query": "bluff catching poker river decisions capped range hero call",
    },
    "wtsd_showdown": {
        "label": "摊牌与看牌倾向",
        "query": "went to showdown WTSD poker stat exploit sticky player strategy",
    },
    "exploitative_adjustments": {
        "label": "剥削性调整通则",
        "query": "exploitative adjustments poker strategy deviating from GTO population tendencies",
    },
    "preflop_rfi_ranges": {
        "label": "翻前开池范围",
        "query": "preflop RFI open raise ranges 6max poker by position",
    },
}


def label(concept: str) -> str:
    c = CONCEPTS.get(concept)
    return c["label"] if c else concept


def _ratio(d: Dict, k_key: str, n_key: str = "n") -> Optional[float]:
    try:
        n = float(d.get(n_key, 0) or 0)
        if n <= 0:
            return None
        return float(d.get(k_key, 0) or 0) / n
    except (TypeError, ValueError):
        return None


def concepts_for_opponent(
    counters: Dict, leaks: Optional[Dict] = None, *, tag: Optional[str] = None, limit: int = 4
) -> List[str]:
    """把对手聚合统计映射到最相关的少数概念（有优先级、去重、限量）。"""
    c = counters or {}
    leaks = leaks or {}
    picks: List[str] = []

    def add(*keys: str) -> None:
        for k in keys:
            if k in CONCEPTS and k not in picks:
                picks.append(k)

    vs = c.get("pf_vs_open", {}) if isinstance(c.get("pf_vs_open"), dict) else {}
    vs_n = float(vs.get("n", 0) or 0)
    call_r = _ratio(vs, "call")
    fold_r = _ratio(vs, "fold")
    tb_r = _ratio(vs, "raise")
    af = c.get("af_post", {}) if isinstance(c.get("af_post"), dict) else {}
    af_total = float(af.get("aggr", 0) or 0) + float(af.get("passive", 0) or 0)
    af_r = (float(af.get("aggr", 0) or 0) / af_total) if af_total > 0 else None
    fvc = c.get("fold_vs_cbet_flop", {}) if isinstance(c.get("fold_vs_cbet_flop"), dict) else {}
    fvc_n = float(fvc.get("n", 0) or 0)
    fvc_r = _ratio(fvc, "k")
    wtsd = c.get("wtsd", {}) if isinstance(c.get("wtsd"), dict) else {}
    wtsd_n = float(wtsd.get("n", 0) or 0)
    wtsd_r = _ratio(wtsd, "k")
    cbet_n = float((c.get("cbet_flop", {}) or {}).get("n", 0) or 0)

    tag_l = (tag or "").strip()

    # 按信号强度/优先级挑概念
    if leaks.get("too_loose", 0) or (call_r is not None and vs_n >= 6 and call_r > 0.5) or ("站" in tag_l or "松" in tag_l):
        add("calling_station_exploit", "thin_value_river")
    if leaks.get("too_tight", 0) or (fold_r is not None and vs_n >= 6 and fold_r > 0.7) or ("紧" in tag_l):
        add("tight_player_exploit", "overfold_exploit")
    if leaks.get("too_aggressive", 0) or (af_r is not None and af_total >= 6 and af_r > 0.6) or ("激进" in tag_l):
        add("aggressive_bluffer_exploit", "bluff_catching")
    if fvc_r is not None and fvc_n >= 4 and fvc_r > 0.6:
        add("fold_to_cbet_exploit")
    if wtsd_r is not None and wtsd_n >= 6 and wtsd_r > 0.42:
        add("wtsd_showdown", "bluff_catching")
    if tb_r is not None and vs_n >= 6 and tb_r > 0.15:
        add("three_bet_pot_strategy")
    if cbet_n > 0:
        add("cbet_strategy", "range_advantage_cbet")

    # 兜底：始终给一个通则概念，保证有参考资料可注入
    add("exploitative_adjustments")
    return picks[:limit]
