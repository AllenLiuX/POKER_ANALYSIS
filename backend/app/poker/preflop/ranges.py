"""范围数据模型：把紧凑的范围字符串展开为逐类别的动作频率。

数据文件（JSON）示意：
    {
      "meta": {"format": "6max_100bb", "position": "CO", "spot": "RFI", "source": "..."},
      "actions": {"raise": "22+, A2s+, K9s+, ..."},   // 各非 fold 动作的范围字符串
      "mixed":   {"A5s": {"raise": 0.5}}               // 可选：对特定类别覆盖频率（混合策略）
    }
fold 频率 = 1 - Σ(其它动作频率)。
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

import eval7

from app.poker.preflop.handclass import all_classes, combos_for_class, hand_class

# backend/data/ranges
RANGES_DIR = Path(__file__).resolve().parents[3] / "data" / "ranges"


def expand_range_string(s: str) -> Dict[str, float]:
    """范围字符串（如 'QQ+, AKs, A5s'）-> {类别: 频率∈[0,1]}。"""
    if not s or not s.strip():
        return {}
    hr = eval7.HandRange(s)
    counts: Dict[str, float] = defaultdict(float)
    for cards, weight in hr.hands:
        cls = hand_class(str(cards[0]), str(cards[1]))
        counts[cls] += float(weight)
    return {cls: min(1.0, total / combos_for_class(cls)) for cls, total in counts.items()}


@dataclass
class PreflopSpot:
    meta: Dict[str, object]
    actions: List[str]  # 非 fold 动作（如 ['raise'] 或 ['call','raise']）
    frequencies: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def hand_freqs(self, cls: str) -> Dict[str, float]:
        if cls not in self.frequencies:
            raise ValueError(f"未知手牌类别：{cls}")
        return self.frequencies[cls]


def build_spot(
    meta: Dict[str, object],
    actions_ranges: Dict[str, str],
    mixed: Dict[str, Dict[str, float]] | None = None,
) -> PreflopSpot:
    mixed = mixed or {}
    action_freq = {a: expand_range_string(s) for a, s in actions_ranges.items()}
    actions = list(actions_ranges.keys())
    freqs: Dict[str, Dict[str, float]] = {}
    for cls in all_classes():
        d: Dict[str, float] = {a: action_freq[a].get(cls, 0.0) for a in actions}
        if cls in mixed:
            for a, v in mixed[cls].items():
                d[a] = float(v)
        nonfold = sum(d.values())
        if nonfold > 1.0:  # 归一，避免溢出
            scale = 1.0 / nonfold
            for a in actions:
                d[a] *= scale
            nonfold = 1.0
        d["fold"] = round(max(0.0, 1.0 - nonfold), 6)
        for a in actions:
            d[a] = round(d[a], 6)
        freqs[cls] = d
    return PreflopSpot(meta=meta, actions=actions, frequencies=freqs)


def load_spot_file(path: Path) -> PreflopSpot:
    data = json.loads(path.read_text(encoding="utf-8"))
    return build_spot(
        meta=data.get("meta", {}),
        actions_ranges=data["actions"],
        mixed=data.get("mixed"),
    )


def _spot_key(fmt: str, spot: str, position: str) -> str:
    return f"{fmt}/{spot}/{position}"


def list_spots() -> List[Dict[str, str]]:
    """扫描 data/ranges 目录，返回可用 spot 索引。"""
    out: List[Dict[str, str]] = []
    if not RANGES_DIR.exists():
        return out
    for path in sorted(RANGES_DIR.glob("*/*/*.json")):
        position = path.stem
        spot = path.parent.name
        fmt = path.parent.parent.name
        out.append({"format": fmt, "spot": spot, "position": position})
    return out


def load_spot(fmt: str, spot: str, position: str) -> PreflopSpot:
    path = RANGES_DIR / fmt / spot / f"{position}.json"
    if not path.exists():
        raise FileNotFoundError(f"范围不存在：{_spot_key(fmt, spot, position)}")
    return load_spot_file(path)
