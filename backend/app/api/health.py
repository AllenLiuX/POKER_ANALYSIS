"""健康检查与能力探测。"""
from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings

router = APIRouter()


@router.get("/health")
def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": "0.1.0",
        "capabilities": {
            "equity": True,
            "llm_gateway_configured": bool(settings.model_gateway_key),
            "openai_fallback_configured": bool(settings.openai_api_key),
        },
    }
