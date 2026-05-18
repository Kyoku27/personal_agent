"""
Wiki 节点解析：将 Lark Wiki 下挂的多维表格 node_token 解析为真正的 bitable app_token。

Lark Wiki 的 URL 形如:
    https://xxx.larksuite.com/wiki/<node_token>?table=<table_id>&...

这里的 <node_token> 不能直接当作 bitable 的 app_token。
需要调用 wiki API 拿到该 wiki 节点对应的 obj_token（即真正的 bitable app_token）。

API 文档: https://open.larksuite.com/document/server-docs/docs/wiki-v2/space-node/get_node
"""
from __future__ import annotations

import requests

from .bot_client import FEISHU_BASE_URL, _get_tenant_access_token


def resolve_wiki_to_bitable(node_token: str) -> str:
    """根据 wiki node_token 获取真正的 bitable app_token。

    :param node_token: Lark Wiki 节点 token，URL 中 /wiki/ 后面那串字符
    :return: 该 wiki 节点对应的 bitable app_token
    :raises RuntimeError: 当 API 调用失败、节点不是 bitable 等情况
    """
    if not node_token:
        raise RuntimeError("wiki node_token 为空，请检查 FEISHU_RAKUTEN_WIKI_NODE_TOKEN")

    token = _get_tenant_access_token()
    url = f"{FEISHU_BASE_URL}/wiki/v2/spaces/get_node"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    params = {"token": node_token, "obj_type": "wiki"}

    resp = requests.get(url, headers=headers, params=params, timeout=15)
    if not resp.ok:
        raise RuntimeError(
            f"[wiki_resolver] HTTP {resp.status_code}: {resp.text[:300]}"
        )

    data = resp.json()
    if data.get("code") != 0:
        # 常见 code:
        # 1254303 - 没权限（机器人没有加到 wiki 共享）
        raise RuntimeError(
            f"[wiki_resolver] 解析失败 code={data.get('code')} msg={data.get('msg')}"
            f" - 请确认机器人已加入该 wiki 的成员/共享"
        )

    node = (data.get("data") or {}).get("node") or {}
    obj_type = node.get("obj_type")
    obj_token = node.get("obj_token") or ""

    if obj_type != "bitable":
        raise RuntimeError(
            f"[wiki_resolver] node 不是 bitable，实际 obj_type={obj_type}"
        )
    if not obj_token:
        raise RuntimeError("[wiki_resolver] 返回结果中没有 obj_token")

    return obj_token
