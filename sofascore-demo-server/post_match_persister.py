"""
post_match_persister.py — Tự động ghi dữ liệu trận vào PocketBase
===================================================================
Luồng hoạt động:

  DURING LIVE:
    NATS stream → handle_nats_event()
      ├─ broadcast WebSocket (frontend nhận live)
      └─ upsert matches (score + status update real-time)

  WHEN STATUS = "finished":
    NATS stream phát hiện status thay đổi
      └─ trigger on_match_finished(match_id)
            ├─ Match.stats()          → matches (full stats)
            ├─ Match.incidents()      → match_events
            ├─ Match.lineups_home/away() → match_lineups
            └─ Player ratings         → match_lineups (update rating)

Kết quả sau trận:
  matches:       score, status, possession, shots, corners, xG (nếu SS có)
  match_events:  goals, cards, subs (structured rows)
  match_lineups: starters + subs với rating từng người
"""

import asyncio
import hashlib
import httpx
from typing import Optional

# ─── CONFIG ──────────────────────────────────────────────────
PB_URL         = "http://127.0.0.1:8090"
ADMIN_EMAIL    = "admin@sofascore.local"
ADMIN_PASSWORD = "Admin123456789"

# Status Sofascore trả về khi trận kết thúc
FINISHED_STATUSES = {"finished", "ended", "after extra time", "after penalties", "aet", "ap"}

# Tránh persist cùng 1 match 2 lần
_persisted_matches: set[str] = set()


# ─── ID HELPER ───────────────────────────────────────────────
def make_id(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:15]


# ─── PB WRITER (sync, dùng trong async context) ──────────────
class PBWriter:
    def __init__(self):
        self.token = self._login()

    def _login(self) -> str:
        for ep in [
            f"{PB_URL}/api/admins/auth-with-password",
            f"{PB_URL}/api/collections/_superusers/auth-with-password",
        ]:
            try:
                r = httpx.post(ep, json={"identity": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=5)
                if r.status_code == 200:
                    return r.json()["token"]
            except Exception:
                pass
        raise RuntimeError("PocketBase login failed")

    @property
    def _h(self):
        return {"Authorization": self.token}

    def upsert(self, collection: str, source_key: str, data: dict) -> bool:
        """Ghi hoặc cập nhật record theo source_key hash"""
        rec_id = make_id(source_key)
        data = {**data, "id": rec_id}
        with httpx.Client(timeout=10) as c:
            check = c.get(f"{PB_URL}/api/collections/{collection}/records/{rec_id}", headers=self._h)
            if check.status_code == 200:
                r = c.patch(f"{PB_URL}/api/collections/{collection}/records/{rec_id}", headers=self._h, json=data)
            else:
                r = c.post(f"{PB_URL}/api/collections/{collection}/records", headers=self._h, json=data)
        ok = r.status_code in [200, 201]
        if not ok:
            print(f"[PB ERR] {collection}: {r.text[:200]}")
        return ok

    def upsert_event(self, collection: str, source_key: str, data: dict) -> bool:
        """Như upsert nhưng không overwrite nếu đã tồn tại (idempotent insert)"""
        rec_id = make_id(source_key)
        data = {**data, "id": rec_id}
        with httpx.Client(timeout=10) as c:
            check = c.get(f"{PB_URL}/api/collections/{collection}/records/{rec_id}", headers=self._h)
            if check.status_code == 200:
                return True  # Đã có → bỏ qua, không overwrite events
            r = c.post(f"{PB_URL}/api/collections/{collection}/records", headers=self._h, json=data)
        return r.status_code in [200, 201]


# ─── MATCH PERSISTER ─────────────────────────────────────────

class PostMatchPersister:
    """
    Nhận match_id từ NATS event, gọi Sofascore API để lấy toàn bộ dữ liệu,
    và ghi vào PocketBase collections.
    """

    def __init__(self, api_instance):
        self.api = api_instance
        self.pb  = PBWriter()

    async def on_match_finished(self, match_id: int, raw_event: dict):
        """
        Entry point khi NATS báo trận kết thúc.
        Được gọi từ handle_nats_realtime_event() trong server.py.
        """
        mid = str(match_id)

        # Chỉ persist 1 lần
        if mid in _persisted_matches:
            return
        _persisted_matches.add(mid)

        print(f"[PERSIST] Match {mid} kết thúc — bắt đầu lưu dữ liệu vào PocketBase...")

        # Chạy song song: gọi nhiều endpoints cùng lúc
        await asyncio.gather(
            self._persist_match_core(mid, raw_event),
            self._persist_match_stats(mid),
            self._persist_incidents(mid),
            self._persist_lineups(mid),
            return_exceptions=True  # không crash nếu 1 bước lỗi
        )

        print(f"[PERSIST] Match {mid} ✓ đã lưu xong.")

    # ── BƯỚC 1: Ghi thông tin cơ bản trận ───────────────────

    async def _persist_match_core(self, match_id: str, raw_event: dict):
        """
        Lưu thông tin cốt lõi từ NATS event vào matches collection.
        NATS event đã có: score, status, teams, tournament, startTimestamp.
        """
        home_score = raw_event.get("homeScore.display",
                      raw_event.get("homeScore", {}).get("display"))
        away_score = raw_event.get("awayScore.display",
                      raw_event.get("awayScore", {}).get("display"))
        status_raw = raw_event.get("status.type",
                      raw_event.get("status", {}).get("type", ""))
        winner_code_raw = raw_event.get("winnerCode", 0)
        winner_map = {1: "home", 2: "away", 3: "draw", 0: None}

        home_team = raw_event.get("homeTeam", {})
        away_team = raw_event.get("awayTeam", {})
        tournament = raw_event.get("tournament", {})
        unique_tournament = tournament.get("uniqueTournament", {})

        data = {
            "sofascore_id":    match_id,
            "league_id":       str(unique_tournament.get("id", "")),
            "start_timestamp": raw_event.get("startTimestamp"),
            "home_team_id":    str(home_team.get("id", "")),
            "away_team_id":    str(away_team.get("id", "")),
            "home_score":      home_score,
            "away_score":      away_score,
            "status":          status_raw,
            "winner_code":     winner_map.get(winner_code_raw),
            "source":          "sofascore",
        }

        self.pb.upsert("matches", f"match:ss:{match_id}", data)
        print(f"[PERSIST] ✓ Core match data: {home_score}-{away_score}")

    # ── BƯỚC 2: Ghi full match stats ─────────────────────────

    async def _persist_match_stats(self, match_id: str):
        """
        Gọi Match.stats() để lấy:
          possession, shots, shots on target, corners, fouls,
          yellow cards, free kicks, tackles, ...
        và PATCH vào matches record.
        """
        try:
            from sofascore_wrapper.match import Match
            match_obj = Match(self.api, match_id=int(match_id))
            stats_raw = await match_obj.stats()
        except Exception as e:
            print(f"[PERSIST] WARN match stats {match_id}: {e}")
            return

        # Sofascore stats trả về dạng list groups
        # groups: [{ groupName, statisticsItems: [{name, home, away}] }]
        groups = stats_raw.get("statistics", [])
        if not groups:
            groups = stats_raw.get("groups", [])

        # Flatten tất cả stats items thành dict
        flat: dict = {}
        for group in groups:
            items = group.get("statisticsItems", []) or group.get("items", [])
            for item in items:
                key = item.get("name", "").lower().replace(" ", "_")
                flat[f"home_{key}"] = item.get("home")
                flat[f"away_{key}"] = item.get("away")

        # Map tên Sofascore → tên field PocketBase
        STAT_MAP = {
            "ball_possession":          ("home_possession",  "away_possession"),
            "total_shots":              ("home_shots",        "away_shots"),
            "shots_on_target":          ("home_shots_ot",     "away_shots_ot"),
            "corner_kicks":             ("home_corners",      "away_corners"),
            "fouls":                    ("home_fouls",        "away_fouls"),
            "yellow_cards":             ("home_yellow",       "away_yellow"),
            "red_cards":                ("home_red",          "away_red"),
            "expected_goals":           ("home_xg",           "away_xg"),
            "big_chances":              (None,                None),  # bỏ qua
        }

        patch_data: dict = {}
        for ss_key, (home_field, away_field) in STAT_MAP.items():
            if home_field and f"home_{ss_key}" in flat:
                val = flat[f"home_{ss_key}"]
                if val is not None:
                    try:
                        patch_data[home_field] = float(str(val).replace("%", ""))
                    except (ValueError, TypeError):
                        pass
            if away_field and f"away_{ss_key}" in flat:
                val = flat[f"away_{ss_key}"]
                if val is not None:
                    try:
                        patch_data[away_field] = float(str(val).replace("%", ""))
                    except (ValueError, TypeError):
                        pass

        patch_data["match_stats_json"] = flat  # lưu toàn bộ raw để không mất gì

        if patch_data:
            self.pb.upsert("matches", f"match:ss:{match_id}", patch_data)
            print(f"[PERSIST] ✓ Match stats: {len(patch_data)} fields")

    # ── BƯỚC 3: Ghi incidents (goals, cards, subs) ───────────

    async def _persist_incidents(self, match_id: str):
        """
        Gọi Match.incidents() và ghi từng incident thành 1 row
        trong match_events collection.
        Idempotent: không ghi đè nếu đã có.
        """
        try:
            from sofascore_wrapper.match import Match
            match_obj = Match(self.api, match_id=int(match_id))
            data = await match_obj.incidents()
        except Exception as e:
            print(f"[PERSIST] WARN incidents {match_id}: {e}")
            return

        incidents = data.get("incidents", [])
        count = 0

        for inc in incidents:
            inc_type = inc.get("incidentType", "")

            # Chỉ lưu các loại quan trọng
            if inc_type not in ("goal", "card", "substitution", "inGamePenalty", "varDecision"):
                continue

            time      = inc.get("time", 0)
            added     = inc.get("addedTime", 0)
            is_home   = inc.get("isHome", True)

            # Player
            player    = inc.get("player", {}) or {}
            player_in = inc.get("playerIn", {}) or {}   # substitution: cầu thủ vào
            player_out= inc.get("playerOut", {}) or {}  # substitution: cầu thủ ra

            player_name = player.get("name") or player_in.get("name", "")
            assist_name = (inc.get("assist1", {}) or {}).get("name", "")

            # Normalize type
            type_map = {
                "goal":          "goal",
                "card":          "card",
                "substitution":  "substitution",
                "inGamePenalty": "penalty",
                "varDecision":   "var",
            }
            ev_type   = type_map.get(inc_type, inc_type)
            ev_detail = inc.get("incidentClass", "")   # normal|yellow|red|missed...

            # Source key đảm bảo không trùng: dùng incident id từ Sofascore nếu có
            inc_id = str(inc.get("id", f"{match_id}_{time}_{ev_type}_{count}"))
            source_key = f"event:ss:{match_id}:{inc_id}"

            row = {
                "match_id":    match_id,
                "sofascore_id":inc_id,
                "time":        time,
                "added_time":  added,
                "type":        ev_type,
                "detail":      ev_detail,
                "team_id":     "home" if is_home else "away",  # sẽ resolve real ID sau
                "player_name": player_name,
                "assist_name": assist_name,
                "description": (
                    f"Out: {player_out.get('name', '')} → In: {player_in.get('name', '')}"
                    if inc_type == "substitution"
                    else inc.get("description", "")
                ),
                "source":      "sofascore",
            }

            self.pb.upsert_event("match_events", source_key, row)
            count += 1

        print(f"[PERSIST] ✓ Incidents: {count} events ghi vào match_events")

    # ── BƯỚC 4: Ghi lineups + ratings ────────────────────────

    async def _persist_lineups(self, match_id: str):
        """
        Gọi lineups_home() và lineups_away() để lấy đội hình + rating từng cầu thủ.
        Ghi vào match_lineups collection.
        """
        try:
            from sofascore_wrapper.match import Match
            match_obj = Match(self.api, match_id=int(match_id))
            home_raw  = await match_obj.lineups_home()
            away_raw  = await match_obj.lineups_away()
        except Exception as e:
            print(f"[PERSIST] WARN lineups {match_id}: {e}")
            return

        count = 0
        for side, raw in [("home", home_raw), ("away", away_raw)]:
            # Sofascore lineup thường trả về:
            # { "lineup": { "players": [...], "formation": "4-3-3" } }
            lineup_data = raw.get("lineup", raw)
            players = (
                lineup_data.get("players") or
                lineup_data.get("home", {}).get("players") or
                lineup_data.get("away", {}).get("players") or
                []
            )
            formation = (
                lineup_data.get("formation") or
                lineup_data.get("home", {}).get("formation") or ""
            )

            # PATCH formation vào matches
            if formation:
                field = "home_formation" if side == "home" else "away_formation"
                self.pb.upsert("matches", f"match:ss:{match_id}", {field: formation})

            for entry in players:
                player    = entry.get("player", {}) or {}
                stats     = entry.get("statistics", {}) or {}
                is_starter = not entry.get("substitute", False)

                player_id  = str(player.get("id", ""))
                player_name= player.get("name", "")
                jersey     = player.get("jerseyNumber") or entry.get("shirtNumber")
                position   = entry.get("position", player.get("position", ""))
                rating_val = stats.get("rating")  # float, vd: 7.8

                # Nếu không có rating → thử lấy từ nested
                if rating_val is None:
                    rating_val = entry.get("statistics", {}).get("ratingVersions", {}).get("original")

                source_key = f"lineup:ss:{match_id}:{player_id}"
                row = {
                    "match_id":      match_id,
                    "team_id":       side,  # "home" | "away"
                    "player_id":     player_id,
                    "player_name":   player_name,
                    "jersey_number": int(jersey) if jersey else None,
                    "position":      position,
                    "is_starter":    is_starter,
                    "rating":        float(rating_val) if rating_val else None,
                    "source":        "sofascore",
                }
                self.pb.upsert_event("match_lineups", source_key, row)
                count += 1

        print(f"[PERSIST] ✓ Lineups: {count} players ghi vào match_lineups")


# ─── LIVE SCORE UPDATER ──────────────────────────────────────

class LiveScoreUpdater:
    """
    Cập nhật score + status vào matches collection TRONG KHI trận đang live.
    Gọi mỗi khi nhận được event từ NATS.
    """

    def __init__(self):
        self.pb = PBWriter()

    def update_live_score(self, match_id: str, home_score, away_score, status: str, minute: int = 0):
        """PATCH score vào DB ngay lập tức (non-blocking nếu gọi trong thread)"""
        self.pb.upsert("matches", f"match:ss:{match_id}", {
            "sofascore_id": match_id,
            "home_score":   home_score,
            "away_score":   away_score,
            "status":       status,
            "source":       "sofascore",
        })


# ─── TÍCH HỢP VÀO server.py ──────────────────────────────────
# Thêm vào lifespan() trong server.py:
#
#   from post_match_persister import PostMatchPersister, LiveScoreUpdater, FINISHED_STATUSES
#   persister = PostMatchPersister(api_instance)
#   live_updater = LiveScoreUpdater()
#
#   async def handle_nats_realtime_event(pub_data):
#       match_id = pub_data.get("id", "unknown")
#       home_score = pub_data.get("homeScore.display", ...)
#       away_score = pub_data.get("awayScore.display", ...)
#       status = pub_data.get("status.description", ...).lower()
#
#       # [1] Broadcast WebSocket như cũ
#       await manager.broadcast({...})
#
#       # [2] THÊM: Cập nhật live score vào DB
#       live_updater.update_live_score(match_id, home_score, away_score, status)
#
#       # [3] THÊM: Nếu trận vừa kết thúc → persist full data
#       if status in FINISHED_STATUSES:
#           asyncio.create_task(persister.on_match_finished(int(match_id), pub_data))
# ─────────────────────────────────────────────────────────────
