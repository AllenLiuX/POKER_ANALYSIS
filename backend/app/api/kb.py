"""知识库 API：状态查询 / 预热刷新 / 调试搜索。"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.kb import search as kb_search
from app.kb import store
from app.kb.concepts import CONCEPTS, label
from app.kb.retrieve import ensure_fresh

router = APIRouter(prefix="/kb", tags=["kb"])


@router.get("/status")
def kb_status() -> dict:
    s = get_settings()
    st = store.stats()
    return {
        "enabled": s.kb_enabled,
        "search_configured": kb_search.is_configured(),
        "ready": s.kb_ready,
        "ttl_days": s.kb_ttl_days,
        "concepts_total": len(CONCEPTS),
        "concepts": [{"key": k, "label": v["label"]} for k, v in CONCEPTS.items()],
        "store": st,
    }


class RefreshRequest(BaseModel):
    concepts: Optional[List[str]] = Field(None, description="要刷新的概念 key；缺省=全部")
    max_fetch: int = Field(16, ge=1, le=64)


@router.post("/refresh")
def kb_refresh(req: RefreshRequest) -> dict:
    s = get_settings()
    if not s.kb_ready:
        raise HTTPException(status_code=503, detail="知识库未就绪：需配置 TAVILY_API_KEY 且 KB_ENABLED=true。")
    keys = req.concepts or list(CONCEPTS.keys())
    keys = [k for k in keys if k in CONCEPTS]
    if not keys:
        raise HTTPException(status_code=400, detail="没有可刷新的有效概念 key。")
    fetched = ensure_fresh(keys, max_fetch=req.max_fetch)
    return {"requested": len(keys), "fetched": fetched, "store": store.stats()}


class KbSearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    max_results: Optional[int] = Field(None, ge=1, le=10)


@router.post("/search")
def kb_search_debug(req: KbSearchRequest) -> dict:
    """直接透传一次联网搜索（调试用；不落库）。"""
    if not kb_search.is_configured():
        raise HTTPException(status_code=503, detail="TAVILY_API_KEY 未配置。")
    results = kb_search.search(req.query, max_results=req.max_results)
    return {"query": req.query, "count": len(results), "results": results}
