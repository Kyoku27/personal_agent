from __future__ import annotations

from pathlib import Path
import os
from urllib.parse import quote

import requests

from src.core.config_manager import BASE_DIR, get_env

from .bot_client import FEISHU_BASE_URL


REDIRECT_URI = "http://localhost:8000/api/lark/oauth/callback"
DEFAULT_SCOPES = [
    "offline_access",
    "bitable:app",
    "base:app:read",
    "base:table:read",
    "base:table:create",
    "base:table:update",
    "base:field:read",
    "base:field:create",
    "base:field:update",
    "base:record:read",
    "base:record:retrieve",
    "base:record:update",
]


def _get_app_access_token() -> str:
    app_id = get_env("FEISHU_APP_ID", "") or ""
    app_secret = get_env("FEISHU_APP_SECRET", "") or ""
    if not app_id or not app_secret:
        raise RuntimeError("FEISHU_APP_ID / FEISHU_APP_SECRET is not configured")
    url = f"{FEISHU_BASE_URL}/auth/v3/app_access_token/internal"
    resp = requests.post(url, json={"app_id": app_id, "app_secret": app_secret}, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Failed to get app_access_token: {data}")
    token = data.get("app_access_token") or ""
    if not token:
        raise RuntimeError("app_access_token is empty")
    return token


def build_oauth_url() -> str:
    app_id = get_env("FEISHU_APP_ID", "") or ""
    if not app_id:
        raise RuntimeError("FEISHU_APP_ID is not configured")
    scope = quote(" ".join(DEFAULT_SCOPES), safe="")
    redirect_uri = quote(REDIRECT_URI, safe="")
    return (
        "https://accounts.larksuite.com/open-apis/authen/v1/authorize"
        f"?app_id={app_id}&redirect_uri={redirect_uri}&scope={scope}"
    )


def _set_env_value(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    prefix = f"{key}="
    updated = False
    next_lines: list[str] = []
    for line in lines:
        if line.startswith(prefix):
            next_lines.append(f"{key}={value}")
            updated = True
        else:
            next_lines.append(line)
    if not updated:
        next_lines.append(f"{key}={value}")
    path.write_text("\n".join(next_lines) + "\n", encoding="utf-8")
    os.environ[key] = value


def exchange_code_and_store(code: str) -> dict[str, object]:
    if not code:
        raise RuntimeError("OAuth code is empty")
    app_token = _get_app_access_token()
    url = f"{FEISHU_BASE_URL}/authen/v1/access_token"
    headers = {
        "Authorization": f"Bearer {app_token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {"grant_type": "authorization_code", "code": code}
    resp = requests.post(url, headers=headers, json=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Failed to exchange OAuth code: {data}")
    body = data.get("data") or {}
    access_token = body.get("access_token") or ""
    refresh_token = body.get("refresh_token") or ""
    if not access_token:
        raise RuntimeError(f"OAuth response did not include access_token: {data}")

    env_path = BASE_DIR / ".env"
    _set_env_value(env_path, "LARK_USER_ACCESS_TOKEN", access_token)
    if refresh_token:
        _set_env_value(env_path, "LARK_USER_REFRESH_TOKEN", refresh_token)
    return {
        "success": True,
        "message": "Lark user authorization saved. Restart backend or retry the monthly template action.",
        "expires_in": body.get("expires_in"),
        "has_refresh_token": bool(refresh_token),
    }


def refresh_user_access_token() -> dict[str, object]:
    refresh_token = get_env("LARK_USER_REFRESH_TOKEN", "") or ""
    if not refresh_token:
        raise RuntimeError("LARK_USER_REFRESH_TOKEN is not configured. Re-authorize from /api/lark/oauth/url.")

    app_token = _get_app_access_token()
    url = f"{FEISHU_BASE_URL}/authen/v1/refresh_access_token"
    headers = {
        "Authorization": f"Bearer {app_token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    resp = requests.post(url, headers=headers, json=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Failed to refresh OAuth token: {data}")
    body = data.get("data") or {}
    access_token = body.get("access_token") or ""
    next_refresh_token = body.get("refresh_token") or ""
    if not access_token:
        raise RuntimeError(f"Refresh response did not include access_token: {data}")

    env_path = BASE_DIR / ".env"
    _set_env_value(env_path, "LARK_USER_ACCESS_TOKEN", access_token)
    if next_refresh_token:
        _set_env_value(env_path, "LARK_USER_REFRESH_TOKEN", next_refresh_token)
    return {
        "success": True,
        "message": "Lark user authorization refreshed. Restart backend or retry the monthly template action.",
        "expires_in": body.get("expires_in"),
        "has_refresh_token": bool(next_refresh_token or refresh_token),
    }
