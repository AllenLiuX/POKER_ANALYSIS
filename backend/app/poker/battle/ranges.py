"""对战翻前范围助手：全部复用 data/ranges 的 6-max BTN/BB 数据作 HU 近似。

约定（与翻后训练器 CONFIGS 一致）：
- BTN 开池 = RFI/BTN 的 raise 频率；
- BB 防守 = vs_RFI/BB_vs_BTN 的 call / raise(3-bet) 频率；
- 面对 3-bet 的跟注 = 一段固定强牌集合（无对应范围数据时的合理近似）。

对手按这些频率**可复现地混合**决策；英雄的这些决策也用同一频率判分（接地）。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Dict, List

from app.poker.preflop.handclass import all_classes
from app.poker.preflop.ranges import load_spot

FMT = "6max_100bb"

# 面对 3-bet 的跟注范围（BTN vs BB 3-bet 的近似强牌集）。
VS_3BET_CALL = {
    "AA", "KK", "QQ", "JJ", "TT", "99",
    "AKs", "AQs", "AJs", "ATs", "KQs", "KJs", "QJs",
    "AKo", "AQo",
}


@lru_cache(maxsize=1)
def _btn_rfi():
    return load_spot(FMT, "RFI", "BTN")


@lru_cache(maxsize=1)
def _bb_def():
    return load_spot(FMT, "vs_RFI", "BB_vs_BTN")


def btn_open_freqs(cls: str) -> Dict[str, float]:
    """BTN 开池的动作频率（raise / fold）。"""
    return _btn_rfi().frequencies.get(cls, {"fold": 1.0})


def bb_defend_freqs(cls: str) -> Dict[str, float]:
    """BB 面对 BTN 开池的动作频率（call / raise / fold）。"""
    return _bb_def().frequencies.get(cls, {"fold": 1.0})


def calls_3bet(cls: str) -> bool:
    return cls in VS_3BET_CALL


def _range_string(classes: List[str]) -> str:
    return ", ".join(classes) if classes else ""


@lru_cache(maxsize=1)
def btn_open_range_str() -> str:
    ps = _btn_rfi()
    return _range_string([c for c in all_classes() if ps.frequencies.get(c, {}).get("raise", 0.0) > 0.0])


@lru_cache(maxsize=1)
def bb_call_range_str() -> str:
    ps = _bb_def()
    return _range_string([c for c in all_classes() if ps.frequencies.get(c, {}).get("call", 0.0) > 0.0])


@lru_cache(maxsize=1)
def bb_3bet_range_str() -> str:
    ps = _bb_def()
    return _range_string([c for c in all_classes() if ps.frequencies.get(c, {}).get("raise", 0.0) > 0.0])


def vs_3bet_call_range_str() -> str:
    return _range_string(sorted(VS_3BET_CALL))
