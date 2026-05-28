import asyncio
import json
import os
import sys
from datetime import datetime
import httpx

# Add sofascore-demo-server to path so we can import cache_manager
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "sofascore-demo-server")))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from cache_manager import PocketBaseCacheManager

API_BASE = "http://127.0.0.1:8000"

async def get_player_details(player_name: str):
    print(f"==================================================")
    print(f"  OFFLINE PLAYER STATS FINDER — '{player_name}'")
    print(f"==================================================")

    # 1. Thử lấy dữ liệu từ FastAPI server trước
    server_online = False
    search_results = None
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{API_BASE}/api/search?q={player_name}")
            if resp.status_code == 200:
                search_results = resp.json()
                server_online = True
                print("[INFO] Đang tải qua REST API server...")
    except Exception:
        print("[INFO] REST API server offline. Chuyển sang truy vấn PocketBase trực tiếp...")

    # 2. Khởi tạo PocketBase Cache Manager để làm fallback/truy vấn trực tiếp
    cm = PocketBaseCacheManager()
    
    # 3. Tìm kiếm cầu thủ (Offline Search)
    target_player = None
    player_id = None

    if server_online and search_results:
        players = [item for item in search_results.get("results", []) if item.get("type") == "player"]
        if players:
            target_player = players[0].get("entity", {})
            player_id = target_player.get("id")
    
    # Nếu server offline hoặc không tìm thấy, truy vấn DB trực tiếp
    if not player_id:
        try:
            db_search = await cm.search_player_cache(player_name)
            players = db_search.get("results", [])
            if players:
                target_player = players[0].get("entity", {})
                player_id = target_player.get("id")
        except Exception as e:
            print(f"[ERR] Lỗi tìm kiếm DB: {e}")

    if not player_id:
        print(f"Không tìm thấy cầu thủ '{player_name}' trong dữ liệu offline.")
        return

    display_name = target_player.get("name", player_name)
    current_team = (target_player.get("team") or {}).get("name", "Không rõ CLB")
    print(f"Tìm thấy: {display_name} (ID: {player_id}) - Đội bóng: {current_team}")

    # 4. Lấy thông tin cá nhân chi tiết (Offline Details)
    p_data = None
    attributes = {}

    if server_online:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{API_BASE}/api/player/{player_id}")
                if resp.status_code == 200:
                    player_detail = resp.json()
                    p_data = player_detail.get("player", {})
                    attributes = player_detail.get("attributes", {})
        except Exception:
            pass

    if not p_data:
        # Truy cập DB trực tiếp
        db_detail = await cm.get_player_cache(player_id, ignore_expiration=True)
        if db_detail:
            p_data = db_detail.get("player", {})
            attributes = db_detail.get("attributes", {})
        else:
            p_data = target_player

    # Parse thông tin cá nhân
    height = p_data.get("height", "N/A")
    foot = p_data.get("preferredFoot", "N/A")
    market_val = p_data.get("proposedMarketValueRaw", {}).get("value", 0) or p_data.get("proposedMarketValue", 0)
    dob_ts = p_data.get("dateOfBirthTimestamp")
    
    dob_str = "N/A"
    age = "N/A"
    if dob_ts:
        dob_dt = datetime.fromtimestamp(dob_ts)
        dob_str = dob_dt.strftime("%d/%m/%Y")
        today = datetime.now()
        age = today.year - dob_dt.year - ((today.month, today.day) < (dob_dt.month, dob_dt.day))
    elif p_data.get("dateOfBirth"):
        try:
            dob_dt = datetime.fromisoformat(p_data.get("dateOfBirth").replace("Z", "+00:00"))
            dob_str = dob_dt.strftime("%d/%m/%Y")
            today = datetime.now()
            age = today.year - dob_dt.year - ((today.month, today.day) < (dob_dt.month, dob_dt.day))
        except Exception:
            pass

    market_val_str = f"€{market_val/1000000:,.1f}M" if market_val else "N/A"
    
    print("\n=== THÔNG TIN CÁ NHÂN (PERSONAL INFO - OFFLINE) ===")
    print(f" - Tên đầy đủ: {p_data.get('name')}")
    print(f" - Ngày sinh: {dob_str} ({age} tuổi)")
    print(f" - Quốc tịch: {p_data.get('country', {}).get('name', 'N/A')}")
    print(f" - Chiều cao: {height} cm")
    print(f" - Chân thuận: {foot}")
    print(f" - Số áo: {p_data.get('jerseyNumber', p_data.get('shirtNumber', 'N/A'))}")
    print(f" - Vị trí: {p_data.get('position', 'N/A')}")
    print(f" - Giá trị chuyển nhượng: {market_val_str}")

    # 5. Lấy thống kê chi tiết (Offline Statistics)
    print("\n=== THỐNG KÊ CHI TIẾT MÙA GIẢI (SEASON STATS) ===")
    stats_list = []

    if server_online:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{API_BASE}/api/player/{player_id}/stats")
                if resp.status_code == 200:
                    stats_list = resp.json().get("stats", [])
        except Exception:
            pass

    if not stats_list:
        # Truy cập DB trực tiếp
        try:
            search_id = display_name.lower().replace(" ", "_")
            stats_records = cm.client.collection("player_season_stats").get_full_list(
                query_params={"filter": f"player_id = '{search_id}'"}
            )
            for r in stats_records:
                stats_list.append(r.to_dict() if hasattr(r, 'to_dict') else r.__dict__)
        except Exception as e:
            print(f"[WARN] Lỗi lấy stats trực tiếp từ DB: {e}")

    # Hiển thị thống kê FBref / Understat
    if stats_list:
        for idx, s in enumerate(stats_list):
            source = str(s.get("source", "N/A")).upper()
            season = s.get("season", "N/A")
            stat_type = s.get("stat_type", "N/A")
            print(f"\n[{idx+1}] Nguồn: {source} | Mùa giải: {season} | Loại: {stat_type}")
            
            if s.get("games") is not None:
                print(f"  - Số trận thi đấu (Appearances): {s.get('games')}")
            if s.get("games_starts") is not None:
                print(f"  - Số trận đá chính: {s.get('games_starts')}")
            if s.get("minutes") is not None:
                print(f"  - Số phút thi đấu: {s.get('minutes')}")
            if s.get("goals") is not None:
                print(f"  - Bàn thắng (Goals): {s.get('goals')}")
            if s.get("assists") is not None:
                print(f"  - Kiến tạo (Assists): {s.get('assists')}")
            if s.get("xg") is not None:
                print(f"  - Bàn thắng kỳ vọng (xG): {s.get('xg'):.2f}")
            if s.get("xg_assist") is not None:
                print(f"  - Kiến tạo kỳ vọng (xA): {s.get('xg_assist'):.2f}")
            if s.get("key_passes") is not None:
                print(f"  - Key Passes: {s.get('key_passes')}")
            if s.get("tackles") is not None:
                tackles_won = s.get("tackles_won") or 0
                print(f"  - Tắc bóng thành công: {tackles_won} / {s.get('tackles')}")
            if s.get("interceptions") is not None:
                print(f"  - Cắt bóng (Interceptions): {s.get('interceptions')}")
            if s.get("pass_pct") is not None:
                print(f"  - Tỷ lệ chuyền bóng chính xác: {s.get('pass_pct'):.1f}%")
    else:
        # Fallback: Hiển thị chỉ số radar nếu có cached Sofascore attributes
        overview = (attributes.get("playerAttributeOverviews") and attributes["playerAttributeOverviews"][0]) or {}
        if overview:
            print("\n(Không tìm thấy dữ liệu thống kê FBref/Understat. Hiển thị chỉ số Sofascore đã cache)")
            print(f"  - Tấn công (Attacking): {overview.get('attacking', 'N/A')}")
            print(f"  - Kỹ thuật (Technical): {overview.get('technical', 'N/A')}")
            print(f"  - Chiến thuật (Tactical): {overview.get('tactical', 'N/A')}")
            print(f"  - Phòng ngự (Defending): {overview.get('defending', 'N/A')}")
            print(f"  - Sáng tạo (Creativity): {overview.get('creativity', 'N/A')}")
        else:
            print("\nKhông có dữ liệu thống kê offline nào cho cầu thủ này.")

if __name__ == "__main__":
    # Bukayo Saka là cầu thủ đã có sẵn trong offline database
    asyncio.run(get_player_details("Bukayo Saka"))
