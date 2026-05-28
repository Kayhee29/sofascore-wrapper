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
    
    async def get_squad_cache(self, team_id: int) -> Optional[dict]:
        """
        Lấy cache đội hình của câu lạc bộ. Trả về dict hoặc None nếu hết hạn/không có.
        """
        try:
            record = self.client.collection("squads").get_first_list_item(
                f"sofascore_id = '{team_id}'"
            )
            # Kiểm tra thời hạn TTL
            expired_time = datetime.datetime.fromisoformat(record.ttl_expired.replace("Z", "+00:00"))
            if datetime.datetime.now(datetime.timezone.utc) > expired_time:
                print(f"[CACHE] Đội hình của Team ID {team_id} đã quá hạn TTL.")
                return None
                
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
        except Exception as e:
            print(f"[CACHE ERROR] Lỗi set_squad_cache: {e}")

    # --- TEAM (THÔNG TIN ĐỘI BÓNG) CACHING ---

    async def get_team_cache(self, team_id: int) -> Optional[dict]:
        """
        Lấy cache thông tin câu lạc bộ. 
        Nếu có ảnh logo đã lưu cục bộ, tự động viết đè URL tĩnh của PocketBase để phản hồi nhanh.
        """
        try:
            record = self.client.collection("teams").get_first_list_item(
                f"sofascore_id = '{team_id}'"
            )
            expired_time = datetime.datetime.fromisoformat(record.ttl_expired.replace("Z", "+00:00"))
            if datetime.datetime.now(datetime.timezone.utc) > expired_time:
                print(f"[CACHE] Dữ liệu Team ID {team_id} đã quá hạn TTL.")
                return None
                
            raw_json = record.raw_json
            
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

    # --- PLAYER (HỒ SƠ CẦU THỦ) CACHING ---

    async def get_player_cache(self, player_id: int) -> Optional[dict]:
        """
        Lấy cache hồ sơ và chỉ số cầu thủ. 
        Nếu có tệp ảnh chân dung cục bộ, tự động viết đè link ảnh cục bộ PB.
        """
        try:
            record = self.client.collection("players").get_first_list_item(
                f"sofascore_id = '{player_id}'"
            )
            expired_time = datetime.datetime.fromisoformat(record.ttl_expired.replace("Z", "+00:00"))
            if datetime.datetime.now(datetime.timezone.utc) > expired_time:
                print(f"[CACHE] Dữ liệu Cầu thủ ID {player_id} đã quá hạn TTL.")
                return None
                
            raw_json = record.raw_json
            
            # Ghi đè đường dẫn ảnh cục bộ
            if record.avatar_file and "player" in raw_json:
                local_avatar_url = f"{self.pb_url}/api/files/{record.collection_name}/{record.id}/{record.avatar_file}"
                raw_json["player"]["avatar_url_local"] = local_avatar_url
                
            # Đảm bảo trả về cả attributes đã cache
            raw_json["attributes"] = record.attributes
            
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
    
    async def get_league_cache(self, league_id: int, season_id: int) -> Optional[dict]:
        """
        Lấy cache bảng xếp hạng giải đấu cho một mùa giải cụ thể.
        Nếu có tệp logo giải đấu đã cache cục bộ, tự động viết đè URL tĩnh PocketBase để phản hồi nhanh.
        """
        key = f"{league_id}_{season_id}"
        try:
            record = self.client.collection("leagues").get_first_list_item(
                f"sofascore_id = '{key}'"
            )
            expired_time = datetime.datetime.fromisoformat(record.ttl_expired.replace("Z", "+00:00"))
            if datetime.datetime.now(datetime.timezone.utc) > expired_time:
                print(f"[CACHE] Dữ liệu Giải đấu {key} đã quá hạn TTL.")
                return None
                
            raw_json = record.raw_json
            
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
