import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from typing import List, Dict, Any, Optional

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException
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

cache_manager = PocketBaseCacheManager()
live_updater  = LiveScoreUpdater()

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
    global api_instance, realtime_client, persister
    
    # Khởi tạo API và Realtime Client
    print("[Server] Đang khởi tạo ứng dụng và kết nối đến Sofascore...")
    api_instance = SofascoreAPI()
    realtime_client = SofascoreRealtimeClient(api_instance.token_manager)
    persister = PostMatchPersister(api_instance)
    
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

# --- CÁC ENDPOINT HTTP REST API ---

@app.get("/")
async def root():
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/match/{match_id}/stats")
async def api_match_stats(match_id: int):
    """
    Lấy thông tin thống kê chi tiết của một trận đấu
    """
    try:
        match_obj = Match(api_instance, match_id=match_id)
        res = await match_obj.stats()
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/match/{match_id}/commentary")
async def api_match_commentary(match_id: int):
    """
    Lấy nội dung bình luận trực tiếp bằng văn bản (live text commentary)
    """
    try:
        match_obj = Match(api_instance, match_id=match_id)
        res = await match_obj.commentary()
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/team/{team_id}")
async def api_team_info(team_id: int):
    """
    Lấy chi tiết thông tin về một câu lạc bộ bóng đá
    """
    # 1. Thử lấy từ cache PocketBase trước
    cached_data = await cache_manager.get_team_cache(team_id)
    if cached_data:
        return cached_data

    # 2. Cache MISS -> Gọi wrapper để lấy dữ liệu mới
    try:
        team_obj = Team(api_instance, team_id=team_id)
        res = await team_obj.get_team()
        
        # 3. Ghi cache ngầm
        asyncio.create_task(cache_manager.set_team_cache(team_id, res))
        
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/team/{team_id}/transfers")
async def api_team_transfers(team_id: int):
    """
    Lấy lịch sử chuyển nhượng cầu thủ mới nhất của câu lạc bộ
    """
    cached_data = await cache_manager.get_transfer_cache(team_id)
    if cached_data:
        return cached_data

    try:
        team_obj = Team(api_instance, team_id=team_id)
        trans_in = await team_obj.transfers_in()
        trans_out = await team_obj.transfers_out()
        res = {
            "transfers_in": trans_in,
            "transfers_out": trans_out
        }

        asyncio.create_task(cache_manager.set_transfer_cache(team_id, res))

        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/team/{team_id}/players")
async def api_team_squad(team_id: int):
    """
    Lấy danh sách đội hình cầu thủ của câu lạc bộ
    """
    # 1. Thử lấy từ cache PocketBase trước
    cached_data = await cache_manager.get_squad_cache(team_id)
    if cached_data:
        return cached_data

    # 2. Cache MISS -> Gọi wrapper
    try:
        team_obj = Team(api_instance, team_id=team_id)
        res = await team_obj.squad()
        
        # 3. Ghi cache ngầm
        asyncio.create_task(cache_manager.set_squad_cache(team_id, res))
        
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/player/{player_id}")
async def api_player_detail(player_id: int):
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
        player_obj = Player(api_instance, player_id=player_id)
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
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/league/{league_id}/standings")
async def api_league_standings(league_id: int, season: Optional[int] = None):
    """
    Lấy bảng xếp hạng giải đấu. Nếu không truyền mã mùa giải 'season', server tự động lấy mùa mới nhất.
    """
    try:
        league_obj = League(api_instance, league_id=league_id)
        
        # Tự động lấy season hiện tại nếu không truyền
        if not season:
            season_obj = await league_obj.current_season()
            if not season_obj:
                raise Exception("Không thể tự động tìm thấy mùa giải hiện tại cho giải đấu này.")
            season = season_obj["id"]
            
        # 1. Thử lấy từ cache PocketBase trước
        cached_data = await cache_manager.get_league_cache(league_id, season)
        cached_league = (cached_data or {}).get("league") or {}
        if cached_data and cached_league.get("name"):
            return cached_data

        # 2. Cache MISS -> Gọi wrapper để lấy dữ liệu mới từ Sofascore
        res = await league_obj.standings(season)
        
        # Lấy thêm thông tin chi tiết giải đấu để điền vào cache (Name, Country, Flag)
        try:
            league_info = await league_obj.get_league()
            league_info = league_info.get("uniqueTournament") or league_info
            category = league_info.get("category") or {}
            name = league_info.get("name", "")
            country = category.get("name", "")
            flag = category.get("flag", "")
        except Exception as info_err:
            print(f"[Server Warning] Không lấy được thông tin chi tiết giải đấu {league_id}: {info_err}")
            name, country, flag = "", "", ""

        response_data = {
            "season_id": season,
            "league": {
                "id": league_id,
                "name": name,
                "country": country,
                "flag": flag
            },
            "standings": res
        }

        # 3. Ghi cache ngầm
        asyncio.create_task(cache_manager.set_league_cache(
            league_id=league_id,
            season_id=season,
            raw_json=response_data,
            name=name,
            country=country,
            flag=flag
        ))
        
        return response_data
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
