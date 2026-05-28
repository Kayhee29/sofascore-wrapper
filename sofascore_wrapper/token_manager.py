import asyncio
import base64
import json
import time
from playwright.async_api import async_playwright
from typing import Dict, Optional, Tuple

class TokenManager:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(TokenManager, cls).__new__(cls, *args, **kwargs)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self.token: Optional[str] = None
        self.cookies: Dict[str, str] = {}
        self.user_agent: Optional[str] = None
        self.expire_time: float = 0.0
        self._lock = asyncio.Lock()
        self._initialized = True

    def decode_jwt_expiry(self, token: str) -> float:
        """
        Giải mã JWT payload để đọc trường 'exp' trả về epoch timestamp.
        """
        try:
            parts = token.split('.')
            if len(parts) < 2:
                return 0.0
            
            # Base64 decode payload
            payload_b64 = parts[1]
            # Thêm padding cho đúng chuẩn base64
            payload_b64 += '=' * (-len(payload_b64) % 4)
            
            decoded_bytes = base64.b64decode(payload_b64)
            payload_data = json.loads(decoded_bytes.decode('utf-8'))
            
            return float(payload_data.get('exp', 0.0))
        except Exception as e:
            print(f"[TokenManager] Lỗi khi giải mã JWT: {e}")
            return 0.0

    def is_token_expired(self) -> bool:
        """
        Kiểm tra token hiện tại đã hết hạn hoặc chuẩn bị hết hạn (dưới 5 phút) chưa.
        """
        if not self.token or self.expire_time == 0.0:
            return True
        # Nếu thời gian còn lại ít hơn 5 phút (300 giây) thì coi như đã hết hạn
        return (self.expire_time - time.time()) < 300

    async def get_session(self, force_refresh: bool = False) -> Tuple[Optional[str], Dict[str, str], Optional[str]]:
        """
        Trả về bộ ba (token, cookies, user_agent). Tự động lấy mới nếu token hết hạn.
        """
        async with self._lock:
            if self.is_token_expired() or force_refresh:
                print("[TokenManager] Token hết hạn hoặc yêu cầu lấy mới. Tiến hành khởi tạo Playwright...")
                await self._refresh_session()
            return self.token, self.cookies, self.user_agent

    async def _refresh_session(self) -> None:
        """
        Sử dụng Playwright để trích xuất Token mới, Cookies và User-Agent từ Sofascore.
        """
        async with async_playwright() as p:
            # Khởi chạy trình duyệt headless chromium
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()

            extracted_token = None

            async def handle_response(response):
                nonlocal extracted_token
                # Bắt API config chứa thông tin websocket token
                if "user/config" in response.url or "bootstrap" in response.url:
                    try:
                        data = await response.json()
                        if 'websocket' in data and 'token' in data['websocket']:
                            extracted_token = data['websocket']['token']
                    except Exception:
                        pass

            page.on("response", handle_response)

            try:
                # Truy cập trang chủ của Sofascore để kích hoạt API
                await page.goto("https://www.sofascore.com/", timeout=30000)
                # Chờ tối đa 5 giây cho các API config/bootstrap hoàn tất
                for _ in range(50):
                    if extracted_token:
                        break
                    await asyncio.sleep(0.1)

                # Trích xuất Cookies từ browser context
                playwright_cookies = await context.cookies()
                self.cookies = {cookie['name']: cookie['value'] for cookie in playwright_cookies}
                
                # Trích xuất User-Agent
                self.user_agent = await page.evaluate("navigator.userAgent")

                if extracted_token:
                    self.token = extracted_token
                    self.expire_time = self.decode_jwt_expiry(extracted_token)
                    print(f"[TokenManager] Lấy Session thành công! Hạn sử dụng token: {time.ctime(self.expire_time)}")
                else:
                    # Sofascore đã chuyển sang NATS, không cần token kết nối, ta thiết lập thời gian hết hạn cookie sau 1 giờ.
                    self.token = "none"
                    self.expire_time = time.time() + 3600
                    print(f"[TokenManager] Lấy Cookies và User-Agent thành công! (Không dùng token WebSocket do Sofascore chuyển sang NATS)")
            except Exception as e:
                print(f"[TokenManager] Lỗi trong quá trình refresh session: {e}")
                # Nếu xảy ra lỗi, giữ lại thông tin cũ nhưng có thể bị block tiếp
                raise e
            finally:
                await context.close()
                await browser.close()
