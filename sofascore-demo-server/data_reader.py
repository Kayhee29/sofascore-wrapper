"""
data_reader.py — Unified Read Layer trên PocketBase
====================================================
Giải quyết bài toán: 3 nguồn dữ liệu, cấu hình khác nhau
→ Client chỉ cần gọi 1 hàm, nhận về 1 object thống nhất.

Chiến lược đọc:
  1. MERGED RECORD  : match, team, player  → 1 row đã merge sẵn trong PB
  2. MULTI-ROW MERGE: player_season_stats  → nhiều rows, merge theo priority
  3. CHILD COLLECTION: shot_events, match_events → fetch riêng, attach vào parent
  4. FALLBACK CHAIN : field null → thử source khác → trả về null nếu không có
"""

import httpx
from typing import Optional, Any


PB_URL        = "http://127.0.0.1:8090"
ADMIN_EMAIL   = "admin@sofascore.local"
ADMIN_PASSWORD = "Admin123456789"


# ─── FIELD MERGE PRIORITY ────────────────────────────────────
# Khi cùng 1 field có ở nhiều source → dùng source nào?
# Thứ tự: ưu tiên cao nhất → thấp nhất
FIELD_SOURCE_PRIORITY = {
    # Match fields
    "home_score":    ["sofascore"],
    "away_score":    ["sofascore"],
    "status":        ["sofascore"],
    "winner_code":   ["sofascore"],
    "home_formation":["sofascore"],
    "away_formation":["sofascore"],
    "home_kit":      ["sofascore"],
    "away_kit":      ["sofascore"],
    "referee":       ["sofascore", "fbref"],
    "venue":         ["sofascore", "fbref"],
    # xG — Understat > FBref (US dùng post-shot xG, FBref dùng pre-shot)
    "home_xg":       ["understat", "fbref"],
    "away_xg":       ["understat", "fbref"],
    # Season player stats
    "goals":         ["fbref", "sofascore"],
    "assists":       ["fbref", "sofascore"],
    "minutes":       ["fbref", "sofascore"],
    "xg":            ["understat", "fbref"],
    "xg_assist":     ["understat", "fbref"],
    "xg_chain":      ["understat"],
    "xg_buildup":    ["understat"],
    "motm_count":    ["sofascore"],
    "rating":        ["sofascore", "whoscored"],
    "elo_rating":    ["clubelo"],
}

# Mapping stat_type → source (để biết row nào chứa field nào)
STAT_TYPE_SOURCE = {
    "standard":   "fbref",
    "shooting":   "fbref",
    "passing":    "fbref",
    "defense":    "fbref",
    "gk":         "fbref",
    "playingtime":"fbref",
    "understat":  "understat",
    "sofascore":  "sofascore",
    "whoscored":  "whoscored",
}


# ─── POCKETBASE CLIENT ───────────────────────────────────────

class PBReader:
    def __init__(self, url: str = PB_URL, email: str = ADMIN_EMAIL, password: str = ADMIN_PASSWORD):
        self.url = url.rstrip("/")
        self.token = self._login(email, password)

    def _login(self, email: str, password: str) -> str:
        for ep in [
            f"{self.url}/api/admins/auth-with-password",
            f"{self.url}/api/collections/_superusers/auth-with-password",
        ]:
            r = httpx.post(ep, json={"identity": email, "password": password}, timeout=5)
            if r.status_code == 200:
                return r.json()["token"]
        raise RuntimeError("PocketBase login failed")

    @property
    def _h(self):
        return {"Authorization": self.token}

    def get_one(self, collection: str, record_id: str) -> Optional[dict]:
        r = httpx.get(f"{self.url}/api/collections/{collection}/records/{record_id}", headers=self._h, timeout=5)
        return r.json() if r.status_code == 200 else None

    def query(self, collection: str, filter_str: str, sort: str = "", expand: str = "", per_page: int = 50) -> list:
        params = {"filter": filter_str, "perPage": per_page}
        if sort:
            params["sort"] = sort
        if expand:
            params["expand"] = expand
        r = httpx.get(f"{self.url}/api/collections/{collection}/records", headers=self._h, params=params, timeout=10)
        if r.status_code == 200:
            return r.json().get("items", [])
        return []


# ─── MERGE HELPERS ───────────────────────────────────────────

def pick(value: Any) -> bool:
    """Trả về True nếu value có giá trị thực (không null/empty/0 cho rating)"""
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def merge_by_priority(rows: list[dict], field: str) -> Any:
    """
    Từ nhiều rows (mỗi row từ 1 source), lấy giá trị field theo priority.

    Ví dụ:
      rows = [
        {"source": "fbref",     "xg": 8.1},
        {"source": "understat", "xg": 8.3},
      ]
      merge_by_priority(rows, "xg")
      → 8.3  (understat có priority cao hơn fbref cho xg)
    """
    priority_list = FIELD_SOURCE_PRIORITY.get(field, [])
    source_map = {row.get("source", "unknown"): row for row in rows}

    # Thử theo thứ tự priority
    for source in priority_list:
        if source in source_map and pick(source_map[source].get(field)):
            return source_map[source][field]

    # Không có source trong priority list → lấy bất kỳ row nào có giá trị
    for row in rows:
        if pick(row.get(field)):
            return row[field]

    return None


def merge_stat_rows(rows: list[dict]) -> dict:
    """
    Merge nhiều player_season_stats rows thành 1 object thống nhất.

    Input:
      Row 1: {source:"fbref",     stat_type:"standard", goals:7, assists:5, xg:8.1}
      Row 2: {source:"understat", stat_type:"understat", xg:8.3, xg_chain:12.1}
      Row 3: {source:"sofascore", stat_type:"sofascore", motm_count:3, rating:7.6}

    Output:
      {goals:7, assists:5, xg:8.3, xg_chain:12.1, motm_count:3, rating:7.6,
       _sources: ["fbref", "understat", "sofascore"]}
    """
    if not rows:
        return {}

    # Tập hợp tất cả fields có trong các rows
    all_fields = set()
    for row in rows:
        all_fields.update(row.keys())

    # Loại bỏ metadata fields
    skip = {"id", "created", "updated", "collectionId", "collectionName",
            "player_id", "team_id", "league_id", "season", "stat_type",
            "source", "raw_json"}
    data_fields = all_fields - skip

    merged = {}
    for field in data_fields:
        merged[field] = merge_by_priority(rows, field)

    # Thêm metadata về sources đã dùng
    merged["_sources"] = list({r.get("source") for r in rows if r.get("source")})
    merged["_stat_types"] = list({r.get("stat_type") for r in rows if r.get("stat_type")})
    merged["_completeness"] = _calc_completeness(merged)

    return merged


def _calc_completeness(data: dict) -> str:
    """Đánh giá mức độ đầy đủ dữ liệu"""
    sources = set(data.get("_sources", []))
    has_basic   = bool(data.get("goals") is not None or data.get("minutes") is not None)
    has_xg      = bool(data.get("xg") is not None)
    has_advanced= bool(data.get("xg_chain") is not None)
    has_live    = "sofascore" in sources

    if has_basic and has_xg and has_advanced and has_live:
        return "full"
    elif has_basic and has_xg:
        return "standard"
    elif has_basic:
        return "basic"
    return "minimal"


# ─── READ FUNCTIONS ──────────────────────────────────────────

class FootballDataReader:
    """
    Unified read interface — client gọi vào đây, không cần biết PB bên dưới.
    """

    def __init__(self):
        self.pb = PBReader()

    # ── 1. ĐỌC TRẬN ĐẤU ─────────────────────────────────────

    def get_match(self, sofascore_id: str, include_events: bool = True,
                  include_lineups: bool = True, include_shots: bool = False) -> dict:
        """
        Đọc 1 trận đấu đầy đủ.

        Chiến lược:
          - matches collection đã merge sẵn dữ liệu SS + FBref + US tại thời điểm write
          - Nếu field nào null → trả về null (frontend tự handle)
          - attach thêm match_events, match_lineups, shot_events nếu yêu cầu
        """
        # Bước 1: Lấy record chính (đã merge sẵn)
        items = self.pb.query("matches", f"sofascore_id='{sofascore_id}'")
        if not items:
            return {"error": f"Match {sofascore_id} not found"}

        match = items[0]

        # Bước 2: Làm sạch null fields, thêm computed fields
        match["score"] = f"{match.get('home_score', '?')} - {match.get('away_score', '?')}"
        match["has_xg"]   = pick(match.get("home_xg"))
        match["has_stats"] = pick(match.get("home_possession"))
        match["_data_completeness"] = self._match_completeness(match)

        # Bước 3: Attach child collections nếu cần
        if include_events:
            match["events"] = self._get_match_events(sofascore_id)

        if include_lineups:
            home_tid = match.get("home_team_id", "")
            away_tid = match.get("away_team_id", "")
            match["lineups"] = {
                "home": self._get_lineup(sofascore_id, home_tid),
                "away": self._get_lineup(sofascore_id, away_tid),
            }

        if include_shots:
            match["shot_events"] = self._get_shot_events(sofascore_id)

        return match

    def _match_completeness(self, match: dict) -> dict:
        """Trả về map những gì có/không có — frontend dùng để hiển thị placeholder"""
        return {
            "score":      pick(match.get("home_score")),
            "xg":         pick(match.get("home_xg")),
            "possession": pick(match.get("home_possession")),
            "lineups":    pick(match.get("home_formation")),
            "shots_map":  False,  # cần query shot_events
        }

    def _get_match_events(self, match_id: str) -> list:
        rows = self.pb.query("match_events", f"match_id='{match_id}'", sort="time")
        return [
            {
                "time":        r.get("time"),
                "added_time":  r.get("added_time"),
                "type":        r.get("type"),       # goal|card|sub|var
                "detail":      r.get("detail"),     # yellow_card|normal_goal|...
                "team_id":     r.get("team_id"),
                "player":      r.get("player_name"),
                "assist":      r.get("assist_name"),
                "description": r.get("description"),
            }
            for r in rows
        ]

    def _get_lineup(self, match_id: str, team_id: str) -> dict:
        rows = self.pb.query(
            "match_lineups",
            f"match_id='{match_id}' && team_id='{team_id}'",
            sort="is_starter,jersey_number"
        )
        starters  = [r for r in rows if r.get("is_starter")]
        subs      = [r for r in rows if not r.get("is_starter")]
        return {"starters": starters, "substitutes": subs}

    def _get_shot_events(self, match_id: str) -> list:
        return self.pb.query("shot_events", f"match_id='{match_id}'", sort="minute")

    # ── 2. ĐỌC LỊCH SỬ TRẬN ─────────────────────────────────

    def get_team_matches(self, team_id: str, season: str = "",
                         limit: int = 20, status: str = "") -> list:
        """
        Đọc lịch sử / lịch thi đấu của đội.
        Trả về list matches đã merge (score + xg + basic info).
        """
        filters = [f"(home_team_id='{team_id}' || away_team_id='{team_id}')"]
        if season:
            filters.append(f"season='{season}'")
        if status:
            filters.append(f"status='{status}'")

        rows = self.pb.query(
            "matches",
            " && ".join(filters),
            sort="-start_timestamp",
            per_page=limit
        )

        return [self._format_match_summary(r, team_id) for r in rows]

    def _format_match_summary(self, row: dict, pov_team_id: str) -> dict:
        """Format 1 dòng lịch sử trận — tối giản cho list view"""
        is_home = row.get("home_team_id") == pov_team_id
        my_score  = row.get("home_score") if is_home else row.get("away_score")
        opp_score = row.get("away_score") if is_home else row.get("home_score")
        my_xg     = row.get("home_xg")   if is_home else row.get("away_xg")
        opp_xg    = row.get("away_xg")   if is_home else row.get("home_xg")

        winner = row.get("winner_code")
        result = "W" if winner == ("home" if is_home else "away") \
            else "L" if winner == ("away" if is_home else "home") \
            else "D" if winner == "draw" else "?"

        return {
            "match_id":      row.get("sofascore_id"),
            "date":          row.get("date"),
            "competition":   row.get("league_id"),
            "is_home":       is_home,
            "opponent_id":   row.get("away_team_id") if is_home else row.get("home_team_id"),
            "score":         f"{my_score}-{opp_score}",
            "result":        result,
            # xG chỉ hiện nếu có (có thể null nếu Understat chưa sync)
            "xg":            round(my_xg, 2) if my_xg is not None else None,
            "opp_xg":        round(opp_xg, 2) if opp_xg is not None else None,
            "status":        row.get("status"),
            "_has_xg":       pick(my_xg),
            "_has_details":  pick(row.get("home_formation")),
        }

    # ── 3. ĐỌC STATS CẦU THỦ ────────────────────────────────

    def get_player_stats(self, player_id: str, season: str = "") -> dict:
        """
        Đọc stats cầu thủ — merge nhiều rows từ nhiều nguồn thành 1 object.

        Ví dụ input (player_season_stats):
          Row A: {source:"fbref",     stat_type:"standard",  goals:7, assists:5, xg:8.1, minutes:2222}
          Row B: {source:"fbref",     stat_type:"shooting",  shots:74, shots_on_target:28}
          Row C: {source:"understat", stat_type:"understat", xg:8.3, xg_chain:12.1, xg_buildup:6.8}
          Row D: {source:"sofascore", stat_type:"sofascore", motm_count:3, rating:7.58}

        Output: 1 dict thống nhất với tất cả fields từ A+B+C+D
        """
        filters = [f"player_id='{player_id}'"]
        if season:
            filters.append(f"season='{season}'")

        rows = self.pb.query(
            "player_season_stats",
            " && ".join(filters),
            per_page=20
        )

        if not rows:
            return {"error": f"No stats found for player {player_id}"}

        # Merge tất cả rows thành 1 object
        merged = merge_stat_rows(rows)

        # Thêm player profile từ players collection
        player_info = self.pb.query("players", f"sofascore_id='{player_id}'")
        if player_info:
            p = player_info[0]
            merged["name"]          = p.get("name")
            merged["position"]      = p.get("position")
            merged["avatar_url"]    = p.get("avatar_url")
            merged["market_value"]  = p.get("marketValue")
            merged["nationality"]   = p.get("nationality")
            merged["jersey_number"] = p.get("jersey_number")
            merged["attributes"]    = p.get("attributes")  # Sofascore skill ratings

        merged["season"] = season or (rows[0].get("season") if rows else "")
        merged["player_id"] = player_id

        return merged

    def get_player_stats_by_source(self, player_id: str, season: str = "") -> dict:
        """
        Trả về stats theo từng source riêng biệt (dùng cho debug / compare view).
        Ví dụ: UI muốn show "FBref says xG=8.1, Understat says xG=8.3"
        """
        filters = [f"player_id='{player_id}'"]
        if season:
            filters.append(f"season='{season}'")

        rows = self.pb.query("player_season_stats", " && ".join(filters), per_page=20)

        # Group by source
        by_source: dict[str, list] = {}
        for row in rows:
            src = row.get("source", "unknown")
            by_source.setdefault(src, []).append(row)

        # Merge các rows cùng source (vd: fbref có nhiều stat_type)
        result = {}
        for source, source_rows in by_source.items():
            combined = {}
            for row in source_rows:
                for k, v in row.items():
                    if v is not None and k not in combined:
                        combined[k] = v
            result[source] = combined

        return result

    # ── 4. ĐỌC STANDING / BXH ───────────────────────────────

    def get_league_table(self, league_id: str, season: str = "") -> dict:
        """
        Đọc bảng xếp hạng — kết hợp standings từ Sofascore cache
        + Elo từ team records
        """
        # Lấy cache standings từ leagues collection
        league_cache = self.pb.query(
            "leagues",
            f"sofascore_id='{league_id}'",
            per_page=1
        )
        standings_raw = {}
        if league_cache:
            raw = league_cache[0].get("raw_json") or {}
            standings_raw = raw.get("standings", {})

        # Enrich mỗi team với Elo từ teams collection
        if isinstance(standings_raw, dict):
            rows = standings_raw.get("standings", [])
        else:
            rows = []

        enriched = []
        for entry in rows:
            team = entry.get("team", {})
            team_ss_id = str(team.get("id", ""))

            # Fetch Elo
            team_rec = self.pb.query("teams", f"sofascore_id='{team_ss_id}'", per_page=1)
            elo = None
            if team_rec:
                elo = team_rec[0].get("elo_rating")

            enriched.append({
                **entry,
                "elo_rating": elo,  # null nếu ClubElo chưa sync
            })

        return {
            "league_id": league_id,
            "season":    season,
            "table":     enriched,
            "_sources":  ["sofascore"] + (["clubelo"] if any(e.get("elo_rating") for e in enriched) else []),
        }


# ─── DEMO ────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    reader = FootballDataReader()

    print("\n=== [1] Lịch sử trận Arsenal (team_id=42) ===")
    matches = reader.get_team_matches(
        team_id="42",
        season="2024/25",
        limit=5,
        status="finished"
    )
    print(json.dumps(matches, indent=2, ensure_ascii=False, default=str))

    print("\n=== [2] Chi tiết trận 14023942 ===")
    match = reader.get_match("14023942", include_events=True, include_lineups=False)
    print(json.dumps(match, indent=2, ensure_ascii=False, default=str))

    print("\n=== [3] Stats Bukayo Saka (sofascore_id=761778) ===")
    stats = reader.get_player_stats("761778", season="2024/25")
    print(json.dumps(stats, indent=2, ensure_ascii=False, default=str))

    print("\n=== [4] Stats by source (để so sánh) ===")
    by_src = reader.get_player_stats_by_source("761778", season="2024/25")
    print(json.dumps(by_src, indent=2, ensure_ascii=False, default=str))
