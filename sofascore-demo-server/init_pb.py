import httpx
import json
import sys
import time

PB_URL = "http://127.0.0.1:8090"
ADMIN_EMAIL = "admin@sofascore.local"
ADMIN_PASSWORD = "Admin123456789"

def create_admin():
    print("[INIT] Đang kết nối tới PocketBase để khởi tạo Admin...")
    url = f"{PB_URL}/api/admins"
    payload = {
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD,
        "passwordConfirm": ADMIN_PASSWORD
    }
    try:
        resp = httpx.post(url, json=payload, timeout=5.0)
        if resp.status_code in [200, 241, 201]:
            print("[INIT] Khởi tạo tài khoản Admin thành công!")
        else:
            # Nếu tài khoản đã tồn tại, sẽ trả về lỗi nhưng chúng ta có thể tiếp tục đăng nhập
            print(f"[INIT] Admin đã được tạo trước đó hoặc báo lỗi: {resp.text}")
    except Exception as e:
        print(f"[INIT] Lỗi kết nối đến PocketBase: {e}")
        sys.exit(1)

def get_admin_token():
    print("[INIT] Đang đăng nhập tài khoản Admin...")
    url = f"{PB_URL}/api/admins/auth-with-password"
    payload = {
        "identity": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD
    }
    resp = httpx.post(url, json=payload, timeout=5.0)
    if resp.status_code == 200:
        data = resp.json()
        print("[INIT] Đăng nhập thành công!")
        return data["token"]
    else:
        print(f"[INIT] Lỗi đăng nhập Admin: {resp.text}")
        sys.exit(1)

def create_collections(token):
    print("[INIT] Đang thiết lập các Collections trong database...")
    headers = {"Authorization": f"Bearer {token}"}
    
    # Định nghĩa cấu trúc 4 collections
    collections = [
        {
            "name": "leagues",
            "type": "base",
            "schema": [
                {"name": "sofascore_id", "type": "text", "required": True, "options": {"min": 1}},
                {"name": "name", "type": "text"},
                {"name": "country", "type": "text"},
                {"name": "flag", "type": "text"},
                {"name": "logo_url", "type": "text"},
                {"name": "logo_file", "type": "file", "options": {"maxSelect": 1, "maxSize": 5242880, "mimeTypes": ["image/png", "image/jpeg", "image/webp", "image/svg+xml"]}},
                {"name": "raw_json", "type": "json", "options": {"maxSize": 10485760}},
                {"name": "ttl_expired", "type": "date"}
            ],
            "indexes": [
                "CREATE UNIQUE INDEX idx_league_sofascore_id ON leagues (sofascore_id)"
            ]
        },
        {
            "name": "teams",
            "type": "base",
            "schema": [
                {"name": "sofascore_id", "type": "text", "required": True, "options": {"min": 1}},
                {"name": "name", "type": "text"},
                {"name": "fullName", "type": "text"},
                {"name": "managerName", "type": "text"},
                {"name": "venueName", "type": "text"},
                {"name": "capacity", "type": "number"},
                {"name": "logo_url", "type": "text"},
                {"name": "logo_file", "type": "file", "options": {"maxSelect": 1, "maxSize": 5242880, "mimeTypes": ["image/png", "image/jpeg", "image/webp", "image/svg+xml"]}},
                {"name": "raw_json", "type": "json", "options": {"maxSize": 10485760}},
                {"name": "ttl_expired", "type": "date"}
            ],
            "indexes": [
                "CREATE UNIQUE INDEX idx_team_sofascore_id ON teams (sofascore_id)"
            ]
        },
        {
            "name": "players",
            "type": "base",
            "schema": [
                {"name": "sofascore_id", "type": "text", "required": True, "options": {"min": 1}},
                {"name": "name", "type": "text"},
                {"name": "position", "type": "text"},
                {"name": "height", "type": "number"},
                {"name": "preferredFoot", "type": "text"},
                {"name": "marketValue", "type": "number"},
                {"name": "avatar_url", "type": "text"},
                {"name": "avatar_file", "type": "file", "options": {"maxSelect": 1, "maxSize": 5242880, "mimeTypes": ["image/png", "image/jpeg", "image/webp"]}},
                {"name": "attributes", "type": "json", "options": {"maxSize": 10485760}},
                {"name": "raw_json", "type": "json", "options": {"maxSize": 10485760}},
                {"name": "ttl_expired", "type": "date"}
            ],
            "indexes": [
                "CREATE UNIQUE INDEX idx_player_sofascore_id ON players (sofascore_id)"
            ]
        },
        {
            "name": "squads",
            "type": "base",
            "schema": [
                {"name": "sofascore_id", "type": "text", "required": True, "options": {"min": 1}},
                {"name": "players_list", "type": "json", "options": {"maxSize": 10485760}},
                {"name": "ttl_expired", "type": "date"}
            ],
            "indexes": [
                "CREATE UNIQUE INDEX idx_squad_sofascore_id ON squads (sofascore_id)"
            ]
        }
    ]
    
    # Gửi request tạo từng collection
    for col in collections:
        url = f"{PB_URL}/api/collections"
        resp = httpx.post(url, json=col, headers=headers, timeout=5.0)
        if resp.status_code == 200:
            print(f"[INIT] Đã tạo thành công Collection '{col['name']}'")
        elif resp.status_code == 400 and "already exists" in resp.text:
            print(f"[INIT] Collection '{col['name']}' đã tồn tại sẵn.")
        else:
            print(f"[INIT] Lỗi khi tạo Collection '{col['name']}': {resp.text}")

if __name__ == "__main__":
    # Đợi 2 giây cho PocketBase khởi động ổn định nếu mới chạy
    time.sleep(1.0)
    create_admin()
    tok = get_admin_token()
    create_collections(tok)
    print("[INIT] Hoàn thành khởi tạo PocketBase Caching DB!")
