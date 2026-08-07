"""翻前范围 API 测试。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_ranges_index():
    r = client.get("/api/ranges")
    assert r.status_code == 200
    spots = r.json()["spots"]
    positions = {s["position"] for s in spots}
    assert {"UTG", "MP", "CO", "BTN", "SB"}.issubset(positions)


def test_range_grid():
    r = client.get("/api/ranges/6max_100bb/RFI/CO")
    assert r.status_code == 200
    body = r.json()
    assert len(body["cells"]) == 169
    assert body["ranks"][0] == "A"
    aa = next(c for c in body["cells"] if c["hand_class"] == "AA")
    assert aa["freqs"]["raise"] == 1.0


def test_range_grid_404():
    r = client.get("/api/ranges/6max_100bb/RFI/BB")
    assert r.status_code == 404


def test_preflop_score():
    # AA at UTG -> optimal raise
    r = client.post(
        "/api/preflop/score",
        json={"position": "UTG", "hero": ["As", "Ad"], "action": "raise"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["hand_class"] == "AA"
    assert body["correct"] is True
    assert body["grade"] == "optimal"

    # 72o at UTG raise -> mistake
    r = client.post(
        "/api/preflop/score",
        json={"position": "UTG", "hero": ["7c", "2d"], "action": "raise"},
    )
    assert r.json()["grade"] == "mistake"
