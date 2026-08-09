"""Tavily 联网搜索 provider（同步版；与 ~/ai_study_platform 同一 provider 语义）。

对外只暴露 `search`：返回 [{title,url,content,score}]。任何异常/无 key → 返回 []，不抛错。
Provider 解耦：将来切 Brave/SerpAPI/OpenAI web_search 只需替换本文件实现。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

TAVILY_ENDPOINT = "https://api.tavily.com/search"
_HTTP_TIMEOUT = 12.0


def is_configured() -> bool:
    return bool(get_settings().tavily_api_key)


def search(
    query: str,
    *,
    max_results: Optional[int] = None,
    depth: Optional[str] = None,
    timeout: float = _HTTP_TIMEOUT,
) -> List[Dict[str, object]]:
    """联网搜索一条 query，返回结果片段列表；失败/无 key/空 query → []。"""
    s = get_settings()
    if not s.tavily_api_key:
        return []
    cleaned = (query or "").strip()
    if not cleaned:
        return []
    if len(cleaned) > 400:
        cleaned = cleaned[:400]

    payload = {
        "api_key": s.tavily_api_key,
        "query": cleaned,
        "search_depth": depth or s.tavily_search_depth or "basic",
        "max_results": max_results or s.tavily_max_results or 5,
        "include_answer": False,  # 要原始片段，把推理留给主 LLM
        "include_raw_content": False,
        "include_images": False,
    }

    try:
        with httpx.Client(timeout=timeout, trust_env=True) as client:
            resp = client.post(TAVILY_ENDPOINT, json=payload)
    except httpx.HTTPError as exc:  # 超时/网络错误
        logger.warning("tavily search error: %s", exc)
        return []

    if resp.status_code != 200:
        logger.warning("tavily search http %s: %s", resp.status_code, resp.text[:200])
        return []

    data = resp.json() or {}
    out: List[Dict[str, object]] = []
    for r in data.get("results") or []:
        url = (r.get("url") or "").strip()
        content = (r.get("content") or "").strip()
        title = (r.get("title") or url or "").strip()
        if not url or not content:
            continue
        try:
            score = float(r.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        out.append(
            {
                "title": title[:200],
                "url": url,
                "content": content,
                "score": max(0.0, min(score, 1.0)),
            }
        )
    return out
