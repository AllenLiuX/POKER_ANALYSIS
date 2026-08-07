"""截图导入 API（Phase 6 · S1 提取 + S2 重建）。

POST /api/ingest/extract：批量上传 WePoker 截图 → 每张返回观测事实 + 下注序列重建（阶段①②）。
无法识别的图不报错，而是返回 recognized=false + 友好提示。当前仅同步处理、不落库。
"""
from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.ingest.extract import LLMUnavailable, extract_observations
from app.llm.provider import get_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest", tags=["ingest"])

_MAX_BYTES = 8 * 1024 * 1024  # 单张上限，与 model_client 一致
_MAX_FILES = 12
_ALLOWED_MIME_PREFIX = "image/"


@router.post("/extract")
async def ingest_extract(files: List[UploadFile] = File(...)) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail="未收到文件")
    if len(files) > _MAX_FILES:
        raise HTTPException(status_code=413, detail=f"一次最多 {_MAX_FILES} 张")

    # 能力探测：整批一次性判断，未配置 LLM 直接 503（而非逐图报错）。
    provider = get_provider()
    if not (provider.gateway_ready or provider.openai_ready):
        raise HTTPException(
            status_code=503,
            detail="LLM 未配置：请在 backend/.env 设置 MODEL_GATEWAY_KEY（网关）或 OPENAI_API_KEY（兜底）。",
        )

    results: List[dict] = []
    for f in files:
        name = f.filename or "screenshot"
        if f.content_type and not f.content_type.startswith(_ALLOWED_MIME_PREFIX):
            results.append({"filename": name, "ok": False, "error": f"仅支持图片，收到 {f.content_type}"})
            continue
        data = await f.read()
        if not data:
            results.append({"filename": name, "ok": False, "error": "空文件"})
            continue
        if len(data) > _MAX_BYTES:
            results.append({"filename": name, "ok": False, "error": "图片过大（上限 8MB）"})
            continue
        try:
            res = extract_observations(data, mime=f.content_type, log_id=f"ingest-{name}")
            results.append({"filename": name, "ok": True, **res})
        except LLMUnavailable as exc:
            # 配置问题：整批中止
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — 单图失败不影响其它图
            logger.warning("[ingest] extract failed for %s: %s", name, exc)
            results.append({"filename": name, "ok": False, "error": f"解析失败：{exc}"})

    return {"count": len(results), "results": results}
