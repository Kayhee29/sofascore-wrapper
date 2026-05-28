import asyncio
from cache_manager import PocketBaseCacheManager
from pocketbase.utils import ClientResponseError

async def test():
    cm = PocketBaseCacheManager()
    team_id = "Arsenal"
    try:
        team_rec = cm.client.collection("teams").get_first_list_item(
            f"name = '{team_id}' || fullName = '{team_id}'"
        )
        print(f"Success! Found team: {team_rec.name}, sofascore_id: {team_rec.sofascore_id}")
        d = team_rec.to_dict() if hasattr(team_rec, 'to_dict') else team_rec.__dict__
        print(f"Fields: {list(d.keys())}")
    except ClientResponseError as e:
        print(f"ClientResponseError: status={e.status}, data={e.data}")
    except Exception as e:
        print(f"Generic error: {e}")

if __name__ == "__main__":
    asyncio.run(test())




