"""逐对手可加计数器贡献（opponent_contrib）单测。"""
from fastapi.testclient import TestClient

from app.ingest.opponent_contrib import hand_contributions
from app.main import app

client = TestClient(app)


def _by_alias(res):
    return {p["alias"]: p for p in res["players"]}


def _hu_srp_cbet_fold():
    # HU 单加注底池：BTN 开池、BB 跟注；翻牌 BTN c-bet，BB 弃牌
    return {
        "confidence": 0.9,
        "board": ["As", "Kd", "7c"],
        "players": [
            {"alias": "Hero", "position": "BTN", "is_hero": True, "hole_cards": ["Ah", "Ac"], "net": 10,
             "actions": [
                 {"action": "raise", "amount": 3, "street": "翻前"},
                 {"action": "bet", "amount": 4, "street": "翻牌"},
             ]},
            {"alias": "Villain", "position": "BB", "is_hero": False, "hole_cards": [], "net": -10,
             "actions": [
                 {"action": "call", "amount": 3, "street": "翻前"},
                 {"action": "fold", "amount": None, "street": "翻牌"},
             ]},
        ],
    }


def test_hu_srp_open_call_cbet_fold_counters():
    res = hand_contributions({}, _hu_srp_cbet_fold())
    m = _by_alias(res)
    hero, vil = m["Hero"]["counters"], m["Villain"]["counters"]

    # 开池方（BTN）
    assert hero["pf_open"] == {"n": 1, "k": 1}
    assert hero["pfr"]["k"] == 1 and hero["vpip"]["k"] == 1
    assert hero["cbet_flop"] == {"n": 1, "k": 1}
    assert hero["saw_flop"]["k"] == 1

    # 防守方（BB）
    assert vil["pf_vs_open"] == {"n": 1, "fold": 0, "call": 1, "raise": 0}
    assert vil["pfr"]["k"] == 0 and vil["vpip"]["k"] == 1
    assert vil["fold_vs_cbet_flop"] == {"n": 1, "k": 1}
    assert vil["saw_flop"]["k"] == 1 and vil["wtsd"] == {"n": 1, "k": 0}  # 未摊牌
    assert res["players"][1]["net"] == -10.0


def test_vs_open_threebet_counter():
    recon = {
        "confidence": 0.9, "board": [],
        "players": [
            {"alias": "CO", "position": "CO", "is_hero": False, "hole_cards": [], "net": -3,
             "actions": [{"action": "raise", "amount": 3, "street": "翻前"}, {"action": "fold", "amount": None, "street": "翻前"}]},
            {"alias": "BTN", "position": "BTN", "is_hero": True, "hole_cards": ["Ah", "Ad"], "net": 3,
             "actions": [{"action": "raise", "amount": 9, "street": "翻前"}]},
        ],
    }
    m = _by_alias(hand_contributions({}, recon))
    assert m["CO"]["counters"]["pf_open"] == {"n": 1, "k": 1}
    assert m["BTN"]["counters"]["pf_vs_open"] == {"n": 1, "fold": 0, "call": 0, "raise": 1}


def test_showdown_won_and_af_post():
    # 两人走到摊牌，赢家净额为正、有底牌 → won_sd 命中；翻后激进度分量正确
    recon = {
        "confidence": 0.9, "board": ["As", "Kd", "7c", "2h", "9s"],
        "players": [
            {"alias": "Hero", "position": "BTN", "is_hero": True, "hole_cards": ["Ah", "Ac"], "net": 40,
             "actions": [
                 {"action": "raise", "amount": 3, "street": "翻前"},
                 {"action": "bet", "amount": 5, "street": "翻牌"},
                 {"action": "bet", "amount": 12, "street": "转牌"},
             ]},
            {"alias": "Fish", "position": "BB", "is_hero": False, "hole_cards": ["Kh", "Qd"], "net": -40,
             "actions": [
                 {"action": "call", "amount": 3, "street": "翻前"},
                 {"action": "call", "amount": 5, "street": "翻牌"},
                 {"action": "call", "amount": 12, "street": "转牌"},
             ]},
        ],
    }
    m = _by_alias(hand_contributions({}, recon))
    fish = m["Fish"]["counters"]
    assert fish["wtsd"] == {"n": 1, "k": 1}          # 看翻牌且摊牌
    assert fish["won_sd"] == {"n": 1, "k": 0}        # 摊牌但净额为负
    assert fish["af_post"] == {"aggr": 0, "passive": 2}  # 翻牌+转牌各跟注一次
    hero = m["Hero"]["counters"]
    assert hero["won_sd"] == {"n": 1, "k": 1}


def test_tier2_leaks_from_analysis():
    recon = _hu_srp_cbet_fold()
    analysis = {
        "supported": True,
        "players": [
            {"alias": "Villain", "is_hero": False, "deviations": [
                {"spot": "vs_RFI", "grounded": True, "grade": "mistake", "deviation_type": "too_tight"},
            ]},
        ],
    }
    m = _by_alias(hand_contributions({}, recon, analysis))
    vil = m["Villain"]["counters"]
    assert vil["graded_pre"] == {"n": 1, "mistakes": 1}
    assert vil["leaks_pre"] == {"too_tight": 1}


def test_contributions_endpoint_from_facts():
    facts = {
        "screenshot_type": "hand_replay",
        "board": ["As", "Kd", "7c"],
        "pot": 8,
        "players": [
            {"alias": "Opener", "position": "BTN", "hole_cards": ["Ah", "Ac"], "net": 5,
             "actions_by_street": {"preflop": ["加注3"], "flop": ["下注4"]}},
            {"alias": "Hero", "position": "BB", "is_hero": True, "hole_cards": ["Kd", "Qc"], "net": -5,
             "actions_by_street": {"preflop": ["跟注3"], "flop": ["弃牌"]}},
        ],
    }
    r = client.post("/api/ingest/contributions", json={"facts": facts})
    assert r.status_code == 200
    contribs = r.json()["contributions"]
    assert contribs["players"], "应产出逐玩家贡献"
    m = _by_alias(contribs)
    assert m["Opener"]["counters"]["cbet_flop"] == {"n": 1, "k": 1}
    assert m["Hero"]["counters"]["fold_vs_cbet_flop"] == {"n": 1, "k": 1}
