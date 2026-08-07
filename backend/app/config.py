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

    # ---- Supabase（Phase 4 接入）----
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""

    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.backend_cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
