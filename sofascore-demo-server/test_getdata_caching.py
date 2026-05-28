import sys
import os
import asyncio
import httpx

# Configure stdout to use UTF-8
sys.stdout.reconfigure(encoding='utf-8')

# Add parent dir to path so we can import sofascore_wrapper
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from sofascore_wrapper.api import SofascoreAPI
from sofascore_wrapper.team import Team
from cache_manager import PocketBaseCacheManager

async def main():
    # 1. Initialize API and Cache Manager
    print("Initializing SofascoreAPI...")
    api = SofascoreAPI()
    print("Initializing PocketBaseCacheManager...")
    cache_manager = PocketBaseCacheManager()
    
    try:
        # 2. Let's pick a team ID that we want to test with.
        # Chelsea has ID 38. Let's see if we can get Chelsea's details.
        team_id = 38
        
        print(f"Checking if Team ID {team_id} (Chelsea) is already in PocketBase...")
        cached_team = await cache_manager.get_team_cache(team_id)
        if cached_team:
            print(f"Found cached team: {cached_team['team']['name']}. Deleting it from PocketBase to test cache-miss flow...")
            # Delete record
            record = cache_manager.client.collection("teams").get_first_list_item(f"sofascore_id = '{team_id}'")
            cache_manager.client.collection("teams").delete(record.id)
            print("Successfully deleted team cache.")
        else:
            print("Team cache is not present in PocketBase.")
        
        # 3. Call Sofascore API to get team details (cache-miss getdata simulation)
        print(f"Fetching Team ID {team_id} details from Sofascore via wrapper...")
        team_wrapper = Team(api, team_id=team_id)
        team_data = await team_wrapper.get_team()
        print(f"Fetched team: {team_data['team']['name']} ({team_data['team']['fullName']})")
        
        # 4. Save to pocketbase
        print(f"Saving Team ID {team_id} details to PocketBase...")
        await cache_manager.set_team_cache(team_id, team_data)
        
        # 5. Read back from pocketbase and verify
        print("Reading back from PocketBase cache...")
        retrieved_team = await cache_manager.get_team_cache(team_id)
        if retrieved_team and retrieved_team.get("team", {}).get("name") == "Chelsea":
            print("[SUCCESS] Caching flow works perfectly! Data was written and retrieved correctly from PocketBase.")
            if "logo_url_local" in retrieved_team["team"]:
                print(f"Local logo URL in cache: {retrieved_team['team']['logo_url_local']}")
        else:
            print("[FAILURE] Caching flow failed: retrieved data did not match or was not found.")
            
    except Exception as e:
        print(f"[ERROR] Exception occurred during test: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await api.close()

if __name__ == "__main__":
    asyncio.run(main())
