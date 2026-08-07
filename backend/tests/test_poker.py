"""扑克引擎冒烟测试。"""
from __future__ import annotations

import pytest

from app.poker.cards import normalize_card, normalize_cards
from app.poker.equity import equity_hand_vs_range, ev_call, pot_odds
from app.poker.evaluate import evaluate, hand_type


def test_normalize_card():
    assert normalize_card("as") == "As"
    assert normalize_card("Td") == "Td"
    assert normalize_card("10s") == "Ts"
    with pytest.raises(ValueError):
        normalize_card("Zx")
    with pytest.raises(ValueError):
        normalize_cards(["As", "As"])  # 重复


def test_evaluate_ranks_full_house_over_two_pair():
    # 葫芦 > 两对
    full_house = evaluate(["Ts", "Th", "Td", "3c", "3d"])
    two_pair = evaluate(["As", "Ad", "4c", "4d", "9h"])
    assert full_house > two_pair
    assert "Full House" in hand_type(["Ts", "Th", "Td", "3c", "3d"])


def test_equity_aa_vs_random_is_high():
    res = equity_hand_vs_range(["As", "Ad"], "22+, A2s+, K2s+", trials=3000, seed=42)
    assert 0.0 <= res["equity"] <= 1.0
    assert res["samples"] > 0
    # AA 对宽范围应显著领先
    assert res["equity"] > 0.7


def test_equity_dominated_on_board():
    # 英雄成葫芦（十满三），河牌已定；对手仅两对（KK+33），英雄必胜。
    res = equity_hand_vs_range(
        ["Ts", "Th"], "KhKd", board=["3s", "5s", "Td", "3d", "Ac"], trials=500, seed=1
    )
    assert res["equity"] > 0.99  # board 与 hero 全定，结果确定


def test_pot_odds_and_ev():
    odds = pot_odds(pot=100, call=50)
    assert abs(odds["required_equity"] - (50 / 150)) < 1e-9
    # equity 恰好等于所需赔率时 EV 约为 0
    assert abs(ev_call(50 / 150, pot=100, call=50)) < 1e-6
