import asyncio
import json
from datetime import datetime
from sofascore_wrapper.api import SofascoreAPI
from sofascore_wrapper.team import Team

async def get_arsenal_fixtures_may():
    api = SofascoreAPI()
    try:
        # Arsenal ID = 42
        team = Team(api, 42)
        
        print("Fetching next and last fixtures for Arsenal...")
        # Lấy cả next và last fixtures
        try:
            next_f = await team.next_fixtures()
        except Exception as e:
            print(f"Warning: Failed to fetch next fixtures: {e}")
            next_f = []
            
        try:
            last_f = await team.last_fixtures()
        except Exception as e:
            print(f"Warning: Failed to fetch last fixtures: {e}")
            last_f = []
            
        all_fixtures = last_f + next_f
        print(f"Retrieved {len(all_fixtures)} total raw fixtures. Filtering for May 2026...")
        
        # Lọc các trận đấu trong tháng 5/2026
        may_fixtures = []
        for event in all_fixtures:
            start_ts = event.get("startTimestamp")
            if not start_ts:
                continue
                
            dt = datetime.fromtimestamp(start_ts)
            # Kiểm tra xem trận đấu có thuộc tháng 5 năm 2026 không
            if dt.year == 2026 and dt.month == 5:
                # Trích xuất thông tin
                match_id = event.get("id")
                home_team = event.get("homeTeam", {}).get("name", "Unknown")
                away_team = event.get("awayTeam", {}).get("name", "Unknown")
                
                home_score = event.get("homeScore", {}).get("current", "-")
                away_score = event.get("awayScore", {}).get("current", "-")
                
                status_desc = event.get("status", {}).get("description", "Unknown")
                status_type = event.get("status", {}).get("type", "Unknown")
                tournament_name = event.get("tournament", {}).get("name", "Unknown")
                
                time_str = dt.strftime("%d/%m/%Y %H:%M")
                
                may_fixtures.append({
                    "id": match_id,
                    "datetime": time_str,
                    "timestamp": start_ts,
                    "tournament": tournament_name,
                    "home_team": home_team,
                    "away_team": away_team,
                    "home_score": home_score,
                    "away_score": away_score,
                    "status_desc": status_desc,
                    "status_type": status_type
                })
                
        # Loại bỏ các trận đấu trùng lặp (nếu có) bằng cách dùng ID làm key
        unique_fixtures = {}
        for f in may_fixtures:
            unique_fixtures[f["id"]] = f
            
        result_list = list(unique_fixtures.values())
        # Sắp xếp các trận đấu theo thời gian bắt đầu tăng dần
        result_list.sort(key=lambda x: x["timestamp"])
        
        output_file = r"C:\Users\Kayhee29\sofascore-wrapper\arsenal_may_fixtures.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(result_list, f, ensure_ascii=False, indent=2)
            
        print(f"\nSuccessfully saved {len(result_list)} May fixtures to {output_file}")
        
        # In ra bảng Markdown (Sử dụng nhãn tiếng Anh để tránh lỗi Unicode console trên Windows)
        print("\n### ARSENAL FIXTURES - MAY 2026")
        print("| Match ID | Date & Time | Tournament | Home Team | Score | Away Team | Status |")
        print("|---|---|---|---|---|---|---|")
        for f in result_list:
            score_str = f"{f['home_score']} - {f['away_score']}" if f['status_type'] == 'finished' else "vs"
            print(f"| {f['id']} | {f['datetime']} | {f['tournament']} | {f['home_team']} | {score_str} | {f['away_team']} | {f['status_desc']} |")
            
    finally:
        await api.close()

if __name__ == "__main__":
    asyncio.run(get_arsenal_fixtures_may())
