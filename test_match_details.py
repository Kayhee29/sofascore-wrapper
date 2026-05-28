import asyncio
import json
from sofascore_wrapper.api import SofascoreAPI
from sofascore_wrapper.match import Match

async def get_match_full_details(match_id: int):
    api = SofascoreAPI()
    try:
        match = Match(api, match_id)
        
        print(f"Fetching details for Match ID: {match_id}...")
        
        # 1. Thông tin chung & Trọng tài
        try:
            info = await match.get_match()
        except Exception as e:
            print(f"Failed to fetch match info: {e}")
            info = {}
            
        # 2. Các sự kiện chính (incidents)
        try:
            incidents_data = await match.incidents()
        except Exception as e:
            print(f"Failed to fetch incidents: {e}")
            incidents_data = {}
            
        # 3. Đội hình đội nhà
        try:
            lineup_h = await match.lineups_home()
        except Exception as e:
            print(f"Failed to fetch home lineups: {e}")
            lineup_h = {}
            
        # 4. Đội hình đội khách
        try:
            lineup_a = await match.lineups_away()
        except Exception as e:
            print(f"Failed to fetch away lineups: {e}")
            lineup_a = {}

        # Tổng hợp dữ liệu thô để phân tích
        raw_data = {
            "info": info,
            "incidents": incidents_data,
            "lineup_home": lineup_h,
            "lineup_away": lineup_a
        }
        
        # Lưu file thô để debug nếu cần
        with open("raw_match_data.json", "w", encoding="utf-8") as f:
            json.dump(raw_data, f, ensure_ascii=False, indent=2)
            
        # Bắt đầu phân tích
        event_info = info.get("event", info) # Đôi khi Sofascore bọc trong "event"
        referee = event_info.get("referee", {}).get("name", "Unknown Referee")
        
        home_team_name = event_info.get("homeTeam", {}).get("name", "Home Team")
        away_team_name = event_info.get("awayTeam", {}).get("name", "Away Team")
        
        # Sơ đồ & Màu kit
        home_formation = lineup_h.get("formation", "N/A")
        away_formation = lineup_a.get("formation", "N/A")
        
        home_kit = lineup_h.get("player_colour", {})
        away_kit = lineup_a.get("player_colour", {})
        
        # Phân tích sự kiện
        goals = []
        cards = []
        subs = []
        
        incidents_list = incidents_data.get("incidents", [])
        # Sắp xếp sự kiện theo thời gian tăng dần
        incidents_list = sorted(incidents_list, key=lambda x: x.get("time", 0))
        
        for inc in incidents_list:
            inc_type = inc.get("incidentType")
            time = inc.get("time")
            added_time = inc.get("addedTime")
            time_str = f"{time}'" if not added_time else f"{time}+{added_time}'"
            
            player_name = inc.get("player", {}).get("name", "Unknown Player")
            is_home = inc.get("isHome", False)
            team_acted = home_team_name if is_home else away_team_name
            
            if inc_type == "goal":
                scorer = player_name
                assist_player = inc.get("assist", {}).get("name")
                assist_str = f" ( kiến tạo: {assist_player} )" if assist_player else ""
                goal_type = inc.get("incidentClass", "regular") # penalty, ownGoal, regular
                
                goal_desc = f"[{time_str}] {team_acted} Ghi bàn: {scorer}{assist_str} ({goal_type})"
                goals.append(goal_desc)
                
            elif inc_type == "card":
                card_color = inc.get("incidentClass") # yellow, red, yellowRed
                card_desc = f"[{time_str}] {team_acted} Thẻ: {player_name} ({card_color})"
                cards.append(card_desc)
                
            elif inc_type == "substitution":
                player_in = inc.get("playerIn", {}).get("name", "Unknown")
                player_out = inc.get("playerOut", {}).get("name", "Unknown")
                sub_desc = f"[{time_str}] {team_acted} Thay người: {player_in} vào thay {player_out}"
                subs.append(sub_desc)

        # Trích xuất danh sách cầu thủ ra sân chính thức
        def get_squad_names(lineup_dict):
            starters = []
            for p in lineup_dict.get("starters", []):
                p_name = p.get("player", {}).get("name", "Unknown")
                jersey = p.get("player", {}).get("jerseyNumber", "N/A")
                pos = p.get("player", {}).get("position", "N/A")
                rating = p.get("rating", "N/A")
                starters.append(f"#{jersey} {p_name} ({pos}) - Rating: {rating}")
                
            substitutes = []
            for p in lineup_dict.get("substitutes", []):
                p_name = p.get("player", {}).get("name", "Unknown")
                jersey = p.get("player", {}).get("jerseyNumber", "N/A")
                pos = p.get("player", {}).get("position", "N/A")
                rating = p.get("rating", "N/A")
                substitutes.append(f"#{jersey} {p_name} ({pos}) - Rating: {rating}")
                
            return starters, substitutes

        home_starters, home_subs = get_squad_names(lineup_h)
        away_starters, away_subs = get_squad_names(lineup_a)

        # Đóng gói dữ liệu sạch
        clean_result = {
            "match_id": match_id,
            "referee": referee,
            "home_team": {
                "name": home_team_name,
                "formation": home_formation,
                "kit_colors": home_kit,
                "starters": home_starters,
                "substitutes": home_subs
            },
            "away_team": {
                "name": away_team_name,
                "formation": away_formation,
                "kit_colors": away_kit,
                "starters": away_starters,
                "substitutes": away_subs
            },
            "incidents": {
                "goals": goals,
                "cards": cards,
                "substitutions": subs
            }
        }

        # Lưu file kết quả sạch
        output_file = f"match_{match_id}_details.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(clean_result, f, ensure_ascii=False, indent=2)
            
        print(f"\nSuccessfully saved parsed match details to {output_file}")
        
        # In ra màn hình (Nhãn tiếng Anh để tránh lỗi Unicode console)
        print("\n=============================================")
        print(f"MATCH REPORT: {home_team_name} vs {away_team_name}")
        print(f"Match ID: {match_id}")
        print(f"Referee: {referee}")
        print("=============================================")
        
        print(f"\n[FORMATIONS & KITS]")
        print(f" - {home_team_name}: Formation {home_formation} | Kit Colors: {home_kit}")
        print(f" - {away_team_name}: Formation {away_formation} | Kit Colors: {away_kit}")
        
        print("\n[GOALS]")
        for g in goals:
            print(f" ⚽ {g}")
        if not goals:
            print(" No goals scored.")
            
        print("\n[CARDS (YELLOW/RED)]")
        for c in cards:
            print(f" 🟨🟥 {c}")
        if not cards:
            print(" No cards issued.")
            
        print("\n[SUBSTITUTIONS]")
        for s in subs:
            print(f" 🔄 {s}")
        if not subs:
            print(" No substitutions made.")
            
        print(f"\n[{home_team_name.upper()} STARTERS]")
        for p in home_starters[:11]:
            print(f"   {p}")
            
        print(f"\n[{away_team_name.upper()} STARTERS]")
        for p in away_starters[:11]:
            print(f"   {p}")

    finally:
        await api.close()

if __name__ == "__main__":
    import sys
    match_id = 14023942
    if len(sys.argv) > 1:
        match_id = int(sys.argv[1])
    asyncio.run(get_match_full_details(match_id))
