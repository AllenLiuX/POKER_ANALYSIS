# Backend — FastAPI

德州扑克进阶学习平台后端。纯扑克引擎放在 `app/poker/`（无 Web 依赖，可独立测试）。

## 结构

```
app/
├── main.py          # FastAPI 入口 + CORS
├── config.py        # 配置（全部从 env 读，绝不硬编码密钥）
├── api/             # 路由：health, equity（后续 trainer/ranges/ingest/...）
├── poker/           # 扑克引擎：cards / evaluate / equity
├── llm/             # LLM provider：model_client(vendored) + provider 抽象
└── schemas/         # Pydantic 模型
```

## 运行

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # 填密钥（可留空，LLM 功能会降级）
uvicorn app.main:app --reload --port 8000
```

- 健康检查：`GET http://127.0.0.1:8000/health`
- 交互文档：`http://127.0.0.1:8000/docs`

## 测试

```bash
pytest
```

## API（Phase 0）

- `GET  /health` — 健康 + 能力探测
- `POST /api/equity` — `{hero, villain_range, board?, trials?}` → win/tie/lose/equity
- `POST /api/potodds` — `{pot, call, equity?}` → required_equity / ev_call

## 密钥

所有密钥经 `.env` 注入（已 `.gitignore`）。`model_client` 的网关 AK 从 `MODEL_GATEWAY_KEY` /
`MODEL_GATEWAY_KEY_GPT4O` 读取；OpenAI 兜底用 `OPENAI_API_KEY`。**任何密钥不进 git。**
