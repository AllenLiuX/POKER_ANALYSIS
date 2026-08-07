"""难度加权与临界手牌抽样测试。"""
from __future__ import annotations

import random

from app.poker.preflop.handclass import hand_class
from app.poker.preflop.ranges import load_spot
from app.poker.preflop.scenario import deal_combo_for_class, generate_scenario
from app.poker.preflop.weighting import (
    action_entropy,
    class_weights,
    is_critical,
    sample_class,
)


def test_entropy_pure_vs_mixed():
    assert action_entropy({"raise": 1.0, "fold": 0.0}) == 0.0
    # 二元 50/50 熵应为 1 bit
    assert abs(action_entropy({"raise": 0.5, "fold": 0.5}) - 1.0) < 1e-9
    # 混合比纯策略熵大
    assert action_entropy({"raise": 0.7, "fold": 0.3}) > 0.0


def test_is_critical():
    assert is_critical({"raise": 0.5, "fold": 0.5}) is True
    assert is_critical({"raise": 1.0, "fold": 0.0}) is False
    assert is_critical({"raise": 0.95, "fold": 0.05}) is False


def test_deal_combo_for_class_matches():
    rng = random.Random(0)
    for cls in ("AA", "AKs", "AKo", "72o", "T9s", "55"):
        for _ in range(20):
            c = deal_combo_for_class(cls, rng)
            assert len(c) == 2 and c[0] != c[1]
            assert hand_class(c[0], c[1]) == cls


def test_easy_weights_are_natural_frequency():
    ps = load_spot("6max_100bb", "RFI", "CO")
    w = class_weights(ps, "easy")
    # easy 档权重 = 组合数：对子 6、同花 4、非同花 12
    assert w["AA"] == 6
    assert w["AKs"] == 4
    assert w["AKo"] == 12


def test_hard_boosts_critical_over_pure():
    from app.poker.preflop.handclass import combos_for_class

    ps = load_spot("6max_100bb", "RFI", "CO")
    w = class_weights(ps, "hard")
    # 找混合类别与纯 fold 类别对比（归一到每组合权重，排除组合数因素）
    mixed = [c for c, f in ps.frequencies.items() if is_critical(f)]
    assert mixed, "期望 CO RFI 存在混合手牌"
    pure_fold = "72o"
    for c in mixed:
        per_combo_mixed = w[c] / combos_for_class(c)
        per_combo_pure = w[pure_fold] / combos_for_class(pure_fold)
        assert per_combo_mixed > per_combo_pure


def test_hard_difficulty_yields_more_critical_hands():
    hard_crit = easy_crit = 0
    n = 120
    for seed in range(n):
        sh = generate_scenario(spot="RFI", position="CO", difficulty="hard", seed=seed)
        se = generate_scenario(spot="RFI", position="CO", difficulty="easy", seed=seed)
        hard_crit += int(bool(sh["is_critical"]))
        easy_crit += int(bool(se["is_critical"]))
    assert hard_crit > easy_crit


def test_generate_scenario_tags_difficulty():
    s = generate_scenario(spot="RFI", position="CO", difficulty="hard", seed=1)
    assert s["difficulty"] == "hard"
    assert isinstance(s["is_critical"], bool)


def test_sample_class_deterministic_with_seed():
    ps = load_spot("6max_100bb", "RFI", "BTN")
    a = sample_class(ps, "standard", random.Random(5))
    b = sample_class(ps, "standard", random.Random(5))
    assert a == b
