"""对战翻前范围助手：复用 data/ranges 的 6-max 数据，支持不同位置对位。

约定（与翻后训练器 CONFIGS 一致）：
- 开池方 = RFI/{opener} 的 raise 频率；
- 防守方 = vs_RFI/{defender}_vs_{opener} 的 call / raise(3-bet) 频率；
- 面对 3-bet 的跟注 = 一段固定强牌集合（无对应范围数据时的合理近似）。

对手按这些频率**可复现地混合**决策；英雄的这些决策也用同一频率判分（接地）。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Dict, List

from app.poker.preflop.handclass import all_classes
from app.poker.preflop.ranges import load_spot

FMT = "6max_100bb"

# 面对 3-bet 的跟注范围（无对应范围数据时的近似强牌集）。
VS_3BET_CALL = {
    "AA", "KK", "QQ", "JJ", "TT", "99",
    "AKs", "AQs", "AJs", "ATs", "KQs", "KJs", "QJs",
    "AKo", "AQo",
}


@lru_cache(maxsize=8)
def _rfi(opener: str):
    return load_spot(FMT, "RFI", opener)


@lru_cache(maxsize=16)
def _defend(vs_spot: str):
    return load_spot(FMT, "vs_RFI", vs_spot)


def open_freqs(opener: str, cls: str) -> Dict[str, float]:
    """开池方的动作频率（raise / fold）。"""
    return _rfi(opener).frequencies.get(cls, {"fold": 1.0})


def defend_freqs(vs_spot: str, cls: str) -> Dict[str, float]:
    """防守方面对开池的动作频率（call / raise / fold）。"""
    return _defend(vs_spot).frequencies.get(cls, {"fold": 1.0})


def calls_3bet(cls: str) -> bool:
    return cls in VS_3BET_CALL


def _range_string(classes: List[str]) -> str:
    return ", ".join(classes) if classes else ""


@lru_cache(maxsize=8)
def open_range_str(opener: str) -> str:
    ps = _rfi(opener)
    return _range_string([c for c in all_classes() if ps.frequencies.get(c, {}).get("raise", 0.0) > 0.0])


@lru_cache(maxsize=16)
def call_range_str(vs_spot: str) -> str:
    ps = _defend(vs_spot)
    return _range_string([c for c in all_classes() if ps.frequencies.get(c, {}).get("call", 0.0) > 0.0])


@lru_cache(maxsize=16)
def threebet_range_str(vs_spot: str) -> str:
    ps = _defend(vs_spot)
    return _range_string([c for c in all_classes() if ps.frequencies.get(c, {}).get("raise", 0.0) > 0.0])


def vs_3bet_call_range_str() -> str:
    return _range_string(sorted(VS_3BET_CALL))
