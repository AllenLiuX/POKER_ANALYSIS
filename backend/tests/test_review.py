"""AI 复盘：倾向分类 + 接地 prompt + 端点（mock LLM / 503 / 400）。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.review import ReviewRequest, build_review_prompt, classify_leak
from app.main import app

client = TestClient(app)


def test_classify_leak_directions():
    assert classify_leak("fold", "raise") == "too_tight"
    assert classify_leak("fold", "call") == "too_tight"
    assert classify_leak("raise", "fold") == "too_loose"
    assert classify_leak("call", "fold") == "too_loose"
    assert classify_leak("call", "raise") == "too_passive"
    assert classify_leak("check", "bet") == "too_passive"
    assert classify_leak("raise", "call") == "too_aggressive"
    assert classify_leak("bet", "check") == "too_aggressive"
    assert classify_leak("raise", "raise") is None


def test_prompt_grounds_stats_and_leaks():
    req = ReviewRequest(
        total=40,
        accuracy=0.75,
        current_streak=3,
        best_streak=9,
        by_grade={"optimal": 26, "acceptable": 4, "mistake": 10},
        by_spot=[{"key": "vs_RFI", "total": 25, "correct": 16}, {"key": "RFI", "total": 15, "correct": 14}],
        by_position=[{"key": "BB vs BTN", "total": 12, "correct": 6}],
        mistakes=[
            {"spot": "vs_RFI", "hero_position": "BB", "opener": "BTN", "hand_class": "KTs",
             "action": "fold", "optimal_action": "raise"},
            {"spot": "vs_RFI", "hero_position": "BB", "opener": "BTN", "hand_class": "A9o",
             "action": "fold", "optimal_action": "call"},
        ],
    )
    p = build_review_prompt(req)
    assert "75%" in p  # accuracy 接地
    assert "翻前防守" in p  # spot label
    assert "BB vs BTN" in p
    assert "过紧" in p  # 倾向聚合
    assert "KTs" in p  # 错题样例


class _FakeProvider:
    def __init__(self, gateway_ready=True, openai_ready=False):
        self.gateway_ready = gateway_ready
        self.openai_ready = openai_ready

    def text(self, prompt, **kwargs):
        assert "总体：累计" in prompt  # 事实进了 prompt
        return "1) 总体评估：你在 BB 防守偏紧。\n2) 主要漏洞：KTs 该 3-bet 却弃牌…"


def test_review_endpoint_ok(monkeypatch):
    from app.api import review as review_api

    monkeypatch.setattr(review_api, "get_provider", lambda: _FakeProvider())
    r = client.post(
        "/api/trainer/review",
        json={
            "total": 20,
            "accuracy": 0.7,
            "by_grade": {"optimal": 12, "acceptable": 2, "mistake": 6},
            "by_spot": [{"key": "vs_RFI", "total": 20, "correct": 14}],
            "mistakes": [
                {"spot": "vs_RFI", "hero_position": "BB", "opener": "BTN",
                 "hand_class": "KTs", "action": "fold", "optimal_action": "raise"}
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["report"]
    assert body["analyzed"] == 20
    assert body["mistakes_considered"] == 1


def test_review_endpoint_400_when_empty(monkeypatch):
    from app.api import review as review_api

    monkeypatch.setattr(review_api, "get_provider", lambda: _FakeProvider())
    r = client.post("/api/trainer/review", json={"total": 0, "accuracy": 0})
    assert r.status_code == 400


def test_review_endpoint_503_when_llm_unavailable(monkeypatch):
    from app.api import review as review_api

    class _No:
        gateway_ready = False
        openai_ready = False

    monkeypatch.setattr(review_api, "get_provider", lambda: _No())
    r = client.post("/api/trainer/review", json={"total": 5, "accuracy": 0.6})
    assert r.status_code == 503
