"""截图导入 API（Phase 6 · S1 提取 + S2 重建）。

POST /api/ingest/extract：批量上传 WePoker 截图 → 每张返回观测事实 + 下注序列重建（阶段①②）。
无法识别的图不报错，而是返回 recognized=false + 友好提示。当前仅同步处理、不落库。
"""
from __future__ import annotations

import asyncio
import functools
import logging
import os
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.ingest.deviation import analyze_deviations
from app.ingest.extract import LLMUnavailable, extract_observations
from app.llm.provider import get_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest", tags=["ingest"])

_MAX_BYTES = 8 * 1024 * 1024  # 单张上限，与 model_client 一致
_MAX_FILES = 12
_ALLOWED_MIME_PREFIX = "image/"
# 并发上限：多张图并行解析（每张走线程池，避免阻塞事件循环 / 拖垮整机）。
_MAX_CONCURRENCY = max(1, int(os.getenv("INGEST_MAX_CONCURRENCY", "4")))
# 单张墙钟超时：兜底防止某张图的视觉调用卡住整批（底层 client 亦有 60s 请求超时）。
_PER_IMAGE_TIMEOUT = float(os.getenv("INGEST_IMAGE_TIMEOUT", "120"))


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

    # 先在事件循环里把文件读出来并做轻校验（UploadFile 不能跨线程用），再并发解析。
    results: List[Optional[dict]] = [None] * len(files)
    jobs: List[tuple] = []  # (index, name, data, mime)
    for i, f in enumerate(files):
        name = f.filename or "screenshot"
        if f.content_type and not f.content_type.startswith(_ALLOWED_MIME_PREFIX):
            results[i] = {"filename": name, "ok": False, "error": f"仅支持图片，收到 {f.content_type}"}
            continue
        data = await f.read()
        if not data:
            results[i] = {"filename": name, "ok": False, "error": "空文件"}
            continue
        if len(data) > _MAX_BYTES:
            results[i] = {"filename": name, "ok": False, "error": "图片过大（上限 8MB）"}
            continue
        jobs.append((i, name, data, f.content_type))

    sem = asyncio.Semaphore(_MAX_CONCURRENCY)

    async def worker(index: int, name: str, data: bytes, mime: Optional[str]) -> None:
        async with sem:
            try:
                # 阻塞的视觉/文本调用放到线程池，避免阻塞事件循环；并加墙钟超时兜底。
                res = await asyncio.wait_for(
                    run_in_threadpool(
                        functools.partial(
                            extract_observations, data, mime=mime, log_id=f"ingest-{name}"
                        )
                    ),
                    timeout=_PER_IMAGE_TIMEOUT,
                )
                results[index] = {"filename": name, "ok": True, **res}
            except asyncio.TimeoutError:
                logger.warning("[ingest] extract timeout for %s (>%.0fs)", name, _PER_IMAGE_TIMEOUT)
                results[index] = {
                    "filename": name,
                    "ok": False,
                    "error": f"解析超时（>{int(_PER_IMAGE_TIMEOUT)}s），请重试或换更清晰的截图",
                }
            except LLMUnavailable as exc:
                results[index] = {"filename": name, "ok": False, "error": str(exc)}
            except Exception as exc:  # noqa: BLE001 — 单图失败不影响其它图
                logger.warning("[ingest] extract failed for %s: %s", name, exc)
                results[index] = {"filename": name, "ok": False, "error": f"解析失败：{exc}"}

    if jobs:
        await asyncio.gather(*(worker(i, n, d, m) for (i, n, d, m) in jobs))

    return {"count": len(results), "results": [r for r in results if r is not None]}


class AnalyzeRequest(BaseModel):
    """阶段③：对已有的观测事实 + 重建结果标注 GTO 偏离（确定性，无 LLM）。

    供前端为历史记录（早于 S3 的条目）按需回填偏离标注。
    """

    facts: dict = Field(default_factory=dict, description="阶段①观测事实")
    reconstruction: Optional[dict] = Field(None, description="阶段②重建结果")


@router.post("/analyze")
def ingest_analyze(req: AnalyzeRequest) -> dict:
    if not req.reconstruction:
        raise HTTPException(status_code=400, detail="缺少重建结果，无法标注偏离")
    return {"analysis": analyze_deviations(req.facts, req.reconstruction)}
