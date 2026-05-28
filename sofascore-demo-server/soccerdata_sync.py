"""
soccerdata_sync.py — Đồng bộ dữ liệu offline từ soccerdata vào PocketBase
===========================================================================
Yêu cầu:
    pip install soccerdata

Cách dùng:
    python soccerdata_sync.py

Chịu trách nhiệm:
    - FBref    : schedule, player_season_stats, team_season_stats (tất cả stat_type)
    - Understat: player stats (xG, xGChain), shot events từng trận
    - ClubElo  : lịch sử Elo rating từng đội

NOTE: soccerdata sẽ cache HTML vào thư mục local (~/.soccerdata/)
      để tránh scrape lại mỗi lần chạy.
"""

import hashlib
import json
import sys
import time
import datetime
from typing import Optional

import httpx

# ─── CONFIG ──────────────────────────────────────────────────
PB_URL           = "http://127.0.0.1:8090"
ADMIN_EMAIL      = "admin@sofascore.local"
ADMIN_PASSWORD   = "Admin123456789"

# Mapping giải đấu: sofascore_id -> soccerdata identifiers
LEAGUE_MAP = {
    "17":   {"fbref": "ENG-Premier League",  "understat": "EPL",       "name": "Premier League"},
    "8":    {"fbref": "ESP-La Liga",          "understat": "La_liga",   "name": "La Liga"},
    "35":   {"fbref": "DEU-Bundesliga",       "understat": "Bundesliga","name": "Bundesliga"},
    "23":   {"fbref": "FRA-Ligue 1",          "understat": "Ligue_1",   "name": "Ligue 1"},
    "132":  {"fbref": "ITA-Serie A",          "understat": "Serie_A",   "name": "Serie A"},
    "7":    {"fbref": "EUR-Champions League", "understat": None,        "name": "Champions League"},
}

# Mùa giải mặc định
DEFAULT_SEASON = "2025"  # soccerdata dùng "2025" cho 2024/25

FBREF_STAT_TYPES = [
    "standard",
    "shooting",
    "passing",
    "defense",
    "gk",
    "playingtime",
]


# ─── ID HELPER ───────────────────────────────────────────────
def make_id(source_key: str) -> str:
    """Hash ổn định → 15 ký tự alphanumeric cho PocketBase"""
    return hashlib.sha256(source_key.encode()).hexdigest()[:15]


# ─── POCKETBASE CLIENT ───────────────────────────────────────
class PBClient:
    def __init__(self, url: str, email: str, password: str):
        self.url = url.rstrip("/")
        self.token = None
        self._login(email, password)

    def _login(self, email: str, password: str):
        for ep in [
            f"{self.url}/api/admins/auth-with-password",
            f"{self.url}/api/collections/_superusers/auth-with-password",
        ]:
            try:
                r = httpx.post(ep, json={"identity": email, "password": password}, timeout=5.0)
                if r.status_code == 200:
                    self.token = r.json()["token"]
                    print(f"[PB] Đăng nhập OK: {ep}")
                    return
            except Exception:
                pass
        print("[PB] FAIL: Không thể đăng nhập PocketBase!")
        sys.exit(1)

    @property
    def _headers(self):
        return {"Authorization": self.token, "Content-Type": "application/json"}

    def upsert(self, collection: str, source_key: str, data: dict) -> bool:
        """Upsert bản ghi theo source_key (hash → 15-char ID)"""
        rec_id = make_id(source_key)
        data = {**data, "id": rec_id}

        with httpx.Client(timeout=10.0) as client:
            check = client.get(
                f"{self.url}/api/collections/{collection}/records/{rec_id}",
                headers=self._headers
            )
            if check.status_code == 200:
                r = client.patch(
                    f"{self.url}/api/collections/{collection}/records/{rec_id}",
                    headers=self._headers, json=data
                )
            elif check.status_code == 404:
                r = client.post(
                    f"{self.url}/api/collections/{collection}/records",
                    headers=self._headers, json=data
                )
            else:
                print(f"[PB] ERR check {collection}/{rec_id}: HTTP {check.status_code}")
                return False

        if r.status_code not in [200, 201]:
            print(f"[PB] ERR upsert {collection}: {r.text[:200]}")
            return False
        return True


# ─── HELPERS ─────────────────────────────────────────────────

def safe_float(val) -> Optional[float]:
    """Chuyển đổi an toàn sang float, trả về None nếu NaN/None"""
    try:
        import math
        v = float(val)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def safe_int(val) -> Optional[int]:
    f = safe_float(val)
    return int(f) if f is not None else None


def df_row_to_dict(row) -> dict:
    """Chuyển pandas Series row thành dict JSON-serializable"""
    result = {}
    for k, v in row.items():
        if hasattr(v, 'item'):  # numpy scalar
            v = v.item()
        try:
            import math
            if isinstance(v, float) and math.isnan(v):
                v = None
        except (TypeError, ValueError):
            pass
        result[str(k)] = v
    return result


# ─── FBREF SYNC ──────────────────────────────────────────────

def sync_fbref_schedule(pb: PBClient, league_ss_id: str, season: str = DEFAULT_SEASON):
    """Sync lịch thi đấu + kết quả từ FBref vào collection 'matches'"""
    try:
        import soccerdata as sd
    except ImportError:
        print("[ERR] Chưa cài soccerdata. Chạy: pip install soccerdata")
        return

    league_info = LEAGUE_MAP.get(league_ss_id)
    if not league_info:
        print(f"[SKIP] Không có mapping FBref cho league SS ID {league_ss_id}")
        return

    fbref_league = league_info["fbref"]
    print(f"\n[FBref] Sync schedule: {fbref_league} {season}...")

    try:
        fbref = sd.FBref(fbref_league, season)
        schedule = fbref.read_schedule()
    except Exception as e:
        print(f"[ERR] FBref schedule: {e}")
        return

    count = 0
    for idx, row in schedule.iterrows():
        row_dict = df_row_to_dict(row)

        # FBref game_id thường là dạng "2024-2025-...-..."
        fbref_game_id = str(row_dict.get("game_id", idx))
        source_key = f"match:fbref:{fbref_league}:{season}:{fbref_game_id}"

        home_score = row_dict.get("score_home")
        away_score = row_dict.get("score_away")
        date_val = row_dict.get("date")

        data = {
            "fbref_id":       fbref_game_id,
            "league_id":      league_ss_id,
            "season":         f"{int(season)-1}/{season[-2:]}",
            "round":          safe_int(row_dict.get("round")),
            "date":           str(date_val) if date_val else None,
            "home_team_id":   str(row_dict.get("home_team", "")),
            "away_team_id":   str(row_dict.get("away_team", "")),
            "home_score":     safe_int(home_score),
            "away_score":     safe_int(away_score),
            "status":         "finished" if home_score is not None else "scheduled",
            "home_xg":        safe_float(row_dict.get("home_xg")),
            "away_xg":        safe_float(row_dict.get("away_xg")),
            "venue":          str(row_dict.get("venue", "")),
            "referee":        str(row_dict.get("referee", "")),
            "source":         "fbref",
            "raw_json":       row_dict,
        }

        if pb.upsert("matches", source_key, data):
            count += 1

    print(f"[FBref] ✓ Synced {count} matches cho {fbref_league} {season}")


def sync_fbref_player_stats(pb: PBClient, league_ss_id: str, season: str = DEFAULT_SEASON):
    """Sync tất cả stat_type cầu thủ từ FBref vào 'player_season_stats'"""
    try:
        import soccerdata as sd
    except ImportError:
        print("[ERR] Chưa cài soccerdata.")
        return

    league_info = LEAGUE_MAP.get(league_ss_id)
    if not league_info:
        return

    fbref_league = league_info["fbref"]

    for stat_type in FBREF_STAT_TYPES:
        print(f"\n[FBref] Sync player stats '{stat_type}': {fbref_league} {season}...")
        try:
            fbref = sd.FBref(fbref_league, season)
            df = fbref.read_player_season_stats(stat_type=stat_type)
        except Exception as e:
            print(f"[WARN] {stat_type}: {e}")
            continue

        count = 0
        for idx, row in df.iterrows():
            row_dict = df_row_to_dict(row)

            # Index của FBref thường là MultiIndex (league, team, player)
            player_name = str(row_dict.get("player", idx[-1] if hasattr(idx, '__len__') else idx))
            team_name = str(row_dict.get("team", ""))
            source_key = f"pss:fbref:{fbref_league}:{season}:{stat_type}:{player_name}:{team_name}"

            data = {
                "fbref_id":       player_name.lower().replace(" ", "_"),
                "team_id":        team_name,
                "league_id":      league_ss_id,
                "season":         f"{int(season)-1}/{season[-2:]}",
                "stat_type":      stat_type,
                "source":         "fbref",
                # Standard
                "games":          safe_int(row_dict.get("games")),
                "games_starts":   safe_int(row_dict.get("games_starts")),
                "minutes":        safe_int(row_dict.get("minutes")),
                "goals":          safe_int(row_dict.get("goals")),
                "assists":        safe_int(row_dict.get("assists")),
                "pens_made":      safe_int(row_dict.get("pens_made")),
                "pens_att":       safe_int(row_dict.get("pens_att")),
                "yellow_cards":   safe_int(row_dict.get("cards_yellow")),
                "red_cards":      safe_int(row_dict.get("cards_red")),
                "xg":             safe_float(row_dict.get("xg")),
                "xg_assist":      safe_float(row_dict.get("xg_assist")),
                "npxg":           safe_float(row_dict.get("npxg")),
                "progressive_carries": safe_int(row_dict.get("progressive_carries")),
                "progressive_passes":  safe_int(row_dict.get("progressive_passes")),
                # Shooting
                "shots":          safe_int(row_dict.get("shots")),
                "shots_on_target":safe_int(row_dict.get("shots_on_target")),
                "shot_pct":       safe_float(row_dict.get("shots_on_target_pct")),
                "goals_per_shot": safe_float(row_dict.get("goals_per_shot")),
                # Passing
                "passes_completed": safe_int(row_dict.get("passes_completed")),
                "passes_total":   safe_int(row_dict.get("passes")),
                "pass_pct":       safe_float(row_dict.get("passes_pct")),
                "key_passes":     safe_int(row_dict.get("assisted_shots")),  # key passes
                "crosses":        safe_int(row_dict.get("crosses")),
                # Defense
                "tackles":        safe_int(row_dict.get("tackles")),
                "tackles_won":    safe_int(row_dict.get("tackles_won")),
                "interceptions":  safe_int(row_dict.get("interceptions")),
                "clearances":     safe_int(row_dict.get("clearances")),
                "blocks":         safe_int(row_dict.get("blocks")),
                "errors":         safe_int(row_dict.get("errors")),
                # GK
                "gk_shots_on_target_against": safe_int(row_dict.get("gk_shots_on_target_against")),
                "gk_saves":       safe_int(row_dict.get("gk_saves")),
                "gk_save_pct":    safe_float(row_dict.get("gk_save_pct")),
                "gk_clean_sheets":safe_int(row_dict.get("gk_clean_sheets")),
                "gk_psxg":        safe_float(row_dict.get("gk_psxg")),
                "raw_json":       row_dict,
            }

            if pb.upsert("player_season_stats", source_key, data):
                count += 1

        print(f"[FBref] ✓ {stat_type}: {count} cầu thủ")
        time.sleep(2)  # Tránh rate limit


def sync_fbref_team_stats(pb: PBClient, league_ss_id: str, season: str = DEFAULT_SEASON):
    """Sync stats đội từ FBref vào 'team_season_stats'"""
    try:
        import soccerdata as sd
    except ImportError:
        return

    league_info = LEAGUE_MAP.get(league_ss_id)
    if not league_info:
        return

    fbref_league = league_info["fbref"]

    for stat_type in ["standard", "shooting", "passing", "defense", "gk"]:
        print(f"\n[FBref] Sync team stats '{stat_type}': {fbref_league} {season}...")
        try:
            fbref = sd.FBref(fbref_league, season)
            df = fbref.read_team_season_stats(stat_type=stat_type)
        except Exception as e:
            print(f"[WARN] {stat_type}: {e}")
            continue

        count = 0
        for idx, row in df.iterrows():
            row_dict = df_row_to_dict(row)
            team_name = str(row_dict.get("team", idx if isinstance(idx, str) else str(idx)))
            source_key = f"tss:fbref:{fbref_league}:{season}:{stat_type}:{team_name}"

            data = {
                "team_id":        team_name,
                "league_id":      league_ss_id,
                "season":         f"{int(season)-1}/{season[-2:]}",
                "stat_type":      stat_type,
                "source":         "fbref",
                "goals":          safe_int(row_dict.get("goals")),
                "goals_against":  safe_int(row_dict.get("goals_against")),
                "xg":             safe_float(row_dict.get("xg")),
                "xg_against":     safe_float(row_dict.get("xg_against")),
                "possession_avg": safe_float(row_dict.get("possession")),
                "shots":          safe_int(row_dict.get("shots")),
                "shots_on_target":safe_int(row_dict.get("shots_on_target")),
                "passes_completed":safe_int(row_dict.get("passes_completed")),
                "pass_pct":       safe_float(row_dict.get("passes_pct")),
                "tackles":        safe_int(row_dict.get("tackles")),
                "interceptions":  safe_int(row_dict.get("interceptions")),
                "clean_sheets":   safe_int(row_dict.get("gk_clean_sheets")),
                "raw_json":       row_dict,
            }

            if pb.upsert("team_season_stats", source_key, data):
                count += 1

        print(f"[FBref] ✓ {stat_type}: {count} đội")
        time.sleep(2)


# ─── UNDERSTAT SYNC ──────────────────────────────────────────

def sync_understat_player_stats(pb: PBClient, league_ss_id: str, season: str = DEFAULT_SEASON):
    """Sync xG, xGChain, xGBuildup cầu thủ từ Understat"""
    try:
        import soccerdata as sd
    except ImportError:
        return

    league_info = LEAGUE_MAP.get(league_ss_id)
    understat_league = (league_info or {}).get("understat")
    if not understat_league:
        print(f"[SKIP] Không có Understat mapping cho SS ID {league_ss_id}")
        return

    understat_season = str(int(season) - 1)  # Understat dùng năm bắt đầu (2024 cho 2024/25)
    print(f"\n[Understat] Sync player stats: {understat_league} {understat_season}...")

    try:
        us = sd.Understat(understat_league, understat_season)
        df = us.read_player_season_stats()
    except Exception as e:
        print(f"[ERR] Understat: {e}")
        return

    count = 0
    for idx, row in df.iterrows():
        row_dict = df_row_to_dict(row)
        player_name = str(row_dict.get("player", idx))
        source_key = f"pss:understat:{understat_league}:{understat_season}:{player_name}"

        data = {
            "fbref_id":     player_name.lower().replace(" ", "_"),
            "league_id":    league_ss_id,
            "season":       f"{understat_season}/{str(int(understat_season)+1)[-2:]}",
            "stat_type":    "understat",
            "source":       "understat",
            "games":        safe_int(row_dict.get("games")),
            "minutes":      safe_int(row_dict.get("time")),
            "goals":        safe_int(row_dict.get("goals")),
            "assists":      safe_int(row_dict.get("assists")),
            "xg":           safe_float(row_dict.get("xG")),
            "xg_assist":    safe_float(row_dict.get("xA")),
            "xg_chain":     safe_float(row_dict.get("xGChain")),
            "xg_buildup":   safe_float(row_dict.get("xGBuildup")),
            "shots":        safe_int(row_dict.get("shots")),
            "yellow_cards": safe_int(row_dict.get("yellow_cards")),
            "red_cards":    safe_int(row_dict.get("red_cards")),
            "raw_json":     row_dict,
        }

        if pb.upsert("player_season_stats", source_key, data):
            count += 1

    print(f"[Understat] ✓ {count} cầu thủ")


def sync_understat_shot_events(pb: PBClient, league_ss_id: str, season: str = DEFAULT_SEASON):
    """Sync shot events (shot map) từ Understat vào 'shot_events'"""
    try:
        import soccerdata as sd
    except ImportError:
        return

    league_info = LEAGUE_MAP.get(league_ss_id)
    understat_league = (league_info or {}).get("understat")
    if not understat_league:
        return

    understat_season = str(int(season) - 1)
    print(f"\n[Understat] Sync shot events: {understat_league} {understat_season}...")

    try:
        us = sd.Understat(understat_league, understat_season)
        df = us.read_shot_events()
    except Exception as e:
        print(f"[ERR] Understat shot events: {e}")
        return

    count = 0
    for idx, row in df.iterrows():
        row_dict = df_row_to_dict(row)
        understat_id = str(row_dict.get("id", idx))
        match_id = str(row_dict.get("match_id", ""))
        source_key = f"shot:understat:{understat_id}"

        data = {
            "match_id":      match_id,
            "understat_id":  understat_id,
            "minute":        safe_int(row_dict.get("minute")),
            "player_name":   str(row_dict.get("player", "")),
            "team_id":       str(row_dict.get("team", "")),
            "situation":     str(row_dict.get("situation", "")),
            "shot_type":     str(row_dict.get("shotType", "")),
            "result":        str(row_dict.get("result", "")),
            "x":             safe_float(row_dict.get("X")),
            "y":             safe_float(row_dict.get("Y")),
            "xg":            safe_float(row_dict.get("xG")),
            "assist_player": str(row_dict.get("player_assisted", "")),
            "assist_type":   str(row_dict.get("lastAction", "")),
            "raw_json":      row_dict,
        }

        if pb.upsert("shot_events", source_key, data):
            count += 1

    print(f"[Understat] ✓ {count} shot events")


# ─── CLUBELO SYNC ────────────────────────────────────────────

def sync_clubelo(pb: PBClient, team_name: str, team_ss_id: str):
    """Sync lịch sử Elo rating của một đội từ ClubElo"""
    try:
        import soccerdata as sd
    except ImportError:
        return

    print(f"\n[ClubElo] Sync Elo history: {team_name}...")

    try:
        elo = sd.ClubElo()
        df = elo.read_by_team(team_name)
    except Exception as e:
        print(f"[ERR] ClubElo {team_name}: {e}")
        return

    count = 0
    for idx, row in df.iterrows():
        row_dict = df_row_to_dict(row)
        date_val = str(row_dict.get("date", idx))
        source_key = f"elo:{team_name}:{date_val}"

        data = {
            "team_id":   team_ss_id,
            "team_name": team_name,
            "date":      date_val,
            "elo":       safe_float(row_dict.get("elo")),
            "rank":      safe_int(row_dict.get("rank")),
            "league_id": str(row_dict.get("league", "")),
        }

        if pb.upsert("club_elo_history", source_key, data):
            count += 1

    print(f"[ClubElo] ✓ {count} Elo snapshots cho {team_name}")


# ─── MAIN ────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  Soccerdata → PocketBase Sync")
    print("=" * 55)

    pb = PBClient(PB_URL, ADMIN_EMAIL, ADMIN_PASSWORD)

    # ── Chọn giải đấu và mùa cần sync ──
    # Mặc định: EPL 2024/25 (season="2025" trong soccerdata)
    target_league = "17"   # 17 = Premier League trong Sofascore
    target_season = "2025" # 2025 = mùa 2024/25

    print(f"\n[INFO] Target: {LEAGUE_MAP[target_league]['name']} {target_season}")

    # 1. FBref Schedule (lịch + kết quả)
    sync_fbref_schedule(pb, target_league, target_season)

    # 2. FBref Player Stats (tất cả stat_type)
    sync_fbref_player_stats(pb, target_league, target_season)

    # 3. FBref Team Stats
    sync_fbref_team_stats(pb, target_league, target_season)

    # 4. Understat Player Stats (xG, xGChain...)
    sync_understat_player_stats(pb, target_league, target_season)

    # 5. Understat Shot Events (tùy chọn — dữ liệu lớn, comment ra nếu không cần)
    # sync_understat_shot_events(pb, target_league, target_season)

    # 6. ClubElo — Elo history cho Arsenal
    sync_clubelo(pb, "Arsenal", "42")

    print("\n" + "=" * 55)
    print("  SYNC HOÀN TẤT!")
    print(f"  Kiểm tra: {PB_URL}/_/")
    print("=" * 55)


if __name__ == "__main__":
    main()
