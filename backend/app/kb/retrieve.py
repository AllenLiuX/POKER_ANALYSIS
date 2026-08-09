"""知识库检索：TTL 缓存刷新（ensure_fresh）+ 组装可注入的「参考资料」上下文。

策略：分析时先按概念读本地 KB；缺失/过期的概念才联网补齐（受 max_fetch 限流 + TTL 保护），
搜到的片段落库持久化，下次直接命中缓存，控成本、稳延迟。全程优雅降级。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from app.config import get_settings
from app.kb import search, store
from app.kb.concepts import CONCEPTS, label

logger = logging.getLogger(__name__)


def ensure_fresh(concepts: List[str], *, max_fetch: int = 4) -> int:
    """确保这些概念在 KB 里是新鲜的；缺失/过期的联网补齐并落库。返回本次联网补齐的概念数。

    未配置 Tavily / 未启用 KB → 直接返回 0（不影响分析）。
    """
    s = get_settings()
    if not s.kb_ready:
        return 0
    fetched = 0
    for key in concepts:
        if key not in CONCEPTS:
            continue
        if store.is_fresh(key, s.kb_ttl_days):
            continue
        if fetched >= max_fetch:
            break
        query = CONCEPTS[key]["query"]
        try:
            docs = search.search(query, max_results=s.tavily_max_results, depth=s.tavily_search_depth)
        except Exception as exc:  # noqa: BLE001 — 搜索失败不致命
            logger.warning("kb search failed for %s: %s", key, exc)
            docs = []
        try:
            store.upsert_concept(key, query, docs)  # 空结果也记 meta，TTL 内不再重试
        except Exception as exc:  # noqa: BLE001
            logger.warning("kb store failed for %s: %s", key, exc)
        fetched += 1
    return fetched


def knowledge_context(
    concepts: List[str], *, max_chunks: int = 6, per_concept: int = 2, snippet_chars: int = 360
) -> Tuple[str, List[Dict[str, str]]]:
    """按概念取片段，组装成 prompt 可注入的参考资料块 + 去重后的来源列表。

    Returns: (context_text, sources)；无片段时返回 ("", [])。
    """
    docs = store.get_docs(concepts, per_concept=per_concept)
    if not docs:
        return "", []
    # 概念内已按 score 排序；跨概念交错，避免单一概念占满
    docs = docs[:max_chunks]

    seen_urls = set()
    lines: List[str] = []
    sources: List[Dict[str, str]] = []
    idx = 1
    for d in docs:
        url = str(d.get("url") or "")
        if url in seen_urls:
            continue
        seen_urls.add(url)
        clabel = label(str(d.get("concept") or ""))
        title = str(d.get("title") or "").strip()
        content = str(d.get("content") or "").strip().replace("\n", " ")
        if len(content) > snippet_chars:
            content = content[:snippet_chars] + "…"
        lines.append(f"[{idx}] （{clabel}）{title}：{content}")
        sources.append({"n": str(idx), "title": title[:160], "url": url, "concept": clabel})
        idx += 1

    context = "\n".join(lines)
    return context, sources


def knowledge_for(
    concepts: List[str], *, max_fetch: int = 4, max_chunks: int = 6
) -> Tuple[str, List[Dict[str, str]]]:
    """一步到位：先确保新鲜，再组装上下文 + 来源。任何异常 → ("", [])。"""
    try:
        ensure_fresh(concepts, max_fetch=max_fetch)
        return knowledge_context(concepts, max_chunks=max_chunks)
    except Exception as exc:  # noqa: BLE001 — 知识库永不阻塞主分析
        logger.warning("knowledge_for failed: %s", exc)
        return "", []
