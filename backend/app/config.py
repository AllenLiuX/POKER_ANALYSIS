"""集中配置：全部从环境变量 / .env 读取，绝不硬编码密钥。"""
from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- App ----
    app_name: str = "poker-analysis-api"
    environment: str = "development"
    log_level: str = "INFO"

    # ---- CORS（逗号分隔）----
    backend_cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # ---- LLM 网关 (model_client) ----
    # 见 app/llm/model_client.py；密钥只放 .env。
    model_gateway_key: str = ""       # 共享网关 AK（gpt-5.x / gemini）
    model_gateway_key_gpt4o: str = ""  # gpt-4o 专用 AK
    model_client_network: str = "office"  # corp | office(公网)

    # ---- OpenAI 兜底 ----
    openai_api_key: str = ""
    # 生产环境（如 AWS）连不通公司网关时置 true：文本与视觉都直连 OpenAI，
    # 完全跳过网关，避免逐个视觉模型等到超时。需同时配 OPENAI_API_KEY，
    # 视觉/文本模型分别由 OPENAI_VISION_MODEL / OPENAI_TEXT_MODEL 指定（默认 gpt-4o）。
    llm_force_openai: bool = False

    # ---- Supabase（Phase 4 接入）----
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""

    # ---- 知识库联网搜索（Tavily；与 ~/ai_study_platform 同一 provider）----
    # 留空 = 知识库接地自动关闭，AI 分析退回纯统计接地（不受影响）。
    tavily_api_key: str = ""
    tavily_max_results: int = 5
    tavily_search_depth: str = "basic"  # basic ≈ $0.005/次；advanced ≈ $0.04/次
    # 知识库（RAG 接地）：检索到的德州策略片段按「概念」持久化到本地 SQLite，TTL 内复用不重复搜索。
    kb_enabled: bool = True
    kb_ttl_days: int = 30
    kb_path: str = "data/poker_kb.sqlite3"  # 相对 backend/ 根；store 会解析为绝对路径

    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.backend_cors_origins.split(",") if o.strip()]

    @property
    def kb_ready(self) -> bool:
        """知识库接地是否可用：已启用且配了 Tavily key。"""
        return bool(self.kb_enabled) and bool(self.tavily_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
