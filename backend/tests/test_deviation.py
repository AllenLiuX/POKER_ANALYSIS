"""阶段③ GTO 偏离标注（确定性引擎）+ /api/ingest/analyze 端点。

真相来自范围表 + score_action；这里验证 spot 判定、判级、倾向、离树处理与优雅降级。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.ingest.deviation import analyze_deviations
from app.main import app

client = TestClient(app)


def _pre(alias, pos, cards, action, amount=None, is_hero=False, net=None):
    return {
        "alias": alias,
        "position": pos,
        "is_hero": is_hero,
        "hole_cards": cards,
        "net": net,
        "actions": [{"action": action, "amount": amount, "street": "翻前"}],
    }


def test_rfi_open_optimal():
    recon = {"confidence": 0.9, "board": [], "players": [_pre("H", "CO", ["Tc", "Th"], "raise", 3, is_hero=True)]}
    a = analyze_deviations({}, recon)
    dev = a["players"][0]["deviations"][0]
    assert dev["spot"] == "RFI" and dev["position"] == "CO"
    assert dev["grade"] == "optimal" and dev["optimal_action"] == "raise"
    assert a["counts"]["grounded"] == 1 and a["counts"]["mistakes"] == 0


def test_vs_rfi_bb_overfold_is_too_tight():
    # CO 开池、BB 拿 KQo 弃牌 → 面对开池 GTO 该防守 → 偏离/过紧
    recon = {
        "confidence": 0.9,
        "board": [],
        "players": [
            _pre("Opener", "CO", ["As", "Ks"], "raise", 3),
            _pre("Hero", "BB", ["Kd", "Qc"], "fold", is_hero=True),
        ],
    }
    a = analyze_deviations({}, recon)
    hero = next(p for p in a["players"] if p["is_hero"])
    dev = hero["deviations"][0]
    assert dev["spot"] == "vs_RFI" and dev["opener"] == "CO"
    assert dev["grade"] == "mistake"
    assert dev["deviation_type"] == "too_tight"


def test_off_tree_flat_call_flagged():
    # SB 面对 CO 开池的 GTO 基线是 3bet/弃牌（无平跟）；平跟应标离树偏离/过松
    recon = {
        "confidence": 0.8,
        "board": [],
        "players": [
            _pre("Opener", "CO", ["As", "Ks"], "raise", 3),
            _pre("SBcaller", "SB", ["Ah", "4d"], "call", 3),
        ],
    }
    a = analyze_deviations({}, recon)
    sb = next(p for p in a["players"] if p["alias"] == "SBcaller")
    dev = sb["deviations"][0]
    assert dev["spot"] == "vs_RFI"
    assert dev["off_tree"] is True
    assert dev["grade"] == "mistake"
    assert dev["actual"] == "call" and dev["chosen_freq"] == 0.0


def test_threebet_pot_downstream_ungrounded():
    # 开池后有人 3bet；3bet 者本身可接地（vs_RFI 的 raise），但开池者面对 3bet（4bet 决策）不接地
    recon = {
        "confidence": 0.9,
        "board": [],
        "players": [
            _pre("Opener", "CO", ["Ah", "Kh"], "raise", 3, is_hero=True),   # 面对随后 3bet 的决策不在此动作里
            _pre("ThreeBettor", "BTN", ["Qs", "Qd"], "raise", 10),          # BTN vs CO 3bet → vs_RFI raise 可接地
        ],
    }
    a = analyze_deviations({}, recon)
    tb = next(p for p in a["players"] if p["alias"] == "ThreeBettor")
    dev = tb["deviations"][0]
    assert dev["spot"] == "vs_RFI" and dev["opener"] == "CO"
    # 开池者只有 RFI 那一个接地决策（其面对 3bet 的后续动作没有给出/不覆盖）
    opener = next(p for p in a["players"] if p["is_hero"])
    assert all(d["spot"] in ("RFI",) for d in opener["deviations"] if d["spot"] != "postflop")


def test_postflop_heuristic_note_for_showdown_value():
    recon = {
        "confidence": 0.9,
        "board": ["3s", "5s", "Ts", "3d", "Ah"],
        "players": [
            {
                "alias": "H", "position": "CO", "is_hero": True, "hole_cards": ["Tc", "Th"], "net": 100,
                "actions": [
                    {"action": "raise", "amount": 3, "street": "翻前"},
                    {"action": "bet", "amount": 5, "street": "翻牌"},
                    {"action": "allin", "amount": 90, "street": "河牌"},
                ],
            }
        ],
    }
    a = analyze_deviations({}, recon)
    notes = [d for d in a["players"][0]["deviations"] if d["spot"] == "postflop"]
    assert notes and notes[0]["approximate"] is True and notes[0]["grounded"] is False
    assert "葫芦" in notes[0]["note"]


def test_postflop_grounded_cbet_value_hu_srp():
    # HU 单加注底池：BTN 开池、BB 跟注；翻牌 A K 7，BTN 持 AA 顶set c-bet → 接地打分（价值下注=最优）
    recon = {
        "confidence": 0.9,
        "board": ["As", "Kd", "7c"],
        "players": [
            {
                "alias": "Hero", "position": "BTN", "is_hero": True, "hole_cards": ["Ah", "Ac"], "net": 20,
                "actions": [
                    {"action": "raise", "amount": 3, "street": "翻前"},
                    {"action": "bet", "amount": 4, "street": "翻牌"},
                ],
            },
            {
                "alias": "Villain", "position": "BB", "is_hero": False, "hole_cards": [], "net": -20,
                "actions": [
                    {"action": "call", "amount": 3, "street": "翻前"},
                    {"action": "fold", "amount": None, "street": "翻牌"},
                ],
            },
        ],
    }
    a = analyze_deviations({}, recon)
    hero = next(p for p in a["players"] if p["is_hero"])
    pf = [d for d in hero["deviations"] if d["spot"].startswith("postflop")]
    assert pf, "应产生接地的翻后决策打分"
    fl = next(d for d in pf if d["street"] == "翻牌")
    assert fl["grounded"] is True and fl["approximate"] is False
    assert fl["actual"] == "bet" and fl["grade"] in ("optimal", "acceptable")
    assert 0.0 <= fl["equity"] <= 1.0
    assert "villain_range_label" in fl


def test_no_holecards_not_graded():
    recon = {"confidence": 0.9, "board": [], "players": [_pre("Folder", "BTN", [], "fold")]}
    a = analyze_deviations({}, recon)
    assert a["supported"] is False
    assert a["counts"]["graded"] == 0


def test_reanalyze_endpoint_recomputes_from_edited_facts():
    # 用户修正后的事实（BB 拿 KQo 面对 CO 开池弃牌）→ 重跑得到 too_tight
    facts = {
        "screenshot_type": "hand_replay",
        "board": [],
        "pot": 8,
        "players": [
            {"alias": "Opener", "position": "CO", "hole_cards": ["As", "Ks"], "net": 5,
             "actions_by_street": {"preflop": ["加注3"]}},
            {"alias": "Hero", "position": "BB", "is_hero": True, "hole_cards": ["Kd", "Qc"], "net": -1,
             "actions_by_street": {"preflop": ["弃牌"]}},
        ],
    }
    r = client.post("/api/ingest/reanalyze", json={"facts": facts})
    assert r.status_code == 200
    body = r.json()
    assert "reconstruction" in body and "analysis" in body and "facts" in body
    hero = next(p for p in body["analysis"]["players"] if p["is_hero"])
    dev = next(d for d in hero["deviations"] if d["spot"] == "vs_RFI")
    assert dev["deviation_type"] == "too_tight"
    # 归一：编辑里若写 10♠ 会被清洗为 Ts
    facts2 = {**facts, "board": ["10♠", "Kd", "7c"]}
    r2 = client.post("/api/ingest/reanalyze", json={"facts": facts2})
    assert r2.status_code == 200
    assert r2.json()["facts"]["board"][0] == "Ts"


def test_analyze_endpoint_ok_and_400():
    recon = {"confidence": 0.9, "board": [], "players": [_pre("H", "BTN", ["As", "Ah"], "raise", 3, is_hero=True)]}
    r = client.post("/api/ingest/analyze", json={"facts": {}, "reconstruction": recon})
    assert r.status_code == 200
    a = r.json()["analysis"]
    assert a["supported"] is True
    assert a["players"][0]["deviations"][0]["grade"] == "optimal"

    r2 = client.post("/api/ingest/analyze", json={"facts": {}, "reconstruction": None})
    assert r2.status_code == 400
