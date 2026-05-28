"""
sync_apifootball_ids.py
========================
Script đồng bộ mapping giữa Sofascore ID và api-football ID cho collection `teams`.

Quy trình:
  1. Lấy bảng xếp hạng PL từ api-football (có logo URL + api-football team ID)
  2. So khớp với teams trong PocketBase theo tên đội (fuzzy match)
  3. Patch PocketBase: điền apifootball_id + apifootball_logo_url
  4. Thêm field vào schema nếu chưa có (patch collection schema live)

Chạy:
  python sync_apifootball_ids.py
  python sync_apifootball_ids.py --dry-run   # xem kết quả match mà không ghi
  python sync_apifootball_ids.py --force      # ghi đè cả record đã có apifootball_id
"""

import asyncio
import argparse
import httpx
import json
import sys
import re
from pathlib import Path
from typing import Optional

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

import os
sys.path.insert(0, str(Path(__file__).parent))

from api_football_client import ApiFootballClient, KeyRotationManager

# ─────────────────────────────────
PB_URL        = "http://127.0.0.1:8090"
ADMIN_EMAIL   = "admin@sofascore.local"
ADMIN_PASS    = "Admin123456789"
PL_LEAGUE_ID  = int(os.getenv("APIFOOTBALL_PL_LEAGUE_ID", "39"))
PL_SEASON     = int(os.getenv("APIFOOTBALL_PL_SEASON", "2024"))
# ─────────────────────────────────


def get_admin_token() -> str:
    for ep in [
        f"{PB_URL}/api/admins/auth-with-password",
        f"{PB_URL}/api/collections/_superusers/auth-with-password",
    ]:
        try:
            r = httpx.post(ep, json={"identity": ADMIN_EMAIL, "password": ADMIN_PASS}, timeout=5)
            if r.status_code == 200:
                return r.json()["token"]
        except Exception:
            pass
    print("[ERROR] Không thể đăng nhập PocketBase Admin")
    sys.exit(1)


def ensure_fields_exist(headers: dict) -> None:
    """Thêm apifootball_id và apifootball_logo_url vào schema teams nếu chưa có."""
    r = httpx.get(f"{PB_URL}/api/collections/teams", headers=headers, timeout=5)
    if r.status_code != 200:
        print(f"[WARN] Không lấy được schema teams: {r.text[:200]}")
        return

    col = r.json()
    existing_fields = {f["name"] for f in col.get("schema", col.get("fields", []))}
    new_fields = []

    if "apifootball_id" not in existing_fields:
        new_fields.append({"name": "apifootball_id", "type": "text"})
        print("[SCHEMA] Sẽ thêm field: apifootball_id")

    if "apifootball_logo_url" not in existing_fields:
        new_fields.append({"name": "apifootball_logo_url", "type": "text"})
        print("[SCHEMA] Sẽ thêm field: apifootball_logo_url")

    if not new_fields:
        print("[SCHEMA] Cả 2 field đã tồn tại trong schema.")
        return

    # Patch schema: merge với fields hiện tại
    current_fields = col.get("schema", col.get("fields", []))
    updated_fields = current_fields + new_fields

    # Thử cả hai cấu trúc API (PB < v0.22 dùng "schema", >= v0.22 dùng "fields")
    patch_body = dict(col)
    if "schema" in col:
        patch_body["schema"] = updated_fields
    else:
        patch_body["fields"] = updated_fields

    pr = httpx.patch(
        f"{PB_URL}/api/collections/{col['id']}",
        json=patch_body,
        headers=headers,
        timeout=10,
    )
    if pr.status_code in [200, 201]:
        print(f"[SCHEMA] ✅ Đã thêm {len(new_fields)} field(s) vào collection 'teams'")
    else:
        print(f"[SCHEMA] ⚠️  Patch schema thất bại: {pr.status_code} {pr.text[:300]}")


def normalize_name(name: str) -> str:
    """Chuẩn hóa tên đội để so khớp mờ (dùng làm fallback)."""
    name = name.lower().strip()
    for suffix in ["fc", "afc", "united", "city", "town", "rovers", "wanderers", "athletic", "albion"]:
        name = re.sub(rf"\b{suffix}\b", "", name)
    name = re.sub(r"[^a-z0-9 ]", "", name)
    return re.sub(r"\s+", " ", name).strip()


# Bảng mapping Sofascore ID → api-football ID
# Nguồn: so sánh kết quả standings 2 API với nhau
SOFASCORE_TO_APIFB: dict[str, str] = {
    # Premier League 2024/25
    "44": "40",   # Liverpool FC
    "42": "42",   # Arsenal
    "17": "50",   # Manchester City
    "38": "49",   # Chelsea
    "39": "34",   # Newcastle United
    "40": "66",   # Aston Villa
    "14": "65",   # Nottingham Forest
    "30": "51",   # Brighton & Hove Albion
    "60": "35",   # Bournemouth
    "50": "55",   # Brentford
    "43": "36",   # Fulham
     "7": "52",   # Crystal Palace
    "48": "45",   # Everton
    "37": "48",   # West Ham United
    "35": "33",   # Manchester United
     "3": "39",   # Wolverhampton
    "33": "47",   # Tottenham Hotspur
    "31": "46",   # Leicester City
    "57": "57",   # Ipswich Town
    "41": "41",   # Southampton
    # Championship / Relegated teams có trong PB
    "34": "34",   # Leeds United
     "6": "6",    # Burnley
    "41": "41",   # Sunderland
}


def get_apifb_info(sofascore_id: str, api_rows: list) -> Optional[dict]:
    """
    Tra cứu api-football team info cho một sofascore_id.
    Ưu tiên mapping cứng, fallback sang fuzzy match tên.
    """
    apifb_id = SOFASCORE_TO_APIFB.get(sofascore_id)
    if apifb_id:
        for row in api_rows:
            t = row.get("team", {})
            if str(t.get("id", "")) == apifb_id:
                return {"apifootball_id": apifb_id, "logo": t.get("logo", "")}
    return None


def fuzzy_match_api(api_name: str, pb_teams: list) -> Optional[object]:
    """Fuzzy match tên api-football → PB record (chỉ dùng khi không có trong mapping cứng)."""
    api_norm = normalize_name(api_name)
    for t in pb_teams:
        for field in ["name", "fullName"]:
            val = t.__dict__.get(field, "")
            if val and normalize_name(val) == api_norm:
                return t
    for t in pb_teams:
        pb_norm = normalize_name(t.__dict__.get("name", ""))
        if api_norm in pb_norm or pb_norm in api_norm:
            return t
    return None


async def main(dry_run: bool = False, force: bool = False):
    print("=" * 60)
    print("sync_apifootball_ids — Đồng bộ api-football ID → PocketBase")
    print("=" * 60)

    # 1. Lấy token PocketBase
    token = get_admin_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    print(f"[PB] Đăng nhập thành công")

    # 2. Đảm bảo fields tồn tại trong schema
    if not dry_run:
        ensure_fields_exist(headers)

    # 3. Lấy tất cả teams từ PocketBase
    r = httpx.get(
        f"{PB_URL}/api/collections/teams/records",
        headers=headers,
        params={"perPage": 500, "fields": "id,sofascore_id,name,fullName,apifootball_id"},
        timeout=10,
    )
    if r.status_code != 200:
        print(f"[ERROR] Không lấy được teams từ PB: {r.text[:300]}")
        sys.exit(1)

    pb_teams_raw = r.json().get("items", [])

    # Wrap thành objects để dùng getattr
    class TeamRecord:
        def __init__(self, d):
            self.__dict__.update(d)

    pb_teams = [TeamRecord(t) for t in pb_teams_raw]
    print(f"[PB] Có {len(pb_teams)} đội trong database")

    # 4. Lấy standings từ api-football
    client = ApiFootballClient()
    print(f"[API] Đang lấy bảng xếp hạng League {PL_LEAGUE_ID} Season {PL_SEASON}...")
    data = await client.get_standings(league_id=PL_LEAGUE_ID, season=PL_SEASON)
    api_standings = data.get("response", [])

    if not api_standings:
        print("[ERROR] Không có dữ liệu standings từ api-football. Kiểm tra API key.")
        sys.exit(1)

    api_rows = api_standings[0].get("league", {}).get("standings", [[]])[0]
    print(f"[API] Nhận được {len(api_rows)} đội từ api-football")

    # 5. Match và patch — duyệt PB teams, tra cứu mapping cứng theo sofascore_id
    print("\n── Kết quả match ────────────────────────────────────────")
    matched = 0
    skipped = 0
    failed  = 0

    # Build index api_rows theo api-football team id để lookup nhanh logo
    api_teams_by_id = {}
    for row in api_rows:
        t = row.get("team", {})
        api_teams_by_id[str(t.get("id", ""))] = t

    for pb_team in pb_teams:
        sofascore_id = pb_team.__dict__.get("sofascore_id", "")
        pb_name      = pb_team.__dict__.get("name", "")

        apifb_id = SOFASCORE_TO_APIFB.get(sofascore_id)
        if not apifb_id:
            print(f"  ⚠️  NO MAP   | {pb_name:<30} (sofascore_id={sofascore_id}) — chưa có trong bảng mapping")
            failed += 1
            continue

        existing = pb_team.__dict__.get("apifootball_id", "")
        if existing and not force:
            print(f"  ⏭  SKIP     | {pb_name:<30} (đã có apifootball_id={existing})")
            skipped += 1
            continue

        api_team = api_teams_by_id.get(apifb_id, {})
        api_logo = api_team.get("logo", f"https://media.api-sports.io/football/teams/{apifb_id}.png")
        api_name = api_team.get("name", "")

        print(f"  ✅ MAP      | {pb_name:<30} → apifb_id={apifb_id} ({api_name}) | logo={api_logo[:60]}...")
        matched += 1

        if dry_run:
            continue

        patch = {"apifootball_id": apifb_id, "apifootball_logo_url": api_logo}
        pr = httpx.patch(
            f"{PB_URL}/api/collections/teams/records/{pb_team.__dict__['id']}",
            json=patch,
            headers=headers,
            timeout=5,
        )
        if pr.status_code in [200, 201]:
            print(f"             └─ Saved ✓")
        else:
            print(f"             └─ Lỗi: {pr.status_code} {pr.text[:150]}")

    print("\n── Tổng kết ─────────────────────────────────────────────")
    print(f"  ✅ Đã match & cập nhật : {matched}")
    print(f"  ⏭  Đã bỏ qua (đã có)  : {skipped}")
    print(f"  ❌ Không match được    : {failed}")
    if dry_run:
        print("\n  [DRY-RUN] Không có thay đổi nào được ghi vào database.")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync api-football IDs into PocketBase teams collection")
    parser.add_argument("--dry-run", action="store_true", help="Xem kết quả match mà không ghi database")
    parser.add_argument("--force",   action="store_true", help="Ghi đè ngay cả khi record đã có apifootball_id")
    args = parser.parse_args()

    asyncio.run(main(dry_run=args.dry_run, force=args.force))
