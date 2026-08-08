"""HU 对战引擎 + API 测试。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.poker.battle import engine as E

client = TestClient(app)


def _passive(b):
    """英雄尽量被动地把牌打到结束：能过牌/跟注就过牌/跟注，否则跟注/过牌，再不行弃牌。"""
    while b.result is None:
        legal = b.legal_actions("hero")
        for pref in ("check", "call"):
            if pref in legal:
                action = pref
                break
        else:
            action = "call" if "call" in legal else ("check" if "check" in legal else "fold")
        pub = b.to_public()
        b = E.act(b.deal_seed, b.hero_pos, pub["history"], action, None)
    return b


def test_new_hand_shape():
    b = E.new_hand(42, "BTN")
    pub = b.to_public()
    assert len(pub["hero"]) == 2
    assert pub["hero_pos"] == "BTN" and pub["villain_pos"] == "BB"
    assert pub["pot_bb"] == 1.5  # 盲注 0.5 + 1.0
    assert "result" in pub
    # 开池节点：加注或弃牌
    if not pub["complete"]:
        assert set(pub["available_actions"]) <= {"fold", "raise", "call", "check", "bet"}


def test_villain_hidden_until_showdown():
    b = E.new_hand(7, "BB")
    pub = b.to_public()
    assert "villain" not in pub  # 顶层不含对手底牌
    # 只在 result 里（摊牌/结束）才出现
    if pub["result"]:
        assert "villain" in pub["result"]


def test_play_to_completion_and_zero_sum():
    seen_showdown = False
    for i in range(30):
        b = E.new_hand(5000 + i, "BTN" if i % 2 else "BB")
        b = _passive(b)
        r = b.result
        assert r is not None
        assert isinstance(r["hero_net"], (int, float))
        assert abs(r["hero_net"]) <= r["pot_bb"] + 1e-6
        if r["reason"] == "showdown":
            seen_showdown = True
            assert len(r["board"]) == 5
            assert len(r["villain"]) == 2
    assert seen_showdown  # 被动打法应至少摊牌一次


def test_determinism_same_seed_same_result():
    a = _passive(E.new_hand(9999, "BTN"))
    b = _passive(E.new_hand(9999, "BTN"))
    assert a.result == b.result
    assert [e["action"] for e in a.history] == [e["action"] for e in b.history]


def test_grades_embedded_and_replayed():
    b = E.new_hand(123, "BTN")
    # 英雄第一步加注开池
    pub = b.to_public()
    b = E.act(b.deal_seed, b.hero_pos, pub["history"], "raise", None)
    pub = b.to_public()
    hero_events = [e for e in pub["history"] if e["actor"] == "hero"]
    assert hero_events and "hero_grade" in hero_events[0]
    # 重放能恢复出等量的英雄判分
    rb = E.replay(b.deal_seed, b.hero_pos, pub["history"])
    assert len(rb.grades) == len(hero_events)


def test_api_new_and_act():
    r = client.post("/api/battle/new", json={"hero_pos": "BTN", "seed": 42})
    assert r.status_code == 200
    st = r.json()["state"]
    assert st["hero_pos"] == "BTN"
    if not st["complete"] and "raise" in st["available_actions"]:
        r2 = client.post(
            "/api/battle/act",
            json={
                "deal_seed": st["deal_seed"],
                "hero_pos": st["hero_pos"],
                "history": st["history"],
                "action": "raise",
            },
        )
        assert r2.status_code == 200
        assert "state" in r2.json()


def test_api_act_rejects_out_of_turn_when_complete():
    # 用一个英雄立即弃牌结束的手，再尝试 act 应报错
    r = client.post("/api/battle/new", json={"hero_pos": "BTN", "seed": 1})
    st = r.json()["state"]
    if st["complete"]:
        r2 = client.post(
            "/api/battle/act",
            json={"deal_seed": st["deal_seed"], "hero_pos": "BTN", "history": st["history"], "action": "fold"},
        )
        assert r2.status_code == 400


def test_analyze_400_when_empty():
    r = client.post("/api/battle/analyze", json={"hands": []})
    assert r.status_code == 400


def test_analyze_ok_with_fake_provider(monkeypatch):
    from app.api import battle as battle_api

    class _Fake:
        gateway_ready = True
        openai_ready = False

        def text(self, prompt, **kwargs):
            assert "问题手" in prompt
            return "总体偏松：多手在翻前该弃却入池。建议收紧 BB 防守范围。"

    monkeypatch.setattr(battle_api, "get_provider", lambda: _Fake())
    r = client.post(
        "/api/battle/analyze",
        json={
            "hands": [
                {
                    "hero_glyphs": ["7♦", "2♣"],
                    "hero_pos": "BB",
                    "villain_pos": "BTN",
                    "board_glyphs": [],
                    "hero_net": -2.5,
                    "reason": "fold",
                    "winner": "villain",
                    "decisions": [
                        {
                            "street": "preflop",
                            "spot_label": "翻前防守",
                            "hand_class": "72o",
                            "action": "call",
                            "optimal_action": "fold",
                            "grade": "mistake",
                        }
                    ],
                }
            ]
        },
    )
    assert r.status_code == 200
    assert r.json()["report"]
