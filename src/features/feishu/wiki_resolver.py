from __future__ import annotations

from src.core.config_manager import get_env

from .bot_client import FEISHU_BASE_URL, _get_tenant_access_token

import requests


def resolve_wiki_to_bitable(node_token: str | None = None, direct_app_token: str | None = None) -> str:
    """Resolve a Lark wiki node token to the underlying Bitable app token."""
    direct_app_token = direct_app_token or get_env("FEISHU_RAKUTEN_BITABLE_APP_TOKEN", "") or ""
    if direct_app_token:
        return direct_app_token

    node_token = node_token or get_env("FEISHU_RAKUTEN_WIKI_NODE_TOKEN", "") or ""
    if not node_token:
        raise RuntimeError("FEISHU_RAKUTEN_WIKI_NODE_TOKEN is not configured")

    token = _get_tenant_access_token()
    url = f"{FEISHU_BASE_URL}/wiki/v2/spaces/get_node"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"token": node_token, "obj_type": "wiki"}
    resp = requests.get(url, headers=headers, params=params, timeout=15)
    if not resp.ok:
        raise RuntimeError(f"Lark API HTTP {resp.status_code} GET /wiki/v2/spaces/get_node: {resp.text}")

    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Lark wiki resolve failed: {data}")

    node = (data.get("data") or {}).get("node") or {}
    app_token = node.get("obj_token") or ""
    if not app_token:
        raise RuntimeError(f"Wiki node did not return a bitable app token: {data}")
    return app_token
