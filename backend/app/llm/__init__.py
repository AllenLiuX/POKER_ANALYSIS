"""LLM 层：统一 provider 抽象。

优先 model_client 网关（公网可用），失败 fallback 到 env 的个人 OpenAI。
铁律：LLM 只负责解析与解释，不负责计算数字/判定（那是引擎/求解器的事）。
"""
from app.llm.provider import LLMProvider, get_provider  # noqa: F401
