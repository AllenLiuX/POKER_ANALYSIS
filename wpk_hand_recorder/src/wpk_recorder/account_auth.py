"""Email login for the local recorder dashboard.

The browser talks only to this process. Signup and login use the Supabase
publishable key. The service role key stays out of the page and out of the
account file. A successful login writes the email into the data directory so
the capture process can stamp new hands with that owner.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from .cloud_sync import apply_default_cloud_sync


class AuthError(RuntimeError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def sign_up(email: str, password: str, data_dir: Path) -> dict:
    address = _address(email)
    _password(password)
    payload = _auth_request(
        "POST",
        "/auth/v1/signup",
        {"email": address, "password": password},
    )
    session = _session_from(payload)
    if session is not None:
        _save_account(data_dir, session)
        return session
    if _already_registered(payload):
        raise AuthError("这个邮箱已经注册，请直接登录。刚才不会重发确认信，密码也不会被改掉。", 400)
    return {
        "email": address,
        "needs_confirm": True,
        "info": "注册成功。确认信由 Supabase 发出，请查收邮箱，也看一下垃圾箱。确认后即可登录。",
    }


def send_recovery(email: str) -> dict:
    address = _address(email)
    _auth_request("POST", "/auth/v1/recover", {"email": address})
    return {
        "email": address,
        "info": "如果这个邮箱注册过，重置信会发到邮箱。也看一下垃圾箱。",
    }


def resend_signup(email: str) -> dict:
    address = _address(email)
    _auth_request(
        "POST",
        "/auth/v1/resend",
        {"type": "signup", "email": address},
    )
    return {
        "email": address,
        "info": "确认信已再次提交发送。也看一下垃圾箱。",
    }


def sign_in(email: str, password: str, data_dir: Path) -> dict:
    address = _address(email)
    _password(password)
    payload = _auth_request(
        "POST",
        "/auth/v1/token?grant_type=password",
        {"email": address, "password": password},
    )
    session = _session_from(payload)
    if session is None:
        raise AuthError("邮箱或密码不正确", 401)
    _save_account(data_dir, session)
    return session


def refresh(refresh_token: str, data_dir: Path) -> dict:
    token = (refresh_token or "").strip()
    if not token:
        raise AuthError("请先登录", 401)
    payload = _auth_request(
        "POST",
        "/auth/v1/token?grant_type=refresh_token",
        {"refresh_token": token},
    )
    session = _session_from(payload)
    if session is None:
        raise AuthError("登录已过期，请重新登录", 401)
    _save_account(data_dir, session)
    return session


def sign_out(data_dir: Path, access_token: str = "") -> None:
    if access_token:
        try:
            _auth_request("POST", "/auth/v1/logout", {}, access_token=access_token)
        except AuthError:
            pass
    path = Path(data_dir) / "account.json"
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def current_user(access_token: str) -> dict:
    token = (access_token or "").strip()
    if not token:
        raise AuthError("请先登录", 401)
    payload = _auth_request("GET", "/auth/v1/user", None, access_token=token)
    user_id = str(payload.get("id") or "")
    email = str(payload.get("email") or "").strip().lower()
    if not user_id or "@" not in email:
        raise AuthError("请先登录", 401)
    profile = _profile(user_id, email)
    return {
        "user_id": user_id,
        "email": email,
        "is_admin": bool(profile.get("is_admin")),
        "plan": profile.get("plan") or "free",
        "entitled": bool(profile.get("entitled")),
        "hands": profile.get("hands") or 0,
    }


def _profile(user_id: str, email: str) -> dict:
    from .admin_subscriptions import SubscriptionError, _assume_user, _connect

    connection = None
    try:
        connection = _connect()
        _assume_user(connection, user_id, email)
        row = connection.execute("select public.wpk_me()").fetchone()
        connection.rollback()
        return dict(row[0] or {})
    except SubscriptionError:
        return {}
    except Exception:
        if connection is not None:
            connection.rollback()
        return {}
    finally:
        if connection is not None:
            connection.close()


def _save_account(data_dir: Path, session: dict) -> None:
    path = Path(data_dir) / "account.json"
    path.write_text(
        json.dumps(
            {"email": session["email"], "user_id": session["user_id"]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    os.chmod(path, 0o600)


def _address(email: str) -> str:
    address = (email or "").strip().lower()
    if "@" not in address or len(address) > 200:
        raise AuthError("请填写有效邮箱")
    return address


def _password(password: str) -> None:
    if len(password or "") < 6:
        raise AuthError("密码至少 6 位")


def _already_registered(payload: dict) -> bool:
    user = payload.get("user") if isinstance(payload.get("user"), dict) else payload
    if not isinstance(user, dict):
        return False
    identities = user.get("identities")
    return isinstance(identities, list) and len(identities) == 0


def _session_from(payload: dict) -> Optional[dict]:
    token = payload.get("access_token")
    user = payload.get("user") or {}
    email = str(user.get("email") or payload.get("email") or "").strip().lower()
    user_id = str(user.get("id") or "")
    if not token or not user_id or "@" not in email:
        return None
    return {
        "email": email,
        "user_id": user_id,
        "access_token": token,
        "refresh_token": payload.get("refresh_token") or "",
        "expires_in": payload.get("expires_in") or 3600,
    }


def _auth_request(method: str, path: str, body: Optional[dict], access_token: str = "") -> dict:
    base, key = _config()
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        base + path,
        data=data,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {access_token or key}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise AuthError(_message(raw, exc.code), _status(exc.code)) from exc
    except urllib.error.URLError as exc:
        raise AuthError("登录服务暂时连不上") from exc
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise AuthError("登录服务返回了无法识别的结果") from exc
    if not isinstance(parsed, dict):
        raise AuthError("登录服务返回了无法识别的结果")
    return parsed


def _config() -> tuple:
    apply_default_cloud_sync()
    base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    if not base or not key:
        raise AuthError("云端账号未配置")
    return base, key


def _status(code: int) -> int:
    if code in {400, 401, 422}:
        return 401 if code == 401 else 400
    return 503


def _message(raw: str, code: int) -> str:
    text = raw
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            text = str(
                payload.get("error_description")
                or payload.get("msg")
                or payload.get("message")
                or payload.get("error")
                or raw
            )
    except ValueError:
        pass
    lowered = text.lower()
    if "invalid login" in lowered or "invalid_grant" in lowered:
        return "邮箱或密码不正确"
    if "already" in lowered and "registered" in lowered:
        return "这个邮箱已经注册"
    if "password" in lowered and ("weak" in lowered or "at least" in lowered or "short" in lowered):
        return "密码至少 6 位"
    if code >= 500:
        return "登录服务暂时不可用"
    cleaned = text.splitlines()[0][:180]
    if "postgresql://" in cleaned.lower() or "service_role" in cleaned.lower():
        return "登录失败"
    return cleaned or "登录失败"
