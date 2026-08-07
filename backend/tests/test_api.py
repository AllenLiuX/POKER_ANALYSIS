"""API 冒烟测试。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "capabilities" in body


def test_equity_endpoint():
    r = client.post(
        "/api/equity",
        json={"hero": ["As", "Ad"], "villain_range": "KK-22", "trials": 2000, "seed": 7},
    )
    assert r.status_code == 200
    body = r.json()
    assert 0.0 <= body["equity"] <= 1.0
    assert body["samples"] > 0


def test_equity_bad_input():
    r = client.post(
        "/api/equity",
        json={"hero": ["As"], "villain_range": "KK", "trials": 500},
    )
    assert r.status_code == 400


def test_potodds_endpoint():
    r = client.post("/api/potodds", json={"pot": 100, "call": 50, "equity": 0.4})
    assert r.status_code == 200
    body = r.json()
    assert "required_equity" in body
    assert body["profitable"] is True
