"""统一的 "调用大模型做分析" 基础代码 (文本 + 图片多模态). 内网 / 公网都能用.

Vendored 自内部通用模块。**唯一改动**：所有 API key 改为从环境变量读取
(MODEL_GATEWAY_KEY / MODEL_GATEWAY_KEY_GPT4O)，绝不硬编码、绝不入 git。

设计目标
--------
    - 一个函数 :func:`call_model` 同时覆盖 **纯文本** 和 **文本 + 图片** 两种输入;
    - 一份 :data:`MODEL_REGISTRY` 声明所有可选模型 (gpt / gemini), 调用方用 ``model=``
      指定要哪个, 不指定就按注册顺序自动选 + 失败自动 fallback 到下一个;
    - 内网 (corp / VPN) 与 **公网 / 办公网** 走同一套模型, 只是 host 不同 —— 通过
      env ``MODEL_CLIENT_NETWORK`` 或 ``call_model(..., network=...)`` 一键切换,
      **公网默认可用** (走 *.tiktok-row.net / *.tiktok-row.org 网关);
    - 图片入参既支持本地 bytes, 也支持公网 http(s) url;
    - openai SDK 做 lazy import, 没装也不污染 import 链路.
"""

import base64
import logging
import os
import time
import urllib.error
import urllib.request
from typing import List, Optional, Sequence, Union

logger = logging.getLogger(__name__)

ImageInput = Union[bytes, bytearray, str]

# ===========================================================================
# 网络 — 内网 / 公网(办公网) host 切换
# ===========================================================================
_OFFICE_HOST_MAP = {
    "search-va.byteintl.net": "genai-va-og.tiktok-row.org",   # gpt-4o (VA)
    "gpt-i18n.byteintl.net": "genai-sg-og.tiktok-row.org",    # gpt-5.x (SG)
    "aidp-i18ntt-sg.byteintl.net": "aidp-i18ntt-sg.tiktok-row.net",  # gemini modelhub
}

_OFFICE_ALIASES = {"office", "public", "local", "corp-out", "外网", "公网", "办公网"}
_CORP_ALIASES = {"corp", "online", "intranet", "prod", "内网"}


def _resolve_network(network: Optional[str] = None) -> str:
    """决定走内网还是公网. 优先级: 显式入参 > env MODEL_CLIENT_NETWORK > 默认 corp."""
    raw = (network or os.getenv("MODEL_CLIENT_NETWORK", "") or "corp").strip().lower()
    if raw in _OFFICE_ALIASES:
        return "office"
    return "corp"


def _route_host(host: str, network: str) -> str:
    """公网时把内网 host 换成对应的办公网 host; 内网 / 未知 host 原样返回."""
    if network == "office":
        return _OFFICE_HOST_MAP.get(host, host)
    return host


# ===========================================================================
# 模型注册表 —— 声明顺序即默认优先级 (靠前的先用 / 先 fallback)
# ===========================================================================
# 密钥从环境变量读取（不硬编码）：
#   MODEL_GATEWAY_KEY        —— 共享网关 AK（gpt-5.x / gemini）
#   MODEL_GATEWAY_KEY_GPT4O  —— gpt-4o 专用 AK
_SHARED_KEY = os.getenv("MODEL_GATEWAY_KEY", "")
_GPT4O_KEY = os.getenv("MODEL_GATEWAY_KEY_GPT4O", "")

MODEL_REGISTRY: dict = {
    # ---- 文本首选: 最新 gpt-5.6 ----
    "gpt-5.6-sol": {
        "name": "gpt-5.6-sol",
        "host": "gpt-i18n.byteintl.net",
        "endpoint_path": "/gpt/openapi/online/v2/crawl",
        "api_key": _SHARED_KEY,
        "api_version": "2024-02-01",
        "max_tokens": 6000,
        "supports_temperature": False,  # 实测: 只接受默认 temperature=1
        "supports_vision": False,
    },
    # ---- 文本备选: 便宜 / 快 ----
    "gpt-5.4-mini": {
        "name": "gpt-5.4-mini-2026-03-17",
        "host": "gpt-i18n.byteintl.net",
        "endpoint_path": "/gpt/openapi/online/v2/crawl",
        "api_key": _SHARED_KEY,
        "api_version": "2024-02-01",
        "max_tokens": 6000,
        "supports_temperature": True,
        "supports_vision": False,
    },
    # ---- 文本 / 视觉通吃 ----
    "gpt-4o": {
        "name": "gpt-4o-2024-05-13",
        "host": "search-va.byteintl.net",
        "endpoint_path": "/gpt/openapi/online/v2/crawl",
        "api_key": _GPT4O_KEY,
        "api_version": "2023-07-01-preview",
        "max_tokens": 4000,
        "supports_temperature": True,
        "supports_vision": True,
    },
    # ---- 视觉首选: modelhub gemini (带图请求最稳) ----
    "gemini-flash": {
        "name": "gemini-2.5-flash",
        "host": "aidp-i18ntt-sg.byteintl.net",
        "endpoint_path": "/api/modelhub/online/multimodal/crawl",
        "api_key": _SHARED_KEY,
        "api_version": "2024-03-01-preview",
        "max_tokens": 2000,
        "supports_temperature": True,
        "supports_vision": True,
    },
    # ---- 视觉 fallback: 更准 / 更慢 / 更贵 ----
    "gemini-pro": {
        "name": "gemini-2.5-pro",
        "host": "aidp-i18ntt-sg.byteintl.net",
        "endpoint_path": "/api/modelhub/online/v2/crawl",
        "api_key": _SHARED_KEY,
        "api_version": "2024-03-01-preview",
        "max_tokens": 2000,
        "supports_temperature": True,
        "supports_vision": True,
    },
}


def list_models() -> List[str]:
    """返回当前可选的 model key (按默认优先级排序)."""
    return list(MODEL_REGISTRY.keys())


# ===========================================================================
# Lazy init: 真正调用时才 import openai / 建 client. 按 network 缓存.
# ===========================================================================
_CLIENTS: dict = {}      # network -> {model_key -> AzureOpenAI}
_OPENAI_MOD = None       # 缓存 openai 模块, 用于异常类型判断
_REQUEST_TIMEOUT = float(os.getenv("MODEL_CLIENT_TIMEOUT", "60"))


def _ensure_openai():
    global _OPENAI_MOD
    if _OPENAI_MOD is not None:
        return _OPENAI_MOD
    try:
        import openai as _mod
    except ImportError as exc:
        raise RuntimeError(
            "openai 包未安装. 请 pip install openai (>=1.0)."
        ) from exc
    _OPENAI_MOD = _mod
    for noisy in ("openai", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return _mod


def _ensure_clients(network: str) -> dict:
    """按 network lazy 建一套 AzureOpenAI client (每个 model 一个), 结果缓存."""
    if network in _CLIENTS:
        return _CLIENTS[network]
    mod = _ensure_openai()
    clients = {
        key: mod.AzureOpenAI(
            azure_endpoint=f"https://{_route_host(cfg['host'], network)}{cfg['endpoint_path']}",
            api_version=cfg["api_version"],
            api_key=cfg["api_key"],
            timeout=_REQUEST_TIMEOUT,
            max_retries=0,
        )
        for key, cfg in MODEL_REGISTRY.items()
    }
    _CLIENTS[network] = clients
    logger.info("[model_client] init clients network=%s models=%s", network, list(clients.keys()))
    return clients


# ===========================================================================
# image -> data-url (http(s) URL 先下载 + 嗅探 mime, 再内联)
# ===========================================================================
_DOWNLOAD_TIMEOUT_SEC = 10
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_DOWNLOAD_UA = "Mozilla/5.0 (compatible; PokerAnalysisModelClient/1.0)"

_BLOCKED_URL_MARKERS = (
    "facebook.com/tr",
    "scorecardresearch.com",
    "google-analytics.com",
    "doubleclick.net",
    "googletagmanager.com",
)


def _sniff_image_mime(data: bytes) -> Optional[str]:
    if not data:
        return None
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    return None


def _bytes_to_data_url(data: bytes, mime: Optional[str] = None) -> str:
    mime = mime or _sniff_image_mime(data) or "image/jpeg"
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _download_image_bytes(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _DOWNLOAD_UA,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT_SEC) as resp:
            chunks: List[bytes] = []
            total = 0
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_IMAGE_BYTES:
                    raise ValueError(f"image larger than {_MAX_IMAGE_BYTES} bytes: {url[:120]}")
                chunks.append(chunk)
            return b"".join(chunks)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"image download HTTP {exc.code}: {url[:120]}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"image download failed: {exc.reason!r} url={url[:120]}") from exc
    except TimeoutError as exc:
        raise ValueError(f"image download timeout: {url[:120]}") from exc


def _http_url_to_data_url(url: str) -> str:
    if any(marker in url.lower() for marker in _BLOCKED_URL_MARKERS):
        raise ValueError(f"blocked non-image url: {url[:120]}")
    data = _download_image_bytes(url)
    if not data:
        raise ValueError(f"image download returned empty body: {url[:120]}")
    mime = _sniff_image_mime(data)
    if mime is None:
        raise ValueError(f"downloaded bytes are not a recognised image: {url[:120]}")
    return _bytes_to_data_url(data, mime=mime)


def _normalize_image_input(image: ImageInput) -> str:
    if isinstance(image, (bytes, bytearray)):
        if not image:
            raise ValueError("image bytes 为空")
        return _bytes_to_data_url(bytes(image))
    if isinstance(image, str):
        if not image.strip():
            raise ValueError("image str 为空")
        if image.startswith("data:"):
            return image
        if image.startswith(("http://", "https://")):
            return _http_url_to_data_url(image)
        raise ValueError(
            "image str 必须以 http:// / https:// / data: 开头; 本地路径请先 open(path,'rb').read() 传 bytes"
        )
    raise TypeError(f"image must be bytes / bytearray / str, got {type(image).__name__}")


def _coerce_images(images: Optional[Union[ImageInput, Sequence[ImageInput]]]) -> List[ImageInput]:
    if images is None:
        return []
    if isinstance(images, (bytes, bytearray, str)):
        return [images]
    return list(images)


# ===========================================================================
# 组装 message + 模型选择
# ===========================================================================
def _build_messages(
    prompt: str,
    image_urls: List[str],
    system: Optional[str],
    history: Optional[List[dict]],
) -> List[dict]:
    messages: List[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    if history:
        messages.extend(history)

    if image_urls:
        content: List[dict] = [{"type": "text", "text": prompt}]
        for url in image_urls:
            content.append({"type": "image_url", "image_url": {"url": url, "detail": "auto"}})
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": prompt})
    return messages


def _resolve_order(model: str, need_vision: bool) -> List[str]:
    keys = list(MODEL_REGISTRY.keys())
    if model and model not in MODEL_REGISTRY:
        raise ValueError(f"未知 model={model!r}. 可选: {keys}")

    if need_vision:
        pool = [k for k in keys if MODEL_REGISTRY[k]["supports_vision"]]
    else:
        pool = keys

    if model:
        rest = [k for k in pool if k != model]
        return [model] + rest
    return pool


# ===========================================================================
# 核心调用: 模型池 fallback
# ===========================================================================
def call_model(
    prompt: str,
    *,
    images: Optional[Union[ImageInput, Sequence[ImageInput]]] = None,
    model: str = "",
    system: Optional[str] = None,
    history: Optional[List[dict]] = None,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    network: Optional[str] = None,
    log_id: Optional[str] = None,
) -> str:
    """文本 (+ 可选多张图) -> 模型回复 (str). 内网 / 公网都能用."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt 必须是非空字符串")

    net = _resolve_network(network)
    img_list = _coerce_images(images)
    image_urls = [_normalize_image_input(img) for img in img_list]
    need_vision = bool(image_urls)

    order = _resolve_order(model, need_vision)
    messages = _build_messages(prompt.strip(), image_urls, system, history)
    clients = _ensure_clients(net)
    headers = {"X-TT-LOGID": log_id or f"poker-model-{os.getpid()}"}

    tried: List[str] = []
    for choice in order:
        cfg = MODEL_REGISTRY[choice]
        client = clients[choice]
        try:
            kwargs = {
                "model": cfg["name"],
                "messages": messages,
                "max_tokens": max_tokens or cfg["max_tokens"],
                "extra_headers": headers,
            }
            if cfg.get("supports_temperature", False):
                kwargs["temperature"] = temperature
            completion = client.chat.completions.create(**kwargs)
            return (completion.choices[0].message.content or "").strip()
        except Exception as exc:  # noqa: BLE001 — 多模型轮换必须 catch all
            if _OPENAI_MOD is not None and isinstance(exc, _OPENAI_MOD.BadRequestError):
                raise RuntimeError(f"model_client rejected request (model={choice}): {exc}") from exc
            logger.warning("[model_client] %s failed (will try next): %s", choice, exc)
            tried.append(choice)
            time.sleep(1)

    raise RuntimeError(f"model_client failed: 所有模型都未成功 (network={net}, tried={tried}).")


def call_model_batch(
    prompt: str,
    *,
    image_groups: Optional[Sequence[Optional[Union[ImageInput, Sequence[ImageInput]]]]] = None,
    count: Optional[int] = None,
    model: str = "",
    system: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    network: Optional[str] = None,
    log_id: Optional[str] = None,
) -> List[str]:
    """同一 prompt 串行跑多组输入, 返回与输入对齐的 list[str]."""
    if image_groups is None and count is None:
        raise ValueError("call_model_batch 需要提供 image_groups 或 count 之一")

    groups: Sequence = image_groups if image_groups is not None else [None] * int(count or 0)
    if not groups:
        raise ValueError("没有任何输入")

    results: List[str] = []
    for group in groups:
        results.append(
            call_model(
                prompt,
                images=group,
                model=model,
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
                network=network,
                log_id=log_id,
            )
        )
    return results
