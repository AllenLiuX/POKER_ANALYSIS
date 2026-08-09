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
from app.ingest.reconstruct import parse_actions, parse_actions_by_street, reconstruct_hand
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


def test_parse_actions_by_street_assigns_correct_streets():
    acts = parse_actions_by_street(
        {"preflop": ["加注32"], "flop": ["过牌", "加注38"], "river": ["下注188"]}
    )
    assert [(a["action"], a["street"]) for a in acts] == [
        ("raise", "翻前"),
        ("check", "翻牌"),
        ("raise", "翻牌"),  # 同一街多个动作都保留在该街
        ("bet", "河牌"),
    ]


def test_reconstruct_prefers_actions_by_street_over_positional():
    # 翻牌先过牌再加注：位置法会把"过牌"错判成翻前；分街分组则正确
    facts = {
        "screenshot_type": "hand_replay",
        "pot": 100,
        "players": [
            {
                "alias": "A",
                "net": 50,
                "actions_raw": "过牌 → 加注38",
                "actions_by_street": {"flop": ["过牌", "加注38"]},
            },
            {"alias": "B", "net": -50, "actions_raw": "下注38 → 弃牌", "actions_by_street": {"flop": ["下注38", "弃牌"]}},
        ],
    }
    r = reconstruct_hand(facts)
    a = next(p for p in r["players"] if p["alias"] == "A")
    assert [x["street"] for x in a["actions"]] == ["翻牌", "翻牌"]  # 都在翻牌，不再错标翻前


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


def test_extract_vision_cache_hits_same_image(monkeypatch):
    calls = {"n": 0}

    class Counting(_FakeProvider):
        def vision(self, prompt, images, **kwargs):
            calls["n"] += 1
            return _SAMPLE_OUTPUT

    monkeypatch.setattr(extract_mod, "get_provider", lambda: Counting())
    img = b"UNIQUE-CACHE-IMG-\x01\x02"  # 唯一字节，避免与其它用例的缓存串扰
    r1 = extract_observations(img, mime="image/png")
    r2 = extract_observations(img, mime="image/png")
    assert r1["recognized"] and r2["recognized"]
    assert calls["n"] == 1  # 第二次命中视觉缓存，不再调模型
    assert r2["reconstruction"] is not None  # 重建/偏离仍按最新引擎重算


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


class _ReadyProvider:
    gateway_ready = True
    openai_ready = False


def test_api_extract_runs_concurrently(monkeypatch):
    """6 张各 sleep 0.4s：顺序需 ~2.4s，并发（≥4）应 <1.6s；顺序也须保持。"""
    import time

    from app.api import ingest as ingest_api

    monkeypatch.setattr(ingest_api, "get_provider", lambda: _ReadyProvider())

    def slow_extract(data, *, mime=None, log_id=None):
        time.sleep(0.4)
        return {
            "recognized": True,
            "facts": {"tag": data[-1]},
            "reconstruction": None,
            "analysis": None,
        }

    monkeypatch.setattr(ingest_api, "extract_observations", slow_extract)

    files = [
        ("files", (f"{i}.png", b"\x89PNG\r\n\x1a\n" + bytes([i]), "image/png"))
        for i in range(6)
    ]
    t0 = time.time()
    resp = client.post("/api/ingest/extract", files=files)
    elapsed = time.time() - t0

    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 6
    assert all(r["ok"] for r in body["results"])
    # 结果按上传顺序返回
    assert [r["filename"] for r in body["results"]] == [f"{i}.png" for i in range(6)]
    assert elapsed < 1.6, f"看起来仍是顺序执行：{elapsed:.2f}s"


def test_api_extract_isolates_single_failure(monkeypatch):
    """单张解析抛错不影响其它张，也不会让整批 500。"""
    from app.api import ingest as ingest_api

    monkeypatch.setattr(ingest_api, "get_provider", lambda: _ReadyProvider())

    def flaky(data, *, mime=None, log_id=None):
        if data.endswith(b"boom"):
            raise RuntimeError("boom")
        return {"recognized": True, "facts": {}, "reconstruction": None, "analysis": None}

    monkeypatch.setattr(ingest_api, "extract_observations", flaky)

    resp = client.post(
        "/api/ingest/extract",
        files=[
            ("files", ("ok.png", b"\x89PNG\r\n\x1a\n ok", "image/png")),
            ("files", ("bad.png", b"\x89PNG\r\n\x1a\n boom", "image/png")),
        ],
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert results[0]["ok"] is True
    assert results[1]["ok"] is False
    assert "boom" in results[1]["error"]
