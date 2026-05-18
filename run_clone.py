#!/usr/bin/env python3
import sys
sys.path.insert(0, str(__file__).rsplit('\\', 1)[0])

from src.core.config_manager import get_env
from src.features.feishu.bot_client import _get_tenant_access_token
from src.features.feishu.wiki_resolver import resolve_wiki_to_bitable
from src.features.ecommerce.rakuten.daily_template import LarkBaseClient, create_rakuten_daily_template

wiki_node_token = get_env("FEISHU_RAKUTEN_WIKI_NODE_TOKEN") or "TLGPwyCYpiFGKVkqAXnji9iupue"
base_token = resolve_wiki_to_bitable(wiki_node_token)
user_token = _get_tenant_access_token()
client = LarkBaseClient(base_token, token=user_token or None)

source_name = "3月_日別"
target_name = "4月_日別"

try:
    tables = client.list_tables()
    source = next((t for t in tables if t.get("name") == source_name), None)
    target = next((t for t in tables if t.get("name") == target_name), None)

    if not source:
        print(f"ERROR: source table {source_name} not found")
        sys.exit(1)

    if target:
        target_id = target.get("table_id") or target.get("id")
        print(f"Deleting existing target table {target_name} ({target_id})...")
        client._request("DELETE", f"/bitable/v1/apps/{base_token}/tables/{target_id}")
        print("Deleted.")

    # Call the template creation (clone)
    res = create_rakuten_daily_template(target_month=str(4), source_month=str(3), dry_run=False)
    print("RESULT:")
    import json
    print(json.dumps(res, ensure_ascii=False, indent=2))
except Exception as e:
    print("EXCEPTION:", str(e))
    raise
