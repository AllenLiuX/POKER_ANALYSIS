"""app 包初始化：尽早把 backend/.env 载入 os.environ。

pydantic-settings 只把 .env 读进 Settings 对象，不会写入 os.environ；
而 vendored 的 model_client 依赖 os.getenv 读取网关密钥，故这里显式 load_dotenv。
"""
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:  # noqa: BLE001 — dotenv 缺失或读取失败不应阻断导入
    pass
