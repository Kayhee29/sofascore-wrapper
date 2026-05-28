"""
init_pb.py — Khởi tạo PocketBase Schema
========================================
Hỗ trợ dữ liệu từ:
  - Sofascore    (live scores, lineups, incidents, player attrs)
  - FBref        (season stats: standard, shooting, passing, defense, gk)
  - Understat    (xG, xA, xGChain, shot events)
  - WhoScored    (player ratings, heatmaps, event stream)
  - ClubElo      (historical Elo ratings)
  - ESPN         (fixtures cross-reference)
"""

import httpx
import json
import sys
import time

PB_URL = "http://127.0.0.1:8090"
ADMIN_EMAIL = "admin@sofascore.local"
ADMIN_PASSWORD = "Admin123456789"


def get_admin_token() -> str:
    """Đăng nhập Admin, tự động thử cả endpoint cũ và mới"""
    endpoints = [
        f"{PB_URL}/api/admins/auth-with-password",                   # PB < v0.22
        f"{PB_URL}/api/collections/_superusers/auth-with-password",  # PB >= v0.22
    ]
    payload = {"identity": ADMIN_EMAIL, "password": ADMIN_PASSWORD}

    for ep in endpoints:
        try:
            resp = httpx.post(ep, json=payload, timeout=5.0)
            if resp.status_code == 200:
                print(f"[INIT] Đăng nhập thành công qua: {ep}")
                return resp.json()["token"]
            else:
                print(f"[INIT] [{resp.status_code}] {ep}")
        except Exception as e:
            print(f"[INIT] Lỗi: {ep}: {e}")

    print("[INIT] Không thể đăng nhập. Kiểm tra ADMIN_EMAIL và ADMIN_PASSWORD.")
    sys.exit(1)


def get_existing_collections(headers: dict) -> dict:
    """Lấy danh sách collections hiện có"""
    resp = httpx.get(f"{PB_URL}/api/collections", headers=headers, params={"perPage": 200}, timeout=5.0)
    if resp.status_code == 200:
        return {c["name"]: c for c in resp.json().get("items", [])}
    return {}


def create_or_update_collection(col_def: dict, existing: dict, headers: dict):
    """Tạo collection mới hoặc bỏ qua nếu đã tồn tại"""
    name = col_def["name"]
    if name in existing:
        print(f"[INIT] Collection '{name}' đã tồn tại — bỏ qua.")
        return

    resp = httpx.post(f"{PB_URL}/api/collections", json=col_def, headers=headers, timeout=10.0)
    if resp.status_code in [200, 201]:
        print(f"[INIT] ✓ Tạo thành công '{name}'")
    else:
        print(f"[INIT] ✗ Lỗi tạo '{name}': {resp.text[:300]}")


# ─────────────────────────────────────────────────────────────
#  SCHEMA DEFINITIONS
# ─────────────────────────────────────────────────────────────

COLLECTIONS = [

    # ── 1. LEAGUES ──────────────────────────────────────────
    {
        "name": "leagues",
        "type": "base",
        "schema": [
            # Sofascore
            {"name": "sofascore_id",   "type": "text", "required": True},
            {"name": "name",           "type": "text"},
            {"name": "country",        "type": "text"},
            {"name": "flag",           "type": "text"},
            {"name": "logo_url",       "type": "text"},
            {"name": "logo_file",      "type": "file", "options": {"maxSelect": 1, "maxSize": 5242880,
                                        "mimeTypes": ["image/png","image/jpeg","image/webp","image/svg+xml"]}},
            # Cross-source IDs
            {"name": "fbref_id",       "type": "text"},  # vd: "ENG-Premier League"
            {"name": "understat_slug", "type": "text"},  # vd: "EPL"
            {"name": "whoscored_id",   "type": "text"},
            {"name": "espn_id",        "type": "text"},
            # Metadata
            {"name": "raw_json",       "type": "json", "options": {"maxSize": 10485760}},
            {"name": "ttl_expired",    "type": "date"},
        ],
        "indexes": [
            "CREATE UNIQUE INDEX idx_league_sofascore_id ON leagues (sofascore_id)"
        ]
    },

    # ── 2. TEAMS ─────────────────────────────────────────────
    {
        "name": "teams",
        "type": "base",
        "schema": [
            # Sofascore
            {"name": "sofascore_id",   "type": "text", "required": True},
            {"name": "name",           "type": "text"},
            {"name": "fullName",       "type": "text"},
            {"name": "managerName",    "type": "text"},
            {"name": "venueName",      "type": "text"},
            {"name": "capacity",       "type": "number"},
            {"name": "logo_url",       "type": "text"},
            {"name": "logo_file",      "type": "file", "options": {"maxSelect": 1, "maxSize": 5242880,
                                        "mimeTypes": ["image/png","image/jpeg","image/webp","image/svg+xml"]}},
            # Cross-source IDs
            {"name": "fbref_id",              "type": "text"},  # FBref squad slug
            {"name": "understat_id",          "type": "text"},
            {"name": "whoscored_id",          "type": "text"},
            {"name": "espn_id",               "type": "text"},
            {"name": "apifootball_id",        "type": "text"},  # api-football.com team ID
            {"name": "apifootball_logo_url",  "type": "text"},  # logo URL từ media.api-sports.io
            # Extra info
            {"name": "country",        "type": "text"},
            {"name": "founded_year",   "type": "number"},
            {"name": "primary_color",  "type": "text"},  # hex
            {"name": "secondary_color","type": "text"},  # hex
            # ClubElo
            {"name": "elo_rating",     "type": "number"},
            {"name": "elo_rank",       "type": "number"},
            {"name": "elo_updated_at", "type": "text"},
            # Cache
            {"name": "raw_json",       "type": "json", "options": {"maxSize": 10485760}},
            {"name": "ttl_expired",    "type": "date"},
        ],
        "indexes": [
            "CREATE UNIQUE INDEX idx_team_sofascore_id ON teams (sofascore_id)"
        ]
    },

    # ── 3. PLAYERS ───────────────────────────────────────────
    {
        "name": "players",
        "type": "base",
        "schema": [
            # Sofascore
            {"name": "sofascore_id",      "type": "text", "required": True},
            {"name": "name",              "type": "text"},
            {"name": "position",          "type": "text"},
            {"name": "height",            "type": "number"},
            {"name": "preferredFoot",     "type": "text"},
            {"name": "marketValue",       "type": "number"},
            {"name": "avatar_url",        "type": "text"},
            {"name": "avatar_file",       "type": "file", "options": {"maxSelect": 1, "maxSize": 5242880,
                                           "mimeTypes": ["image/png","image/jpeg","image/webp"]}},
            {"name": "attributes",        "type": "json", "options": {"maxSize": 10485760}},
            # Cross-source IDs
            {"name": "fbref_id",          "type": "text"},  # FBref player slug
            {"name": "understat_id",      "type": "text"},
            {"name": "whoscored_id",      "type": "text"},
            {"name": "transfermarkt_id",  "type": "text"},
            # Personal info
            {"name": "nationality",       "type": "text"},
            {"name": "date_of_birth",     "type": "text"},
            {"name": "jersey_number",     "type": "number"},
            {"name": "contract_until",    "type": "text"},
            # Denormalized season stats (season hiện tại — update định kỳ)
            {"name": "goals_season",      "type": "number"},
            {"name": "assists_season",    "type": "number"},
            {"name": "appearances_season","type": "number"},
            {"name": "minutes_played",    "type": "number"},
            {"name": "xg_season",         "type": "number"},  # Understat
            {"name": "xa_season",         "type": "number"},  # Understat
            {"name": "xg_chain",          "type": "number"},  # Understat
            {"name": "xg_buildup",        "type": "number"},  # Understat
            {"name": "motm_count",        "type": "number"},  # Sofascore
            {"name": "yellow_cards",      "type": "number"},
            {"name": "red_cards",         "type": "number"},
            {"name": "rating_sofascore",  "type": "number"},  # avg rating SS
            {"name": "rating_whoscored",  "type": "number"},  # avg rating WhoScored
            # Cache
            {"name": "raw_json",          "type": "json", "options": {"maxSize": 10485760}},
            {"name": "ttl_expired",       "type": "date"},
        ],
        "indexes": [
            "CREATE UNIQUE INDEX idx_player_sofascore_id ON players (sofascore_id)"
        ]
    },

    # ── 4. SQUADS (giữ nguyên) ──────────────────────────────
    {
        "name": "squads",
        "type": "base",
        "schema": [
            {"name": "sofascore_id",  "type": "text", "required": True},
            {"name": "players_list",  "type": "json", "options": {"maxSize": 10485760}},
            {"name": "ttl_expired",   "type": "date"},
        ],
        "indexes": [
            "CREATE UNIQUE INDEX idx_squad_sofascore_id ON squads (sofascore_id)"
        ]
    },

    # ── 5. TEAM_TRANSFERS (giữ nguyên) ──────────────────────
    {
        "name": "team_transfers",
        "type": "base",
        "schema": [
            {"name": "sofascore_id",  "type": "text", "required": True},
            {"name": "transfers_json","type": "json", "options": {"maxSize": 10485760}},
            {"name": "ttl_expired",   "type": "date"},
        ],
        "indexes": [
            "CREATE UNIQUE INDEX idx_team_transfers_sofascore_id ON team_transfers (sofascore_id)"
        ]
    },

    # ── 6. MATCHES ───────────────────────────────────────────
    {
        "name": "matches",
        "type": "base",
        "schema": [
            # Cross-source IDs
            {"name": "sofascore_id",    "type": "text"},
            {"name": "fbref_id",        "type": "text"},
            {"name": "understat_id",    "type": "text"},
            {"name": "espn_id",         "type": "text"},
            # Relations
            {"name": "league_id",       "type": "text"},
            {"name": "season",          "type": "text"},   # "2024/25"
            {"name": "round",           "type": "number"},
            # Match info
            {"name": "date",            "type": "text"},   # ISO date "2025-05-04"
            {"name": "start_timestamp", "type": "number"},
            {"name": "home_team_id",    "type": "text"},
            {"name": "away_team_id",    "type": "text"},
            {"name": "home_score",      "type": "number"},
            {"name": "away_score",      "type": "number"},
            {"name": "status",          "type": "text"},   # finished|live|scheduled
            {"name": "winner_code",     "type": "text"},   # home|away|draw
            {"name": "referee",         "type": "text"},
            {"name": "venue",           "type": "text"},
            # Tactical
            {"name": "home_formation",  "type": "text"},
            {"name": "away_formation",  "type": "text"},
            # Stats — Sofascore
            {"name": "home_possession", "type": "number"},
            {"name": "away_possession", "type": "number"},
            {"name": "home_shots",      "type": "number"},
            {"name": "away_shots",      "type": "number"},
            {"name": "home_shots_ot",   "type": "number"},
            {"name": "away_shots_ot",   "type": "number"},
            {"name": "home_fouls",      "type": "number"},
            {"name": "away_fouls",      "type": "number"},
            {"name": "home_corners",    "type": "number"},
            {"name": "away_corners",    "type": "number"},
            {"name": "home_yellow",     "type": "number"},
            {"name": "away_yellow",     "type": "number"},
            {"name": "home_red",        "type": "number"},
            {"name": "away_red",        "type": "number"},
            # xG — Understat / FBref
            {"name": "home_xg",         "type": "number"},
            {"name": "away_xg",         "type": "number"},
            # Full stats blob
            {"name": "match_stats_json","type": "json", "options": {"maxSize": 10485760}},
            {"name": "home_kit",         "type": "json", "options": {"maxSize": 1048576}},
            {"name": "away_kit",         "type": "json", "options": {"maxSize": 1048576}},
            # Source & Cache
            {"name": "source",          "type": "text"},
            {"name": "ttl_expired",     "type": "date"},
        ],
        "indexes": [
            "CREATE UNIQUE INDEX idx_match_sofascore_id ON matches (sofascore_id) WHERE sofascore_id != ''"
        ]
    },

    # ── 7. MATCH_EVENTS ──────────────────────────────────────
    {
        "name": "match_events",
        "type": "base",
        "schema": [
            {"name": "match_id",     "type": "text", "required": True},  # FK matches.sofascore_id
            {"name": "sofascore_id", "type": "text"},
            {"name": "time",         "type": "number"},
            {"name": "added_time",   "type": "number"},
            {"name": "type",         "type": "text"},  # goal|card|sub|var|penalty|miss_penalty
            {"name": "detail",       "type": "text"},  # normal_goal|yellow_card|red_card|etc.
            {"name": "team_id",      "type": "text"},
            {"name": "player_name",  "type": "text"},
            {"name": "assist_name",  "type": "text"},
            {"name": "description",  "type": "text"},
            {"name": "source",       "type": "text"},
        ]
    },

    # ── 8. MATCH_LINEUPS ─────────────────────────────────────
    {
        "name": "match_lineups",
        "type": "base",
        "schema": [
            {"name": "match_id",      "type": "text", "required": True},
            {"name": "team_id",       "type": "text"},
            {"name": "player_id",     "type": "text"},
            {"name": "player_name",   "type": "text"},
            {"name": "jersey_number", "type": "number"},
            {"name": "position",      "type": "text"},
            {"name": "is_starter",    "type": "bool"},
            {"name": "rating",        "type": "number"},  # Sofascore match rating
            {"name": "source",        "type": "text"},
        ]
    },

    # ── 9. PLAYER_SEASON_STATS ───────────────────────────────
    {
        "name": "player_season_stats",
        "type": "base",
        "schema": [
            {"name": "player_id",         "type": "text", "required": True},  # sofascore_id
            {"name": "fbref_id",          "type": "text"},
            {"name": "team_id",           "type": "text"},
            {"name": "league_id",         "type": "text"},
            {"name": "season",            "type": "text"},   # "2024/25"
            {"name": "stat_type",         "type": "text"},   # standard|shooting|passing|defense|gk|playingtime
            {"name": "source",            "type": "text"},   # fbref|understat|whoscored
            # FBref Standard
            {"name": "games",             "type": "number"},
            {"name": "games_starts",      "type": "number"},
            {"name": "minutes",           "type": "number"},
            {"name": "goals",             "type": "number"},
            {"name": "assists",           "type": "number"},
            {"name": "pens_made",         "type": "number"},
            {"name": "pens_att",          "type": "number"},
            {"name": "yellow_cards",      "type": "number"},
            {"name": "red_cards",         "type": "number"},
            {"name": "xg",                "type": "number"},
            {"name": "xg_assist",         "type": "number"},
            {"name": "npxg",              "type": "number"},  # non-penalty xG
            {"name": "progressive_carries","type": "number"},
            {"name": "progressive_passes", "type": "number"},
            {"name": "progressive_passes_received","type": "number"},
            # FBref Shooting
            {"name": "shots",             "type": "number"},
            {"name": "shots_on_target",   "type": "number"},
            {"name": "shot_pct",          "type": "number"},  # shots on target %
            {"name": "goals_per_shot",    "type": "number"},
            # FBref Passing
            {"name": "passes_completed",  "type": "number"},
            {"name": "passes_total",      "type": "number"},
            {"name": "pass_pct",          "type": "number"},
            {"name": "key_passes",        "type": "number"},
            {"name": "passes_into_final_third","type": "number"},
            {"name": "crosses",           "type": "number"},
            # FBref Defense
            {"name": "tackles",           "type": "number"},
            {"name": "tackles_won",       "type": "number"},
            {"name": "interceptions",     "type": "number"},
            {"name": "clearances",        "type": "number"},
            {"name": "blocks",            "type": "number"},
            {"name": "errors",            "type": "number"},
            # FBref GK
            {"name": "gk_shots_on_target_against","type": "number"},
            {"name": "gk_saves",          "type": "number"},
            {"name": "gk_save_pct",       "type": "number"},
            {"name": "gk_clean_sheets",   "type": "number"},
            {"name": "gk_psxg",           "type": "number"},  # post-shot xG
            # Understat
            {"name": "xg_chain",          "type": "number"},
            {"name": "xg_buildup",        "type": "number"},
            # WhoScored / Sofascore
            {"name": "rating",            "type": "number"},
            {"name": "motm_count",        "type": "number"},
            # Raw
            {"name": "raw_json",          "type": "json", "options": {"maxSize": 10485760}},
        ],
        "indexes": [
            "CREATE INDEX idx_pss_player ON player_season_stats (player_id, season, stat_type)"
        ]
    },

    # ── 10. TEAM_SEASON_STATS ────────────────────────────────
    {
        "name": "team_season_stats",
        "type": "base",
        "schema": [
            {"name": "team_id",          "type": "text", "required": True},
            {"name": "league_id",        "type": "text"},
            {"name": "season",           "type": "text"},
            {"name": "stat_type",        "type": "text"},   # standard|shooting|passing|defense|gk
            {"name": "source",           "type": "text"},
            {"name": "goals",            "type": "number"},
            {"name": "goals_against",    "type": "number"},
            {"name": "xg",               "type": "number"},
            {"name": "xg_against",       "type": "number"},
            {"name": "possession_avg",   "type": "number"},
            {"name": "passes_completed", "type": "number"},
            {"name": "pass_pct",         "type": "number"},
            {"name": "shots",            "type": "number"},
            {"name": "shots_on_target",  "type": "number"},
            {"name": "tackles",          "type": "number"},
            {"name": "interceptions",    "type": "number"},
            {"name": "clean_sheets",     "type": "number"},
            {"name": "elo_rating",       "type": "number"},  # ClubElo snapshot
            {"name": "elo_delta",        "type": "number"},  # thay đổi so với tuần trước
            {"name": "raw_json",         "type": "json", "options": {"maxSize": 10485760}},
        ],
        "indexes": [
            "CREATE INDEX idx_tss_team ON team_season_stats (team_id, season, stat_type)"
        ]
    },

    # ── 11. SHOT_EVENTS (Understat) ──────────────────────────
    {
        "name": "shot_events",
        "type": "base",
        "schema": [
            {"name": "match_id",      "type": "text", "required": True},  # sofascore_id of match
            {"name": "understat_id",  "type": "text"},
            {"name": "minute",        "type": "number"},
            {"name": "player_name",   "type": "text"},
            {"name": "player_id",     "type": "text"},
            {"name": "team_id",       "type": "text"},
            {"name": "situation",     "type": "text"},  # OpenPlay|SetPiece|FromCorner|Penalty|DirectFreekick
            {"name": "shot_type",     "type": "text"},  # LeftFoot|RightFoot|Header|OtherBodyPart
            {"name": "result",        "type": "text"},  # Goal|SavedShot|MissedShots|BlockedShot|OwnGoal
            {"name": "x",             "type": "number"},  # tọa độ 0-1 (trên sân)
            {"name": "y",             "type": "number"},
            {"name": "xg",            "type": "number"},
            {"name": "assist_player", "type": "text"},
            {"name": "assist_type",   "type": "text"},   # Assist|ThroughBall|Pull-back|etc.
            {"name": "raw_json",      "type": "json", "options": {"maxSize": 1048576}},
        ],
        "indexes": [
            "CREATE INDEX idx_shot_match ON shot_events (match_id)"
        ]
    },

    # ── 12. CLUB_ELO_HISTORY ────────────────────────────────
    {
        "name": "club_elo_history",
        "type": "base",
        "schema": [
            {"name": "team_id",    "type": "text"},
            {"name": "team_name",  "type": "text"},
            {"name": "date",       "type": "text"},
            {"name": "elo",        "type": "number"},
            {"name": "rank",       "type": "number"},
            {"name": "league_id",  "type": "text"},
        ],
        "indexes": [
            "CREATE INDEX idx_elo_team_date ON club_elo_history (team_id, date)"
        ]
    },

]


# ─────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  PocketBase Schema Init — Multi-Source Football DB")
    print("=" * 55)

    time.sleep(0.5)  # Đợi PB sẵn sàng

    token = get_admin_token()
    headers = {"Authorization": token}

    existing = get_existing_collections(headers)
    print(f"\n[INIT] Collections hiện có: {list(existing.keys())}\n")

    for col in COLLECTIONS:
        create_or_update_collection(col, existing, headers)

    print("\n[INIT] ✓ Hoàn thành khởi tạo toàn bộ Schema!")
    print(f"[INIT] Truy cập Admin UI: {PB_URL}/_/\n")


if __name__ == "__main__":
    main()
