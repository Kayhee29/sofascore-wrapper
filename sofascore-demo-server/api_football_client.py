"""
api_football_client.py
======================
Client cho api-football.com với key rotation tự động.

Tính năng:
  - Quản lý nhiều API key, xoay vòng khi đạt ngưỡng request/ngày
  - Theo dõi số request mỗi key theo từng ngày (reset lúc 0h)
  - Lưu trạng thái vào file JSON để không mất count khi restart
  - Async (httpx) + thread-safe lock
  - Tự động skip key bị hết quota (429) sang key tiếp theo

Cấu hình:
  Đặt API keys vào .env hoặc truyền thẳng:
    APIFOOTBALL_KEYS=key1,key2,key3
    APIFOOTBALL_DAILY_LIMIT=95   # giới hạn an toàn (free = 100/ngày)
"""

import asyncio
import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

# Tự động load .env từ cùng thư mục
try:
    from dotenv import load_dotenv
    _env_file = Path(__file__).parent / ".env"
    if _env_file.exists():
        load_dotenv(_env_file)
        logging.getLogger(__name__).debug("[ApiFootball] Đã load .env từ %s", _env_file)
except ImportError:
    pass  # python-dotenv chưa cài, dùng env đã set sẵn

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  Hằng số
# ─────────────────────────────────────────────
API_BASE_URL = "https://v3.football.api-sports.io"
STATE_FILE   = Path(__file__).parent / ".apifootball_key_state.json"

# Đọc từ env (hoặc override khi khởi tạo)
DEFAULT_KEYS: List[str] = [
    k.strip()
    for k in os.getenv("APIFOOTBALL_KEYS", "").split(",")
    if k.strip()
]
DEFAULT_DAILY_LIMIT: int = int(os.getenv("APIFOOTBALL_DAILY_LIMIT", "95"))


# ─────────────────────────────────────────────
#  KeyRotationManager
# ─────────────────────────────────────────────
class KeyRotationManager:
    """
    Quản lý xoay vòng API key cho api-football.com.

    Ví dụ sử dụng:
        manager = KeyRotationManager(keys=["key_a", "key_b"], daily_limit=95)
        client  = ApiFootballClient(manager)
        data    = await client.get("/standings", params={"league": 39, "season": 2024})
    """

    def __init__(
        self,
        keys: Optional[List[str]] = None,
        daily_limit: int = DEFAULT_DAILY_LIMIT,
        state_file: Path = STATE_FILE,
    ):
        self._keys: List[str] = keys or DEFAULT_KEYS
        if not self._keys:
            raise ValueError(
                "Không có API key nào. "
                "Set APIFOOTBALL_KEYS=key1,key2 trong .env hoặc truyền keys=[]"
            )

        self._daily_limit   = daily_limit
        self._state_file    = state_file
        self._lock          = asyncio.Lock()

        # state[key] = {"count": int, "date": "YYYY-MM-DD", "exhausted": bool}
        self._state: Dict[str, Dict[str, Any]] = {}
        self._current_index = 0

        self._load_state()
        logger.info(
            "[KeyManager] Khởi tạo với %d key(s), giới hạn %d req/ngày",
            len(self._keys), self._daily_limit,
        )
        self._log_status()

    # ── Persistence ──────────────────────────

    def _load_state(self) -> None:
        today = str(date.today())
        if self._state_file.exists():
            try:
                raw = json.loads(self._state_file.read_text(encoding="utf-8"))
                for key in self._keys:
                    saved = raw.get(key, {})
                    if saved.get("date") == today:
                        self._state[key] = saved
                    else:
                        # Ngày mới → reset count
                        self._state[key] = {
                            "count": 0,
                            "date": today,
                            "exhausted": False,
                        }
                        if saved.get("date") != today:
                            logger.info("[KeyManager] Key ...%s reset (ngày mới)", key[-6:])
            except Exception as e:
                logger.warning("[KeyManager] Không đọc được state file: %s", e)
                self._init_empty_state(today)
        else:
            self._init_empty_state(today)

    def _init_empty_state(self, today: str) -> None:
        for key in self._keys:
            self._state[key] = {"count": 0, "date": today, "exhausted": False}

    def _save_state(self) -> None:
        try:
            self._state_file.write_text(
                json.dumps(self._state, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("[KeyManager] Không lưu được state file: %s", e)

    # ── Key selection ─────────────────────────

    def _refresh_date_if_needed(self, key: str) -> None:
        """Reset count nếu đã sang ngày mới."""
        today = str(date.today())
        entry = self._state[key]
        if entry["date"] != today:
            logger.info("[KeyManager] Key ...%s reset (sang ngày %s)", key[-6:], today)
            self._state[key] = {"count": 0, "date": today, "exhausted": False}

    def _is_available(self, key: str) -> bool:
        self._refresh_date_if_needed(key)
        s = self._state[key]
        return not s["exhausted"] and s["count"] < self._daily_limit

    def _get_active_key(self) -> str:
        """Trả về key khả dụng. Raise nếu tất cả đã hết quota."""
        n = len(self._keys)
        for _ in range(n):
            key = self._keys[self._current_index % n]
            if self._is_available(key):
                return key
            logger.warning(
                "[KeyManager] Key ...%s đã hết quota (%d/%d), chuyển sang key tiếp",
                key[-6:],
                self._state[key]["count"],
                self._daily_limit,
            )
            self._current_index = (self._current_index + 1) % n

        raise RuntimeError(
            f"Tất cả {n} API key đã hết quota ngày hôm nay "
            f"(giới hạn {self._daily_limit} req/key/ngày). "
            "Thêm key hoặc chờ đến 0h hôm sau."
        )

    # ── Public API ────────────────────────────

    async def acquire(self) -> str:
        """Lấy key khả dụng và tăng counter (thread-safe)."""
        async with self._lock:
            key = self._get_active_key()
            self._state[key]["count"] += 1
            self._save_state()
            logger.debug(
                "[KeyManager] Key ...%s | %d/%d req hôm nay",
                key[-6:],
                self._state[key]["count"],
                self._daily_limit,
            )
            return key

    async def mark_exhausted(self, key: str) -> None:
        """Đánh dấu key bị 429 (hết quota server-side) và chuyển sang key khác."""
        async with self._lock:
            if key in self._state:
                self._state[key]["exhausted"] = True
                self._save_state()
                logger.warning(
                    "[KeyManager] Key ...%s bị đánh dấu exhausted (429 từ server)",
                    key[-6:],
                )
            n = len(self._keys)
            self._current_index = (self._current_index + 1) % n

    def status(self) -> List[Dict[str, Any]]:
        """Trả về trạng thái tất cả key (dùng cho logging / endpoint /api/keys/status)."""
        result = []
        for key in self._keys:
            self._refresh_date_if_needed(key)
            s = self._state[key]
            result.append({
                "key_hint":   f"...{key[-6:]}",
                "date":       s["date"],
                "count":      s["count"],
                "limit":      self._daily_limit,
                "remaining":  max(0, self._daily_limit - s["count"]),
                "exhausted":  s["exhausted"],
                "available":  self._is_available(key),
            })
        return result

    def _log_status(self) -> None:
        for s in self.status():
            status_str = "❌ exhausted" if s["exhausted"] else (
                "✅ OK" if s["available"] else "⚠️ hết quota"
            )
            logger.info(
                "[KeyManager] Key %s | %d/%d req | %s",
                s["key_hint"], s["count"], s["limit"], status_str,
            )


# ─────────────────────────────────────────────
#  ApiFootballClient
# ─────────────────────────────────────────────
class ApiFootballClient:
    """
    HTTP client cho api-football.com.
    Tự động xoay key, retry khi 429.
    """

    def __init__(
        self,
        key_manager: Optional[KeyRotationManager] = None,
        timeout: float = 15.0,
    ):
        self._manager = key_manager or KeyRotationManager()
        self._timeout = timeout

    async def get(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        retries: int = 3,
    ) -> Dict[str, Any]:
        """
        Gọi GET tới api-football. Tự xoay key khi 429.

        Args:
            endpoint: Ví dụ "/standings" hoặc "/teams"
            params:   Dict query params, ví dụ {"league": 39, "season": 2024}
            retries:  Số lần thử lại khi bị 429

        Returns:
            JSON response dict (field "response" chứa dữ liệu)
        """
        url = f"{API_BASE_URL}{endpoint}"

        for attempt in range(1, retries + 1):
            key = await self._manager.acquire()
            headers = {
                "X-apisports-key": key,
                "Accept": "application/json",
            }
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as http:
                    resp = await http.get(url, headers=headers, params=params)

                if resp.status_code == 429:
                    logger.warning(
                        "[ApiFootball] 429 Too Many Requests với key ...%s (lần %d/%d)",
                        key[-6:], attempt, retries,
                    )
                    await self._manager.mark_exhausted(key)
                    if attempt < retries:
                        await asyncio.sleep(1)
                    continue

                resp.raise_for_status()
                data = resp.json()

                # api-football trả errors trong body dù status 200
                errors = data.get("errors", {})
                if errors:
                    raise RuntimeError(f"api-football error: {errors}")

                logger.info(
                    "[ApiFootball] GET %s | key ...%s | results=%d",
                    endpoint, key[-6:], data.get("results", 0),
                )
                return data

            except httpx.HTTPStatusError as e:
                logger.error("[ApiFootball] HTTP Error %s: %s", e.response.status_code, e)
                raise
            except httpx.RequestError as e:
                logger.error("[ApiFootball] Request Error: %s", e)
                if attempt == retries:
                    raise
                await asyncio.sleep(2 ** attempt)

        raise RuntimeError(f"api-football: thất bại sau {retries} lần thử cho {endpoint}")

    # ── Helpers thường dùng ───────────────────

    async def get_standings(self, league_id: int, season: int) -> Dict[str, Any]:
        """Bảng xếp hạng giải đấu."""
        return await self.get("/standings", params={"league": league_id, "season": season})

    async def get_teams(self, league_id: int, season: int) -> Dict[str, Any]:
        """Danh sách đội + logo của giải đấu."""
        return await self.get("/teams", params={"league": league_id, "season": season})

    async def get_fixtures(
        self,
        league_id: int,
        season: int,
        team_id: Optional[int] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Lịch thi đấu / kết quả."""
        params: Dict[str, Any] = {"league": league_id, "season": season}
        if team_id:
            params["team"] = team_id
        if status:
            params["status"] = status
        return await self.get("/fixtures", params=params)

    async def get_players(self, team_id: int, season: int) -> Dict[str, Any]:
        """Thống kê cầu thủ theo đội."""
        return await self.get("/players", params={"team": team_id, "season": season})

    def key_status(self) -> List[Dict[str, Any]]:
        """Trạng thái tất cả API key."""
        return self._manager.status()


# ─────────────────────────────────────────────
#  Singleton tiện dụng
# ─────────────────────────────────────────────
_default_client: Optional[ApiFootballClient] = None

def get_client(keys: Optional[List[str]] = None, daily_limit: int = DEFAULT_DAILY_LIMIT) -> ApiFootballClient:
    """
    Trả về singleton ApiFootballClient.
    Gọi lần đầu để khởi tạo, các lần sau trả về cùng instance.
    """
    global _default_client
    if _default_client is None:
        manager = KeyRotationManager(keys=keys, daily_limit=daily_limit)
        _default_client = ApiFootballClient(manager)
    return _default_client


# ─────────────────────────────────────────────
#  CLI test nhanh
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    async def main():
        if len(sys.argv) < 2:
            print("Usage: python api_football_client.py <api_key> [api_key2 ...]")
            print("\nVí dụ:")
            print("  python api_football_client.py abc123")
            print("  python api_football_client.py key1 key2 key3")
            sys.exit(1)

        keys = sys.argv[1:]
        client = ApiFootballClient(KeyRotationManager(keys=keys, daily_limit=95))

        print("\n=== Trạng thái key ===")
        for s in client.key_status():
            print(f"  {s['key_hint']} | {s['count']}/{s['limit']} req | available={s['available']}")

        print("\n=== Test lấy bảng xếp hạng Premier League 2024 ===")
        try:
            data = await client.get_standings(league_id=39, season=2024)
            standings = data.get("response", [])
            if standings:
                rows = standings[0].get("league", {}).get("standings", [[]])[0]
                print(f"  Số đội: {len(rows)}")
                for row in rows[:5]:
                    team = row.get("team", {})
                    print(
                        f"  #{row['rank']:2d} {team['name']:<25} "
                        f"| {row['points']} pts | logo: {team.get('logo', 'N/A')}"
                    )
            else:
                print("  Không có dữ liệu (kiểm tra API key)")
        except Exception as e:
            print(f"  Lỗi: {e}")

        print("\n=== Trạng thái key sau khi dùng ===")
        for s in client.key_status():
            print(
                f"  {s['key_hint']} | {s['count']}/{s['limit']} req "
                f"| còn {s['remaining']} | available={s['available']}"
            )

    asyncio.run(main())
