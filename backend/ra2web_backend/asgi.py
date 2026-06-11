"""
ASGI 入口:同一端口同时承载 HTTP 接口与 WebSocket 服务。

WebSocket 路由:
- /wol    -> WOL 聊天大厅(IRC over WebSocket,文本协议)
- /gserv  -> 游戏中继服务(文本+二进制混合协议)
也兼容根路径直连(官方 servers.ini 的 wolUrl/gservUrl 使用独立域名根路径),
通过部署时把不同域名/端口分别反代到对应路径即可。
"""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ra2web_backend.settings")

django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from django.urls import path  # noqa: E402

from apps.gserv.consumers import GservConsumer  # noqa: E402
from apps.wol.consumers import WolConsumer  # noqa: E402

websocket_urlpatterns = [
    path("wol", WolConsumer.as_asgi()),
    path("wol/", WolConsumer.as_asgi()),
    path("gserv", GservConsumer.as_asgi()),
    path("gserv/", GservConsumer.as_asgi()),
    # 根路径默认为 WOL(与官方 wolUrl 直连根路径行为一致)
    path("", WolConsumer.as_asgi()),
]

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": URLRouter(websocket_urlpatterns),
    }
)
