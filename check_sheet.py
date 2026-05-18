
import requests
import json

APP_ID = "cli_a90b78bc4c381e1a"
APP_SECRET = "HHT1YHnzY4KHv0JyU21pAdmtnhRz7nw0"
TOKEN = "KbyvsiPLyhZufEtakX4jPypRpoh"

try:
    print("Fetching token...")
    at_res = requests.post("https://open.larksuite.com/open-apis/auth/v3/tenant_access_token/internal", 
                          json={"app_id": APP_ID, "app_secret": APP_SECRET})
    at = at_res.json()["tenant_access_token"]
    
    print("Listing sheets...")
    s_res = requests.get(f"https://open.larksuite.com/open-apis/sheets/v3/spreadsheets/{TOKEN}/sheets/query", 
                        headers={"Authorization": f"Bearer {at}"})
    sheets = s_res.json()["data"]["sheets"]
    master_id = next(s["sheet_id"] for s in sheets if s["title"] == "Master")
    
    print(f"Reading Master sheet (ID: {master_id})...")
    v_res = requests.get(f"https://open.larksuite.com/open-apis/sheets/v2/spreadsheets/{TOKEN}/values/{master_id}!A1:Z20", 
                        headers={"Authorization": f"Bearer {at}"})
    rows = v_res.json()["data"]["valueRange"]["values"]
    
    print("\n--- MASTER SHEET DATA ---")
    for i, row in enumerate(rows):
        print(f"Row {i+1}: {row}")

except Exception as e:
    print(f"ERROR: {str(e)}")
