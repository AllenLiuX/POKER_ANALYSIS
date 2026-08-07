"""截图导入（Phase 6 · S1 提取 + S2 重建）：解析/归一化/鲁棒降级 + 重建校验 + 批量 API。

LLM 视觉调用被 mock，确保测试离线、确定；真相是解析/归一化/重建/校验逻辑正确。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.ingest import extract as extract_mod
from app.ingest.extract import (
    LLMUnavailable,
    _normalize_card,
    extract_observations,
    parse_json_block,
)
from app.ingest.reconstruct import parse_actions, reconstruct_hand
from app.main import app

client = TestClient(app)


# ---------- JSON 解析 ----------
def test_parse_json_plain():
    assert parse_json_block('{"a": 1}') == {"a": 1}


def test_parse_json_fenced():
    text = "这是模型输出：\n```json\n{\"screenshot_type\": \"hand_replay\"}\n```\n谢谢"
    assert parse_json_block(text) == {"screenshot_type": "hand_replay"}


def test_parse_json_embedded_braces():
    text = 'noise before {"pot": 210} noise after'
    assert parse_json_block(text) == {"pot": 210}


def test_parse_json_empty_raises():
    with pytest.raises(ValueError):
        parse_json_block("   ")


# ---------- 牌面归一化 ----------
def test_normalize_card_glyph_and_ten():
    assert _normalize_card("10♠") == "Ts"
    assert _normalize_card("A♥") == "Ah"
    assert _normalize_card("k d") == "Kd"


def test_normalize_card_invalid():
    assert _normalize_card("XX") is None
    assert _normalize_card(123) is None


# ---------- 动作解析 + 重建校验 ----------
def test_parse_actions_tokens():
    acts = parse_actions("加注32 → 下注38 → 跟注188 → Allin941")
    assert [a["action"] for a in acts] == ["raise", "bet", "call", "allin"]
    assert [a["amount"] for a in acts] == [32, 38, 188, 941]


def test_reconstruct_net_conservation_validated():
    facts = {
        "screenshot_type": "hand_replay",
        "pot": 2445,
        "board": ["3s", "5s", "Ts", "3d", "Ah"],
        "players": [
            {"alias": "先清清兵", "net": 1245, "actions_raw": "加注32 → 下注38 → 跟注188 → Allin941"},
            {"alias": "DV999", "net": -1200, "actions_raw": "跟注32 → 跟注38 → 下注188 → 跟注941"},
            {"alias": "BrightDa", "net": -33, "actions_raw": "跟注32 → 弃牌"},
            {"alias": "JayTsui6", "net": -5, "actions_raw": "弃牌"},
            {"alias": "专治抽牌", "net": -5, "actions_raw": "弃牌"},
            {"alias": "迟到的游", "net": -1, "actions_raw": "弃牌"},
            {"alias": "不会打蓝", "net": -1, "actions_raw": "弃牌"},
        ],
    }
    r = reconstruct_hand(facts)
    assert r["checks"]["net_ok"] is True  # 1245-1200-33-5-5-1-1 = 0
    assert r["checks"]["rows_consistent"] is True
    assert r["status"] == "validated"
    hero = next(p for p in r["players"] if p["alias"] == "先清清兵")
    assert hero["is_winner"] is True
    assert hero["parsed_invested"] == 1199  # 32+38+188+941
    assert hero["invested"] == 1200  # 赢家投入以底池−净额推算：2445-1245
    assert hero["uncertain"] is False
    # 逐街动作带 street 标注
    assert [a["street"] for a in hero["actions"]] == ["翻前", "翻牌", "转牌", "河牌"]
    dv = next(p for p in r["players"] if p["alias"] == "DV999")
    assert dv["invested"] == 1200  # |net|


def test_reconstruct_flags_uncertain_when_actions_drop():
    # DV999 一行动作被漏读成只有"下注32"，但净额显示输了 1245 → 应标记待复核
    facts = {
        "screenshot_type": "hand_replay",
        "pot": 2490,
        "players": [
            {"alias": "W", "net": 1245, "actions_raw": "加注32 → 下注38 → 跟注188 → Allin941"},
            {"alias": "L", "net": -1245, "actions_raw": "下注32"},
        ],
    }
    r = reconstruct_hand(facts)
    assert r["checks"]["net_ok"] is True  # 1245-1245=0
    lo = next(p for p in r["players"] if p["alias"] == "L")
    assert lo["uncertain"] is True
    assert lo["invested"] == 1245  # 以净额推算，纠正了漏读
    assert lo["parsed_invested"] == 32
    assert r["status"] == "needs_review"


def test_reconstruct_needs_user_without_actions():
    facts = {"screenshot_type": "result_summary", "pot": 100, "players": [{"alias": "x", "net": 0}]}
    r = reconstruct_hand(facts)
    assert r["status"] == "needs_user"
    assert r["confidence"] < 0.4


# ---------- 提取（mock LLM）----------
class _FakeProvider:
    def __init__(self, gateway_ready=True, openai_ready=False, output=""):
        self.gateway_ready = gateway_ready
        self.openai_ready = openai_ready
        self._output = output

    def vision(self, prompt, images, **kwargs):
        return self._output


_SAMPLE_OUTPUT = """```json
{
  "screenshot_type": "hand_replay",
  "hand_id": "H123",
  "blinds": "2/4(1)",
  "board": ["3♠", "5♠", "10♠", "3d", "Ah"],
  "pot": 2445,
  "players": [
    {"alias": "先清清兵", "position": "co", "hole_cards": ["10c", "10h"],
     "net": 1245, "made_hand": "葫芦", "is_hero": true,
     "actions_raw": "加注32 → 下注38 → 跟注188 → Allin941",
     "visible_actions": ["加注", "下注", "跟注", "全下"]},
    {"alias": "DV999", "position": "SB", "hole_cards": ["Ah", "4d"],
     "net": -1200, "made_hand": "两对",
     "actions_raw": "跟注32 → 跟注38 → 下注188 → 跟注941"}
  ],
  "extraction_confidence": 0.9,
  "notes": "回放截图，逐街动作可见"
}
```"""


def test_extract_observations_parses_normalizes_and_reconstructs(monkeypatch):
    monkeypatch.setattr(
        extract_mod, "get_provider", lambda: _FakeProvider(output=_SAMPLE_OUTPUT)
    )
    result = extract_observations(b"\x89PNG\r\n\x1a\n fake", mime="image/png")
    assert result["recognized"] is True
    facts = result["facts"]
    assert facts["board"] == ["3s", "5s", "Ts", "3d", "Ah"]
    assert facts["players"][0]["hole_cards"] == ["Tc", "Th"]
    rec = result["reconstruction"]
    assert rec is not None
    assert rec["checks"]["net_ok"] is True  # 1245 + (-1200) = 45 ≤ 2%*2445
    assert rec["players"][0]["parsed_invested"] == 1199
    assert rec["players"][0]["invested"] == 1200  # 赢家：底池−净额


def test_extract_unrecognized_is_graceful(monkeypatch):
    # 两次视觉调用都返回非 JSON（如模型拒答/空）→ 不抛错，recognized=False
    monkeypatch.setattr(
        extract_mod, "get_provider", lambda: _FakeProvider(output="这看起来不是扑克截图。")
    )
    result = extract_observations(b"fake")
    assert result["recognized"] is False
    assert result["reconstruction"] is None
    assert "手牌回放" in result["facts"]["notes"] or result["facts"]["notes"]


def test_extract_raises_when_llm_unavailable(monkeypatch):
    monkeypatch.setattr(
        extract_mod,
        "get_provider",
        lambda: _FakeProvider(gateway_ready=False, openai_ready=False),
    )
    with pytest.raises(LLMUnavailable):
        extract_observations(b"fake")


# ---------- 批量 API ----------
def test_api_extract_batch_ok(monkeypatch):
    monkeypatch.setattr(
        extract_mod, "get_provider", lambda: _FakeProvider(output=_SAMPLE_OUTPUT)
    )
    resp = client.post(
        "/api/ingest/extract",
        files=[
            ("files", ("a.png", b"\x89PNG\r\n\x1a\n a", "image/png")),
            ("files", ("b.png", b"\x89PNG\r\n\x1a\n b", "image/png")),
        ],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert body["results"][0]["ok"] is True
    assert body["results"][0]["recognized"] is True
    assert body["results"][0]["reconstruction"]["status"] == "needs_review" or body[
        "results"
    ][0]["reconstruction"]["status"] == "validated"


def test_api_extract_mixed_non_image(monkeypatch):
    monkeypatch.setattr(
        extract_mod, "get_provider", lambda: _FakeProvider(output=_SAMPLE_OUTPUT)
    )
    resp = client.post(
        "/api/ingest/extract",
        files=[
            ("files", ("notes.txt", b"hello", "text/plain")),
            ("files", ("a.png", b"\x89PNG\r\n\x1a\n a", "image/png")),
        ],
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert results[0]["ok"] is False  # 非图片
    assert results[1]["ok"] is True


def test_api_extract_503_when_unavailable(monkeypatch):
    from app.api import ingest as ingest_api

    class _No:
        gateway_ready = False
        openai_ready = False

    monkeypatch.setattr(ingest_api, "get_provider", lambda: _No())
    resp = client.post(
        "/api/ingest/extract",
        files=[("files", ("a.png", b"\x89PNG\r\n\x1a\n a", "image/png"))],
    )
    assert resp.status_code == 503
