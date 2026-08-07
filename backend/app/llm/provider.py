"""LLMProvider：统一的文本 / 视觉调用抽象。

路由策略（见 docs/ARCHITECTURE.md §5.4）：
    文本  : model_client[gpt-5.6-sol] → (fallback) OpenAI
    视觉  : model_client[gemini-flash→gemini-pro→gpt-4o] → (fallback) OpenAI gpt-4o

铁律：LLM 只负责解析/解释，不负责计算数字或判定。
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import List, Optional, Sequence, Union

from app.config import get_settings

logger = logging.getLogger(__name__)

ImageInput = Union[bytes, bytearray, str]

# 视觉模型优先级（都支持 vision）
_VISION_ORDER = ["gemini-flash", "gemini-pro", "gpt-4o"]
_TEXT_MODEL = "gpt-5.6-sol"


class LLMProvider:
    def __init__(self) -> None:
        self.settings = get_settings()

    # ---------------- 能力探测 ----------------
    @property
    def gateway_ready(self) -> bool:
        return bool(self.settings.model_gateway_key)

    @property
    def openai_ready(self) -> bool:
        return bool(self.settings.openai_api_key)

    # ---------------- 文本 ----------------
    def text(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        log_id: Optional[str] = None,
    ) -> str:
        if self.gateway_ready:
            try:
                from app.llm import model_client

                return model_client.call_model(
                    prompt,
                    model=_TEXT_MODEL,
                    system=system,
                    max_tokens=max_tokens,
                    network=self.settings.model_client_network,
                    log_id=log_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[llm] gateway text failed, fallback to OpenAI: %s", exc)
        return self._openai_text(prompt, system=system, max_tokens=max_tokens)

    # ---------------- 视觉 ----------------
    def vision(
        self,
        prompt: str,
        images: Union[ImageInput, Sequence[ImageInput]],
        *,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        log_id: Optional[str] = None,
    ) -> str:
        if self.gateway_ready:
            try:
                from app.llm import model_client

                # model_client 会在 vision 池内部按顺序 fallback；这里指定首选 gemini-flash
                return model_client.call_model(
                    prompt,
                    images=images,
                    model=_VISION_ORDER[0],
                    system=system,
                    max_tokens=max_tokens,
                    network=self.settings.model_client_network,
                    log_id=log_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[llm] gateway vision failed, fallback to OpenAI: %s", exc)
        return self._openai_vision(prompt, images, system=system, max_tokens=max_tokens)

    # ---------------- OpenAI 兜底 ----------------
    def _openai_client(self):
        if not self.openai_ready:
            raise RuntimeError(
                "LLM 不可用：MODEL_GATEWAY_KEY 与 OPENAI_API_KEY 均未配置（见 .env.example）"
            )
        try:
            import openai
        except ImportError as exc:
            raise RuntimeError("openai 包未安装：pip install openai") from exc
        return openai.OpenAI(api_key=self.settings.openai_api_key)

    def _openai_text(self, prompt: str, *, system: Optional[str], max_tokens: Optional[int]) -> str:
        client = self._openai_client()
        model = os.getenv("OPENAI_TEXT_MODEL", "gpt-4o")
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        resp = client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens or 2000
        )
        return (resp.choices[0].message.content or "").strip()

    def _openai_vision(
        self,
        prompt: str,
        images: Union[ImageInput, Sequence[ImageInput]],
        *,
        system: Optional[str],
        max_tokens: Optional[int],
    ) -> str:
        from app.llm.model_client import _coerce_images, _normalize_image_input

        client = self._openai_client()
        model = os.getenv("OPENAI_VISION_MODEL", "gpt-4o")
        image_urls: List[str] = [_normalize_image_input(i) for i in _coerce_images(images)]
        content: List[dict] = [{"type": "text", "text": prompt}]
        for url in image_urls:
            content.append({"type": "image_url", "image_url": {"url": url}})
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": content}
        ]
        resp = client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens or 2000
        )
        return (resp.choices[0].message.content or "").strip()


@lru_cache
def get_provider() -> LLMProvider:
    return LLMProvider()
