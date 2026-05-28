import requests
import json

def test():
    print("=== Testing Standings ===")
    r = requests.get("http://127.0.0.1:8000/api/league/17/standings?season=2025/26")
    print(f"Status: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        print(f"Source: {data.get('_source')}")
        print(f"League name: {data.get('league', {}).get('name')}")
        print(f"Season ID: {data.get('season_id')}")
        standings = data.get("standings", {}).get("standings", [])
        if standings:
            rows = standings[0].get("rows", [])
            print(f"Total teams in standings: {len(rows)}")
            if rows:
                print("First 3 teams in standings:")
                for i, row in enumerate(rows[:3]):
                    team = row.get("team", {})
                    print(f"  {i+1}. {team.get('name')} (apifb_id={team.get('apifootball_id')}, logo={team.get('apifootball_logo_url')})")
        else:
            print("No standings found!")
            
    print("\n=== Testing Squad ===")
    r = requests.get("http://127.0.0.1:8000/api/team/42/players?season=2025/26")
    print(f"Status: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        print(f"Source: {data.get('_source')}")
        players = data.get("players", [])
        print(f"Total players in squad: {len(players)}")
        players_with_stats = 0
        for p in players:
            p_obj = p.get("player", {})
            stats = p_obj.get("stats", [])
            if stats:
                players_with_stats += 1
        print(f"Players with offline stats: {players_with_stats}")
        if players:
            print("First 3 players in squad:")
            for i, p in enumerate(players[:3]):
                p_obj = p.get("player", {})
                stats = p_obj.get("stats", [])
                stats_summary = [f"{s.get('source')}:{s.get('stat_type')} ({len(s)} fields)" for s in stats]
                print(f"  {i+1}. Name: {p_obj.get('name')} | Position: {p_obj.get('position')} | Stats: {stats_summary}")

if __name__ == "__main__":
    test()
