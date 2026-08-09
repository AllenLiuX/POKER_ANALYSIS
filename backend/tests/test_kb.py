"""知识库（Tavily 接地 RAG）单测：store / concepts / retrieve / 报告集成。

所有联网都被 monkeypatch 掉（不真的打 Tavily），确保确定性、离线可跑。
"""
import time

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.kb import retrieve, store
from app.kb.concepts import concepts_for_opponent
from app.main import app

client = TestClient(app)


@pytest.fixture()
def kb_env(tmp_path, monkeypatch):
    """把 KB 指到临时 sqlite，并开启 KB + 假装配了 tavily key。"""
    s = get_settings()
    monkeypatch.setattr(s, "kb_path", str(tmp_path / "kb.sqlite3"))
    monkeypatch.setattr(s, "kb_enabled", True)
    monkeypatch.setattr(s, "kb_ttl_days", 30)
    monkeypatch.setattr(s, "tavily_api_key", "tvly-test")
    store.reset_for_test()
    yield s
    store.reset_for_test()


def _fake_docs(n=3):
    return [
        {"title": f"Doc {i}", "url": f"https://ex.com/{i}", "content": f"strategy content {i}", "score": 1.0 - i * 0.1}
        for i in range(n)
    ]


def test_store_upsert_and_get(kb_env):
    store.upsert_concept("cbet_strategy", "q", _fake_docs(3))
    docs = store.get_docs(["cbet_strategy"], per_concept=2)
    assert len(docs) == 2  # 每概念取前 2
    assert docs[0]["score"] >= docs[1]["score"]  # 按 score 降序
    assert store.is_fresh("cbet_strategy", 30) is True
    # 覆盖更新同 url，不产生重复
    store.upsert_concept("cbet_strategy", "q", _fake_docs(3))
    assert store.stats()["docs"] == 3


def test_store_ttl_expiry(kb_env):
    store.upsert_concept("mdf_pot_odds", "q", _fake_docs(1))
    assert store.is_fresh("mdf_pot_odds", 30) is True
    # TTL=0 天 → 立刻过期
    assert store.is_fresh("mdf_pot_odds", 0) is False


def test_concepts_calling_station():
    counters = {
        "pf_vs_open": {"n": 20, "fold": 3, "call": 15, "raise": 2},
        "af_post": {"aggr": 3, "passive": 18},
        "cbet_flop": {"n": 6, "k": 2},
        "wtsd": {"n": 14, "k": 11},
    }
    leaks = {"too_loose": 5}
    picks = concepts_for_opponent(counters, leaks)
    assert "calling_station_exploit" in picks
    assert len(picks) <= 4  # 限量


def test_concepts_tight_and_foldy():
    counters = {
        "pf_vs_open": {"n": 12, "fold": 10, "call": 1, "raise": 1},
        "fold_vs_cbet_flop": {"n": 8, "k": 6},
    }
    picks = concepts_for_opponent(counters, {"too_tight": 2})
    assert "tight_player_exploit" in picks
    assert "fold_to_cbet_exploit" in picks


def test_ensure_fresh_fetches_then_caches(kb_env, monkeypatch):
    calls = {"n": 0}

    def fake_search(query, **kw):
        calls["n"] += 1
        return _fake_docs(2)

    monkeypatch.setattr(retrieve.search, "search", fake_search)
    n1 = retrieve.ensure_fresh(["cbet_strategy", "mdf_pot_odds"], max_fetch=4)
    assert n1 == 2 and calls["n"] == 2
    # 第二次都新鲜 → 不再联网
    n2 = retrieve.ensure_fresh(["cbet_strategy", "mdf_pot_odds"], max_fetch=4)
    assert n2 == 0 and calls["n"] == 2


def test_knowledge_for_builds_context_and_sources(kb_env, monkeypatch):
    monkeypatch.setattr(retrieve.search, "search", lambda q, **kw: _fake_docs(2))
    ctx, sources = retrieve.knowledge_for(["cbet_strategy"], max_chunks=4)
    assert ctx and "Doc 0" in ctx
    assert sources and sources[0]["url"].startswith("https://")
    assert "concept" in sources[0]


def test_ensure_fresh_noop_when_not_ready(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "kb_path", str(tmp_path / "kb2.sqlite3"))
    monkeypatch.setattr(s, "tavily_api_key", "")  # 未配置 → 不就绪
    store.reset_for_test()
    assert retrieve.ensure_fresh(["cbet_strategy"]) == 0
    ctx, sources = retrieve.knowledge_for(["cbet_strategy"])
    assert ctx == "" and sources == []
    store.reset_for_test()


def test_kb_status_endpoint():
    r = client.get("/api/kb/status")
    assert r.status_code == 200
    body = r.json()
    assert "concepts_total" in body and body["concepts_total"] >= 10
    assert "store" in body


def test_opponent_report_grounds_with_kb(kb_env, monkeypatch):
    # mock 联网 + LLM，验证报告返回 sources/concepts 且注入了参考资料
    monkeypatch.setattr(retrieve.search, "search", lambda q, **kw: _fake_docs(2))
    from app.api import exploit as exploit_mod

    captured = {}

    class FakeProvider:
        gateway_ready = True
        openai_ready = True

        def text(self, prompt, system=None, max_tokens=None, model=None):
            captured["prompt"] = prompt
            return "- 针对其跟注站做纯价值下注。"

    monkeypatch.setattr(exploit_mod, "get_provider", lambda: FakeProvider())
    payload = {
        "alias": "Fish",
        "hands": 30,
        "net": -400,
        "counters": {
            "pf_vs_open": {"n": 18, "fold": 3, "call": 13, "raise": 2},
            "af_post": {"aggr": 4, "passive": 19},
            "cbet_flop": {"n": 6, "k": 2},
            "wtsd": {"n": 14, "k": 11},
            "leaks_pre": {"too_loose": 5},
        },
    }
    r = client.post("/api/ingest/opponent_report", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sources"], "应返回参考资料来源"
    assert body["concepts"], "应返回命中的概念标签"
    assert "策略参考资料" in captured["prompt"]  # 参考资料确实注入了 prompt
