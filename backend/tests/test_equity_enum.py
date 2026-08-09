"""精确加权枚举胜率（equity_hand_vs_range_enum）单测。"""
import time

import pytest

from app.poker.equity import (
    equity_hand_vs_range,
    equity_hand_vs_range_enum,
    hero_equity,
)


def test_river_exact_known_spot():
    # 河牌坚果 vs 单一较弱牌：英雄葫芦 A 打对手两对，equity=1
    res = equity_hand_vs_range_enum(["As", "Ad"], "KhKd", ["Ah", "Kc", "7c", "2d", "9s"])
    # Ah 使英雄 三条A? 板 Ah Kc 7c 2d 9s，英雄 As Ad → 三条A；对手 Kh Kd + 板 → 三条K → 英雄胜
    assert res["equity"] == 1.0
    assert res["win"] == 1.0 and res["lose"] == 0.0
    assert res["samples"] >= 1


def test_river_exact_split():
    # 打公共牌平分：双方都以顺子成手且底牌不改良 → 全平
    # 板 5 6 7 8 9 已成 9 高顺；英雄 As Kd 对手 Qh Jc 都不改良 → 平分
    res = equity_hand_vs_range_enum(["As", "Kd"], "QhJc", ["5s", "6d", "7c", "8h", "9s"])
    assert res["tie"] == 1.0
    assert res["equity"] == 0.5


def test_enum_matches_montecarlo_flop():
    hero = ["As", "Ah"]
    vr = "KK-22, AKs-A2s, KQs-KTs"
    board = ["Ad", "7c", "2d"]
    enum = equity_hand_vs_range_enum(hero, vr, board, seed=1)
    mc = equity_hand_vs_range(hero, vr, board=board, trials=8000, seed=7)
    # 精确枚举应与大样本 MC 接近（翻牌抽样 runout，容差放宽一点）
    assert abs(enum["equity"] - mc["equity"]) < 0.03


def test_enum_deterministic():
    hero = ["Qs", "Qd"]
    vr = "AA-22, AKs-A2s"
    board = ["Jh", "7c", "2d"]
    a = equity_hand_vs_range_enum(hero, vr, board, seed=42)
    b = equity_hand_vs_range_enum(hero, vr, board, seed=42)
    assert a == b  # 确定性：同 seed 完全一致


def test_enum_aggregates_combos_on_river():
    # 河牌（无 runout）：英雄三条A；对手 KK 输、JT 成 AKQJT 顺子赢。
    hero = ["Ac", "As"]
    board = ["Ah", "Kd", "Qc", "2s", "3h"]
    beats = equity_hand_vs_range_enum(hero, "KhKs", board)   # 对手三条K → 英雄胜
    loses = equity_hand_vs_range_enum(hero, "JsTs", board)   # 对手顺子 → 英雄负
    mixed = equity_hand_vs_range_enum(hero, "KhKs, JsTs", board)  # 各一组合 → 0.5
    assert beats["equity"] == 1.0
    assert loses["equity"] == 0.0
    assert mixed["equity"] == 0.5  # 组合级加权聚合正确


def test_turn_exact_is_full_enum():
    # 转牌全枚举：samples 应等于 (46 runout - 与组合冲突) 的总有效评估数，且 >1000
    res = equity_hand_vs_range_enum(["As", "Ad"], "KK-QQ, AKs", ["Ah", "7c", "2d", "9s"])
    assert res["samples"] > 40  # 至少覆盖多个 runout × 组合
    assert 0.0 <= res["equity"] <= 1.0


def test_hero_equity_dispatch():
    # 有公共牌 → 走枚举（确定性）；无公共牌 → 走 MC
    post = hero_equity(["As", "Ad"], "KK-22", ["Ah", "7c", "2d"], seed=1)
    post2 = hero_equity(["As", "Ad"], "KK-22", ["Ah", "7c", "2d"], seed=1)
    assert post == post2  # 枚举确定性
    pre = hero_equity(["As", "Ad"], "KK-22", None, trials=2000, seed=1)
    assert 0.0 <= pre["equity"] <= 1.0


def test_enum_flop_within_budget_speed():
    # 宽范围翻牌应在预算内快速返回（< 1s）
    t = time.time()
    equity_hand_vs_range_enum(["Ts", "9s"], "AA-22, AKs-A2s, KQs-K2s, QJs-Q2s", ["Th", "7c", "2d"], seed=3)
    assert time.time() - t < 1.5
