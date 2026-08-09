"""对手（AI）策略：翻前按范围频率混合，翻后复用启发式引擎。全部由 rng 驱动、可复现。"""
from __future__ import annotations

import random
from typing import Dict, List, Optional, Tuple

from app.poker.battle import ranges as R
from app.poker.postflop.analyze import analyze_spot


def _sample(freqs: Dict[str, float], rng: random.Random) -> str:
    """按频率抽一个动作（freqs 不必归一，内部归一）。"""
    items = [(a, max(0.0, f)) for a, f in freqs.items()]
    total = sum(f for _, f in items)
    if total <= 0:
        return "fold"
    r = rng.random() * total
    for a, f in items:
        r -= f
        if r <= 0:
            return a
    return items[-1][0]


def villain_preflop(*, situation: str, cls: str, opener: str, vs_spot: str, rng: random.Random) -> str:
    """翻前对手动作。situation ∈ {open, defend, vs_3bet}。返回 fold/raise/call。"""
    if situation == "open":                              # 对手是开池方，首先行动
        return _sample(R.open_freqs(opener, cls), rng)   # raise / fold
    if situation == "defend":                            # 对手是防守方，面对开池
        return _sample(R.defend_freqs(vs_spot, cls), rng)  # call / raise / fold
    if situation == "vs_3bet":                           # 对手是开池方，面对 3-bet
        return "call" if R.calls_3bet(cls) else "fold"
    return "fold"


def _choose(rec: Dict, rng: random.Random) -> str:
    """按引擎建议 + 可接受集合做温和混合（偏向 recommended）。"""
    recommended = str(rec["recommended"])
    accept: List[str] = list(rec.get("accept", [recommended]))
    if not rec.get("mix") or len(accept) <= 1:
        return recommended
    weights = {a: (0.7 if a == recommended else 0.3 / max(1, len(accept) - 1)) for a in accept}
    return _sample(weights, rng)


def villain_postflop(
    *,
    villain: List[str],
    board: List[str],
    villain_range: str,
    hero_range: str,
    pot_bb: float,
    to_call: float,
    rng: random.Random,
) -> Tuple[str, Optional[str]]:
    """翻后对手动作 + 尺度 id。to_call==0 → check/bet；否则 fold/call/raise。"""
    role = "caller" if to_call > 0 else "pfr"
    _t, _h, _eq, rec = analyze_spot(
        role=role,
        hero=villain,
        board=board,
        villain_range=hero_range,      # 对手视角：对面(英雄)的范围
        pot_bb=pot_bb,
        bet_bb=(to_call if to_call > 0 else None),
        hero_range=villain_range,      # 对手自己的范围
        trials=1200,
        ra_trials=250,
    )
    action = _choose(rec, rng)
    if action == "bet":
        return "bet", str(rec.get("recommended_size", "half"))
    if action == "raise":
        return "raise", str(rec.get("recommended_raise_size", "big"))
    return action, None
