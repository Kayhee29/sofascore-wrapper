import asyncio
import json
from datetime import datetime
from sofascore_wrapper.api import SofascoreAPI
from sofascore_wrapper.search import Search
from sofascore_wrapper.player import Player, PlayerSearch

async def get_player_details(player_name: str):
    api = SofascoreAPI()
    try:
        print(f"Searching for player: '{player_name}'...")
        # 1. Tìm kiếm cầu thủ để lấy ID
        search = PlayerSearch(api, player_name)
        search_results = await search.search_player()
        
        players = search_results.get("players", [])
        if not players:
            print(f"No player found matching '{player_name}'.")
            return
            
        # Lấy cầu thủ đầu tiên khớp nhất
        target_player = players[0]
        player_id = target_player.get("id")
        display_name = target_player.get("name")
        current_team = target_player.get("team", {}).get("name", "Unknown")
        print(f"Found: {display_name} (ID: {player_id}) - Playing for {current_team}")
        
        # 2. Lấy thông tin cá nhân chi tiết
        player_obj = Player(api, player_id)
        info = await player_obj.get_player()
        p_data = info.get("player", {})
        
        # Parse thông tin cá nhân
        height = p_data.get("height", "N/A")
        foot = p_data.get("preferredFoot", "N/A")
        market_val = p_data.get("proposedMarketValueRaw", {}).get("value", 0)
        dob_ts = p_data.get("dateOfBirthTimestamp")
        
        dob_str = "N/A"
        age = "N/A"
        if dob_ts:
            dob_dt = datetime.fromtimestamp(dob_ts)
            dob_str = dob_dt.strftime("%d/%m/%Y")
            # Tính tuổi
            today = datetime.now()
            age = today.year - dob_dt.year - ((today.month, today.day) < (dob_dt.month, dob_dt.day))

        market_val_str = f"€{market_val/1000000:,.1f}M" if market_val else "N/A"
        
        print("\n=== THÔNG TIN CÁ NHÂN (PERSONAL INFO) ===")
        print(f" - Tên đầy đủ: {p_data.get('name')}")
        print(f" - Ngày sinh: {dob_str} ({age} tuổi)")
        print(f" - Quốc tịch: {p_data.get('country', {}).get('name', 'N/A')}")
        print(f" - Chiều cao: {height} cm")
        print(f" - Chân thuận: {foot}")
        print(f" - Số áo: {p_data.get('jerseyNumber', 'N/A')}")
        print(f" - Vị trí: {p_data.get('position', 'N/A')}")
        print(f" - Giá trị chuyển nhượng: {market_val_str}")
        
        # 3. Lấy chỉ số mùa giải Ngoại Hạng Anh 24/25 hoặc 25/26
        # Premier League unique tournament ID = 17
        # Season ID: 24/25 là 61627. Hãy thử lấy stats mùa giải này.
        try:
            season_id = 61627  # Mùa 24/25 để có nhiều dữ liệu stats thực tế
            print(f"\nFetching Premier League 24/25 Season Stats (Season ID: {season_id})...")
            stats_data = await player_obj.league_stats(league_id=17, season=season_id)
            stats = stats_data.get("statistics", {})
            
            if stats:
                print("\n=== THỐNG KÊ CHI TIẾT MÙA GIẢI ===")
                print(f" - Điểm đánh giá trung bình (Rating): {stats.get('rating', 'N/A')}")
                print(f" - Số trận thi đấu (Appearances): {stats.get('appearances', 0)}")
                print(f" - Số trận đá chính: {stats.get('matchesStarted', 0)}")
                print(f" - Số phút thi đấu: {stats.get('minutesPlayed', 0)}")
                print(f" - Bàn thắng (Goals): {stats.get('goals', 0)}")
                print(f" - Kiến tạo (Assists): {stats.get('assists', 0)}")
                print(f" - Kiến tạo kỳ vọng (xA): {stats.get('expectedAssists', 0):.2f}")
                print(f" - Bàn thắng kỳ vọng (xG): {stats.get('expectedGoals', 0):.2f}")
                print(f" - Key Passes (Đường chuyền mở cơ hội): {stats.get('keyPasses', 0)}")
                print(f" - Số lần lọt Đội hình tiêu biểu tuần (TOTW): {stats.get('totwAppearances', 0)}")
                print(f" - Tắc bóng thành công: {stats.get('tacklesWon', 0)} / {stats.get('tackles', 0)}")
                print(f" - Cắt bóng (Interceptions): {stats.get('interceptions', 0)}")
                print(f" - Tỷ lệ chuyền bóng chính xác: {stats.get('accuratePassesPercentage', 0):.1f}%")
            else:
                print("No stats data available for this season.")
        except Exception as e:
            print(f"Could not fetch league stats: {e}")
            
    finally:
        await api.close()

if __name__ == "__main__":
    # Test với cầu thủ Bukayo Saka của Arsenal
    asyncio.run(get_player_details("Bukayo Saka"))
