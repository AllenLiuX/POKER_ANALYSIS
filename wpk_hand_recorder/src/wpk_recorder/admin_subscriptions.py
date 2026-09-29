"""Operator console for manual subscriptions.

The recorder dashboard is the admin surface. It connects with the same
Postgres role used for cloud sync, then calls the subscription functions as
the configured admin account. The browser never receives that connection.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .cloud_sync import apply_default_cloud_sync


class SubscriptionError(RuntimeError):
    pass


def fetch_dashboard(user_id: str, email: str) -> dict:
    connection = _connect()
    try:
        _assume_user(connection, user_id, email)
        row = connection.execute("select public.admin_dashboard()").fetchone()
        connection.rollback()
        return dict(row[0])
    except Exception as exc:
        connection.rollback()
        if "管理员" in str(exc):
            raise SubscriptionError("需要管理员权限") from exc
        raise
    finally:
        connection.close()


def set_plan(
    user_id: str,
    plan: str,
    expires_at: Optional[str],
    note: Optional[str],
    actor_id: str,
    actor_email: str,
) -> dict:
    if plan not in {"free", "pro"}:
        raise SubscriptionError("套餐必须是 free 或 pro")
    connection = _connect()
    try:
        _assume_user(connection, actor_id, actor_email)
        row = connection.execute(
            """
            select public.admin_set_plan(
                %s::uuid,
                %s,
                %s::timestamptz,
                %s
            )
            """,
            (user_id, plan, expires_at or None, note or None),
        ).fetchone()
        connection.commit()
        return dict(row[0])
    except Exception as exc:
        connection.rollback()
        if "管理员" in str(exc):
            raise SubscriptionError("需要管理员权限") from exc
        raise
    finally:
        connection.close()


def _connect():
    import os

    import psycopg

    apply_default_cloud_sync()
    url = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not url or "YOUR-PASSWORD" in url:
        raise SubscriptionError("云端数据库未配置")
    try:
        connection = psycopg.connect(
            url,
            connect_timeout=20,
            application_name="wpk-admin",
        )
    except Exception as exc:
        raise SubscriptionError("云端数据库连接失败") from exc
    connection.execute("SET statement_timeout = '60s'")
    return connection


def _assume_user(connection, user_id: str, email: str) -> None:
    claims = json.dumps({
        "sub": user_id,
        "role": "authenticated",
        "email": email,
    })
    connection.execute("select set_config('request.jwt.claims', %s, true)", (claims,))
    connection.execute("select set_config('request.jwt.claim.sub', %s, true)", (user_id,))


def public_error(exc: Exception) -> str:
    if isinstance(exc, SubscriptionError):
        return str(exc)
    text = str(exc).splitlines()[0]
    lowered = text.lower()
    if "password" in lowered or "postgresql://" in lowered:
        return "云端数据库连接失败"
    return text[:300]
