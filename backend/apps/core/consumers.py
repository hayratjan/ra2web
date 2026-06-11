"""
WebSocket 消费者公共基类。

WOL 大厅与 gserv 中继同为"IRC over WebSocket"风格协议,
此基类抽取两者共用的能力:
- 文本行/二进制帧的收发与按行分发;
- 数字回复码消息构造;
- 服务器主动保活探测(测 RTT + 清理死连接);
- 令牌桶消息限流。
"""
import asyncio
import logging
import time

from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

logger = logging.getLogger(__name__)


class BaseIrcConsumer(AsyncWebsocketConsumer):
    """IRC 风格 WebSocket 连接基类。"""

    # 子类可覆盖的保活参数
    KEEPALIVE_INTERVAL = 30
    KEEPALIVE_MISS_LIMIT = 2

    async def connect(self):
        await self.accept()
        self.cfg = settings.RA2WEB
        self.server = self.cfg["SERVER_NAME"]
        self.closed = False
        self._ka_task = None
        self._ka_pending = {}
        self._ka_misses = 0
        self._bucket_tokens = float(self.cfg["MSG_RATE_LIMIT"])
        self._bucket_ts = time.monotonic()
        await self.on_connected()

    async def on_connected(self):
        """子类初始化钩子。"""

    async def disconnect(self, close_code):
        self.closed = True
        if self._ka_task:
            self._ka_task.cancel()
        await self.on_disconnected(close_code)

    async def on_disconnected(self, close_code):
        """子类清理钩子。"""

    def is_open(self) -> bool:
        return not self.closed

    # ------------------------------------------------------------------
    # 收发工具
    # ------------------------------------------------------------------
    async def send_line(self, line: str):
        """发送一行文本消息(自动追加 CRLF)。"""
        if not self.closed:
            try:
                await self.send(text_data=line + "\r\n")
            except Exception:
                self.closed = True

    async def send_code(self, code: int, params: str):
        """发送数字回复::<server> <code> <params>"""
        await self.send_line(f":{self.server} {code} {params}")

    async def send_binary(self, payload: bytes):
        if not self.closed:
            try:
                await self.send(bytes_data=payload)
            except Exception:
                self.closed = True

    # ------------------------------------------------------------------
    # 消息入口:按行拆分后交给子类 handle_line / handle_binary
    # ------------------------------------------------------------------
    async def receive(self, text_data=None, bytes_data=None):
        try:
            if bytes_data is not None:
                await self.handle_binary(bytes_data)
                return
            if text_data is None:
                return
            for line in text_data.replace("\r\n", "\n").split("\n"):
                line = line.strip("\r")
                if not line:
                    continue
                if self.rate_limited():
                    await self.on_rate_limited()
                    return
                await self.handle_line(line)
        except Exception:
            logger.exception("处理 WebSocket 消息失败")

    async def handle_line(self, line: str):
        raise NotImplementedError

    async def handle_binary(self, data: bytes):
        """二进制帧默认忽略(WOL 无二进制协议)。"""

    # ------------------------------------------------------------------
    # 限流(在线连线优化:防刷屏拖垮服务)
    # ------------------------------------------------------------------
    def rate_limited(self) -> bool:
        """令牌桶限流;超限返回 True。"""
        now = time.monotonic()
        rate = self.cfg["MSG_RATE_LIMIT"]
        self._bucket_tokens = min(
            float(rate), self._bucket_tokens + (now - self._bucket_ts) * rate
        )
        self._bucket_ts = now
        if self._bucket_tokens < 1:
            return True
        self._bucket_tokens -= 1
        return False

    async def on_rate_limited(self):
        """超限默认直接断开,子类可先发送错误码。"""
        await self.close()

    # ------------------------------------------------------------------
    # 保活探测(在线连线优化:测 RTT + 清死链)
    # ------------------------------------------------------------------
    def start_keepalive(self):
        if self._ka_task is None:
            self._ka_task = asyncio.get_event_loop().create_task(self._keepalive())

    async def handle_pong(self, token: str):
        """子类在收到 pong 命令时调用,完成一次 RTT 采样。"""
        sent_at = self._ka_pending.pop(token, None)
        if sent_at is not None:
            self._ka_misses = 0
            await self.on_rtt_sample(int((time.monotonic() - sent_at) * 1000))

    async def on_rtt_sample(self, rtt_ms: int):
        """RTT 采样钩子(大厅展示/速率自适应使用)。"""

    async def reply_ping(self, line: str, parts: list):
        """应答客户端的延迟测量:ping :<ts> -> :<server> PONG s :<ts>"""
        payload = line.split(" ", 1)[1] if len(parts) > 1 else ""
        await self.send_line(f":{self.server} PONG s :{payload.lstrip(':')}")

    async def _keepalive(self):
        counter = 0
        try:
            while not self.closed:
                await asyncio.sleep(self.KEEPALIVE_INTERVAL)
                if self._ka_pending:
                    self._ka_misses += 1
                    self._ka_pending.clear()
                    if self._ka_misses >= self.KEEPALIVE_MISS_LIMIT:
                        await self.close()
                        return
                counter += 1
                token = f"k{counter}"
                self._ka_pending[token] = time.monotonic()
                await self.send_line(f"ping {token}")
        except asyncio.CancelledError:
            pass
