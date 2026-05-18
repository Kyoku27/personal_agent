#!/usr/bin/env python3
"""检查3月和4月日别表的差异"""
import sys
sys.path.insert(0, str(__file__).rsplit('\\', 1)[0])

from src.core.config_manager import get_env
from src.features.feishu.bot_client import _get_tenant_access_token
from src.features.ecommerce.rakuten.daily_template import LarkBaseClient, resolve_wiki_to_bitable

wiki_node_token = get_env("FEISHU_RAKUTEN_WIKI_NODE_TOKEN") or "TLGPwyCYpiFGKVkqAXnji9iupue"
base_token = resolve_wiki_to_bitable(wiki_node_token)
token = _get_tenant_access_token()

client = LarkBaseClient(base_token, token=token)

# 获取3月和4月表
tables = client.list_tables()
march_table = next((t for t in tables if t.get("name") == "3月_日別"), None)
april_table = next((t for t in tables if t.get("name") == "4月_日別"), None)

if not march_table:
    print("❌ 找不到 3月_日別")
    sys.exit(1)

if not april_table:
    print("❌ 找不到 4月_日別")
    sys.exit(1)

march_id = march_table["table_id"] or march_table["id"]
april_id = april_table["table_id"] or april_table["id"]

print("\n=== 3月_日別 字段 ===")
march_fields = client.list_fields_v1(march_id)
for i, f in enumerate(march_fields):
    print(f"{i+1}. {f.get('field_name')} (type={f.get('type')}, id={f.get('field_id')[:8]}...)")

print("\n=== 4月_日別 字段 ===")
april_fields = client.list_fields_v1(april_id)
for i, f in enumerate(april_fields):
    print(f"{i+1}. {f.get('field_name')} (type={f.get('type')}, id={f.get('field_id')[:8]}...)")

print("\n=== 字段对比 ===")
march_field_names = {f.get('field_name') for f in march_fields}
april_field_names = {f.get('field_name') for f in april_fields}

missing = march_field_names - april_field_names
extra = april_field_names - march_field_names

if missing:
    print(f"❌ 4月缺少的字段: {missing}")
if extra:
    print(f"⚠️ 4月多出的字段: {extra}")
if not missing and not extra:
    print("✅ 字段一致")
