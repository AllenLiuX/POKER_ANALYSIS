"""翻后启发式引擎 + API 测试。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.poker.postflop.analyze import analyze_spot
from app.poker.postflop.handstrength import classify_hand
from app.poker.postflop.heuristics import (
    mdf,
    pot_odds_required,
    recommend_cbet,
    recommend_defense,
)
from app.poker.postflop.scenario import generate_postflop_scenario
from app.poker.postflop.scoring import score_postflop
from app.poker.postflop.texture import classify_board

client = TestClient(app)


# ---------- 纹理 ----------
def test_texture_dry_high():
    t = classify_board(["Ks", "7d", "2c"])
    assert t["suitedness"] == "rainbow"
    assert not t["paired"]
    assert t["wet_label"] == "干"
    assert t["high_label"] == "高张面"


def test_texture_wet_connected():
    t = classify_board(["9s", "8s", "7d"])  # 两色强连接
    assert t["straightiness"] >= 3
    assert t["wetness"] > classify_board(["Ks", "7d", "2c"])["wetness"]


def test_texture_monotone():
    t = classify_board(["Qh", "8h", "3h"])
    assert t["suitedness"] == "monotone"
    assert t["wetness"] >= 0.5


# ---------- 成手 / 听牌 ----------
def test_classify_set_is_value():
    h = classify_hand(["7c", "7h"], ["7s", "Kd", "2c"])  # 三条
    assert h["made"] == "Trips"
    assert h["tier"] == "value"


def test_classify_top_pair():
    h = classify_hand(["Ah", "Kc"], ["Ad", "9s", "2c"])  # 顶对 A，K 踢脚
    assert h["made"] == "Pair"
    assert h["pair_kind"] == "top_pair"
    assert h["tier"] == "value"


def test_classify_flush_draw():
    h = classify_hand(["As", "5s"], ["Ks", "9s", "2c"])  # 同花听牌（花 A 高）
    assert "flush_draw" in h["draws"]
    assert h["tier"] in ("draw",)  # 无成对，但强听牌


def test_classify_oesd():
    h = classify_hand(["9c", "8d"], ["7s", "6h", "2c"])  # 两头顺听
    assert "oesd" in h["draws"]


def test_classify_air():
    h = classify_hand(["Qc", "4d"], ["Ks", "9h", "2c"])
    assert h["tier"] == "air"


# ---------- 赔率 / MDF ----------
def test_pot_odds_and_mdf():
    # 半池下注：需要 25% 胜率，MDF≈67%
    assert abs(pot_odds_required(8.25, 2.75) - 0.25) < 1e-6
    assert abs(mdf(8.25, 2.75) - 0.75) < 1e-6


def test_defense_air_folds():
    hand = {"tier": "air", "made": "High Card", "made_label": "高牌", "draw_label": ""}
    rec = recommend_defense({"wetness": 0.3}, hand, equity=0.15, pot_bb=8.25, bet_bb=2.75)
    assert rec["recommended"] == "fold"


def test_hand_playing_the_board_is_not_value():
    # 牌面 KK77Q，英雄 J5 一张都用不上：两对全在公共牌，只能平分 → 不是价值牌
    hc = classify_hand(["Jd", "5d"], ["Kh", "Qc", "Kd", "7h", "7c"])
    assert hc["made"] == "Two Pair"
    assert hc["board_dominated"] is True
    assert hc["tier"] == "air"  # 降到空气档，不会再建议"下注取价值"


def test_river_straight_on_board_plays_the_board():
    # 顺子完全在公共牌上，英雄没加强 → 打公共牌，air
    hc = classify_hand(["2c", "3d"], ["5h", "6s", "7d", "8c", "9h"])
    assert hc["board_dominated"] is True
    assert hc["tier"] == "air"


def test_real_two_pair_still_value():
    # 英雄用上底牌构成两对（Kx + Qx），仍是价值牌
    hc = classify_hand(["Kd", "Qs"], ["Kh", "Qc", "7d"])
    assert hc["made"] == "Two Pair"
    assert hc["board_dominated"] is False
    assert hc["tier"] == "value"


def test_board_paired_hero_unrelated_not_medium():
    # 牌面 KKQ，英雄 8 3 无关：只是公共牌的一对 K，无听牌 → 空气，而非"中等成手"
    hc = classify_hand(["8d", "3s"], ["Kh", "Kc", "Qd"])
    assert hc["board_dominated"] is True
    assert hc["tier"] == "air"
    assert hc["tier"] != "medium"


def test_defense_air_calls_when_pot_odds_met():
    # 纯高牌但对宽范围有 47% 胜率、只需 25%：按底池赔率应跟注（修复此前"空气一律弃牌"的 bug）
    hand = {"tier": "air", "made": "High Card", "made_label": "高牌", "draw_label": ""}
    rec = recommend_defense({"wetness": 0.3}, hand, equity=0.47, pot_bb=8.25, bet_bb=2.75)
    assert rec["recommended"] == "call"
    # 理由不再自相矛盾（不会出现 "47% < 25%" 这种）
    assert all("<" not in r for r in rec["reasons"])


def test_defense_value_raises():
    hand = {"tier": "value", "made": "Two Pair", "made_label": "两对", "draw_label": ""}
    rec = recommend_defense({"wetness": 0.4}, hand, equity=0.72, pot_bb=8.25, bet_bb=2.75)
    assert rec["recommended"] == "raise"
    assert "call" in rec["accept"]


def test_defense_draw_calls_when_priced_in():
    # 同花听牌，到河胜率 35% ≥ 半池所需 25% → 跟注（可半诈唬加注）
    hand = {"tier": "draw", "made": "High Card", "made_label": "高牌", "draw_label": "同花听牌"}
    rec = recommend_defense({"wetness": 0.5}, hand, equity=0.35, pot_bb=8.25, bet_bb=2.75)
    assert rec["recommended"] == "call"
    assert "raise" in rec["accept"]


def test_defense_draw_folds_when_underpriced():
    # 同一听牌面对满池下注：需要 50%，35% 不够，且理由不再自相矛盾地写“≥”
    hand = {"tier": "draw", "made": "High Card", "made_label": "高牌", "draw_label": "同花听牌"}
    rec = recommend_defense({"wetness": 0.5}, hand, equity=0.35, pot_bb=5.5, bet_bb=5.5)
    assert rec["recommended"] == "fold"
    assert all("≥" not in r for r in rec["reasons"])


# ---------- c-bet 河牌意识 ----------
def test_cbet_air_range_bets_on_dry_flop_but_checks_on_river():
    # 干燥高张翻牌：空气可高频小注范围下注
    tex_f = classify_board(["Ks", "9h", "2c"])
    hand_f = classify_hand(["Qc", "4d"], ["Ks", "9h", "2c"])
    assert hand_f["tier"] in ("air", "weak")
    rec_f = recommend_cbet(tex_f, hand_f, equity=0.10)
    assert rec_f["recommended"] == "bet"
    # 同样的空气到了河牌：无保护/听牌价值 → 过牌（纯手牌力不乱诈唬）
    tex_r = classify_board(["Ks", "9h", "2c", "5d", "3s"])
    hand_r = classify_hand(["Qc", "4d"], ["Ks", "9h", "2c", "5d", "3s"])
    assert hand_r["tier"] in ("air", "weak")
    rec_r = recommend_cbet(tex_r, hand_r, equity=0.10)
    assert rec_r["recommended"] == "check"


def test_cbet_medium_river_thin_value_needs_edge():
    tex_r = classify_board(["Ks", "9h", "2c", "5d", "3s"])
    hand = {"tier": "medium"}
    # 河牌边缘成手：真的领先才薄价值下注
    assert recommend_cbet(tex_r, hand, equity=0.60)["recommended"] == "bet"
    # 胜率不足以领先跟注范围 → 过牌
    assert recommend_cbet(tex_r, hand, equity=0.45)["recommended"] == "check"


# ---------- 打分 ----------
def test_score_optimal_and_mistake():
    rec = {"recommended": "bet", "accept": ["bet", "check"], "mix": True}
    assert score_postflop(rec, "bet")["grade"] == "optimal"
    assert score_postflop(rec, "check")["grade"] == "acceptable"
    assert score_postflop(rec, "fold")["grade"] == "mistake"


def test_score_size_downgrades_optimal_to_acceptable():
    rec = {
        "recommended": "bet",
        "accept": ["bet"],
        "recommended_size": "big",
        "accept_sizes": ["half", "big", "pot"],
    }
    # 正确尺度 -> 仍 optimal
    ok = score_postflop(rec, "bet", "big")
    assert ok["grade"] == "optimal" and ok["size_ok"] is True
    # 偏离尺度 -> 降为 acceptable，但仍算正确
    off = score_postflop(rec, "bet", "small")
    assert off["grade"] == "acceptable" and off["correct"] is True
    assert off["size_ok"] is False


def test_score_size_ignored_when_action_wrong():
    rec = {
        "recommended": "check",
        "accept": ["check"],
        "recommended_size": "small",
        "accept_sizes": ["small"],
    }
    s = score_postflop(rec, "bet", "pot")
    assert s["grade"] == "mistake" and s["size_ok"] is None


# ---------- 场景 ----------
def test_scenario_reproducible():
    a = generate_postflop_scenario(seed=11)
    b = generate_postflop_scenario(seed=11)
    a.pop("id")
    b.pop("id")
    assert a == b


def test_scenario_shape_pfr():
    s = generate_postflop_scenario(role="pfr", seed=5)
    assert s["role"] == "pfr"
    assert set(s["available_actions"]) == {"check", "bet"}
    assert len(s["board"]) == 3 and len(s["hero"]) == 2
    assert s["villain_range"]
    assert s["bet_bb"] is None
    # c-bet 尺度选项：4 档、有具体 bb 额、含 ½ 池
    assert len(s["bet_sizes"]) == 4
    ids = {o["id"] for o in s["bet_sizes"]}
    assert {"small", "half", "big", "pot"} <= ids
    assert all(o["amount_bb"] > 0 for o in s["bet_sizes"])
    assert not s["raise_sizes"]


def test_scenario_shape_caller():
    s = generate_postflop_scenario(role="caller", seed=6)
    assert s["role"] == "caller"
    assert set(s["available_actions"]) == {"fold", "call", "raise"}
    assert s["bet_bb"] and s["bet_bb"] > 0
    # 加注尺度选项存在，且 raise-to 额大于对手下注
    assert len(s["raise_sizes"]) >= 2
    assert all(o["amount_bb"] > s["bet_bb"] for o in s["raise_sizes"])
    assert not s["bet_sizes"]


# ---------- 门面 + API ----------
def test_analyze_nuts_defense_raises():
    # 87 在 9-8-7 面拿到两对+，面对下注应加注
    _, hand, eq, rec = analyze_spot(
        role="caller",
        hero=["8c", "7c"],
        board=["9s", "8d", "7h"],
        villain_range="AA, KK, QQ, AKs, AKo, AQs",
        pot_bb=8.25,
        bet_bb=2.75,
    )
    assert rec["recommended"] in ("raise", "call")
    assert rec["equity"] > 0.4


def test_postflop_next_and_answer_endpoints():
    r = client.get("/api/trainer/postflop/next", params={"role": "caller", "seed": 7})
    assert r.status_code == 200
    scen = r.json()["scenario"]
    assert scen["role"] == "caller"

    ans = client.post(
        "/api/trainer/postflop/answer",
        json={
            "role": scen["role"],
            "hero": scen["hero"],
            "board": scen["board"],
            "villain_range": scen["villain_range"],
            "pot_bb": scen["pot_bb"],
            "bet_bb": scen["bet_bb"],
            "action": "fold",
            "scenario_id": scen["id"],
        },
    )
    assert ans.status_code == 200
    body = ans.json()
    assert body["approximate"] is True
    assert body["score"]["grade"] in ("optimal", "acceptable", "mistake")
    assert body["feedback"]["headline"]
    assert "recommendation" in body


def test_postflop_answer_deterministic():
    payload = {
        "role": "pfr",
        "hero": ["Ah", "Ad"],
        "board": ["Ks", "7d", "2c"],
        "villain_range": "22-99, AJs, KQs, T9s, 98s",
        "pot_bb": 5.5,
        "bet_bb": None,
        "action": "bet",
    }
    a = client.post("/api/trainer/postflop/answer", json=payload).json()
    b = client.post("/api/trainer/postflop/answer", json=payload).json()
    assert a["equity"] == b["equity"]  # 种子固定 -> 可复现
    assert a["score"]["grade"] == "optimal"  # AA 超对干面必下注


def test_postflop_answer_with_size_label():
    payload = {
        "role": "pfr",
        "hero": ["Ah", "Ad"],
        "board": ["Ks", "7d", "2c"],
        "villain_range": "22-99, AJs, KQs, T9s, 98s",
        "pot_bb": 5.5,
        "bet_bb": None,
        "action": "bet",
        "size": "half",
    }
    body = client.post("/api/trainer/postflop/answer", json=payload).json()
    score = body["score"]
    assert score["size"] == "half"
    assert score.get("size_label")  # 有中文尺度标签
    assert score.get("recommended_size_label")
    assert "启发式近似" not in body["feedback"]["explanation"]  # 不再每次都备注


# ---------- 翻后 LLM 教练（mock provider）----------
def test_postflop_coach_prompt_grounds_engine_facts():
    from app.poker.postflop.analyze import analyze_spot
    from app.poker.postflop.coach_llm import build_postflop_coach_prompt
    from app.poker.postflop.scoring import score_postflop

    texture, hand, equity, rec = analyze_spot(
        role="caller",
        hero=["8c", "7c"],
        board=["9s", "8d", "7h"],
        villain_range="AA, KK, QQ, AKs",
        pot_bb=8.25,
        bet_bb=2.75,
    )
    score = score_postflop(rec, "call")
    prompt = build_postflop_coach_prompt(
        role="caller",
        hero_pos="BB",
        villain_pos="CO",
        board_glyphs="9♠ 8♦ 7♥",
        texture=texture,
        hand=hand,
        equity=equity,
        rec=rec,
        score=score,
    )
    # 关键事实（胜率/MDF/建议）必须出现在 prompt 里，供 LLM 接地
    assert f"{equity:.0%}" in prompt
    assert "MDF" in prompt
    assert "9♠ 8♦ 7♥" in prompt


def test_postflop_coach_endpoint_ok(monkeypatch):
    from app.api import postflop as postflop_api

    class _FakeProvider:
        gateway_ready = True
        openai_ready = False

        def text(self, prompt, **kwargs):
            assert "估算胜率" in prompt  # 确认引擎事实进了 prompt
            return "在湿润连接面上，你的两对已是价值牌，快速下注保护并向对手抽牌收租。"

    monkeypatch.setattr(postflop_api, "get_provider", lambda: _FakeProvider())
    r = client.post(
        "/api/trainer/postflop/coach",
        json={
            "role": "caller",
            "hero": ["8c", "7c"],
            "board": ["9s", "8d", "7h"],
            "villain_range": "AA, KK, QQ, AKs",
            "pot_bb": 8.25,
            "bet_bb": 2.75,
            "hero_position": "BB",
            "villain_position": "CO",
            "action": "call",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["coaching"]
    assert body["action_label"] == "跟注"


def test_postflop_coach_endpoint_503_when_llm_unavailable(monkeypatch):
    from app.api import postflop as postflop_api

    class _NoProvider:
        gateway_ready = False
        openai_ready = False

    monkeypatch.setattr(postflop_api, "get_provider", lambda: _NoProvider())
    r = client.post(
        "/api/trainer/postflop/coach",
        json={
            "role": "pfr",
            "hero": ["Ah", "Ad"],
            "board": ["Ks", "7d", "2c"],
            "villain_range": "22-99, AJs",
            "pot_bb": 5.5,
            "bet_bb": None,
            "action": "bet",
        },
    )
    assert r.status_code == 503
