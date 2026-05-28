"""
PocketBase Sync - Multi-Source Football Data Synchronizer
==========================================================
Kiến trúc:
  - FBref/StaticDB  -> Dữ liệu offline: cầu thủ, đội bóng, thống kê lịch sử
  - Sofascore       -> Dữ liệu live:    lịch thi đấu, sự kiện, lineup, livescore

ID Generation:
  - PocketBase yêu cầu ID đúng 15 ký tự, chỉ alphanumeric (a-z, A-Z, 0-9).
  - Sử dụng hashlib.sha256 để tạo ID ổn định từ một chuỗi source_key.
"""

import json
import os
import hashlib
import httpx
import asyncio

# ─────────────────────────────────────────────
#  CẤU HÌNH
# ─────────────────────────────────────────────
PB_URL = "http://127.0.0.1:8090"
ADMIN_EMAIL = "admin@sofascore.local"
ADMIN_PASSWORD = "Admin123456789"  # Đổi cho khớp tài khoản của bạn


# ─────────────────────────────────────────────
#  ID HELPER — Hash ổn định, 15 ký tự alphanumeric
# ─────────────────────────────────────────────
def make_id(source_key: str) -> str:
    """
    Tạo ID đúng định dạng PocketBase từ một chuỗi bất kỳ.
    - Dùng SHA-256, lấy 15 ký tự hex đầu tiên (hex chỉ có 0-9, a-f).
    - Luôn ổn định: cùng source_key → cùng ID.
    - Ví dụ: make_id("team:42") → "3b4a1c2d5e6f7a8"
    """
    digest = hashlib.sha256(source_key.encode("utf-8")).hexdigest()
    return digest[:15]


# ─────────────────────────────────────────────
#  SCHEMA
# ─────────────────────────────────────────────
COLLECTIONS_SCHEMA = [
    {
        "name": "leagues",
        "type": "base",
        "fields": [
            {"name": "source_id",    "type": "text"},
            {"name": "source",       "type": "text"},   # "sofascore" | "fbref"
            {"name": "name",         "type": "text", "required": True},
            {"name": "slug",         "type": "text"},
            {"name": "country",      "type": "text"},
            {"name": "logo_url",     "type": "text"},
        ]
    },
    {
        "name": "seasons",
        "type": "base",
        "fields": [
            {"name": "league_id",    "type": "text"},
            {"name": "source_id",    "type": "text"},
            {"name": "name",         "type": "text", "required": True},
            {"name": "year",         "type": "text"},
        ]
    },
    {
        "name": "teams",
        "type": "base",
        "fields": [
            {"name": "source_id",    "type": "text"},
            {"name": "source",       "type": "text"},
            {"name": "name",         "type": "text", "required": True},
            {"name": "short_name",   "type": "text"},
            {"name": "name_code",    "type": "text"},
            {"name": "slug",         "type": "text"},
            {"name": "venue_name",   "type": "text"},
            {"name": "manager_name", "type": "text"},
            {"name": "logo_url",     "type": "text"},
        ]
    },
    {
        "name": "players",
        "type": "base",
        "fields": [
            {"name": "source_id",       "type": "text"},
            {"name": "source",          "type": "text"},
            {"name": "team_id",         "type": "text"},
            {"name": "name",            "type": "text", "required": True},
            {"name": "jersey_number",   "type": "number"},
            {"name": "position",        "type": "text"},
            {"name": "nationality",     "type": "text"},
            {"name": "age",             "type": "text"},
            {"name": "height",          "type": "number"},
            {"name": "preferred_foot",  "type": "text"},
            {"name": "market_value",    "type": "text"},
            {"name": "image_url",       "type": "text"},
            # Stats cơ bản
            {"name": "goals",           "type": "number"},
            {"name": "assists",         "type": "number"},
            {"name": "appearances",     "type": "number"},
            {"name": "motm_count",      "type": "number"},  # Man of the Match
            {"name": "yellow_cards",    "type": "number"},
            {"name": "red_cards",       "type": "number"},
        ]
    },
    {
        "name": "matches",
        "type": "base",
        "fields": [
            {"name": "source_id",       "type": "text"},
            {"name": "source",          "type": "text"},
            {"name": "season_id",       "type": "text"},
            {"name": "league_id",       "type": "text"},
            {"name": "round",           "type": "number"},
            {"name": "start_timestamp", "type": "number"},
            {"name": "start_datetime",  "type": "text"},
            {"name": "home_team_id",    "type": "text"},
            {"name": "away_team_id",    "type": "text"},
            {"name": "home_score",      "type": "text"},
            {"name": "away_score",      "type": "text"},
            {"name": "status",          "type": "text"},
            {"name": "referee",         "type": "text"},
            {"name": "venue",           "type": "text"},
            {"name": "home_formation",  "type": "text"},
            {"name": "away_formation",  "type": "text"},
            {"name": "home_kit_color",  "type": "json"},
            {"name": "away_kit_color",  "type": "json"},
            # Sofascore live fields
            {"name": "winner_code",     "type": "text"},   # "home" | "away" | "draw"
            {"name": "home_stats",      "type": "json"},   # possession, shots, etc.
            {"name": "away_stats",      "type": "json"},
        ]
    },
    {
        "name": "match_lineups",
        "type": "base",
        "fields": [
            {"name": "match_id",     "type": "text", "required": True},
            {"name": "team_id",      "type": "text"},
            {"name": "player_id",    "type": "text"},
            {"name": "player_name",  "type": "text"},
            {"name": "jersey_number","type": "number"},
            {"name": "position",     "type": "text"},
            {"name": "is_starter",   "type": "bool"},
        ]
    },
    {
        "name": "match_events",
        "type": "base",
        "fields": [
            {"name": "match_id",     "type": "text", "required": True},
            {"name": "time",         "type": "number"},
            {"name": "added_time",   "type": "number"},
            {"name": "type",         "type": "text"},  # goal | card | substitution | var
            {"name": "detail",       "type": "text"},  # normal goal | yellow card | etc.
            {"name": "team_id",      "type": "text"},
            {"name": "player_name",  "type": "text"},
            {"name": "assist_name",  "type": "text"},
            {"name": "description",  "type": "text"},
        ]
    }
]


# ─────────────────────────────────────────────
#  POCKETBASE CLIENT
# ─────────────────────────────────────────────
class PocketBaseClient:
    def __init__(self, pb_url: str):
        self.url = pb_url.rstrip("/")
        self.token = None
        self.auth_header = {}

    async def login(self, email: str, password: str) -> bool:
        """Đăng nhập Admin — hỗ trợ PocketBase v0.22+ (_superusers) và cũ (admins)"""
        async with httpx.AsyncClient(timeout=10.0) as client:
            endpoints = [
                f"{self.url}/api/collections/_superusers/auth-with-password",  # v0.22+
                f"{self.url}/api/admins/auth-with-password",                   # < v0.22
            ]
            payload = {"identity": email, "password": password}

            for ep in endpoints:
                try:
                    res = await client.post(ep, json=payload)
                    if res.status_code == 200:
                        self.token = res.json().get("token")
                        self.auth_header = {"Authorization": self.token}
                        print(f"[OK] Đăng nhập thành công qua: {ep}")
                        return True
                    else:
                        print(f"     [{res.status_code}] {ep}")
                except Exception as e:
                    print(f"     [ERR] {ep}: {e}")

        print("[FAIL] Không thể đăng nhập. Kiểm tra PB_URL, ADMIN_EMAIL, ADMIN_PASSWORD.")
        return False

    async def setup_collections(self):
        """Tạo các collections theo schema nếu chưa tồn tại"""
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(
                f"{self.url}/api/collections",
                headers=self.auth_header,
                params={"perPage": 200}
            )
            if res.status_code != 200:
                print(f"[ERR] Lấy danh sách collections thất bại: {res.text}")
                return

            existing = {c["name"] for c in res.json().get("items", [])}
            for col in COLLECTIONS_SCHEMA:
                col_name = col["name"]
                if col_name in existing:
                    continue
                print(f"  Tạo collection '{col_name}'...")
                cr = await client.post(
                    f"{self.url}/api/collections",
                    headers=self.auth_header,
                    json=col
                )
                if cr.status_code in [200, 201]:
                    print(f"  [OK] '{col_name}' đã tạo.")
                else:
                    print(f"  [ERR] '{col_name}': {cr.text[:200]}")

    async def upsert(self, collection: str, source_key: str, data: dict) -> bool:
        """
        Upsert bản ghi vào PocketBase.
        source_key: chuỗi định danh nguồn (vd: "team:sofascore:42")
                    → được hash thành ID 15 ký tự alphanumeric.
        """
        record_id = make_id(source_key)
        data["id"] = record_id

        async with httpx.AsyncClient(timeout=10.0) as client:
            check = await client.get(
                f"{self.url}/api/collections/{collection}/records/{record_id}",
                headers=self.auth_header
            )

            if check.status_code == 200:
                # UPDATE (PATCH)
                r = await client.patch(
                    f"{self.url}/api/collections/{collection}/records/{record_id}",
                    headers=self.auth_header,
                    json=data
                )
                if r.status_code != 200:
                    print(f"[PATCH ERR] {collection}/{record_id}: {r.text[:200]}")
                    return False
                return True
            elif check.status_code == 404:
                # CREATE (POST)
                r = await client.post(
                    f"{self.url}/api/collections/{collection}/records",
                    headers=self.auth_header,
                    json=data
                )
                if r.status_code not in [200, 201]:
                    print(f"[POST ERR] {collection}/{record_id}: {r.text[:200]}")
                    return False
                return True
            else:
                print(f"[HTTP ERR] Check {collection}/{record_id}: HTTP {check.status_code}")
                return False


# ─────────────────────────────────────────────
#  SYNC FUNCTIONS
# ─────────────────────────────────────────────

async def sync_team(pb: PocketBaseClient, team_id_ss: int, name: str, **kwargs) -> str:
    """Đồng bộ một đội bóng, trả về source_key để dùng làm FK"""
    source_key = f"team:sofascore:{team_id_ss}"
    await pb.upsert("teams", source_key, {
        "sofascore_id": str(team_id_ss),
        "name":        name,
        "fullName":    name,
        "managerName": kwargs.get("manager_name", "Không rõ"),
        "venueName":   kwargs.get("venue_name", "Không rõ"),
        "logo_url":    kwargs.get("logo_url", "")
    })
    return source_key


async def sync_players_from_fbref(pb: PocketBaseClient, squad_file: str, team_source_key: str):
    """Đồng bộ danh sách cầu thủ từ file JSON của FBref"""
    if not os.path.exists(squad_file):
        print(f"[SKIP] Không tìm thấy {squad_file}")
        return

    with open(squad_file, "r", encoding="utf-8") as f:
        players = json.load(f)

    count = 0
    for p in players:
        player_name = p.get("Player", "Unknown")
        # Source key duy nhất: tên + quốc tịch để tránh trùng
        source_key = f"player:fbref:{team_source_key}:{player_name}"
        player_pb_id = make_id(source_key)
        await pb.upsert("players", source_key, {
            "sofascore_id":   player_pb_id,
            "name":        player_name,
            "nationality": p.get("Nation", ""),
            "position":    p.get("Position", ""),
            "jersey_number": int(p.get("Jersey", 0)) if p.get("Jersey") else None,
            "fbref_id":    player_name.lower().replace(" ", "_")
        })
        count += 1

    print(f"  [OK] Đồng bộ {count} cầu thủ từ FBref.")


async def sync_fixtures_from_sofascore(pb: PocketBaseClient, fixtures_file: str, arsenal_team_key: str):
    """Đồng bộ lịch thi đấu từ file JSON Sofascore"""
    if not os.path.exists(fixtures_file):
        print(f"[SKIP] Không tìm thấy {fixtures_file}")
        return

    with open(fixtures_file, "r", encoding="utf-8") as f:
        fixtures = json.load(f)

    count = 0
    for fix in fixtures:
        match_id = fix.get("id")
        source_key = f"match:sofascore:{match_id}"

        # Xác định đội nhà / khách
        if fix.get("home_team") == "Arsenal":
            home_key = arsenal_team_key
            away_key = f"team:sofascore:{fix.get('away_team', 'unknown').lower()}"
        else:
            home_key = f"team:sofascore:{fix.get('home_team', 'unknown').lower()}"
            away_key = arsenal_team_key

        home_score = fix.get("home_score")
        away_score = fix.get("away_score")
        try:
            home_score = int(home_score) if home_score not in (None, "", "-") else None
        except ValueError:
            home_score = None
        try:
            away_score = int(away_score) if away_score not in (None, "", "-") else None
        except ValueError:
            away_score = None

        await pb.upsert("matches", source_key, {
            "sofascore_id":    str(match_id),
            "start_timestamp": fix.get("timestamp", 0),
            "home_team_id":    make_id(home_key),
            "away_team_id":    make_id(away_key),
            "home_score":      home_score,
            "away_score":      away_score,
            "status":          fix.get("status_type", ""),
            "source":          "sofascore",
        })
        count += 1

    print(f"  [OK] Đồng bộ {count} fixtures từ Sofascore.")


async def sync_match_details(pb: PocketBaseClient, details_file: str):
    """Đồng bộ sự kiện chi tiết (goals, cards, substitutions) của một trận đấu"""
    if not os.path.exists(details_file):
        print(f"[SKIP] Không tìm thấy {details_file}")
        return

    with open(details_file, "r", encoding="utf-8") as f:
        details = json.load(f)

    match_id_ss = str(details.get("match_id", "unknown"))
    match_pb_id = make_id(f"match:sofascore:{match_id_ss}")

    incidents = details.get("incidents", {})
    count = 0

    # Bàn thắng
    for idx, g in enumerate(incidents.get("goals", [])):
        source_key = f"event:goal:{match_id_ss}:{idx}"
        await pb.upsert("match_events", source_key, {
            "match_id":    match_pb_id,
            "type":        "goal",
            "description": str(g),
            "source":      "sofascore",
        })
        count += 1

    # Thẻ phạt
    for idx, c in enumerate(incidents.get("cards", [])):
        source_key = f"event:card:{match_id_ss}:{idx}"
        await pb.upsert("match_events", source_key, {
            "match_id":    match_pb_id,
            "type":        "card",
            "description": str(c),
            "source":      "sofascore",
        })
        count += 1

    # Thay người
    for idx, s in enumerate(incidents.get("substitutions", [])):
        source_key = f"event:sub:{match_id_ss}:{idx}"
        await pb.upsert("match_events", source_key, {
            "match_id":    match_pb_id,
            "type":        "substitution",
            "description": str(s),
            "source":      "sofascore",
        })
        count += 1

    print(f"  [OK] Đồng bộ {count} sự kiện trận {match_id_ss}.")


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
async def main():
    print("=" * 50)
    print("  PocketBase Multi-Source Sync")
    print("=" * 50)

    pb = PocketBaseClient(PB_URL)
    if not await pb.login(ADMIN_EMAIL, ADMIN_PASSWORD):
        return

    print("\n[1/4] Thiết lập Schema Collections...")
    await pb.setup_collections()

    print("\n[2/4] Đồng bộ Đội bóng Arsenal (Sofascore)...")
    arsenal_key = await sync_team(
        pb,
        team_id_ss=42,
        name="Arsenal",
        short_name="Arsenal",
        name_code="ARS",
        slug="arsenal",
        venue_name="Emirates Stadium",
        manager_name="Mikel Arteta",
        logo_url="https://img.sofascore.com/api/v1/team/42/image"
    )
    arsenal_pb_id = make_id(arsenal_key)
    print(f"  Arsenal PocketBase ID: {arsenal_pb_id}")

    print("\n[3/4] Đồng bộ Cầu thủ Arsenal (FBref)...")
    await sync_players_from_fbref(pb, "arsenal_fbref_squad.json", arsenal_key)

    print("\n[4/4] Đồng bộ Lịch thi đấu & Sự kiện (Sofascore)...")
    await sync_fixtures_from_sofascore(pb, "arsenal_may_fixtures.json", arsenal_key)
    await sync_match_details(pb, "match_14023942_details.json")

    print("\n" + "=" * 50)
    print("  SYNC HOÀN TẤT")
    print("=" * 50)
    print(f"\nKiểm tra dữ liệu tại: {PB_URL}/_/")


if __name__ == "__main__":
    asyncio.run(main())
