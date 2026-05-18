import requests
import json
import sys

dry_run = "--dry-run" in sys.argv

payload = {
    "target_month": "3",
    "source_month": "2",
    "dry_run": dry_run
}

mode = "DRY-RUN" if dry_run else "EXECUTE"
print(f"=== {mode} ===")
print(f"payload: {payload}")

resp = requests.post(
    "http://localhost:8000/api/ecommerce/rakuten/daily-template",
    json=payload,
    timeout=60
)
print(f"HTTP Status: {resp.status_code}")
result = resp.json()
# Print with ascii escapes to avoid encoding issues on Windows
print(json.dumps(result, ensure_ascii=True, indent=2))
