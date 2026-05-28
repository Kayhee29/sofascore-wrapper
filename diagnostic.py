import asyncio
import json
import traceback
from sofascore_wrapper.api import SofascoreAPI
from sofascore_wrapper.search import Search
from sofascore_wrapper.league import League
from sofascore_wrapper.team import Team
from sofascore_wrapper.match import Match

async def run_diagnostic():
    api = SofascoreAPI()
    results = {
        "working": [],
        "failed": []
    }

    async def test_endpoint(name, func, *args, **kwargs):
        print(f"Testing {name}...")
        try:
            res = await func(*args, **kwargs)
            results["working"].append({
                "name": name,
                "preview": str(res)[:200] + "..." if res else "None"
            })
            print(f"[SUCCESS] {name}")
            return res
        except Exception as e:
            err_msg = f"{type(e).__name__}: {str(e)}"
            results["failed"].append({
                "name": name,
                "error": err_msg,
                "traceback": traceback.format_exc()
            })
            print(f"[FAILED] {name}: {err_msg}")
            return None

    try:
        # 1. Test Search
        search = Search(api, "arsenal")
        await test_endpoint("Search.search_all", search.search_all, sport="football")
        await test_endpoint("Search.search_match", search.search_match, sport="football")
        await test_endpoint("Search.search_players", search.search_players, sport="football")
        await test_endpoint("Search.search_teams", search.search_teams, sport="football")
        await test_endpoint("Search.search_leagues", search.search_leagues, sport="football")

        # 2. Test League
        league = League(api, 17) # Premier League
        await test_endpoint("League.get_league", league.get_league)
        seasons = await test_endpoint("League.get_seasons", league.get_seasons)
        season_obj = await test_endpoint("League.current_season", league.current_season)
        
        current_season_id = None
        if season_obj and "id" in season_obj:
            current_season_id = season_obj["id"]
        elif seasons and len(seasons) > 0:
            current_season_id = seasons[0]["id"]
        
        if current_season_id:
            await test_endpoint("League.current_round", league.current_round, current_season_id)
            await test_endpoint("League.standings", league.standings, current_season_id)
            await test_endpoint("League.top_players", league.top_players, current_season_id)
            await test_endpoint("League.top_teams", league.top_teams, current_season_id)
        
        await test_endpoint("League.next_fixtures", league.next_fixtures)
        await test_endpoint("League.last_fixtures", league.last_fixtures)

        # 3. Test Team
        team = Team(api, 42) # Arsenal
        await test_endpoint("Team.get_team", team.get_team)
        await test_endpoint("Team.transfers_in", team.transfers_in)
        await test_endpoint("Team.transfers_out", team.transfers_out)
        await test_endpoint("Team.next_fixtures", team.next_fixtures)

        # 4. Test Match (Global/Live)
        match_global = Match(api)
        await test_endpoint("Match.live_games", match_global.live_games)
        await test_endpoint("Match.games_by_date", match_global.games_by_date, "football")

        # Let's search for a finished/live match to test individual Match details
        search_match = Search(api, "girona v arsenal")
        matches_res = await search_match.search_match(sport="football")
        match_id = None
        if matches_res and "results" in matches_res and len(matches_res["results"]) > 0:
            match_id = matches_res["results"][0]["entity"]["id"]
        else:
            # Fallback to a hardcoded match ID or another search
            match_id = 12764241 # From the example
            
        if match_id:
            print(f"Using match_id={match_id} for Match endpoint testing")
            match_detail = Match(api, match_id=match_id)
            await test_endpoint("Match.stats", match_detail.stats)
            await test_endpoint("Match.commentary", match_detail.commentary)
            await test_endpoint("Match.match_channels", match_detail.match_channels)
            await test_endpoint("Match.lineups_away", match_detail.lineups_away)
            await test_endpoint("Match.lineups_home", match_detail.lineups_home)

    finally:
        await api.close()

    # Output report to diagnostic_results.json
    with open("diagnostic_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=4)
        
    print("\n--- DIAGNOSTIC COMPLETED ---")
    print(f"Working endpoints: {len(results['working'])}")
    print(f"Failed endpoints: {len(results['failed'])}")
    print("Results saved to diagnostic_results.json")

if __name__ == "__main__":
    asyncio.run(run_diagnostic())
