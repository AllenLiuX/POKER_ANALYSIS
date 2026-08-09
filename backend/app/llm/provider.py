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
from typing import Iterator, List, Optional, Sequence, Union

from app.config import get_settings

logger = logging.getLogger(__name__)

ImageInput = Union[bytes, bytearray, str]

# 视觉模型优先级（都支持 vision）
_VISION_ORDER = ["gemini-flash", "gemini-pro", "gpt-4o"]
_TEXT_MODEL = "gpt-5.6-sol"
# 首选文本模型偶发返回空内容（推理模型把内容留在思考里）；空时回退到稳定的 gpt-4o。
_TEXT_FALLBACK = "gpt-4o"

# 直连 OpenAI 时，GPT-5 家族 / o 系列推理模型的 chat.completions 参数与 gpt-4o 不同：
#   - 必须用 max_completion_tokens（不再接受 max_tokens）
#   - 不接受显式 temperature（只支持默认值），故一律不发
# 且推理会占用 completion 预算，预算太小会只返回空内容，这里给一个较高的下限。
_OPENAI_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")
_OPENAI_REASONING_MIN_TOKENS = 4000


def _is_openai_reasoning_model(model: str) -> bool:
    return (model or "").lower().startswith(_OPENAI_REASONING_PREFIXES)


def _openai_token_kwargs(model: str, max_tokens: Optional[int], default: int) -> dict:
    """按模型给出正确的 token 上限参数（名字 + 取值）。"""
    budget = max_tokens or default
    if _is_openai_reasoning_model(model):
        return {"max_completion_tokens": max(budget, _OPENAI_REASONING_MIN_TOKENS)}
    return {"max_tokens": budget}


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

    @property
    def use_gateway(self) -> bool:
        """是否走网关。生产强制直连 OpenAI（LLM_FORCE_OPENAI=true）时跳过网关，
        避免 AWS 等环境连不通网关时逐个视觉模型等到超时。"""
        return self.gateway_ready and not self.settings.llm_force_openai

    # ---------------- 文本 ----------------
    def text(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        log_id: Optional[str] = None,
        model: Optional[str] = None,
    ) -> str:
        if self.use_gateway:
            from app.llm import model_client

            # 依次尝试首选模型与稳定回退模型；空内容（非异常）也视为失败继续下一个。
            order = []
            for m in (model or _TEXT_MODEL, _TEXT_FALLBACK):
                if m not in order:
                    order.append(m)
            for m in order:
                try:
                    out = model_client.call_model(
                        prompt,
                        model=m,
                        system=system,
                        max_tokens=max_tokens,
                        network=self.settings.model_client_network,
                        log_id=log_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[llm] gateway text (%s) failed: %s", m, exc)
                    continue
                if out and out.strip():
                    return out
                logger.warning("[llm] gateway text (%s) returned empty, trying next", m)
        return self._openai_text(prompt, system=system, max_tokens=max_tokens)

    # ---------------- 文本（流式）----------------
    def text_stream(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        log_id: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Iterator[str]:
        """逐段产出文本增量。流式失败或没吐出任何内容时，退化为一次性 text()（yield 整段）。"""
        emitted = False
        try:
            gen = (
                self._gateway_text_stream(prompt, system=system, max_tokens=max_tokens, log_id=log_id, model=model)
                if self.use_gateway
                else self._openai_text_stream(prompt, system=system, max_tokens=max_tokens, model=model)
            )
            for piece in gen:
                if piece:
                    emitted = True
                    yield piece
            if emitted:
                return
        except Exception as exc:  # noqa: BLE001 — 流式不稳定则整段兜底
            logger.warning("[llm] text stream failed, fallback to non-stream: %s", exc)
            if emitted:
                return
        full = self.text(prompt, system=system, max_tokens=max_tokens, log_id=log_id, model=model)
        if full:
            yield full

    @staticmethod
    def _iter_stream(stream) -> Iterator[str]:
        for chunk in stream:
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            piece = getattr(choices[0].delta, "content", None)
            if piece:
                yield piece

    def _openai_text_stream(
        self, prompt: str, *, system: Optional[str], max_tokens: Optional[int], model: Optional[str] = None
    ) -> Iterator[str]:
        client = self._openai_client()
        # 复盘类流式建议传非推理模型（如 gpt-4o）以获得逐字即时输出；缺省用 env 配置。
        model = model or os.getenv("OPENAI_TEXT_MODEL", "gpt-4o")
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **_openai_token_kwargs(model, max_tokens, 900)
        )
        yield from self._iter_stream(stream)

    def _gateway_text_stream(
        self,
        prompt: str,
        *,
        system: Optional[str],
        max_tokens: Optional[int],
        log_id: Optional[str],
        model: Optional[str],
    ) -> Iterator[str]:
        from app.llm import model_client as mc

        # 推理模型流式偶发只出思考不出内容，网关流式统一走稳定的 gpt-4o。
        choice = model if (model in mc.MODEL_REGISTRY) else "gpt-4o"
        cfg = mc.MODEL_REGISTRY[choice]
        client = mc._ensure_clients(mc._resolve_network(self.settings.model_client_network))[choice]
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        stream = client.chat.completions.create(
            model=cfg["name"],
            messages=messages,
            max_tokens=max_tokens or cfg["max_tokens"],
            stream=True,
            extra_headers={"X-TT-LOGID": log_id or f"poker-model-{os.getpid()}"},
        )
        yield from self._iter_stream(stream)

    # ---------------- 视觉 ----------------
    def vision(
        self,
        prompt: str,
        images: Union[ImageInput, Sequence[ImageInput]],
        *,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        log_id: Optional[str] = None,
        model: Optional[str] = None,
    ) -> str:
        if self.use_gateway:
            try:
                from app.llm import model_client

                # 默认首选 gemini-flash；model_client 会在 vision 池内按顺序 fallback。
                # 调用方可用 model= 强制某个视觉模型（如重试时强制 gpt-4o）。
                return model_client.call_model(
                    prompt,
                    images=images,
                    model=model or _VISION_ORDER[0],
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
            model=model, messages=messages, **_openai_token_kwargs(model, max_tokens, 2000)
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
            model=model, messages=messages, **_openai_token_kwargs(model, max_tokens, 2000)
        )
        return (resp.choices[0].message.content or "").strip()


@lru_cache
def get_provider() -> LLMProvider:
    return LLMProvider()
