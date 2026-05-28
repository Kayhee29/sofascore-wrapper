import asyncio
import json
import websockets
from typing import Callable, Dict, List, Optional
from .token_manager import TokenManager

class SofascoreRealtimeClient:
    def __init__(self, token_manager: Optional[TokenManager] = None):
        # Mặc dù giao thức NATS của Sofascore không bắt buộc JWT token (kết nối ẩn danh),
        # ta vẫn giữ tham số token_manager để duy trì khả năng tương thích ngược.
        self.token_manager = token_manager or TokenManager()
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.subscriptions: Dict[str, List[Callable]] = {}
        self.is_running = False
        self.current_id = 0
        self._connect_task: Optional[asyncio.Task] = None
        self._channel_to_sid: Dict[str, int] = {}
        self._sid_to_channel: Dict[int, str] = {}

    def _next_sid(self) -> int:
        self.current_id += 1
        return self.current_id

    async def connect(self):
        """
        Khởi chạy tiến trình kết nối và duy trì NATS WebSocket chạy ngầm.
        """
        if self.is_running:
            return
        self.is_running = True
        self._connect_task = asyncio.create_task(self._connection_loop())

    async def disconnect(self):
        """
        Đóng kết nối WebSocket và dừng loop ngầm.
        """
        self.is_running = False
        if self.websocket:
            await self.websocket.close()
        if self._connect_task:
            self._connect_task.cancel()
            try:
                await self._connect_task
            except asyncio.CancelledError:
                pass
        print("[RealtimeClient] Đã đóng kết nối NATS WebSocket an toàn.")

    async def subscribe(self, channel: str, callback: Callable):
        """
        Đăng ký lắng nghe sự kiện trên một kênh NATS (ví dụ: 'sport.football' hoặc 'event.{match_id}').
        """
        if channel not in self.subscriptions:
            self.subscriptions[channel] = []
            sid = self._next_sid()
            self._channel_to_sid[channel] = sid
            self._sid_to_channel[sid] = channel
            
            # Nếu đang có kết nối socket hoạt động, gửi lệnh subscribe ngay
            if self.websocket and self.websocket.state.name == "OPEN":
                await self._send_subscribe(channel, sid)
        
        self.subscriptions[channel].append(callback)
        print(f"[RealtimeClient] Đã đăng ký callback cho kênh: {channel} (SID: {self._channel_to_sid[channel]})")

    async def _send_subscribe(self, channel: str, sid: int):
        """
        Gửi lệnh SUB chuẩn của NATS.
        """
        sub_msg = f"SUB {channel} {sid}\r\n"
        await self.websocket.send(sub_msg)
        print(f"[RealtimeClient] Đã gửi lệnh SUB: {channel} {sid}")

    async def _connection_loop(self):
        """
        Vòng lặp quản lý kết nối và tự động reconnect sử dụng Exponential Backoff.
        """
        backoff = 1.0
        max_backoff = 60.0

        while self.is_running:
            try:
                # Địa chỉ NATS WebSocket của Sofascore
                uri = "wss://ws.sofascore.com:9222/"
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Origin": "https://www.sofascore.com"
                }

                print(f"[RealtimeClient] Đang kết nối tới NATS WebSocket: {uri}...")
                async with websockets.connect(uri, additional_headers=headers) as ws:
                    self.websocket = ws
                    backoff = 1.0  # Reset backoff khi thành công

                    # 1. Nhận tin nhắn INFO đầu tiên từ server
                    info = await ws.recv()
                    # print(f"[RealtimeClient] Nhận INFO từ server: {info}")

                    # 2. Gửi gói tin CONNECT chuẩn NATS
                    connect_msg = 'CONNECT {"protocol":1,"version":"3.1.0","lang":"nats.ws","verbose":false,"pedantic":false,"user":"none","pass":"none","headers":true,"no_responders":true}\r\n'
                    await ws.send(connect_msg)
                    
                    # Gửi PING ban đầu để kiểm tra
                    await ws.send("PING\r\n")

                    # 3. Tự động subscribe lại tất cả các kênh đang đăng ký
                    for channel, sid in self._channel_to_sid.items():
                        await self._send_subscribe(channel, sid)

                    # 4. Vòng lặp lắng nghe tin nhắn từ NATS server
                    async for message in ws:
                        await self._handle_message(message)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[RealtimeClient] Lỗi kết nối NATS: {e}. Sẽ thử lại sau {backoff} giây...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, max_backoff)

    async def _handle_message(self, raw_message):
        """
        Xử lý và phân tích giao thức NATS để trích xuất JSON payload.
        """
        try:
            if isinstance(raw_message, bytes):
                msg_str = raw_message.decode('utf-8', errors='ignore')
            else:
                msg_str = raw_message

            msg_strip = msg_str.strip()

            # Phản hồi PING từ NATS server để giữ kết nối không bị ngắt (Stale Connection)
            if msg_strip == "PING":
                if self.websocket and self.websocket.state.name == "OPEN":
                    await self.websocket.send("PONG\r\n")
                return

            if msg_strip == "PONG":
                return

            # Xử lý tin nhắn xuất bản (Publish Message) của NATS
            # Định dạng: MSG <subject> <sid> [reply-to] <#bytes>\r\n<payload>\r\n
            if msg_str.startswith("MSG "):
                lines = msg_str.split("\r\n")
                if len(lines) >= 2:
                    header = lines[0].split(" ")
                    payload_line = lines[1]
                    
                    # Đọc sid từ header (ví dụ: ["MSG", "sport.football", "1", "509"])
                    if len(header) >= 3:
                        try:
                            sid = int(header[2])
                            channel = self._sid_to_channel.get(sid)
                            
                            if channel and channel in self.subscriptions:
                                # Giải mã JSON payload sự kiện
                                pub_data = json.loads(payload_line)
                                
                                for callback in self.subscriptions[channel]:
                                    try:
                                        if asyncio.iscoroutinefunction(callback):
                                            await callback(pub_data)
                                        else:
                                            callback(pub_data)
                                    except Exception as cb_err:
                                        print(f"[RealtimeClient] Lỗi callback: {cb_err}")
                        except Exception as parse_err:
                            pass
        except Exception as err:
            print(f"[RealtimeClient] Lỗi xử lý tin nhắn NATS: {err}")
