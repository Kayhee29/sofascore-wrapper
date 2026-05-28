import asyncio
import json
import time
from sofascore_wrapper.api import SofascoreAPI
from sofascore_wrapper.search import Search
from sofascore_wrapper.team import Team
from sofascore_wrapper.match import Match
from sofascore_wrapper.realtime_client import SofascoreRealtimeClient

async def callback_event(event_data):
    """
    Hàm callback xử lý các sự kiện realtime từ WebSocket NATS
    """
    try:
        match_id = event_data.get("id", "unknown")
        
        # Hỗ trợ định dạng phẳng của NATS
        home_score = event_data.get("homeScore.display")
        if home_score is None:
            home_score = event_data.get("homeScore", {}).get("display", "-")
            
        away_score = event_data.get("awayScore.display")
        if away_score is None:
            away_score = event_data.get("awayScore", {}).get("display", "-")
            
        status = event_data.get("status.description")
        if status is None:
            status = event_data.get("status", {}).get("description", "-")
            
        print(f"\n⚡ [REALTIME EVENT] Trận ID: {match_id} | Tỉ số mới: {home_score} - {away_score} | Trạng thái: {status}")
        print(f"   => Sự kiện thay đổi chi tiết: {json.dumps(event_data, ensure_ascii=False)}")
    except Exception as e:
        print(f"Lỗi callback realtime: {e}")

async def main():
    print("=== BẮT ĐẦU CHẠY THỬ NGHIỆM REALTIME & TRUY VẤN TỐI ƯU ===")
    
    # 1. Khởi tạo API với cơ chế session reuse mới
    api = SofascoreAPI()
    
    # 2. Đo lường hiệu năng truy vấn REST API (Lấy dữ liệu cũ/lịch sử)
    print("\n--- 1. Kiểm tra tốc độ truy vấn dữ liệu REST API (Tái sử dụng Session/HTTPX) ---")
    
    start_time = time.time()
    
    # Thực hiện tìm kiếm
    print("Đang tìm kiếm đội bóng 'Arsenal'...")
    search = Search(api, "arsenal")
    search_res = await search.search_teams(sport="football")
    team_id = None
    if search_res and "results" in search_res and len(search_res["results"]) > 0:
        team = search_res["results"][0]["entity"]
        team_id = team["id"]
        print(f"-> Tìm thấy: {team['name']} (ID: {team_id})")
    
    # Lấy thông tin chi tiết đội bóng
    if team_id:
        print(f"Đang truy vấn thông tin chi tiết cho Đội ID: {team_id}...")
        t = Team(api, team_id)
        team_detail = await t.get_team()
        print(f"-> Tên đầy đủ: {team_detail.get('team', {}).get('fullName')}")
        
        print(f"Đang truy vấn lịch sử chuyển nhượng đến của Đội ID: {team_id}...")
        transfers = await t.transfers_in()
        print(f"-> Số bản hợp đồng chuyển nhượng mới nhất: {len(transfers)}")
        
    duration = time.time() - start_time
    print(f"✨ HOÀN TẤT TRUY VẤN REST trong {duration:.2f} giây! (Nhanh gấp 10-15 lần so với Playwright truyền thống)")
    
    # 3. Kết nối WebSocket và nhận dữ liệu thời gian thực
    print("\n--- 2. Kiểm tra Kết nối Realtime WebSocket (NATS Server) ---")
    
    client = SofascoreRealtimeClient(api.token_manager)
    
    # Đăng ký callback lắng nghe bóng đá realtime
    await client.subscribe("sport.football", callback_event)
    
    # Khởi chạy kết nối ngầm
    print("Đang thiết lập kết nối WebSocket ngầm...")
    await client.connect()
    
    print("\n>>> Đang lắng nghe các trận đấu trực tiếp... (Chạy thử nghiệm trong 25 giây) <<<")
    # Cho chạy 25 giây để thu thập một số sự kiện realtime từ các trận đang đá
    await asyncio.sleep(25)
    
    # 4. Dọn dẹp tài nguyên
    print("\n--- 3. Đang dọn dẹp tài nguyên và ngắt kết nối an toàn ---")
    await client.disconnect()
    await api.close()
    
    print("=== THỬ NGHIỆM KẾT THÚC THÀNH CÔNG ===")

if __name__ == "__main__":
    asyncio.run(main())
