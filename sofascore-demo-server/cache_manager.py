import asyncio
import datetime
import httpx
from typing import Optional, Dict, Any, Tuple
from pocketbase import PocketBase
from pocketbase.client import FileUpload
from pocketbase.utils import ClientResponseError

class PocketBaseCacheManager:
    def __init__(self, pb_url: str = "http://127.0.0.1:8090"):
        self.pb_url = pb_url
        self.client = PocketBase(pb_url)
        self._authenticate()

    def _authenticate(self):
        try:
            # Sử dụng httpx để đăng nhập qua endpoint tương thích ngược /api/admins/auth-with-password
            # nhằm tránh lỗi tương thích phiên bản SDK _superusers trong PocketBase v0.22+
            url = f"{self.pb_url}/api/admins/auth-with-password"
            payload = {
                "identity": "admin@sofascore.local",
                "password": "Admin123456789"
            }
            resp = httpx.post(url, json=payload, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                token = data["token"]
                # Lưu token vào auth_store của PocketBase SDK để tự động đính kèm vào mọi request tiếp theo
                self.client.auth_store.save(token, None)
                print("[CACHE] Đăng nhập và xác thực PocketBase Admin thành công qua HTTPX fallback.")
            else:
                print(f"[CACHE ERROR] Không thể đăng nhập PocketBase Admin qua HTTPX: {resp.text}")
        except Exception as e:
            print(f"[CACHE ERROR] Lỗi kết nối xác thực PocketBase Admin: {e}")

    async def _download_image(self, url: str) -> Optional[Tuple[str, bytes, str]]:
        """
        Tải ảnh từ một URL và trả về bộ ba (filename, content, mime_type) để upload
        """
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, timeout=5.0)
                if resp.status_code == 200:
                    # Trích xuất kiểu file từ Header Content-Type
                    content_type = resp.headers.get("Content-Type", "image/png")
                    ext = "png"
                    if "jpeg" in content_type:
                        ext = "jpg"
                    elif "webp" in content_type:
                        ext = "webp"
                    elif "svg" in content_type:
                        ext = "svg+xml"
                        
                    filename = f"image_{int(datetime.datetime.now().timestamp())}.{ext}"
                    return filename, resp.content, content_type
        except Exception as e:
            print(f"[CACHE] Không thể tải hình ảnh từ {url}: {e}")
        return None

    # --- SQUAD (ĐỘI HÌNH CÂU LẠC BỘ) CACHING ---
    
    async def get_squad_cache(self, team_id: int, ignore_expiration: bool = False) -> Optional[dict]:
        """
        Lấy cache đội hình của câu lạc bộ. Trả về dict hoặc None nếu hết hạn/không có.
        """
        try:
            record = self.client.collection("squads").get_first_list_item(
                f"sofascore_id = '{team_id}'"
            )
            # Kiểm tra thời hạn TTL
            if not ignore_expiration and record.ttl_expired:
                try:
                    expired_time = datetime.datetime.fromisoformat(record.ttl_expired.replace("Z", "+00:00"))
                    if datetime.datetime.now(datetime.timezone.utc) > expired_time:
                        print(f"[CACHE] Đội hình của Team ID {team_id} đã quá hạn TTL.")
                        return None
                except ValueError:
                    pass
                
            print(f"[CACHE HIT] Lấy Đội hình của Team ID {team_id} từ PocketBase.")
            return record.players_list
        except ClientResponseError:
            return None
        except Exception as e:
            print(f"[CACHE ERROR] Lỗi get_squad_cache: {e}")
            return None

    async def set_squad_cache(self, team_id: int, squad_data: dict, ttl_hours: int = 24):
        """
        Lưu cache đội hình của câu lạc bộ với thời hạn mặc định 24h.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        expired_time = now + datetime.timedelta(hours=ttl_hours)
        
        payload = {
            "sofascore_id": str(team_id),
            "players_list": squad_data,
            "ttl_expired": expired_time.isoformat()
        }
        
        try:
            # Kiểm tra xem đã có bản ghi chưa
            try:
                exist_rec = self.client.collection("squads").get_first_list_item(
                    f"sofascore_id = '{team_id}'"
                )
                self.client.collection("squads").update(exist_rec.id, payload)
                print(f"[CACHE] Đã cập nhật Đội hình của Team ID {team_id}.")
            except ClientResponseError:
                self.client.collection("squads").create(payload)
                print(f"[CACHE] Đã lưu mới Đội hình của Team ID {team_id}.")

            await self.cache_players_from_squad(team_id, squad_data)
        except Exception as e:
            print(f"[CACHE ERROR] Lỗi set_squad_cache: {e}")

    # --- BASIC PLAYER CACHE FROM SQUAD ---

    async def cache_players_from_squad(self, team_id: int, squad_data: dict, ttl_days: int = 7):
        """
        Parse basic player objects from squad JSON into the players collection.
        """
        players = (squad_data or {}).get("players") or []
        parsed_count = 0

        for entry in players:
            player_info = (entry or {}).get("player") or {}
            player_id = player_info.get("id")
            if not player_id:
                continue

            await self.set_basic_player_cache(player_id, player_info, team_id, ttl_days=ttl_days)
            parsed_count += 1

        if parsed_count:
            print(f"[CACHE] Parsed {parsed_count} basic players from Squad Team ID {team_id}.")

    async def set_basic_player_cache(self, player_id: int, player_info: dict, team_id: Optional[int] = None, ttl_days: int = 7):
        """
        Store basic player data from squad without overwriting full attributes.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        expired_time = now + datetime.timedelta(days=ttl_days)
        avatar_url = f"https://img.sofascore.com/api/v1/player/{player_id}/image"

        try:
            existing_record = None
            existing_raw = {}
            existing_attrs = {}

            try:
                existing_record = self.client.collection("players").get_first_list_item(
                    f"sofascore_id = '{player_id}'"
                )
                existing_raw = existing_record.raw_json or {}
                existing_attrs = existing_record.attributes or {}
            except ClientResponseError:
                pass

            existing_player = existing_raw.get("player") or {}
            if existing_attrs:
                merged_player = {
                    **(player_info or {}),
                    **existing_player
                }
            else:
                merged_player = {
                    **existing_player,
                    **(player_info or {})
                }
            if team_id and "cached_from_team_id" not in merged_player:
                merged_player["cached_from_team_id"] = team_id

            raw_json = {
                **existing_raw,
                "player": merged_player,
                "cache_status": "full" if existing_attrs else "basic"
            }

            payload = {
                "sofascore_id": str(player_id),
                "name": merged_player.get("name"),
                "position": merged_player.get("position"),
                "height": merged_player.get("height", 0),
                "preferredFoot": merged_player.get("preferredFoot", "Không rõ"),
                "marketValue": merged_player.get("proposedMarketValue", 0),
                "avatar_url": avatar_url,
                "attributes": existing_attrs,
                "raw_json": raw_json,
                "ttl_expired": expired_time.isoformat()
            }

            if existing_record:
                self.client.collection("players").update(existing_record.id, payload)
            else:
                self.client.collection("players").create(payload)
        except Exception as e:
            print(f"[CACHE ERROR] Loi set_basic_player_cache Player ID {player_id}: {e}")

    # --- TEAM TRANSFERS CACHING ---

    async def get_transfer_cache(self, team_id: int, ignore_expiration: bool = False) -> Optional[dict]:
        """
        Lay cache chuyen nhuong cua cau lac bo.
        """
        try:
            record = self.client.collection("team_transfers").get_first_list_item(
                f"sofascore_id = '{team_id}'"
            )
            if not ignore_expiration and record.ttl_expired:
                try:
                    expired_time = datetime.datetime.fromisoformat(record.ttl_expired.replace("Z", "+00:00"))
                    if datetime.datetime.now(datetime.timezone.utc) > expired_time:
                        print(f"[CACHE] Du lieu chuyen nhuong Team ID {team_id} da qua han TTL.")
                        return None
                except ValueError:
                    pass

            print(f"[CACHE HIT] Lay chuyen nhuong Team ID {team_id} tu PocketBase.")
            return record.transfers_json
        except ClientResponseError:
            return None
        except Exception as e:
            print(f"[CACHE ERROR] Loi get_transfer_cache: {e}")
            return None

    async def set_transfer_cache(self, team_id: int, transfer_data: dict, ttl_hours: int = 24):
        """
        Luu cache chuyen nhuong cau lac bo.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        expired_time = now + datetime.timedelta(hours=ttl_hours)

        payload = {
            "sofascore_id": str(team_id),
            "transfers_json": transfer_data,
            "ttl_expired": expired_time.isoformat()
        }

        try:
            try:
                exist_rec = self.client.collection("team_transfers").get_first_list_item(
                    f"sofascore_id = '{team_id}'"
                )
                self.client.collection("team_transfers").update(exist_rec.id, payload)
                print(f"[CACHE] Da cap nhat chuyen nhuong Team ID {team_id}.")
            except ClientResponseError:
                self.client.collection("team_transfers").create(payload)
                print(f"[CACHE] Da luu moi chuyen nhuong Team ID {team_id}.")
        except Exception as e:
            print(f"[CACHE ERROR] Loi set_transfer_cache: {e}")

    # --- TEAM (THONG TIN DOI BONG) CACHING ---

    async def get_team_cache(self, team_id: int, ignore_expiration: bool = False) -> Optional[dict]:
        """
        Lấy cache thông tin câu lạc bộ. 
        Nếu có ảnh logo đã lưu cục bộ, tự động viết đè URL tĩnh của PocketBase để phản hồi nhanh.
        """
        try:
            team_id_str = str(team_id)
            record = None
            if not team_id_str.isdigit():
                try:
                    record = self.client.collection("teams").get_first_list_item(
                        f"name = '{team_id_str}' || fullName = '{team_id_str}' || sofascore_id = '{team_id_str}'"
                    )
                except ClientResponseError:
                    try:
                        record = self.client.collection("teams").get_first_list_item(
                            f"name ~ '{team_id_str}'"
                        )
                    except ClientResponseError:
                        pass
            
            if not record:
                record = self.client.collection("teams").get_first_list_item(
                    f"sofascore_id = '{team_id_str}'"
                )
            if not ignore_expiration and record.ttl_expired:
                try:
                    expired_time = datetime.datetime.fromisoformat(record.ttl_expired.replace("Z", "+00:00"))
                    if datetime.datetime.now(datetime.timezone.utc) > expired_time:
                        print(f"[CACHE] Dữ liệu Team ID {team_id} đã quá hạn TTL.")
                        return None
                except ValueError:
                    pass
                
            raw_json = record.raw_json or {}
            
            # Nếu có tệp ảnh logo đã cache cục bộ, ghi đè link ảnh động của Sofascore bằng link tĩnh PB
            if record.logo_file and "team" in raw_json:
                local_logo_url = f"{self.pb_url}/api/files/{record.collection_name}/{record.id}/{record.logo_file}"
                # Cập nhật trong cả JSON trả về
                raw_json["team"]["logo_url_local"] = local_logo_url
                
            print(f"[CACHE HIT] Lấy thông tin Team ID {team_id} từ PocketBase.")
            return raw_json
        except ClientResponseError:
            return None
        except Exception as e:
            print(f"[CACHE ERROR] Lỗi get_team_cache: {e}")
            return None

    async def set_team_cache(self, team_id: int, raw_json: dict, ttl_days: int = 7):
        """
        Lưu cache thông tin đội bóng và tự động tải ảnh logo về lưu cục bộ (File Storage).
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        expired_time = now + datetime.timedelta(days=ttl_days)
        
        team_info = raw_json.get("team", {})
        logo_url = f"https://img.sofascore.com/api/v1/team/{team_id}/image"
        
        payload = {
            "sofascore_id": str(team_id),
            "name": team_info.get("name"),
            "fullName": team_info.get("fullName"),
            "managerName": team_info.get("manager", {}).get("name", "Không rõ"),
            "venueName": team_info.get("venue", {}).get("name", "Không rõ"),
            "capacity": team_info.get("venue", {}).get("capacity", 0),
            "logo_url": logo_url,
            "raw_json": raw_json,
            "ttl_expired": expired_time.isoformat()
        }
        
        # Tải ảnh logo về lưu cục bộ
        img_data = await self._download_image(logo_url)
        if img_data:
            filename, content, mime_type = img_data
            payload["logo_file"] = FileUpload((filename, content, mime_type))
            
        try:
            try:
                exist_rec = self.client.collection("teams").get_first_list_item(
                    f"sofascore_id = '{team_id}'"
                )
                self.client.collection("teams").update(exist_rec.id, payload)
                print(f"[CACHE] Đã cập nhật thông tin & ảnh logo cục bộ cho Team ID {team_id}.")
            except ClientResponseError:
                self.client.collection("teams").create(payload)
                print(f"[CACHE] Đã lưu mới thông tin & ảnh logo cục bộ cho Team ID {team_id}.")
        except Exception as e:
            print(f"[CACHE ERROR] Lỗi set_team_cache: {e}")

    # --- TEAM SEARCH CACHE ---

    async def search_team_cache(self, query: str, sport: Optional[str] = None, limit: int = 10) -> dict:
        """
        Tim doi bong trong cache PocketBase truoc khi goi API ngoai.
        Tra ve dung shape cua endpoint /api/search de frontend co the dung lai.
        """
        query_norm = (query or "").strip().casefold()
        sport_norm = (sport or "").strip().casefold()
        if not query_norm:
            return {"results": []}

        try:
            records = self.client.collection("teams").get_full_list(
                batch=200,
                query_params={"sort": "-updated"}
            )
            results = []

            for record in records:
                raw_json = record.raw_json or {}
                team = raw_json.get("team") or {}
                if not team:
                    continue

                team_sport = (team.get("sport") or {}).get("slug", "").casefold()
                if sport_norm and team_sport and team_sport != sport_norm:
                    continue

                searchable_values = [
                    team.get("name"),
                    team.get("fullName"),
                    team.get("shortName"),
                    team.get("nameCode"),
                    team.get("slug"),
                ]
                searchable_text = " ".join(str(value).casefold() for value in searchable_values if value)
                if query_norm not in searchable_text:
                    continue

                if record.logo_file:
                    team["logo_url_local"] = f"{self.pb_url}/api/files/{record.collection_name}/{record.id}/{record.logo_file}"

                results.append({
                    "type": "team",
                    "entity": team
                })

                if len(results) >= limit:
                    break

            # Bo sung tim kiem tu soccerdata (player_season_stats hoac team_season_stats)
            if len(results) < limit:
                extra_team_names = set()
                try:
                    # Query player_season_stats
                    pss_recs = self.client.collection("player_season_stats").get_full_list(
                        query_params={"filter": f"team_id ~ '{query}'"}
                    )
                    for r in pss_recs:
                        if r.team_id and query.lower() in r.team_id.lower():
                            extra_team_names.add(r.team_id)
                except Exception:
                    pass

                try:
                    # Query team_season_stats
                    tss_recs = self.client.collection("team_season_stats").get_full_list(
                        query_params={"filter": f"team_id ~ '{query}'"}
                    )
                    for r in tss_recs:
                        if r.team_id and query.lower() in r.team_id.lower():
                            extra_team_names.add(r.team_id)
                except Exception:
                    pass

                for t_name in extra_team_names:
                    name_exists = any(
                        res["entity"].get("name", "").lower() == t_name.lower() or 
                        res["entity"].get("fullName", "").lower() == t_name.lower()
                        for res in results
                    )
                    if not name_exists:
                        results.append({
                            "type": "team",
                            "entity": {
                                "id": t_name,
                                "name": t_name,
                                "fullName": t_name,
                                "sport": {"name": "Football", "slug": "football"},
                                "logo_url_local": None
                            }
                        })
                        if len(results) >= limit:
                            break

            if results:
                print(f"[CACHE HIT] Tim thay {len(results)} doi bong trong cache voi tu khoa '{query}'.")

            return {"results": results[:limit]}
        except Exception as e:
            print(f"[CACHE ERROR] Loi search_team_cache: {e}")
            return {"results": []}

    async def search_player_cache(self, query: str, sport: Optional[str] = None, limit: int = 10) -> dict:
        """
        Tim cau thu trong cache PocketBase truoc khi goi API ngoai.
        """
        query_norm = (query or "").strip().casefold()
        sport_norm = (sport or "").strip().casefold()
        if not query_norm:
            return {"results": []}

        try:
            records = self.client.collection("players").get_full_list(
                batch=200,
                query_params={"sort": "-updated"}
            )
            results = []

            for record in records:
                raw_json = record.raw_json or {}
                player = raw_json.get("player") or {}
                if not player:
                    continue

                player_sport = (player.get("sport") or {}).get("slug", "").casefold()
                team_sport = ((player.get("team") or {}).get("sport") or {}).get("slug", "").casefold()
                if sport_norm and player_sport and player_sport != sport_norm:
                    continue
                if sport_norm and not player_sport and team_sport and team_sport != sport_norm:
                    continue

                searchable_values = [
                    player.get("name"),
                    player.get("fullName"),
                    player.get("shortName"),
                    player.get("firstName"),
                    player.get("lastName"),
                    player.get("slug"),
                ]
                searchable_text = " ".join(str(value).casefold() for value in searchable_values if value)
                if query_norm not in searchable_text:
                    continue

                if record.avatar_file:
                    player["avatar_url_local"] = f"{self.pb_url}/api/files/{record.collection_name}/{record.id}/{record.avatar_file}"

                results.append({
                    "type": "player",
                    "entity": player,
                    "cache_status": raw_json.get("cache_status") or ("full" if record.attributes else "basic")
                })

                if len(results) >= limit:
                    break

            # Bo sung tim kiem tu soccerdata (player_season_stats)
            if len(results) < limit:
                try:
                    stats_records = self.client.collection("player_season_stats").get_full_list(
                        query_params={"filter": f"player_id ~ '{query}'"}
                    )
                    extra_players = {}
                    for r in stats_records:
                        p_id = r.player_id
                        p_name = p_id.replace("_", " ").title()
                        if p_id not in extra_players:
                            extra_players[p_id] = {
                                "id": p_id,
                                "name": p_name,
                                "position": "M",
                                "team": {
                                    "name": r.team_id
                                },
                                "sport": {"name": "Football", "slug": "football"}
                            }
                    
                    for p_id, p_obj in extra_players.items():
                        exists = False
                        for res in results:
                            p_entity = res["entity"]
                            p_entity_name = p_entity.get("name", "")
                            if p_entity_name.lower().replace(" ", "_") == p_id:
                                exists = True
                                break
                        if not exists:
                            results.append({
                                "type": "player",
                                "entity": p_obj,
                                "cache_status": "stats_only"
                            })
                            if len(results) >= limit:
                                break
                except Exception as e:
                    print(f"[CACHE] Error searching player_season_stats: {e}")

            if results:
                print(f"[CACHE HIT] Tim thay {len(results)} cau thu trong cache voi tu khoa '{query}'.")

            return {"results": results[:limit]}
        except Exception as e:
            print(f"[CACHE ERROR] Loi search_player_cache: {e}")
            return {"results": []}

    async def search_cache(self, query: str, sport: Optional[str] = None, limit: int = 10) -> dict:
        """
        Search all cached entities currently persisted in PocketBase.
        """
        results = []

        team_results = await self.search_team_cache(query, sport=sport, limit=limit)
        results.extend(team_results.get("results") or [])

        if len(results) < limit:
            player_results = await self.search_player_cache(query, sport=sport, limit=limit - len(results))
            results.extend(player_results.get("results") or [])

        return {"results": results[:limit]}

    # --- PLAYER (HO SO CAU THU) CACHING ---

    async def get_player_cache(self, player_id: int, ignore_expiration: bool = False) -> Optional[dict]:
        """
        Lấy cache hồ sơ và chỉ số cầu thủ. 
        Nếu có tệp ảnh chân dung cục bộ, tự động viết đè link ảnh cục bộ PB.
        """
        try:
            player_id_str = str(player_id)
            record = None
            if not player_id_str.isdigit():
                display_name = player_id_str.replace("_", " ").title()
                try:
                    record = self.client.collection("players").get_first_list_item(
                        f"name = '{display_name}' || sofascore_id = '{player_id_str}'"
                    )
                except ClientResponseError:
                    try:
                        record = self.client.collection("players").get_first_list_item(
                            f"name ~ '{display_name}'"
                        )
                    except ClientResponseError:
                        pass
            
            if not record:
                record = self.client.collection("players").get_first_list_item(
                    f"sofascore_id = '{player_id_str}'"
                )
            if not ignore_expiration and record.ttl_expired:
                try:
                    expired_time = datetime.datetime.fromisoformat(record.ttl_expired.replace("Z", "+00:00"))
                    if datetime.datetime.now(datetime.timezone.utc) > expired_time:
                        print(f"[CACHE] Dữ liệu Cầu thủ ID {player_id} đã quá hạn TTL.")
                        return None
                except ValueError:
                    pass
                
            raw_json = record.raw_json or {}
            
            # Ghi đè đường dẫn ảnh cục bộ
            if record.avatar_file and "player" in raw_json:
                local_avatar_url = f"{self.pb_url}/api/files/{record.collection_name}/{record.id}/{record.avatar_file}"
                raw_json["player"]["avatar_url_local"] = local_avatar_url
                
            # Đảm bảo trả về cả attributes đã cache
            raw_json["attributes"] = record.attributes or {}
            raw_json["cache_status"] = "full" if raw_json["attributes"] else "basic"
            
            print(f"[CACHE HIT] Lấy thông tin Cầu thủ ID {player_id} từ PocketBase.")
            return raw_json
        except ClientResponseError:
            return None
        except Exception as e:
            print(f"[CACHE ERROR] Lỗi get_player_cache: {e}")
            return None

    async def set_player_cache(self, player_id: int, player_data: dict, attributes: dict, ttl_days: int = 3):
        """
        Lưu cache thông tin cầu thủ + chỉ số kỹ năng, và tự động tải ảnh chân dung cục bộ.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        expired_time = now + datetime.timedelta(days=ttl_days)
        
        player_data = player_data or {}
        player_data["cache_status"] = "full"
        player_info = player_data.get("player", {})
        avatar_url = f"https://img.sofascore.com/api/v1/player/{player_id}/image"
        
        payload = {
            "sofascore_id": str(player_id),
            "name": player_info.get("name"),
            "position": player_info.get("position"),
            "height": player_info.get("height", 0),
            "preferredFoot": player_info.get("preferredFoot", "Không rõ"),
            "marketValue": player_info.get("proposedMarketValue", 0),
            "avatar_url": avatar_url,
            "attributes": attributes,
            "raw_json": player_data,
            "ttl_expired": expired_time.isoformat()
        }
        
        # Tải ảnh chân dung về lưu cục bộ
        img_data = await self._download_image(avatar_url)
        if img_data:
            filename, content, mime_type = img_data
            payload["avatar_file"] = FileUpload((filename, content, mime_type))
            
        try:
            try:
                exist_rec = self.client.collection("players").get_first_list_item(
                    f"sofascore_id = '{player_id}'"
                )
                self.client.collection("players").update(exist_rec.id, payload)
                print(f"[CACHE] Đã cập nhật hồ sơ & ảnh đại diện cầu thủ ID {player_id}.")
            except ClientResponseError:
                self.client.collection("players").create(payload)
                print(f"[CACHE] Đã lưu mới hồ sơ & ảnh đại diện cầu thủ ID {player_id}.")
        except Exception as e:
            print(f"[CACHE ERROR] Lỗi set_player_cache: {e}")

    # --- LEAGUE (THÔNG TIN GIẢI ĐẤU & BẢNG XẾP HẠNG) CACHING ---
    
    async def get_league_cache(self, league_id: int, season_id: int, ignore_expiration: bool = False) -> Optional[dict]:
        """
        Lấy cache bảng xếp hạng giải đấu cho một mùa giải cụ thể.
        Nếu có tệp logo giải đấu đã cache cục bộ, tự động viết đè URL tĩnh PocketBase để phản hồi nhanh.
        """
        key = f"{league_id}_{season_id}"
        try:
            record = self.client.collection("leagues").get_first_list_item(
                f"sofascore_id = '{key}'"
            )
            if not ignore_expiration and record.ttl_expired:
                try:
                    expired_time = datetime.datetime.fromisoformat(record.ttl_expired.replace("Z", "+00:00"))
                    if datetime.datetime.now(datetime.timezone.utc) > expired_time:
                        print(f"[CACHE] Dữ liệu Giải đấu {key} đã quá hạn TTL.")
                        return None
                except ValueError:
                    pass
                
            raw_json = record.raw_json or {}
            
            # Ghi đè đường dẫn logo giải đấu cục bộ nếu có
            if record.logo_file and raw_json:
                local_logo_url = f"{self.pb_url}/api/files/{record.collection_name}/{record.id}/{record.logo_file}"
                raw_json["logo_url_local"] = local_logo_url
                
            print(f"[CACHE HIT] Lấy bảng xếp hạng Giải đấu {key} từ PocketBase.")
            return raw_json
        except ClientResponseError:
            return None
        except Exception as e:
            print(f"[CACHE ERROR] Lỗi get_league_cache: {e}")
            return None

    async def set_league_cache(self, league_id: int, season_id: int, raw_json: dict, name: str = "", country: str = "", flag: str = "", ttl_days: int = 1):
        """
        Lưu cache bảng xếp hạng giải đấu + thông tin giải đấu và tự động tải ảnh logo về lưu cục bộ.
        """
        key = f"{league_id}_{season_id}"
        now = datetime.datetime.now(datetime.timezone.utc)
        expired_time = now + datetime.timedelta(days=ttl_days)
        
        logo_url = f"https://img.sofascore.com/api/v1/unique-tournament/{league_id}/image"
        
        payload = {
            "sofascore_id": key,
            "name": name,
            "country": country,
            "flag": flag,
            "logo_url": logo_url,
            "raw_json": raw_json,
            "ttl_expired": expired_time.isoformat()
        }
        
        # Tải logo giải đấu về cục bộ
        img_data = await self._download_image(logo_url)
        if img_data:
            filename, content, mime_type = img_data
            payload["logo_file"] = FileUpload((filename, content, mime_type))
            
        try:
            try:
                exist_rec = self.client.collection("leagues").get_first_list_item(
                    f"sofascore_id = '{key}'"
                )
                self.client.collection("leagues").update(exist_rec.id, payload)
                print(f"[CACHE] Đã cập nhật bảng xếp hạng & logo cục bộ cho Giải đấu {key}.")
            except ClientResponseError:
                self.client.collection("leagues").create(payload)
                print(f"[CACHE] Đã lưu mới bảng xếp hạng & logo cục bộ cho Giải đấu {key}.")
        except Exception as e:
            print(f"[CACHE ERROR] Lỗi set_league_cache: {e}")
