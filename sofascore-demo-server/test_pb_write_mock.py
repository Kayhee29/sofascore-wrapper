import sys
import os
import asyncio
import datetime

# Configure stdout to use UTF-8
sys.stdout.reconfigure(encoding='utf-8')

# Add parent dir to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from cache_manager import PocketBaseCacheManager

async def main():
    print("Initializing PocketBaseCacheManager...")
    cache_manager = PocketBaseCacheManager()
    
    test_team_id = 999999  # Mock Team ID
    mock_team_data = {
        "team": {
            "id": test_team_id,
            "name": "Mock FC",
            "fullName": "Mock Football Club",
            "manager": {"name": "Test Manager"},
            "venue": {"name": "Test Venue", "capacity": 50000},
            "sport": {"slug": "football"}
        }
    }
    
    try:
        # 1. Clean up existing mock cache if any
        try:
            record = cache_manager.client.collection("teams").get_first_list_item(f"sofascore_id = '{test_team_id}'")
            print(f"Found existing mock team record: {record.id}. Deleting...")
            cache_manager.client.collection("teams").delete(record.id)
            print("Deleted old mock record.")
        except Exception:
            print("No existing mock record found.")
            
        # 2. Write mock data
        print(f"Saving mock team {test_team_id} to PocketBase...")
        await cache_manager.set_team_cache(test_team_id, mock_team_data)
        
        # 3. Read back
        print("Reading mock team back from PocketBase...")
        retrieved = await cache_manager.get_team_cache(test_team_id)
        
        if retrieved and retrieved.get("team", {}).get("name") == "Mock FC":
            print("\n[SUCCESS] Writing to and reading from PocketBase is working perfectly!")
            print(f"Retrieved Team Name: {retrieved['team']['name']}")
            print(f"Retrieved Full Name: {retrieved['team']['fullName']}")
            print(f"Retrieved Manager: {retrieved['team']['manager']['name']}")
        else:
            print("\n[FAILURE] Retrieved data is incorrect or missing.")
            
        # 4. Clean up mock record
        try:
            record = cache_manager.client.collection("teams").get_first_list_item(f"sofascore_id = '{test_team_id}'")
            cache_manager.client.collection("teams").delete(record.id)
            print("Cleaned up mock team record from database.")
        except Exception as e:
            print(f"Error cleaning up mock record: {e}")
            
    except Exception as e:
        print(f"[ERROR] Exception occurred during mock test: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
