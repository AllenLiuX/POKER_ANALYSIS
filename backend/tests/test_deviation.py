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


def test_no_holecards_not_graded():
    recon = {"confidence": 0.9, "board": [], "players": [_pre("Folder", "BTN", [], "fold")]}
    a = analyze_deviations({}, recon)
    assert a["supported"] is False
    assert a["counts"]["graded"] == 0


def test_analyze_endpoint_ok_and_400():
    recon = {"confidence": 0.9, "board": [], "players": [_pre("H", "BTN", ["As", "Ah"], "raise", 3, is_hero=True)]}
    r = client.post("/api/ingest/analyze", json={"facts": {}, "reconstruction": recon})
    assert r.status_code == 200
    a = r.json()["analysis"]
    assert a["supported"] is True
    assert a["players"][0]["deviations"][0]["grade"] == "optimal"

    r2 = client.post("/api/ingest/analyze", json={"facts": {}, "reconstruction": None})
    assert r2.status_code == 400
