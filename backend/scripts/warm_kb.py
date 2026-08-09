"""预热德州策略知识库：对全部概念联网检索一遍并落库（TTL 内已新鲜的会跳过）。

用法（在 backend/ 下）：
    ./.venv/bin/python -m scripts.warm_kb
需先在 backend/.env 配好 TAVILY_API_KEY（可复用 ~/ai_study_platform 的 key）。
"""
from __future__ import annotations

import json
import sys

from app.config import get_settings
from app.kb import store
from app.kb.concepts import CONCEPTS
from app.kb.retrieve import ensure_fresh


def main() -> int:
    s = get_settings()
    if not s.kb_ready:
        print("知识库未就绪：请在 backend/.env 配置 TAVILY_API_KEY 且 KB_ENABLED=true。")
        return 1
    keys = list(CONCEPTS.keys())
    print(f"开始预热 {len(keys)} 个概念（TTL={s.kb_ttl_days}d，新鲜的会跳过）…")
    fetched = ensure_fresh(keys, max_fetch=len(keys))
    st = store.stats()
    print(f"本次联网补齐 {fetched} 个概念。")
    print(json.dumps(st, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
