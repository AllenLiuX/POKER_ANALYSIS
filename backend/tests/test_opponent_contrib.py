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


def test_contributions_include_hero_player():
    # 英雄本人也要产出贡献（一手可进多个玩家档案，英雄不例外）。
    res = hand_contributions({}, _hu_srp_cbet_fold())
    m = _by_alias(res)
    assert "Hero" in m and m["Hero"]["is_hero"] is True
    assert "Villain" in m and m["Villain"]["is_hero"] is False
    # 每位有昵称的玩家都在（含英雄）→ 同一手会计入两个档案
    assert len(res["players"]) == 2


def test_contributions_generated_for_needs_review_hand():
    # 「行动作与净额对不上」的待复核手（动作被漏读）也应产出贡献并计入。
    from app.ingest.reconstruct import reconstruct_hand

    facts = {
        "screenshot_type": "hand_replay",
        "pot": 2490,
        "players": [
            {"alias": "W", "net": 1245, "is_hero": True, "actions_by_street": {"preflop": ["Allin1245"]}},
            {"alias": "L", "net": -1245, "actions_by_street": {"preflop": ["下注32"]}},  # 漏读 → 待复核
        ],
    }
    recon = reconstruct_hand(facts)
    assert recon["status"] == "needs_review"  # 确实是对不上的手
    res = hand_contributions(facts, recon)
    m = _by_alias(res)
    assert set(m) == {"W", "L"}  # 两位玩家都进了贡献
    assert m["W"]["is_hero"] is True


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


def test_portrait_counters_call_then_lead_and_missed_initiative():
    recon = {
        "confidence": 0.9,
        "board": ["As", "Kd", "7c", "2h"],
        "players": [
            {
                "alias": "Hero",
                "position": "BTN",
                "is_hero": True,
                "hole_cards": ["Ah", "Ac"],
                "net": 20,
                "actions": [
                    {"action": "raise", "amount": 3, "street": "翻前"},
                    {"action": "bet", "amount": 4, "street": "翻牌"},
                    {"action": "check", "street": "转牌"},
                ],
            },
            {
                "alias": "Villain",
                "position": "BB",
                "is_hero": False,
                "hole_cards": [],
                "net": -20,
                "actions": [
                    {"action": "call", "amount": 3, "street": "翻前"},
                    {"action": "call", "amount": 4, "street": "翻牌"},
                    {"action": "bet", "amount": 10, "street": "转牌"},
                ],
            },
        ],
    }
    m = _by_alias(hand_contributions({"blinds": "1/2"}, recon))
    vil = m["Villain"]["counters"]
    hero = m["Hero"]["counters"]
    assert vil["call_then_lead"] == {"n": 1, "k": 1}
    assert hero["missed_initiative"] == {"n": 1, "k": 0}

    checked = {
        "confidence": 0.9,
        "board": ["As", "Kd", "7c"],
        "players": [
            {
                "alias": "Hero",
                "position": "BTN",
                "is_hero": True,
                "hole_cards": ["Ah", "Ac"],
                "net": 3,
                "actions": [
                    {"action": "raise", "amount": 3, "street": "翻前"},
                    {"action": "check", "street": "翻牌"},
                ],
            },
            {
                "alias": "Villain",
                "position": "BB",
                "is_hero": False,
                "hole_cards": [],
                "net": -3,
                "actions": [
                    {"action": "call", "amount": 3, "street": "翻前"},
                    {"action": "check", "street": "翻牌"},
                ],
            },
        ],
    }
    hero = _by_alias(hand_contributions({"blinds": "1/2"}, checked))["Hero"]["counters"]
    assert hero["missed_initiative"] == {"n": 1, "k": 1}


def test_showdown_portrait_counters_trap_hit_lead_and_air():
    trap = {
        "confidence": 0.9,
        "board": ["As", "8c", "3d"],
        "players": [
            {
                "alias": "Hero",
                "position": "BTN",
                "is_hero": True,
                "hole_cards": ["Ah", "Kd"],
                "net": 3,
                "actions": [
                    {"action": "raise", "amount": 3, "street": "翻前"},
                    {"action": "check", "street": "翻牌"},
                ],
            },
            {
                "alias": "Villain",
                "position": "BB",
                "is_hero": False,
                "hole_cards": ["8s", "8d"],
                "net": -3,
                "actions": [
                    {"action": "call", "amount": 3, "street": "翻前"},
                    {"action": "check", "street": "翻牌"},
                ],
            },
        ],
    }
    m = _by_alias(hand_contributions({"blinds": "1/2"}, trap))
    vil = m["Villain"]["counters"]
    hero = m["Hero"]["counters"]
    assert vil["slowplay_two_pair_plus"] == {"n": 1, "k": 1}
    assert vil["checked_strong_pair"] == {"n": 0, "k": 0}
    assert hero["checked_strong_pair"] == {"n": 1, "k": 1}

    hit_lead = {
        "confidence": 0.9,
        "board": ["Ah", "7h", "2c", "3h"],
        "players": [
            {
                "alias": "Hero",
                "position": "BTN",
                "is_hero": True,
                "hole_cards": ["Kc", "Qd"],
                "net": -20,
                "actions": [
                    {"action": "raise", "amount": 3, "street": "翻前"},
                    {"action": "bet", "amount": 4, "street": "翻牌"},
                    {"action": "check", "street": "转牌"},
                ],
            },
            {
                "alias": "Villain",
                "position": "BB",
                "is_hero": False,
                "hole_cards": ["6h", "5h"],
                "net": 20,
                "actions": [
                    {"action": "call", "amount": 3, "street": "翻前"},
                    {"action": "call", "amount": 4, "street": "翻牌"},
                    {"action": "bet", "amount": 10, "street": "转牌"},
                ],
            },
        ],
    }
    vil = _by_alias(hand_contributions({"blinds": "1/2"}, hit_lead))["Villain"]["counters"]
    assert vil["call_then_lead"] == {"n": 1, "k": 1}
    assert vil["hit_then_lead"] == {"n": 1, "k": 1}
    assert vil["shown_lead_was_hit"] == {"n": 1, "k": 1}

    air = {
        "confidence": 0.9,
        "board": ["As", "7h", "2c", "Kd", "Qc"],
        "players": [
            {
                "alias": "Hero",
                "position": "BTN",
                "is_hero": True,
                "hole_cards": ["9c", "8d"],
                "net": 20,
                "actions": [
                    {"action": "raise", "amount": 3, "street": "翻前"},
                    {"action": "check", "street": "翻牌"},
                    {"action": "check", "street": "转牌"},
                    {"action": "bet", "amount": 8, "street": "河牌"},
                ],
            },
            {
                "alias": "Villain",
                "position": "BB",
                "is_hero": False,
                "hole_cards": ["Jh", "Td"],
                "net": -20,
                "actions": [
                    {"action": "call", "amount": 3, "street": "翻前"},
                    {"action": "check", "street": "翻牌"},
                    {"action": "check", "street": "转牌"},
                    {"action": "check", "street": "河牌"},
                ],
            },
        ],
    }
    hero = _by_alias(hand_contributions({"blinds": "1/2"}, air))["Hero"]["counters"]
    assert hero["shown_air_aggression"]["n"] >= 1
    assert hero["shown_air_aggression"]["k"] >= 1
    assert hero["river_air_bluff"] == {"n": 1, "k": 1}

    extra = {
        "confidence": 0.9,
        "board": ["Ah", "7h", "2c", "3h"],
        "players": [
            {
                "alias": "Hero",
                "position": "BTN",
                "is_hero": True,
                "hole_cards": ["Kc", "Qd"],
                "net": 40,
                "actions": [
                    {"action": "raise", "amount": 3, "street": "翻前"},
                    {"action": "check", "street": "翻牌"},
                    {"action": "fold", "amount": 10, "street": "转牌"},
                ],
            },
            {
                "alias": "Villain",
                "position": "BB",
                "is_hero": False,
                "hole_cards": ["6h", "5h"],
                "net": -40,
                "actions": [
                    {"action": "call", "amount": 3, "street": "翻前"},
                    {"action": "check", "street": "翻牌"},
                    {"action": "bet", "amount": 10, "street": "转牌"},
                ],
            },
        ],
    }
    vil = _by_alias(hand_contributions({"blinds": "1/2"}, extra))["Villain"]["counters"]
    assert vil["bet_aversion"]["n"] >= 1
    assert vil["draw_complete_donk"] == {"n": 1, "k": 1}


def test_showdown_strength_band_counters():
    thin = {
        "confidence": 0.9,
        "board": ["As", "8c", "3d", "2h", "4c"],
        "players": [
            {
                "alias": "Hero",
                "position": "BTN",
                "is_hero": True,
                "hole_cards": ["Ah", "Kd"],
                "net": 20,
                "actions": [
                    {"action": "raise", "amount": 3, "street": "翻前"},
                    {"action": "check", "street": "翻牌"},
                    {"action": "call", "amount": 6, "street": "转牌"},
                    {"action": "bet", "amount": 8, "street": "河牌"},
                ],
            },
            {
                "alias": "Villain",
                "position": "BB",
                "is_hero": False,
                "hole_cards": ["9c", "7d"],
                "net": -20,
                "actions": [
                    {"action": "call", "amount": 3, "street": "翻前"},
                    {"action": "check", "street": "翻牌"},
                    {"action": "bet", "amount": 6, "street": "转牌"},
                    {"action": "check", "street": "河牌"},
                ],
            },
        ],
    }
    m = _by_alias(hand_contributions({"blinds": "1/2"}, thin))
    assert m["Hero"]["counters"]["thin_value_medium"] == {"n": 1, "k": 1}
    assert m["Villain"]["counters"]["thin_value_medium"] == {"n": 0, "k": 0}

    weak = {
        "confidence": 0.9,
        "board": ["As", "7h", "2c", "Kd", "9s"],
        "players": [
            {
                "alias": "Hero",
                "position": "BTN",
                "is_hero": True,
                "hole_cards": ["Kc", "Qd"],
                "net": 50,
                "actions": [
                    {"action": "raise", "amount": 6, "street": "翻前"},
                    {"action": "bet", "amount": 8, "street": "翻牌"},
                    {"action": "bet", "amount": 40, "street": "转牌"},
                ],
            },
            {
                "alias": "Villain",
                "position": "BB",
                "is_hero": False,
                "hole_cards": ["4c", "4d"],
                "net": -50,
                "actions": [
                    {"action": "call", "amount": 6, "street": "翻前"},
                    {"action": "call", "amount": 8, "street": "翻牌"},
                    {"action": "call", "amount": 40, "street": "转牌"},
                ],
            },
        ],
    }
    vil = _by_alias(hand_contributions({"blinds": "1/2"}, weak))["Villain"]["counters"]
    assert vil["weak_pays_big"] == {"n": 1, "k": 1}
    assert vil["medium_calls_big"] == {"n": 0, "k": 0}

    medium = {
        "confidence": 0.9,
        "board": ["As", "7h", "2c", "Kd", "9s"],
        "players": [
            {
                "alias": "Hero",
                "position": "BTN",
                "is_hero": True,
                "hole_cards": ["Kc", "Qd"],
                "net": 50,
                "actions": [
                    {"action": "raise", "amount": 6, "street": "翻前"},
                    {"action": "bet", "amount": 8, "street": "翻牌"},
                    {"action": "bet", "amount": 40, "street": "转牌"},
                ],
            },
            {
                "alias": "Villain",
                "position": "BB",
                "is_hero": False,
                "hole_cards": ["Ah", "Qd"],
                "net": -50,
                "actions": [
                    {"action": "call", "amount": 6, "street": "翻前"},
                    {"action": "call", "amount": 8, "street": "翻牌"},
                    {"action": "call", "amount": 40, "street": "转牌"},
                ],
            },
        ],
    }
    med = _by_alias(hand_contributions({"blinds": "1/2"}, medium))["Villain"]["counters"]
    assert med["medium_calls_big"] == {"n": 1, "k": 1}
    assert med["weak_pays_big"] == {"n": 0, "k": 0}

    river_weak = {
        "confidence": 0.9,
        "board": ["As", "7h", "2c", "Kd", "Qc"],
        "players": [
            {
                "alias": "Hero",
                "position": "BTN",
                "is_hero": True,
                "hole_cards": ["Ah", "Kd"],
                "net": 15,
                "actions": [
                    {"action": "raise", "amount": 6, "street": "翻前"},
                    {"action": "check", "street": "翻牌"},
                    {"action": "check", "street": "转牌"},
                    {"action": "bet", "amount": 3, "street": "河牌"},
                ],
            },
            {
                "alias": "Villain",
                "position": "BB",
                "is_hero": False,
                "hole_cards": ["9c", "8d"],
                "net": -15,
                "actions": [
                    {"action": "call", "amount": 6, "street": "翻前"},
                    {"action": "check", "street": "翻牌"},
                    {"action": "check", "street": "转牌"},
                    {"action": "call", "amount": 3, "street": "河牌"},
                ],
            },
        ],
    }
    rw = _by_alias(hand_contributions({"blinds": "1/2"}, river_weak))["Villain"]["counters"]
    assert rw["river_weak_call"] == {"n": 1, "k": 1}

