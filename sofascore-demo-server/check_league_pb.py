import httpx
import json
import sys

sys.stdout.reconfigure(encoding='utf-8')

PB_URL = "http://127.0.0.1:8090"

# Lấy admin token
url = f"{PB_URL}/api/admins/auth-with-password"
r = httpx.post(url, json={"identity": "admin@sofascore.local", "password": "Admin123456789"})
token = r.json()["token"]
headers = {"Authorization": f"Bearer {token}"}

# Lấy toàn bộ bản ghi trong leagues collection
r2 = httpx.get(f"{PB_URL}/api/collections/leagues/records", headers=headers, timeout=10.0)
data = r2.json()
items = data.get("items", [])
print(f"Total league records: {len(items)}\n")

for item in items:
    raw = item.get("raw_json", {})
    print("=" * 60)
    print(f"Record ID: {item['id']}")
    print(f"sofascore_id: {item.get('sofascore_id')}")
    print(f"name: {item.get('name')!r}")
    print(f"country: {item.get('country')!r}")
    print(f"flag: {item.get('flag')!r}")
    print(f"logo_file: {item.get('logo_file')!r}")
    print(f"logo_url: {item.get('logo_url')!r}")
    print(f"ttl_expired: {item.get('ttl_expired')!r}")
    print()
    print(f"raw_json type: {type(raw)}")
    if isinstance(raw, dict):
        print(f"raw_json keys: {list(raw.keys())}")
        season_id = raw.get("season_id")
        print(f"  season_id: {season_id}")
        standings = raw.get("standings", {})
        if isinstance(standings, dict):
            print(f"  standings keys: {list(standings.keys())}")
            standings_list = standings.get("standings", [])
            print(f"  standings[standings] items count: {len(standings_list)}")
            if standings_list:
                first = standings_list[0]
                print(f"  First standings entry keys: {list(first.keys())}")
                rows = first.get("rows", [])
                print(f"  rows count: {len(rows)}")
                if rows:
                    print(f"  First row keys: {list(rows[0].keys())}")
                    team_in_row = rows[0].get("team", {})
                    print(f"  First row team fields: {list(team_in_row.keys())}")
        else:
            print(f"  standings is: {type(standings)}")
    print()
