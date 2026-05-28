import asyncio
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "sofascore-wrapper", "sofascore-demo-server")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "sofascore-wrapper")))

from cache_manager import PocketBaseCacheManager

async def check():
    cm = PocketBaseCacheManager()
    
    # 1. Get Arsenal squad from PocketBase
    squad_rec = await cm.get_squad_cache("42")
    if not squad_rec:
        # try 42_2025
        squad_rec = await cm.get_squad_cache("42_2025")
        
    if not squad_rec:
        print("No Arsenal squad cache found in PB!")
        return
        
    squad_players = squad_rec.get("players", [])
    print(f"Total players in cached squad: {len(squad_players)}")
    
    # 2. Get player_season_stats for Arsenal from PB
    # In soccerdata_sync.py, what is the team_id used?
    # Let's check team names in player_season_stats
    all_stats = cm.client.collection("player_season_stats").get_full_list(
        query_params={"filter": "season = '2025/26'"}
    )
    print(f"Total stats in PB for 2025/26: {len(all_stats)}")
    
    # Check team_ids present in all_stats
    team_ids_in_stats = set()
    for s in all_stats:
        team_ids_in_stats.add(s.team_id)
    print(f"Team IDs in stats (first 10): {list(team_ids_in_stats)[:10]}")
    
    # Get stats specifically for Arsenal
    arsenal_stats = [s for s in all_stats if s.team_id == "Arsenal"]
    print(f"Total Arsenal stats in PB for 2025/26: {len(arsenal_stats)}")
    
    # Let's inspect some of the Arsenal stats player names/ids
    print("\nFirst 10 Arsenal stats in PB:")
    for s in arsenal_stats[:10]:
        print(f"  player_id={s.player_id} | stat_type={s.stat_type}")
        
    # Let's see how they match with Arsenal squad players
    print("\nArsenal squad players:")
    matched_count = 0
    for idx, p in enumerate(squad_players):
        p_obj = p.get("player", {})
        p_name = p_obj.get("name", "")
        p_id = p_obj.get("id")
        
        slug_name = p_name.lower().replace(" ", "_")
        slug_short = p_obj.get("shortName", "").lower().replace(" ", "_")
        
        # Check matches
        matches = [s for s in arsenal_stats if s.player_id == slug_name or s.player_id == slug_short or str(p_id) == s.player_id]
        if matches:
            matched_count += 1
            print(f"  {idx+1}. {p_name} (ID={p_id}) -> MATCHED {len(matches)} stats ({[m.stat_type for m in matches]})")
        else:
            # Let's print some info to see why it didn't match
            # What are the player_ids in arsenal_stats that might be close?
            close_ids = [s.player_id for s in arsenal_stats if slug_name[:5] in s.player_id or s.player_id[:5] in slug_name]
            print(f"  {idx+1}. {p_name} (ID={p_id}) -> NOT MATCHED. Close IDs: {close_ids}")
            
    print(f"\nTotal matched players: {matched_count} out of {len(squad_players)}")

if __name__ == "__main__":
    asyncio.run(check())
