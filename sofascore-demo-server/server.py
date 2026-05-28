import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from typing import List, Dict, Any, Optional

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# Đảm bảo python có thể import sofascore_wrapper từ thư mục cha và các file cục bộ
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(__file__))

from sofascore_wrapper.api import SofascoreAPI
from sofascore_wrapper.search import Search
from sofascore_wrapper.team import Team
from sofascore_wrapper.match import Match
from sofascore_wrapper.league import League
from sofascore_wrapper.realtime_client import SofascoreRealtimeClient
from sofascore_wrapper.player import Player

from cache_manager import PocketBaseCacheManager
from post_match_persister import PostMatchPersister, LiveScoreUpdater, FINISHED_STATUSES
from api_football_client import ApiFootballClient, KeyRotationManager

# Load .env (api-football keys, v.v.)
try:
    from dotenv import load_dotenv as _load_dotenv
    import pathlib as _pathlib
    _env_file = _pathlib.Path(__file__).parent / ".env"
    if _env_file.exists():
        _load_dotenv(_env_file)
except ImportError:
    pass

# ─── MAPPING: Sofascore ID → api-football ID ─────────────────────────────────
# League IDs
SOFASCORE_TO_APIFB_LEAGUE: dict = {
    "17":  "39",   # Premier League
    "8":   "140",  # La Liga
    "23":  "135",  # Serie A
    "35":  "78",   # Bundesliga
    "34":  "61",   # Ligue 1
    "132": "2",    # UEFA Champions League
    "21":  "3",    # UEFA Europa League
}
# Team IDs (Sofascore → api-football) — dùng làm lookup nhanh không cần query PB
SOFASCORE_TO_APIFB_TEAM: dict = {
    "44": "40",  "42": "42",  "17": "50",  "38": "49",
    "39": "34",  "40": "66",  "14": "65",  "30": "51",
    "60": "35",  "50": "55",  "43": "36",   "7": "52",
    "48": "45",  "37": "48",  "35": "33",   "3": "39",
    "33": "47",  "31": "46",  "57": "57",  "41": "41",
}

# Mapping cho giải Ngoại hạng Anh (league 17) giữa: Tên hiển thị <-> Sofascore Season ID <-> api-football season year
EPL_SEASONS_MAP = {
    "2024/25": {"sofascore": "61627", "apifootball": 2024},
    "2025/26": {"sofascore": "76986", "apifootball": 2025},
}

def resolve_season_info(season_str: Optional[str]) -> tuple:
    """
    Phân giải tham số season từ request thành:
    - apifb_season (int): vd 2025
    - sofascore_season (str): vd "76986"
    - season_display (str): vd "2025/26"
    """
    default_apifb = int(os.getenv("APIFOOTBALL_PL_SEASON", "2025"))
    
    # Mặc định ban đầu
    apifb_season = default_apifb
    sofascore_season = "76986" if default_apifb == 2025 else "61627"
    season_display = "2025/26" if default_apifb == 2025 else "2024/25"
    
    if not season_str:
        return apifb_season, sofascore_season, season_display
        
    s = season_str.strip()
    
    # 1. So khớp theo map cứng
    for name, m in EPL_SEASONS_MAP.items():
        if s == name or s == m["sofascore"] or s == str(m["apifootball"]):
            return m["apifootball"], m["sofascore"], name
            
    # 2. Xử lý định dạng chuỗi nếu không khớp map cứng (ví dụ: "2025/26", "2025-26", "2025-2026")
    import re
    match = re.search(r"\b(20\d{2})\b", s)
    if match:
        yr = int(match.group(1))
        sf = "76986" if yr == 2025 else ("61627" if yr == 2024 else "")
        disp = f"{yr}/{str(yr+1)[-2:]}"
        return yr, sf, disp
        
    try:
        val = int(s)
        if val > 2000 and val < 2100:
            sf = "76986" if val == 2025 else ("61627" if val == 2024 else "")
            return val, sf, f"{val}/{str(val+1)[-2:]}"
        elif val < 100: # e.g. 24 or 25
            yr = 2000 + val
            sf = "76986" if yr == 2025 else ("61627" if yr == 2024 else "")
            return yr, sf, f"{yr}/{str(yr+1)[-2:]}"
    except ValueError:
        pass
        
    return apifb_season, sofascore_season, season_display

cache_manager = PocketBaseCacheManager()
live_updater  = LiveScoreUpdater()
apifb_client: Optional[ApiFootballClient] = None

# --- KHỞI TẠO CONNECTION MANAGER CHO WEBSOCKETS ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        print(f"[WS MANAGER] Client mới kết nối. Tổng số client: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            print(f"[WS MANAGER] Client đã ngắt kết nối. Còn lại: {len(self.active_connections)}")

    async def broadcast(self, message_dict: Dict[str, Any]):
        """
        Gửi dữ liệu trực tiếp dạng JSON tới tất cả các client đang kết nối
        """
        if not self.active_connections:
            return
        
        message_str = json.dumps(message_dict)
        disconnected_clients = []
        
        for connection in self.active_connections:
            try:
                await connection.send_text(message_str)
            except Exception as e:
                print(f"[WS MANAGER] Lỗi gửi tin nhắn cho client, đánh dấu ngắt kết nối: {e}")
                disconnected_clients.append(connection)
                
        # Dọn dẹp các connection lỗi
        for client in disconnected_clients:
            self.disconnect(client)

manager = ConnectionManager()
api_instance: Optional[SofascoreAPI] = None
realtime_client: Optional[SofascoreRealtimeClient] = None
persister: Optional[PostMatchPersister] = None

# --- LIFESPAN EVENTS (STARTUP / SHUTDOWN) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global api_instance, realtime_client, persister, apifb_client
    
    # Khởi tạo API và Realtime Client
    print("[Server] Đang khởi tạo ứng dụng và kết nối đến Sofascore...")
    api_instance = SofascoreAPI()
    realtime_client = SofascoreRealtimeClient(api_instance.token_manager)
    persister = PostMatchPersister(api_instance)

    # Khởi tạo api-football client (key rotation từ .env)
    try:
        apifb_client = ApiFootballClient()
        status = apifb_client.key_status()
        for s in status:
            print(f"[ApiFootball] Key {s['key_hint']} | {s['count']}/{s['limit']} req | available={s['available']}")
    except Exception as apifb_err:
        print(f"[ApiFootball] Không khởi tạo được client: {apifb_err}")
        apifb_client = None
    
    # Callback xử lý sự kiện realtime nhận được từ NATS
    async def handle_nats_realtime_event(pub_data):
        match_id  = str(pub_data.get("id", "unknown"))
        home_score = pub_data.get("homeScore.display", pub_data.get("homeScore", {}).get("display", "-"))
        away_score = pub_data.get("awayScore.display", pub_data.get("awayScore", {}).get("display", "-"))
        status_desc = pub_data.get("status.description", pub_data.get("status", {}).get("description", "-"))
        status_type = pub_data.get("status.type",        pub_data.get("status", {}).get("type", "")).lower()
        
        # [1] Broadcast WebSocket cho frontend (như cũ)
        broadcast_payload = {
            "type":       "live_score_update",
            "match_id":   match_id,
            "home_score": home_score,
            "away_score": away_score,
            "status":     status_desc,
            "raw":        pub_data
        }
        await manager.broadcast(broadcast_payload)

        # [2] Ghi live score vào PocketBase ngay lập tức (non-blocking)
        asyncio.create_task(
            asyncio.to_thread(
                live_updater.update_live_score,
                match_id, home_score, away_score, status_type
            )
        )

        # [3] Nếu trận kết thúc → kích hoạt full post-match persist
        if status_type in FINISHED_STATUSES or status_desc.lower() in FINISHED_STATUSES:
            print(f"[Server] Match {match_id} kết thúc → trigger post-match persist")
            asyncio.create_task(persister.on_match_finished(int(match_id), pub_data))

    # Đăng ký subscribe bóng đá trực tiếp
    await realtime_client.subscribe("sport.football", handle_nats_realtime_event)
    
    # Kết nối WebSocket
    await realtime_client.connect()
    print("[Server] FastAPI Web Server đã khởi chạy thành công và đang lắng nghe sự kiện ngầm.")
    
    yield
    
    # Tắt server
    print("[Server] Đang đóng toàn bộ kết nối và dọn dẹp tài nguyên...")
    if realtime_client:
        await realtime_client.disconnect()
    if api_instance:
        await api_instance.close()
    print("[Server] Đã dọn dẹp xong.")

# --- ĐỊNH NGHĨA APP FASTAPI ---
app = FastAPI(
    title="Sofascore Realtime Wrapper API Server",
    description="REST API và WebSocket Gateway trung chuyển dữ liệu thể thao trực tiếp tốc độ cao.",
    version="1.0.0",
    lifespan=lifespan
)

# Cho phép CORS cho tất cả mọi domain để gọi API thoải mái từ client
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- SERVE STATIC FILES (HTML, CSS, JS) ---
_STATIC_DIR = os.path.dirname(os.path.abspath(__file__))

@app.get("/", include_in_schema=False)
async def serve_index():
    """Phục vụ trang chính dashboard."""
    return FileResponse(os.path.join(_STATIC_DIR, "index.html"))

@app.get("/teams", include_in_schema=False)
async def serve_teams():
    """Phục vụ trang danh sách đội Premier League."""
    return FileResponse(os.path.join(_STATIC_DIR, "teams.html"))

@app.get("/api", include_in_schema=False)
async def api_info():
    return {
        "status": "online",
        "message": "Chào mừng đến với Sofascore Realtime Demo Server!",
        "endpoints": {
            "swagger_docs": "/docs",
            "search": "/api/search?q={keyword}&sport=football",
            "match_stats": "/api/match/{match_id}/stats",
            "match_commentary": "/api/match/{match_id}/commentary",
            "team_info": "/api/team/{team_id}",
            "team_transfers": "/api/team/{team_id}/transfers",
            "league_standings": "/api/league/{league_id}/standings",
            "websocket_realtime": "/ws/live"
        }
    }

# ─── ADAPTER: api-football → Sofascore standings format ─────────────────────
def _apifb_standings_to_sofascore_format(apifb_data: dict, sofascore_league_id: str) -> dict:
    """
    Chuyển đổi response /standings của api-football
    thành format tương thích với frontend (standings.standings[].rows[]).
    """
    response = apifb_data.get("response", [])
    if not response:
        return {}

    league_info = response[0].get("league", {})
    league_name = league_info.get("name", "")
    league_country = league_info.get("country", "")
    league_flag = league_info.get("flag", "")
    season_year = league_info.get("season", "")
    raw_standings = league_info.get("standings", [[]])

    groups = []
    for group in raw_standings:
        rows = []
        for entry in group:
            team = entry.get("team", {})
            all_stats = entry.get("all", {})
            rows.append({
                "position":      entry.get("rank", 0),
                "points":        entry.get("points", 0),
                "played":        all_stats.get("played", 0),
                "wins":          all_stats.get("win", 0),
                "draws":         all_stats.get("draw", 0),
                "losses":        all_stats.get("lose", 0),
                "scoresFor":     all_stats.get("goals", {}).get("for", 0),
                "scoresAgainst": all_stats.get("goals", {}).get("against", 0),
                "description":   entry.get("description", ""),
                "team": {
                    "id":                   team.get("id"),
                    "name":                 team.get("name", ""),
                    "fullName":             team.get("name", ""),
                    "nameCode":             (team.get("name") or "")[:3].upper(),
                    "apifootball_id":       str(team.get("id", "")),
                    "apifootball_logo_url": team.get("logo", ""),
                    # Map ngược sang sofascore_id nếu có
                    "sofascore_id":         next(
                        (sid for sid, apid in SOFASCORE_TO_APIFB_TEAM.items()
                         if apid == str(team.get("id", ""))), None
                    ),
                    "teamColors": {"primary": "#1a1a2e", "secondary": "#ffffff"},
                },
            })
        groups.append({"rows": rows, "description": entry.get("description", "") if group else ""})

    return {
        "season_id":  str(season_year),
        "_source":    "api-football",
        "league": {
            "id":      sofascore_league_id,
            "name":    league_name,
            "country": league_country,
            "flag":    league_flag,
        },
        "standings": {
            "standings": groups
        }
    }


# ─── ADAPTER: api-football /teams → Sofascore team format ────────────────────
def _apifb_team_to_sofascore_format(apifb_data: dict, sofascore_id: str) -> dict:
    """
    Chuyển đổi response /teams của api-football
    thành format {"team": {...}} tương thích frontend.
    """
    response = apifb_data.get("response", [])
    if not response:
        return {}
    item    = response[0]
    team    = item.get("team", {})
    venue   = item.get("venue", {})
    return {
        "_source": "api-football",
        "team": {
            "id":                   sofascore_id,
            "name":                 team.get("name", ""),
            "fullName":             team.get("name", ""),
            "nameCode":             (team.get("code") or (team.get("name") or "")[:3]).upper(),
            "country":              team.get("country", ""),
            "founded":              team.get("founded"),
            "national":             team.get("national", False),
            "apifootball_id":       str(team.get("id", "")),
            "apifootball_logo_url": team.get("logo", ""),
            "sport": {"name": "Football", "slug": "football"},
            "venue": {
                "name":     venue.get("name", "Không rõ"),
                "capacity": venue.get("capacity", 0),
                "city":     venue.get("city", ""),
                "surface":  venue.get("surface", ""),
            },
            "manager":   {"name": "Không rõ"},
            "userCount": 0,
        }
    }


# ─── ADAPTER: api-football /players → squad format ───────────────────────────
def _apifb_players_to_squad_format(apifb_data: dict) -> dict:
    """
    Chuyển đổi response /players của api-football
    thành format {"players": [{"player": {...}, "statistics": [...]}]}.
    """
    response = apifb_data.get("response", [])
    players = []
    for item in response:
        p     = item.get("player", {})
        stats = item.get("statistics", [{}])
        s     = stats[0] if stats else {}
        games = s.get("games", {})
        goals = s.get("goals", {})
        pass_  = s.get("passes", {})
        players.append({
            "player": {
                "id":          p.get("id"),
                "name":        p.get("name", ""),
                "firstName":   p.get("firstname", ""),
                "lastName":    p.get("lastname", ""),
                "dateOfBirth": p.get("birth", {}).get("date", ""),
                "nationality": p.get("nationality", ""),
                "height":      p.get("height", ""),
                "weight":      p.get("weight", ""),
                "injured":     p.get("injured", False),
                "position":    _map_apifb_position(games.get("position", "")),
                "jerseyNumber": games.get("number"),
                "proposedMarketValue": 0,
                "apifootball_id":    str(p.get("id", "")),
                "apifootball_photo": p.get("photo", ""),
            },
            "statistics": [{
                "goals": {"scored": goals.get("total", 0), "assists": goals.get("assists", 0)},
                "games": {"appearances": games.get("appearences", 0), "minutesPlayed": games.get("minutes", 0)},
                "passes": {"accuracy": pass_.get("accuracy", 0)},
            }],
        })
    return {"_source": "api-football", "players": players}


def _map_apifb_position(pos: str) -> str:
    """api-football position string → single-char code (G/D/M/F)."""
    return {"Goalkeeper": "G", "Defender": "D", "Midfielder": "M", "Attacker": "F"}.get(pos, "M")


@app.get("/api/search")
async def api_search(q: str, sport: str = "football"):
    """
    Tìm kiếm đội bóng, cầu thủ, giải đấu hoặc trận đấu
    """
    if not q:
        raise HTTPException(status_code=400, detail="Vui lòng cung cấp tham số tìm kiếm 'q'")
    try:
        cached_res = await cache_manager.search_cache(q, sport=sport)
        if cached_res.get("results"):
            return cached_res

        try:
            search = Search(api_instance, q)
            res = await search.search_all(sport=sport)
            for item in res.get("results", []):
                entity = item.get("entity") or {}
                entity_id = entity.get("id")
                if not entity_id:
                    continue

                if item.get("type") == "team":
                    asyncio.create_task(cache_manager.set_team_cache(entity_id, {"team": entity}))
                elif item.get("type") == "player":
                    asyncio.create_task(cache_manager.set_basic_player_cache(entity_id, entity))
            return res
        except Exception as live_err:
            print(f"[Server Warning] Live search failed for '{q}': {live_err}. Returning cached results.")
            return cached_res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/match/{match_id}/stats")
async def api_match_stats(match_id: str):
    """
    Lấy thông tin thống kê chi tiết của một trận đấu
    """
    try:
        match_id_int = int(match_id) if match_id.isdigit() else match_id
        match_obj = Match(api_instance, match_id=match_id_int)
        res = await match_obj.stats()
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/match/{match_id}/commentary")
async def api_match_commentary(match_id: str):
    """
    Lấy nội dung bình luận trực tiếp bằng văn bản (live text commentary)
    """
    try:
        match_id_int = int(match_id) if match_id.isdigit() else match_id
        match_obj = Match(api_instance, match_id=match_id_int)
        res = await match_obj.commentary()
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/team/{team_id}")
async def api_team_info(team_id: str):
    """
    Lấy chi tiết thông tin câu lạc bộ.
    Thứ tự ưu tiên: api-football → PocketBase cache → Sofascore live
    """
    def _enrich_team_logo(data: dict, sofascore_id: str) -> dict:
        """Inject apifootball_logo_url vào team data từ PocketBase."""
        try:
            rec = cache_manager.client.collection("teams").get_first_list_item(
                f"sofascore_id = '{sofascore_id}'"
            )
            apifb_logo = getattr(rec, "apifootball_logo_url", "") or rec.__dict__.get("apifootball_logo_url", "")
            apifb_id   = getattr(rec, "apifootball_id", "")      or rec.__dict__.get("apifootball_id", "")
            if apifb_logo or apifb_id:
                team = data.get("team", data)
                if apifb_logo:
                    team["apifootball_logo_url"] = apifb_logo
                if apifb_id:
                    team["apifootball_id"] = apifb_id
                    if not apifb_logo:
                        team["apifootball_logo_url"] = f"https://media.api-sports.io/football/teams/{apifb_id}.png"
        except Exception:
            pass
        return data

    # ── 1. api-football (nếu có mapping) ──────────────────────────────────
    apifb_team_id = SOFASCORE_TO_APIFB_TEAM.get(str(team_id))
    if apifb_client and apifb_team_id:
        try:
            print(f"[Server] Fetch team {team_id} từ api-football (apifb_id={apifb_team_id})")
            apifb_data = await apifb_client.get("/teams", params={"id": apifb_team_id})
            if apifb_data.get("response"):
                result = _apifb_team_to_sofascore_format(apifb_data, team_id)
                if result.get("team"):
                    print(f"[Server] ✅ Team '{result['team']['name']}' từ api-football")
                    asyncio.create_task(cache_manager.set_team_cache(team_id, result))
                    return result
        except Exception as apifb_err:
            print(f"[Server Warning] api-football team {team_id} thất bại: {apifb_err}")

    # ── 2. PocketBase cache ────────────────────────────────────────────────
    cached_data = await cache_manager.get_team_cache(team_id)
    if cached_data:
        print(f"[Server] ✅ Team {team_id} từ PocketBase cache")
        return _enrich_team_logo(cached_data, team_id)

    # ── 3. Sofascore live ──────────────────────────────────────────────────
    try:
        team_id_int = int(team_id) if team_id.isdigit() else team_id
        team_obj = Team(api_instance, team_id=team_id_int)
        res = await team_obj.get_team()
        asyncio.create_task(cache_manager.set_team_cache(team_id, res))
        print(f"[Server] ✅ Team {team_id} từ Sofascore live")
        return _enrich_team_logo(res, team_id)
    except Exception as e:
        expired_data = await cache_manager.get_team_cache(team_id, ignore_expiration=True)
        if expired_data:
            print(f"[Server Fallback] Team {team_id}: dùng expired cache")
            return _enrich_team_logo(expired_data, team_id)

        # Fallback soccerdata: dựng synthetic từ player_season_stats
        if not team_id.isdigit():
            try:
                records = cache_manager.client.collection("player_season_stats").get_full_list(
                    query_params={"filter": f"team_id = '{team_id}'", "perPage": 1}
                )
                if records:
                    print(f"[Server Fallback] Synthetic team cho '{team_id}' (soccerdata)")
                    return {
                        "team": {
                            "id": team_id, "name": team_id, "fullName": team_id,
                            "nameCode": team_id[:3].upper(),
                            "sport": {"name": "Football", "slug": "football"},
                            "venue": {"name": "Sân vận động Offline", "capacity": 0},
                            "manager": {"name": "HLV Offline"}, "userCount": 0,
                        }
                    }
            except Exception as pb_err:
                print(f"[Server Fallback ERR] Không thể dựng synthetic team: {pb_err}")

        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/team/{team_id}/transfers")
async def api_team_transfers(team_id: str):
    """
    Lấy lịch sử chuyển nhượng cầu thủ mới nhất của câu lạc bộ
    """
    cached_data = await cache_manager.get_transfer_cache(team_id)
    if cached_data:
        return cached_data

    try:
        team_id_int = int(team_id) if team_id.isdigit() else team_id
        team_obj = Team(api_instance, team_id=team_id_int)
        trans_in = await team_obj.transfers_in()
        trans_out = await team_obj.transfers_out()
        res = {
            "transfers_in": trans_in,
            "transfers_out": trans_out
        }

        asyncio.create_task(cache_manager.set_transfer_cache(team_id, res))

        return res
    except Exception as e:
        # Fallback to expired cache if available
        expired_data = await cache_manager.get_transfer_cache(team_id, ignore_expiration=True)
        if expired_data:
            print(f"[Server Fallback] Live fetch failed for Transfers {team_id}: {e}. Returning expired cache.")
            return expired_data

        # Fallback đặc biệt cho soccerdata-only teams
        if not team_id.isdigit():
            return {"transfers_in": [], "transfers_out": []}

        raise HTTPException(status_code=500, detail=str(e))

async def attach_stats_to_squad(team_id: str, squad_data: dict) -> dict:
    """
    Đi tìm tất cả player_season_stats của câu lạc bộ team_id (theo cả sofascore_id lẫn tên/FBref/Understat slug)
    và gán vào p["player"]["stats"] cho từng cầu thủ trong squad_data["players"].
    """
    if not squad_data or "players" not in squad_data:
        return squad_data

    # 1. Tìm thông tin tên đội bóng / slug để query cho rộng
    team_names = {team_id}
    try:
        # Nếu team_id là số, query trong teams collection để lấy name, fbref_id, understat_id
        if team_id.isdigit():
            try:
                team_rec = cache_manager.client.collection("teams").get_first_list_item(
                    f"sofascore_id = '{team_id}'"
                )
                if team_rec:
                    if getattr(team_rec, "name", None):
                        team_names.add(team_rec.name)
                    if getattr(team_rec, "fbref_id", None):
                        team_names.add(team_rec.fbref_id)
                    if getattr(team_rec, "understat_id", None):
                        team_names.add(team_rec.understat_id)
            except Exception:
                pass
        else:
            # Nếu team_id đã là tên chữ (ví dụ "Girona" hoặc "Arsenal")
            team_names.add(team_id)
            # Thử tìm record để lấy sofascore_id và các slug khác
            try:
                team_rec = cache_manager.client.collection("teams").get_first_list_item(
                    f"name = '{team_id}' || fullName = '{team_id}' || sofascore_id = '{team_id}'"
                )
                if team_rec:
                    team_names.add(team_rec.sofascore_id)
                    if getattr(team_rec, "name", None):
                        team_names.add(team_rec.name)
                    if getattr(team_rec, "fbref_id", None):
                        team_names.add(team_rec.fbref_id)
                    if getattr(team_rec, "understat_id", None):
                        team_names.add(team_rec.understat_id)
            except Exception:
                pass
    except Exception:
        pass

    # 2. Query toàn bộ player_season_stats của đội bóng này từ PocketBase
    stats_map = {} # player_id -> list of stats
    for t_name in list(team_names):
        try:
            records = cache_manager.client.collection("player_season_stats").get_full_list(
                query_params={"filter": f"team_id = '{t_name}'"}
            )
            for r in records:
                p_id = r.player_id
                stat_entry = {
                    "id": r.id,
                    "player_id": r.player_id,
                    "team_id": r.team_id,
                    "league_id": r.league_id,
                    "season": r.season,
                    "stat_type": r.stat_type,
                    "source": r.source,
                    "games": getattr(r, "games", None),
                    "games_starts": getattr(r, "games_starts", None),
                    "minutes": getattr(r, "minutes", None),
                    "goals": getattr(r, "goals", None),
                    "assists": getattr(r, "assists", None),
                    "xg": getattr(r, "xg", None),
                    "xg_assist": getattr(r, "xg_assist", None),
                    "key_passes": getattr(r, "key_passes", None),
                    "tackles": getattr(r, "tackles", None),
                    "tackles_won": getattr(r, "tackles_won", None),
                    "interceptions": getattr(r, "interceptions", None),
                    "pass_pct": getattr(r, "pass_pct", None)
                }
                if p_id not in stats_map:
                    stats_map[p_id] = []
                # Tránh trùng lặp id bản ghi
                if not any(x["id"] == r.id for x in stats_map[p_id]):
                    stats_map[p_id].append(stat_entry)
        except Exception as err:
            print(f"[Server Warning] Lỗi khi lấy stats cho team '{t_name}': {err}")

    # 3. Duyệt qua các cầu thủ trong squad_data để map stats vào
    for p in squad_data.get("players", []):
        player_obj = p.get("player") or {}
        if not player_obj:
            continue
        p_name = player_obj.get("name", "")
        p_id = player_obj.get("id")
        
        # Tạo các dạng slug để so khớp
        slug_name = p_name.lower().replace(" ", "_")
        slug_short = player_obj.get("shortName", "").lower().replace(" ", "_")

        # Tìm stats theo nhiều phương án so khớp slug
        p_stats = []
        if slug_name in stats_map:
            p_stats = stats_map[slug_name]
        elif slug_short in stats_map:
            p_stats = stats_map[slug_short]
        elif p_id:
            # Thử tìm theo sofascore_id dạng chuỗi hoặc số
            p_id_str = str(p_id)
            if p_id_str in stats_map:
                p_stats = stats_map[p_id_str]
            else:
                # Tìm xem có key nào chứa slug tương ứng không
                for key, val in stats_map.items():
                    if slug_name in key or key in slug_name:
                        p_stats = val
                        break

        # Gán stats trực tiếp vào object player
        player_obj["stats"] = p_stats

    return squad_data

async def build_dynamic_squad_from_stats(team_id: str) -> dict:
    """
    Tự động dựng danh sách đội hình (squad) từ player_season_stats nếu không có cache của Sofascore.
    """
    team_names = {team_id}
    try:
        team_rec = cache_manager.client.collection("teams").get_first_list_item(
            f"name = '{team_id}' || fullName = '{team_id}' || sofascore_id = '{team_id}'"
        )
        if team_rec:
            team_names.add(team_rec.sofascore_id)
            if getattr(team_rec, "name", None):
                team_names.add(team_rec.name)
            if getattr(team_rec, "fbref_id", None):
                team_names.add(team_rec.fbref_id)
            if getattr(team_rec, "understat_id", None):
                team_names.add(team_rec.understat_id)
    except Exception:
        pass

    players_map = {} # player_slug -> player_dict
    for t_name in list(team_names):
        try:
            records = cache_manager.client.collection("player_season_stats").get_full_list(
                query_params={"filter": f"team_id = '{t_name}'"}
            )
            for r in records:
                p_id = r.player_id
                if p_id not in players_map:
                    p_name = p_id.replace("_", " ").title()
                    players_map[p_id] = {
                        "player": {
                            "id": p_id,
                            "name": p_name,
                            "slug": p_id.replace("_", "-"),
                            "shortName": p_name,
                            "position": "M",  # Mặc định
                            "jerseyNumber": "-",
                            "proposedMarketValue": 0,
                            "stats": []
                        }
                    }
                
                # Cập nhật vị trí nếu là GK
                if r.stat_type == "gk":
                    players_map[p_id]["player"]["position"] = "G"

                # Đính kèm stats
                stat_entry = {
                    "id": r.id,
                    "player_id": r.player_id,
                    "team_id": r.team_id,
                    "league_id": r.league_id,
                    "season": r.season,
                    "stat_type": r.stat_type,
                    "source": r.source,
                    "games": getattr(r, "games", None),
                    "games_starts": getattr(r, "games_starts", None),
                    "minutes": getattr(r, "minutes", None),
                    "goals": getattr(r, "goals", None),
                    "assists": getattr(r, "assists", None),
                    "xg": getattr(r, "xg", None),
                    "xg_assist": getattr(r, "xg_assist", None),
                    "key_passes": getattr(r, "key_passes", None),
                    "tackles": getattr(r, "tackles", None),
                    "tackles_won": getattr(r, "tackles_won", None),
                    "interceptions": getattr(r, "interceptions", None),
                    "pass_pct": getattr(r, "pass_pct", None)
                }
                if not any(x["id"] == r.id for x in players_map[p_id]["player"]["stats"]):
                    players_map[p_id]["player"]["stats"].append(stat_entry)
        except Exception as err:
            print(f"[Server Warning] Lỗi build squad từ stats cho team '{t_name}': {err}")

    # Đắp thêm thông tin từ players collection nếu có
    for p_id, p_wrapper in players_map.items():
        try:
            p_name = p_wrapper["player"]["name"]
            p_rec = cache_manager.client.collection("players").get_first_list_item(
                f"name = '{p_name}' || sofascore_id = '{p_id}'"
            )
            if p_rec:
                p_wrapper["player"]["id"] = p_rec.sofascore_id
                p_wrapper["player"]["position"] = p_rec.position or p_wrapper["player"]["position"]
                p_wrapper["player"]["jerseyNumber"] = getattr(p_rec, "jersey_number", None) or getattr(p_rec, "shirtNumber", None) or "-"
                p_wrapper["player"]["proposedMarketValue"] = getattr(p_rec, "marketValue", 0) or getattr(p_rec, "proposedMarketValue", 0)
                if p_rec.avatar_file:
                    p_wrapper["player"]["avatar_url_local"] = f"{cache_manager.pb_url}/api/files/{p_rec.collection_name}/{p_rec.id}/{p_rec.avatar_file}"
        except Exception:
            pass

    return {"players": list(players_map.values())}

@app.get("/api/team/{team_id}/players")
async def api_team_squad(team_id: str, season: Optional[str] = None):
    """
    Lấy danh sách đội hình cầu thủ + thống kê.
    Thứ tự ưu tiên: api-football → soccerdata (PocketBase stats) → Sofascore live
    """
    apifb_season, sofascore_season, season_display = resolve_season_info(season)

    # Phân giải team_id sang số nếu là tên chữ
    resolved_team_id = team_id
    if not team_id.isdigit():
        try:
            team_rec = cache_manager.client.collection("teams").get_first_list_item(
                f"name = '{team_id}' || fullName = '{team_id}'"
            )
            if team_rec and team_rec.sofascore_id:
                resolved_team_id = team_rec.sofascore_id
                print(f"[Server] Phân giải '{team_id}' → sofascore_id={resolved_team_id}")
        except Exception:
            pass

    # ── 1. api-football /players ───────────────────────────────────────────
    apifb_team_id = SOFASCORE_TO_APIFB_TEAM.get(str(resolved_team_id))
    # Nếu không có trong map cứng, thử lookup từ PocketBase
    if not apifb_team_id:
        try:
            team_rec = cache_manager.client.collection("teams").get_first_list_item(
                f"sofascore_id = '{resolved_team_id}'"
            )
            apifb_team_id = getattr(team_rec, "apifootball_id", "") or team_rec.__dict__.get("apifootball_id", "")
        except Exception:
            pass

    if apifb_client and apifb_team_id:
        try:
            print(f"[Server] Fetch players từ api-football (team={apifb_team_id} season={apifb_season})")
            apifb_data = await apifb_client.get_players(
                team_id=int(apifb_team_id),
                season=apifb_season
            )
            if apifb_data.get("response"):
                res = _apifb_players_to_squad_format(apifb_data)
                if res.get("players"):
                    print(f"[Server] ✅ {len(res['players'])} cầu thủ từ api-football (season={apifb_season})")
                    res = await attach_stats_to_squad(resolved_team_id, res)
                    asyncio.create_task(cache_manager.set_squad_cache(f"{resolved_team_id}_{apifb_season}", res))
                    return res
        except Exception as apifb_err:
            print(f"[Server Warning] api-football players {team_id} thất bại: {apifb_err}")

    # ── 2. soccerdata: PocketBase squad cache ─────────────────────────────
    # Thử lấy cache theo mùa trước, nếu không có lấy cache chung
    cached_data = await cache_manager.get_squad_cache(f"{resolved_team_id}_{apifb_season}")
    if not cached_data:
        cached_data = await cache_manager.get_squad_cache(resolved_team_id)
    if cached_data:
        print(f"[Server] ✅ Squad {resolved_team_id} từ PocketBase cache")
        cached_data = await attach_stats_to_squad(resolved_team_id, cached_data)
        return cached_data

    # ── 3. Sofascore live squad ────────────────────────────────────────────
    res = None
    if resolved_team_id.isdigit():
        try:
            team_obj = Team(api_instance, team_id=int(resolved_team_id))
            res = await team_obj.squad()
            asyncio.create_task(cache_manager.set_squad_cache(f"{resolved_team_id}_{apifb_season}", res))
            print(f"[Server] ✅ Squad {resolved_team_id} từ Sofascore live")
        except Exception as e:
            print(f"[Server Warning] Sofascore squad {resolved_team_id}: {e}")

    # ── 4. Fallback: expired cache → dựng dynamic từ player_season_stats ──
    if not res:
        expired_data = await cache_manager.get_squad_cache(f"{resolved_team_id}_{apifb_season}", ignore_expiration=True)
        if not expired_data:
            expired_data = await cache_manager.get_squad_cache(resolved_team_id, ignore_expiration=True)
        if expired_data:
            print(f"[Server Fallback] Squad {resolved_team_id}: expired cache")
            res = expired_data
        else:
            print(f"[Server Fallback] Dựng squad dynamic từ soccerdata cho {team_id}")
            res = await build_dynamic_squad_from_stats(team_id)
            if res and res.get("players"):
                asyncio.create_task(cache_manager.set_squad_cache(f"{resolved_team_id}_{apifb_season}", res))

    if res:
        res = await attach_stats_to_squad(resolved_team_id, res)
        return res

    raise HTTPException(status_code=404, detail=f"Không tìm thấy đội hình cho CLB {team_id}")

@app.get("/api/keys/status", tags=["System"])
async def api_keys_status():
    """Trạng thái quota API key api-football (theo dõi giới hạn request/ngày)."""
    if not apifb_client:
        return {"status": "disabled", "message": "api-football client chưa được khởi tạo (thiếu APIFOOTBALL_KEYS trong .env)"}
    return {
        "status": "ok",
        "keys": apifb_client.key_status(),
    }


@app.get("/api/player/{player_id}")
async def api_player_detail(player_id: str):
    """
    Lấy thông tin chi tiết và chỉ số kỹ thuật của một cầu thủ
    """
    # 1. Thử lấy từ cache PocketBase trước
    cached_data = await cache_manager.get_player_cache(player_id)
    cached_attributes = (cached_data or {}).get("attributes") or {}
    if cached_data and cached_attributes:
        return cached_data

    # 2. Cache MISS hoặc chỉ có thông tin cơ bản -> Gọi wrapper bổ sung
    try:
        player_id_int = int(player_id) if player_id.isdigit() else player_id
        player_obj = Player(api_instance, player_id=player_id_int)
        detail = await player_obj.get_player()
        attrs = await player_obj.attributes()
        
        # 3. Ghi cache ngầm
        asyncio.create_task(cache_manager.set_player_cache(player_id, detail, attrs))
        
        return {
            "player": detail.get("player", {}),
            "attributes": attrs
        }
    except Exception as e:
        if cached_data:
            return cached_data
        # Fallback to expired cache if available
        expired_data = await cache_manager.get_player_cache(player_id, ignore_expiration=True)
        if expired_data:
            print(f"[Server Fallback] Live fetch failed for Player {player_id}: {e}. Returning expired cache.")
            return expired_data
        raise HTTPException(status_code=500, detail=str(e))

def _enrich_standings_with_logos(standings_data: dict) -> dict:
    """
    Inject apifootball_logo_url vào mỗi team trong standings.
    Lấy từ PocketBase teams collection theo sofascore_id.
    Không throw exception nếu không tìm thấy — chỉ bỏ qua.
    """
    try:
        standings_obj = standings_data.get("standings") or {}
        standings_list = standings_obj.get("standings", []) if isinstance(standings_obj, dict) else []

        # Build mapping sofascore_id → apifootball_logo_url từ PocketBase
        logo_map: dict = {}
        try:
            records = cache_manager.client.collection("teams").get_full_list(
                query_params={"fields": "sofascore_id,apifootball_logo_url,apifootball_id"}
            )
            for rec in records:
                sid = getattr(rec, "sofascore_id", None) or rec.__dict__.get("sofascore_id", "")
                logo = getattr(rec, "apifootball_logo_url", "") or rec.__dict__.get("apifootball_logo_url", "")
                apifb_id = getattr(rec, "apifootball_id", "") or rec.__dict__.get("apifootball_id", "")
                if sid:
                    logo_map[str(sid)] = {
                        "apifootball_logo_url": logo or (f"https://media.api-sports.io/football/teams/{apifb_id}.png" if apifb_id else ""),
                        "apifootball_id": apifb_id,
                    }
        except Exception as pb_err:
            print(f"[EnrichLogos] Không lấy được logo map từ PB: {pb_err}")

        if not logo_map:
            return standings_data

        # Inject vào mỗi team row
        enriched = 0
        for standing_group in standings_list:
            for row in standing_group.get("rows", []):
                team = row.get("team", {})
                sid = str(team.get("id", ""))
                if sid in logo_map:
                    team["apifootball_logo_url"] = logo_map[sid]["apifootball_logo_url"]
                    team["apifootball_id"]      = logo_map[sid]["apifootball_id"]
                    enriched += 1

        if enriched:
            print(f"[EnrichLogos] Đã inject logo URL cho {enriched} team rows")

    except Exception as e:
        print(f"[EnrichLogos] Lỗi: {e}")

    return standings_data


@app.get("/api/league/{league_id}/standings")
async def api_league_standings(league_id: str, season: Optional[str] = None):
    """
    Lấy bảng xếp hạng giải đấu.
    Thứ tự ưu tiên: api-football → PocketBase cache → Sofascore live
    """
    # Phân giải season
    apifb_season, sofascore_season, season_display = resolve_season_info(season)

    # ── 1. api-football (nguồn ưu tiên cao nhất) ──────────────────────────
    apifb_league_id = SOFASCORE_TO_APIFB_LEAGUE.get(str(league_id))
    if apifb_client and apifb_league_id:
        try:
            print(f"[Server] Fetch standings từ api-football: league={apifb_league_id} season={apifb_season}")
            apifb_data = await apifb_client.get_standings(
                league_id=int(apifb_league_id),
                season=apifb_season
            )
            if apifb_data.get("response"):
                result = _apifb_standings_to_sofascore_format(apifb_data, league_id)
                if result.get("standings"):
                    print(f"[Server] ✅ Standings từ api-football ({len(result['standings'].get('standings', [[]])[0].get('rows', []) if result['standings'].get('standings') else [])} đội) cho season {apifb_season}")
                    # Ghi cache ngầm vào PocketBase để làm fallback
                    asyncio.create_task(cache_manager.set_league_cache(
                        league_id=league_id,
                        season_id=result.get("season_id", str(apifb_season)),
                        raw_json=result,
                        name=result.get("league", {}).get("name", ""),
                        country=result.get("league", {}).get("country", ""),
                        flag=result.get("league", {}).get("flag", ""),
                    ))
                    return result
        except Exception as apifb_err:
            print(f"[Server Warning] api-football standings thất bại: {apifb_err}. Chuyển fallback.")

    # ── 2. PocketBase cache (Sofascore data hoặc api-football đã lưu) ─────
    try:
        league_id_int = int(league_id) if league_id.isdigit() else league_id
        league_obj = League(api_instance, league_id=league_id_int)

        # Quyết định season_id để truy cập cache và Sofascore live
        # Đối với EPL (league_id == 17): sử dụng sofascore_season (như "61627" hoặc "76986") hoặc apifb_season làm fallback
        if str(league_id) == "17":
            season_id_to_use = sofascore_season
        else:
            season_id_to_use = season
            
        if not season_id_to_use:
            try:
                season_obj = await league_obj.current_season()
                if season_obj:
                    season_id_to_use = str(season_obj["id"])
            except Exception as season_err:
                print(f"[Server Warning] Could not fetch current season: {season_err}")

        # Thử lấy từ cache trước
        if season_id_to_use:
            cached_data = await cache_manager.get_league_cache(league_id, season_id_to_use)
            if not cached_data and str(league_id) == "17":
                # Thử thêm cache dưới dạng apifb_season (ví dụ: "2025" hoặc "2024")
                cached_data = await cache_manager.get_league_cache(league_id, str(apifb_season))
                
            cached_league = (cached_data or {}).get("league") or {}
            if cached_data and cached_league.get("name"):
                print(f"[Server] ✅ Standings từ PocketBase cache (season={season_id_to_use})")
                return _enrich_standings_with_logos(cached_data)
        else:
            # Fallback lấy bất kỳ cache nào mới nhất
            try:
                records = cache_manager.client.collection("leagues").get_full_list(
                    query_params={"filter": f"sofascore_id ~ '{league_id}_'"}
                )
                if records:
                    records.sort(key=lambda r: r.updated, reverse=True)
                    cached_data = records[0].raw_json
                    if cached_data:
                        print(f"[Server] ✅ Standings từ PocketBase cache (key={records[0].sofascore_id})")
                        return _enrich_standings_with_logos(cached_data)
            except Exception as pb_err:
                print(f"[Server Warning] PocketBase query failed: {pb_err}")

        # ── 3. Sofascore live ──────────────────────────────────────────────
        if not season_id_to_use:
            raise Exception("Cannot fetch standings without season ID (offline).")

        print(f"[Server] Fetch standings từ Sofascore live (league={league_id} season={season_id_to_use})")
        season_int = int(season_id_to_use) if season_id_to_use.isdigit() else season_id_to_use
        res = await league_obj.standings(season_int)

        try:
            league_info = await league_obj.get_league()
            league_info = league_info.get("uniqueTournament") or league_info
            category = league_info.get("category") or {}
            name    = league_info.get("name", "")
            country = category.get("name", "")
            flag    = category.get("flag", "")
        except Exception:
            name, country, flag = "", "", ""

        response_data = {
            "_source": "sofascore",
            "season_id": str(season_id_to_use),
            "league": {"id": league_id, "name": name, "country": country, "flag": flag},
            "standings": res
        }
        asyncio.create_task(cache_manager.set_league_cache(
            league_id=league_id, season_id=season_id_to_use, raw_json=response_data,
            name=name, country=country, flag=flag
        ))
        print(f"[Server] ✅ Standings từ Sofascore live")
        return _enrich_standings_with_logos(response_data)
    except Exception as e:
        if season_id_to_use:
            expired_data = await cache_manager.get_league_cache(league_id, season_id_to_use, ignore_expiration=True)
            if not expired_data and str(league_id) == "17":
                expired_data = await cache_manager.get_league_cache(league_id, str(apifb_season), ignore_expiration=True)
            if expired_data:
                print(f"[Server Fallback] Live fetch failed for League {league_id} Season {season_id_to_use}: {e}. Returning expired cache.")
                return _enrich_standings_with_logos(expired_data)
        try:
            records = cache_manager.client.collection("leagues").get_full_list(
                query_params={"filter": f"sofascore_id ~ '{league_id}_'"}
            )
            if records:
                records.sort(key=lambda r: r.updated, reverse=True)
                cached_data = records[0].raw_json
                if cached_data:
                    print(f"[Server Fallback] Live fetch failed, returning last cached season for league {league_id}")
                    return _enrich_standings_with_logos(cached_data)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/player/{player_id}/stats")
async def api_player_stats(player_id: str):
    """
    Lấy thống kê mùa giải của cầu thủ từ dữ liệu offline (FBref/Understat)
    """
    try:
        player_name = None
        # Nếu player_id là số, tra cứu trong cache để tìm tên cầu thủ
        if player_id.isdigit():
            player_data = await cache_manager.get_player_cache(player_id, ignore_expiration=True)
            if player_data and "player" in player_data:
                player_name = player_data["player"].get("name")
        else:
            player_name = player_id

        # Nếu không tìm thấy, thử tìm bản ghi trong database bằng sofascore_id
        if not player_name:
            try:
                record = cache_manager.client.collection("players").get_first_list_item(
                    f"sofascore_id = '{player_id}'"
                )
                player_name = record.name
            except Exception:
                pass

        if not player_name:
            return {"stats": []}

        # Tìm trong player_season_stats theo tên (lowercase, dấu gạch dưới)
        search_id = player_name.lower().replace(" ", "_")
        try:
            records = cache_manager.client.collection("player_season_stats").get_full_list(
                query_params={"filter": f"player_id = '{search_id}'"}
            )
            
            stats_list = []
            for r in records:
                # Trích xuất dữ liệu thô từ record
                stats_list.append({
                    "id": r.id,
                    "player_id": r.player_id,
                    "team_id": r.team_id,
                    "league_id": r.league_id,
                    "season": r.season,
                    "stat_type": r.stat_type,
                    "source": r.source,
                    "games": getattr(r, "games", None),
                    "games_starts": getattr(r, "games_starts", None),
                    "minutes": getattr(r, "minutes", None),
                    "goals": getattr(r, "goals", None),
                    "assists": getattr(r, "assists", None),
                    "xg": getattr(r, "xg", None),
                    "xg_assist": getattr(r, "xg_assist", None),
                    "key_passes": getattr(r, "key_passes", None),
                    "tackles": getattr(r, "tackles", None),
                    "tackles_won": getattr(r, "tackles_won", None),
                    "interceptions": getattr(r, "interceptions", None),
                    "pass_pct": getattr(r, "pass_pct", None)
                })
            return {"stats": stats_list}
        except Exception as pb_err:
            print(f"[Server Warning] Could not fetch stats from player_season_stats: {pb_err}")
            return {"stats": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- ENDPOINT WEBSOCKET GATEWAY ---

@app.websocket("/ws/live")
async def websocket_live_gateway(websocket: WebSocket):
    """
    Cổng kết nối WebSocket nhận tỷ số trực tiếp toàn thế giới từ NATS
    """
    await manager.connect(websocket)
    try:
        # Giữ kết nối hoạt động bằng cách liên tục lắng nghe tin nhắn rỗng từ client (Heartbeat)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        print(f"[WS SERVER] Lỗi kết nối client: {e}")
        manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    # Khởi chạy server uvicorn trực tiếp
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False)
