"""把开源图表包（poker-charts, MIT）转换成本仓库的逐类别频率 JSON。

来源：https://github.com/AHTOOOXA/poker-charts （MIT License）
  - scripts/vendor/pekarstas.ts —— GGPoker chart pack（默认 provider）
  - scripts/vendor/greenline.ts —— Greenline Poker charts（可选）

cell 语义（见 vendor/poker_types.ts）：
  - "raise"                       → 100% 该动作
  - ["raise","fold"]              → 两动作各 50%（等分）
  - {weight, actions:{raise:70}}  → 在范围内 weight%，再按 actions 百分比拆分

用法：
  python scripts/import_ranges.py            # 默认导入 pekarstas 的 RFI + vs_RFI
  python scripts/import_ranges.py --provider greenline
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, List

import json5

SCRIPT_DIR = Path(__file__).resolve().parent
VENDOR_DIR = SCRIPT_DIR / "vendor"
DATA_DIR = SCRIPT_DIR.parent / "data" / "ranges" / "6max_100bb"

ACTION_ORDER = ["raise", "call", "allin"]
KEY_RE = re.compile(r"^([A-Z]{2,3})-(RFI|vs-open|vs-3bet|vs-4bet)(?:-([A-Z]{2,3}))?$")

PROVIDER_META = {
    "pekarstas": {
        "label": "Pekarstas (GGPoker chart pack)",
        "url": "https://github.com/AHTOOOXA/poker-charts",
        "license": "MIT",
    },
    "greenline": {
        "label": "Greenline Poker charts",
        "url": "https://github.com/AHTOOOXA/poker-charts",
        "license": "MIT",
    },
}


def load_charts(ts_path: Path) -> Dict[str, Dict[str, object]]:
    """从 TS 模块里抠出 `export const charts = { ... }` 并用 json5 解析。"""
    text = ts_path.read_text(encoding="utf-8")
    idx = text.index("charts")
    eq = text.index("=", idx)
    start = text.index("{", eq)
    end = text.rindex("}")
    return json5.loads(text[start : end + 1])


def cell_to_freqs(cell: object) -> Dict[str, float]:
    """poker-charts 的 cell -> {动作: 频率}（不含 fold；fold 由 1-Σ 推出）。"""
    if isinstance(cell, str):
        return {} if cell == "fold" else {cell: 1.0}
    if isinstance(cell, list):
        share = 1.0 / len(cell)
        out: Dict[str, float] = {}
        for a in cell:
            if a == "fold":
                continue
            out[a] = out.get(a, 0.0) + share
        return out
    if isinstance(cell, dict):
        weight = float(cell.get("weight", 100)) / 100.0
        actions = cell.get("actions", {}) or {}
        denom = sum(float(v) for v in actions.values()) or 100.0
        out = {}
        for a, pct in actions.items():
            if a == "fold":
                continue
            out[a] = round(weight * (float(pct) / denom), 4)
        return out
    return {}


def chart_to_grid(chart: Dict[str, object]) -> tuple[List[str], Dict[str, Dict[str, float]]]:
    grid: Dict[str, Dict[str, float]] = {}
    used: set[str] = set()
    for hand, cell in chart.items():
        freqs = {a: round(f, 4) for a, f in cell_to_freqs(cell).items() if f > 0}
        if freqs:
            grid[hand] = freqs
            used.update(freqs.keys())
    actions = [a for a in ACTION_ORDER if a in used]
    return actions, grid


def vpip(grid: Dict[str, Dict[str, float]]) -> float:
    """按组合数加权的 VPIP（进入底池比例），用于人工校验合理性。"""
    from app.poker.preflop.handclass import combos_for_class

    played = total = 0.0
    from app.poker.preflop.handclass import all_classes

    for cls in all_classes():
        c = combos_for_class(cls)
        total += c
        nonfold = sum(grid.get(cls, {}).values())
        played += c * min(1.0, nonfold)
    return played / total


def write_spot(path: Path, meta: dict, actions: List[str], grid: Dict[str, Dict[str, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta, "actions": actions, "grid": dict(sorted(grid.items()))}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="pekarstas", choices=list(PROVIDER_META))
    ap.add_argument("--open-size-bb", type=float, default=2.5)
    args = ap.parse_args()

    prov = PROVIDER_META[args.provider]
    charts = load_charts(VENDOR_DIR / f"{args.provider}.ts")

    written = 0
    for key, chart in charts.items():
        m = KEY_RE.match(key)
        if not m:
            continue
        hero, scen, villain = m.group(1), m.group(2), m.group(3)
        actions, grid = chart_to_grid(chart)
        if not grid:
            continue
        base_meta = {
            "format": "6max_100bb",
            "source": prov["label"],
            "provider": args.provider,
            "license": prov["license"],
            "source_url": prov["url"],
            "note": "开源图表包导出的近 GTO 简化策略；离散频率（含 50/50 混合）。",
        }
        if scen == "RFI":
            meta = {**base_meta, "spot": "RFI", "position": hero, "hero_position": hero}
            path = DATA_DIR / "RFI" / f"{hero}.json"
        elif scen == "vs-open":
            pos = f"{hero}_vs_{villain}"
            meta = {
                **base_meta,
                "spot": "vs_RFI",
                "position": pos,
                "hero_position": hero,
                "opener_position": villain,
                "open_size_bb": args.open_size_bb,
            }
            path = DATA_DIR / "vs_RFI" / f"{pos}.json"
        else:
            continue  # vs-3bet / vs-4bet 暂不接入训练器
        vp = vpip(grid)
        meta["vpip_pct"] = round(vp * 100, 1)
        write_spot(path, meta, actions, grid)
        written += 1
        print(f"  {key:20s} -> {path.relative_to(DATA_DIR.parent)}  (VPIP {vp*100:.1f}%)")

    print(f"\n写入 {written} 个 spot（provider={args.provider}）。")


if __name__ == "__main__":
    main()
