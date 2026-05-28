import httpx
from playwright.async_api import async_playwright
from .token_manager import TokenManager

BASE_URL = "https://www.sofascore.com/api/v1"

class SofascoreAPI:
    def __init__(self):
        self.browser = None
        self.page = None
        self.playwright = None
        self.token_manager = TokenManager()
        self.http_client = httpx.AsyncClient(timeout=15.0)

    async def _init_browser(self):
        if self.playwright is None:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(headless=True)
            self.page = await self.browser.new_page()

    async def _get(self, endpoint):
        url = f"{BASE_URL}{endpoint}"
        return await self._request_with_fallback(url, endpoint)

    async def _raw_get(self, url):
        return await self._request_with_fallback(url, url)

    async def _request_with_fallback(self, url: str, display_name: str):
        """
        Thực hiện gửi request sử dụng HTTPX kết hợp cookies.
        Nếu gặp Cloudflare (403/429) hoặc lỗi cookie, tự động refresh và fallback qua Playwright.
        """
        # Bước 1: Thử gọi nhanh qua HTTPX dùng session hiện tại
        try:
            _, cookies, user_agent = await self.token_manager.get_session()
            headers = {
                "User-Agent": user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Origin": "https://www.sofascore.com",
                "Referer": "https://www.sofascore.com/"
            }
            
            response = await self.http_client.get(url, headers=headers, cookies=cookies)
            if response.status_code == 200:
                return response.json()
            elif response.status_code in (403, 429):
                print(f"[SofascoreAPI] Gặp lỗi {response.status_code} khi gọi {display_name}. Thử tự động làm mới session...")
            else:
                raise Exception(f"Failed to fetch {display_name}: {response.status_code}")
        except Exception as e:
            print(f"[SofascoreAPI] Lỗi khi kết nối HTTPX tới {display_name}: {e}")

        # Bước 2: Thử lấy Session mới và gọi lại qua HTTPX lần hai
        try:
            print(f"[SofascoreAPI] Đang buộc làm mới session...")
            _, cookies, user_agent = await self.token_manager.get_session(force_refresh=True)
            headers = {
                "User-Agent": user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Origin": "https://www.sofascore.com",
                "Referer": "https://www.sofascore.com/"
            }
            response = await self.http_client.get(url, headers=headers, cookies=cookies)
            if response.status_code == 200:
                return response.json()
            else:
                print(f"[SofascoreAPI] Vẫn lỗi {response.status_code} sau khi làm mới session. Chuyển sang Fallback hoàn toàn qua Playwright...")
        except Exception as e:
            print(f"[SofascoreAPI] Lỗi khi thử lại với session mới: {e}. Chuyển sang Fallback qua Playwright...")

        # Bước 3: Fallback qua Playwright (Phương án bảo hiểm chống Cloudflare tuyệt đối)
        try:
            await self._init_browser()
            response = await self.page.goto(url)
            if response.status == 200:
                # Cập nhật lại cookies mới từ Playwright sang cho TokenManager
                playwright_cookies = await self.page.context.cookies()
                new_cookies = {c['name']: c['value'] for c in playwright_cookies}
                self.token_manager.cookies = new_cookies
                return await response.json()
            else:
                raise Exception(f"Failed to fetch {display_name} via Playwright: {response.status}")
        except Exception as e:
            print(f"[SofascoreAPI] Lỗi Fallback Playwright cho {display_name}: {e}")
            raise e

    async def close(self):
        # Đóng client HTTPX
        await self.http_client.aclose()
        # Đóng browser Playwright nếu có mở
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

