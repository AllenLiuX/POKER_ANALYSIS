"""翻前范围引擎测试。"""
from __future__ import annotations

from app.poker.preflop.handclass import all_classes, hand_class
from app.poker.preflop.ranges import expand_range_string, load_spot
from app.poker.preflop.scoring import score_action


def test_hand_class():
    assert hand_class("Ah", "As") == "AA"
    assert hand_class("Ah", "Kh") == "AKs"
    assert hand_class("Ah", "Kd") == "AKo"
    assert hand_class("2c", "7d") == "72o"


def test_169_classes_unique():
    classes = all_classes()
    assert len(classes) == 169
    assert len(set(classes)) == 169
    assert classes.count("AA") == 1


def test_expand_range_string():
    f = expand_range_string("77+, AKs, A5s")
    assert f["AA"] == 1.0
    assert f["77"] == 1.0
    assert "66" not in f
    assert f["AKs"] == 1.0
    assert f["A5s"] == 1.0


def test_load_spot_and_fold_complement():
    ps = load_spot("6max_100bb", "RFI", "UTG")
    assert "raise" in ps.actions
    # AA 必开
    aa = ps.hand_freqs("AA")
    assert aa["raise"] == 1.0
    assert aa["fold"] == 0.0
    # 垃圾牌全 fold
    trash = ps.hand_freqs("72o")
    assert trash["fold"] == 1.0
    assert trash["raise"] == 0.0
    # 混合：UTG 的 66 应为 0.5 raise / 0.5 fold
    six = ps.hand_freqs("66")
    assert abs(six["raise"] - 0.5) < 1e-6
    assert abs(six["fold"] - 0.5) < 1e-6


def test_scoring_optimal_and_mistake():
    ps = load_spot("6max_100bb", "RFI", "UTG")
    # AA raise = optimal
    r = score_action(ps.hand_freqs("AA"), "raise")
    assert r["correct"] and r["grade"] == "optimal"
    # AA fold = mistake
    r = score_action(ps.hand_freqs("AA"), "fold")
    assert not r["correct"] and r["grade"] == "mistake"
    # 混合 66：raise 与 fold 都 acceptable
    r_raise = score_action(ps.hand_freqs("66"), "raise")
    r_fold = score_action(ps.hand_freqs("66"), "fold")
    assert r_raise["correct"] and r_fold["correct"]
    assert r_raise["is_mixed"] is True
