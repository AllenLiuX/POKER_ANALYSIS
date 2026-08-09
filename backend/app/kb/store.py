"""知识库持久化：本地 SQLite，按「概念」缓存检索到的策略片段。

- kb_docs：每个 (concept, url) 一条片段；同 url 覆盖更新。
- kb_concept_meta：每个概念上次刷新时间 + 用的 query，用于 TTL 判断，避免重复联网。

线程安全：FastAPI 的同步路由跑在线程池里，这里用单连接 + 全局锁串行化读写
（KB 是低频写、共享只读为主，串行化足够且简单）。
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from app.config import get_settings

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None
_conn_path: Optional[str] = None


def _resolve_path() -> str:
    p = get_settings().kb_path or "data/poker_kb.sqlite3"
    path = Path(p)
    if not path.is_absolute():
        # 相对 backend/ 根（app/kb/store.py → parents[2] == backend/）
        path = Path(__file__).resolve().parents[2] / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def _get_conn() -> sqlite3.Connection:
    global _conn, _conn_path
    want = _resolve_path()
    if _conn is not None and _conn_path == want:
        return _conn
    if _conn is not None:
        try:
            _conn.close()
        except Exception:  # noqa: BLE001
            pass
    _conn = sqlite3.connect(want, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute(
        """create table if not exists kb_docs (
            concept text not null,
            url text not null,
            title text not null default '',
            content text not null default '',
            score real not null default 0,
            retrieved_at integer not null default 0,
            primary key (concept, url)
        )"""
    )
    _conn.execute(
        """create table if not exists kb_concept_meta (
            concept text primary key,
            query text not null default '',
            refreshed_at integer not null default 0,
            doc_count integer not null default 0
        )"""
    )
    _conn.commit()
    _conn_path = want
    return _conn


def upsert_concept(concept: str, query: str, docs: List[Dict[str, object]]) -> int:
    """写入某概念的检索结果（覆盖同 url），并更新其刷新时间。返回写入片段数。

    注意：即使 docs 为空也会更新 meta.refreshed_at（表示"这个概念刚搜过、TTL 内别再搜"），
    避免持续 0 结果的概念反复触发联网。
    """
    now = int(time.time())
    with _lock:
        conn = _get_conn()
        for d in docs:
            url = str(d.get("url") or "").strip()
            if not url:
                continue
            conn.execute(
                """insert into kb_docs (concept, url, title, content, score, retrieved_at)
                   values (?,?,?,?,?,?)
                   on conflict(concept, url) do update set
                     title=excluded.title, content=excluded.content,
                     score=excluded.score, retrieved_at=excluded.retrieved_at""",
                (concept, url, str(d.get("title") or ""), str(d.get("content") or ""),
                 float(d.get("score") or 0.0), now),
            )
        cur = conn.execute("select count(*) as n from kb_docs where concept=?", (concept,))
        cnt = int(cur.fetchone()["n"])
        conn.execute(
            """insert into kb_concept_meta (concept, query, refreshed_at, doc_count)
               values (?,?,?,?)
               on conflict(concept) do update set
                 query=excluded.query, refreshed_at=excluded.refreshed_at, doc_count=excluded.doc_count""",
            (concept, query, now, cnt),
        )
        conn.commit()
        return len(docs)


def is_fresh(concept: str, ttl_days: int) -> bool:
    """该概念是否在 TTL 内刷新过（刷新过即视为新鲜，哪怕 0 结果）。"""
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "select refreshed_at from kb_concept_meta where concept=?", (concept,)
        ).fetchone()
    if not row:
        return False
    return (int(time.time()) - int(row["refreshed_at"])) < ttl_days * 86400


def get_docs(concepts: List[str], per_concept: int = 2) -> List[Dict[str, object]]:
    """取给定概念的 top 片段（每概念按 score 取前 per_concept 条）。"""
    if not concepts:
        return []
    out: List[Dict[str, object]] = []
    with _lock:
        conn = _get_conn()
        for c in concepts:
            rows = conn.execute(
                "select concept, url, title, content, score from kb_docs "
                "where concept=? order by score desc, retrieved_at desc limit ?",
                (c, per_concept),
            ).fetchall()
            for r in rows:
                out.append(
                    {
                        "concept": r["concept"],
                        "url": r["url"],
                        "title": r["title"],
                        "content": r["content"],
                        "score": float(r["score"]),
                    }
                )
    return out


def stats() -> Dict[str, object]:
    with _lock:
        conn = _get_conn()
        docs = int(conn.execute("select count(*) as n from kb_docs").fetchone()["n"])
        rows = conn.execute(
            "select concept, doc_count, refreshed_at from kb_concept_meta order by refreshed_at desc"
        ).fetchall()
    return {
        "docs": docs,
        "concepts": len(rows),
        "by_concept": [
            {"concept": r["concept"], "docs": int(r["doc_count"]), "refreshed_at": int(r["refreshed_at"])}
            for r in rows
        ],
        "path": _resolve_path(),
    }


def reset_for_test() -> None:
    """测试用：关闭当前连接，迫使下次按新 kb_path 重新建连接。"""
    global _conn, _conn_path
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:  # noqa: BLE001
                pass
        _conn = None
        _conn_path = None
