"""翻后多街泛化 + 范围优势（range advantage）测试。"""
from __future__ import annotations

from app.poker.postflop.analyze import analyze_spot
from app.poker.postflop.handstrength import classify_hand
from app.poker.postflop.range_advantage import advantage_labels, range_vs_range
from app.poker.postflop.texture import classify_board


# ---------- 多街泛化：转牌 / 河牌 ----------
def test_texture_accepts_turn_and_river():
    t4 = classify_board(["Ks", "7d", "2c", "9h"])
    assert t4["high_card"] == 13 and t4["suitedness"] in ("rainbow", "two-tone")
    t5 = classify_board(["Ks", "7d", "2c", "9h", "3s"])
    assert 0.0 <= t5["wetness"] <= 1.0


def test_texture_rejects_out_of_range():
    for bad in (["Ks", "7d"], ["Ks", "7d", "2c", "9h", "3s", "4d"]):
        try:
            classify_board(bad)
            assert False, "应拒绝非 3~5 张"
        except ValueError:
            pass


def test_flop_behavior_unchanged():
    # 3 张时数值与旧版一致（回归保护）
    t = classify_board(["9s", "8s", "7d"])
    assert t["straightiness"] >= 3
    assert t["suitedness"] == "two-tone"


def test_river_has_no_draws():
    # 河牌上即便是"4 张同花色"也不再算听牌（无后续发牌）
    h = classify_hand(["As", "5s"], ["Ks", "9s", "2c", "7s", "3d"])
    assert h["draws"] == [] and h["outs"] == 0


def test_made_hand_on_turn():
    h = classify_hand(["Ah", "Kc"], ["Ad", "9s", "2c", "7h"])  # 顶对
    assert h["made"] == "Pair" and h["tier"] in ("value", "medium")


# ---------- 范围 vs 范围优势 ----------
def test_range_advantage_pfr_ahead_on_ace_high():
    # A 高干燥面：宽开池方(含大量 A/K)通常范围+坚果占优
    ra = range_vs_range(
        pfr_range="22+, A2s+, K9s+, ATo+, KQo",
        caller_range="22-99, A2s-A9s, KTs-KJs, QJs, JTs, T9s",
        board=["As", "Kd", "4c"],
        trials=400,
    )
    assert ra is not None
    assert 0.4 < ra["range_equity"] < 0.75
    assert ra["range_advantage"] > 0  # PFR 领先
    r_label, n_label = advantage_labels(ra)
    assert "范围" in r_label and "坚果" in n_label


def test_range_advantage_none_on_conflict():
    assert range_vs_range("AA", "AA", ["As", "Ad", "Ah"], trials=50) is None


def test_range_advantage_reproducible():
    kw = dict(pfr_range="QQ+, AKs", caller_range="JJ-88, AQs", board=["Qs", "8d", "3c"], trials=300)
    a = range_vs_range(**kw)
    b = range_vs_range(**kw)
    assert a == b  # 种子由 board 决定，可复现


# ---------- 门面接入 ----------
def test_analyze_spot_attaches_range_advantage_when_hero_range_given():
    _, _, _, rec = analyze_spot(
        role="pfr",
        hero=["Ah", "Kh"],
        board=["As", "Kd", "4c"],
        villain_range="22-99, A2s-A9s, KTs, QJs, JTs, T9s",
        pot_bb=5.5,
        bet_bb=None,
        hero_range="22+, A2s+, K9s+, ATo+, KQo",
    )
    assert "range_advantage" in rec and "range_label" in rec
    assert rec["recommended"] == "bet"  # 两对价值牌必下注


def test_analyze_spot_backward_compatible_without_hero_range():
    _, _, _, rec = analyze_spot(
        role="pfr",
        hero=["Ah", "Ad"],
        board=["Ks", "7d", "2c"],
        villain_range="22-99, AJs, KQs",
        pot_bb=5.5,
        bet_bb=None,
    )
    # 不传 hero_range 时不含范围字段，行为与旧版一致
    assert "range_advantage" not in rec
    assert rec["recommended"] == "bet"
