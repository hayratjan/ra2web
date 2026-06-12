"""
ra2web 后端 Django 配置。

与前端(dist/ra2web.min.js)对接的三类服务:
- wladderUrl  -> HTTP  天梯接口(apps.ladder)
- wgameresUrl -> HTTP  战绩上报接口(apps.gameres)
- wolUrl      -> WS    WOL 聊天大厅服务(apps.wol)
- gservUrl    -> WS    游戏中继服务(apps.gserv)
"""
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "RA2WEB_SECRET_KEY", "django-insecure-ra2web-dev-key-change-me"
)

DEBUG = os.environ.get("RA2WEB_DEBUG", "1") == "1"

ALLOWED_HOSTS = os.environ.get("RA2WEB_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "daphne",
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "apps.accounts",
    "apps.ladder",
    "apps.gameres",
    "apps.wol",
    "apps.gserv",
]

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
    "apps.core.middleware.cors_middleware",
]

ROOT_URLCONF = "ra2web_backend.urls"

TEMPLATES = []

WSGI_APPLICATION = "ra2web_backend.wsgi.application"
ASGI_APPLICATION = "ra2web_backend.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("RA2WEB_DB_PATH", BASE_DIR / "db.sqlite3"),
    }
}

# 单进程部署使用内存通道层;多进程部署请改用 channels_redis
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# 请求体大小上限(战绩包不足 64KB,留余量;防内存放大攻击)
DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024

# 前端静态站点根目录(同端口托管 index.html/dist/res 等,
# 实现"一个端口跑完整个游戏";置空则关闭静态托管)
FRONTEND_ROOT = os.environ.get("RA2WEB_FRONTEND_ROOT", str(BASE_DIR.parent))

# 生产环境务必通过环境变量覆盖:
#   RA2WEB_SECRET_KEY  随机密钥
#   RA2WEB_DEBUG=0     关闭调试
#   RA2WEB_ALLOWED_HOSTS=你的域名

# ---------------------------------------------------------------------------
# ra2web 自定义配置(与前端 network/WolConfig 等模块保持一致)
# ---------------------------------------------------------------------------

# 前端 WolConfig.allClientSettings: Cdral2 -> sku=16640, channelType=45
RA2WEB = {
    # 服务器名,出现在所有 IRC 回复的 prefix 中
    "SERVER_NAME": os.environ.get("RA2WEB_SERVER_NAME", "ra2web-py"),
    # 客户端 SKU(前端 WolConfig.getClientSku())
    "CLIENT_SKU": 16640,
    # 游戏频道类型(前端 WolConfig.getClientChannelType())
    "CLIENT_CHANNEL_TYPE": 45,
    # 全局频道密码(前端 WolConfig.getGlobalChannelPass())
    "GLOBAL_CHANNEL_PASS": "zotclot9",
    # 快速匹配机器人昵称(前端 WolConfig.MATCH_BOT_NAME)
    "MATCH_BOT_NAME": "matchbot",
    # 快速匹配频道 id(前端 qmChanIds: Solo1v1 -> 50)
    "QM_CHANNEL_IDS": {"1v1": 50},
    # 用户名/密码长度限制(与前端 WolConfig 一致)
    "MIN_USERNAME_LEN": 2,
    "MAX_USERNAME_LEN": 15,
    "MIN_PASS_LEN": 8,
    "MAX_PASS_LEN": 128,
    # 首次登录是否自动注册账号(经典 WOL 行为)
    "AUTO_REGISTER": os.environ.get("RA2WEB_AUTO_REGISTER", "1") == "1",
    # cvers 校验:为空表示接受任意客户端版本
    "MIN_CLIENT_VERSION": os.environ.get("RA2WEB_MIN_CLIENT_VERSION", ""),
    # gserv 二进制协议 API 版本(前端 gservConfig.API_VERSION)
    "GSERV_API_VERSION": 2,
    # startg 时下发给客户端的游戏服地址;为空时回显客户端通过
    # startg/servers.ini 提供的候选地址
    "GSERV_URL": os.environ.get("RA2WEB_GSERV_URL", ""),
    # 在线人数上限,超出后进入登录排队(在线连线优化)
    "MAX_ONLINE_USERS": int(os.environ.get("RA2WEB_MAX_ONLINE_USERS", "2000")),
    # 单连接消息速率限制(条/秒),超出回复 711 并断开
    "MSG_RATE_LIMIT": int(os.environ.get("RA2WEB_MSG_RATE_LIMIT", "40")),
    # 每日 MOTD(登录后公告),支持多行;为默认(简体中文)文案
    "MOTD": os.environ.get("RA2WEB_MOTD", "欢迎来到 ra2web 自建服务器!"),
    # 按 WOL 地区代码(apps/core/wol_locales.py)下发的多语言 MOTD,
    # 客户端登录前通过 setlocale 上报语言,未匹配时回退到 MOTD
    "MOTD_BY_LOCALE": {
        2: "Welcome to the ra2web self-hosted server!",   # 英语(美国)
        4: "Welcome to the ra2web self-hosted server!",   # 英语(英国)
        23: "歡迎來到 ra2web 自建伺服器!",                  # 繁体中文(台湾)
    },
    # 天梯赛季长度(天),按战绩时间自动归档
    "SEASON_DAYS": int(os.environ.get("RA2WEB_SEASON_DAYS", "60")),
    # ------------------------------------------------------------------
    # 快速匹配(matchbot)配置
    # ------------------------------------------------------------------
    # 匹配地图池:必须配置客户端可加载的官方地图,否则快速匹配不可用
    # 每项: {"name": 地图文件名, "title": 标题, "sizeBytes": 文件大小,
    #        "digest": 地图摘要, "official": True}
    # 可通过环境变量 RA2WEB_QM_MAP_POOL 覆盖(JSON 数组)
    "QM_MAP_POOL": json.loads(os.environ["RA2WEB_QM_MAP_POOL"])
    if os.environ.get("RA2WEB_QM_MAP_POOL")
    else [
        {
            "name": "tn04t2.map",
            "title": "[2] Tournament Map 4",
            "sizeBytes": 74496,
            "digest": "",
            "official": True,
        },
        {
            "name": "mp5t5.map",
            "title": "[4] Hostile Station",
            "sizeBytes": 102400,
            "digest": "",
            "official": True,
        },
        {
            "name": "mp2t2.map",
            "title": "[2] Big Rock Candy Mountain",
            "sizeBytes": 81920,
            "digest": "",
            "official": True,
        },
    ],
    # 匹配成功后的开局倒计时(秒),与前端 10 秒等待窗口配合
    "QM_COUNTDOWN_SECONDS": 3,
    # 匹配局游戏设置(serializeOptions 各字段)
    "QM_GAME_SETTINGS": {
        "gameSpeed": 4,        # 序列化时写 6-gameSpeed
        "credits": 10000,
        "unitCount": 10,
        "shortGame": 1,
        "superWeapons": 0,
        "buildOffAlly": 0,
        "mcvRepacks": 1,
        "cratesAppear": 0,
        "gameMode": 1,
        "hostTeams": 0,
        "maxSlots": 8,
    },
    # 随机国家解析范围(RA2 多人可选国家数)
    "QM_COUNTRY_COUNT": 9,
    # 随机颜色解析范围(多人可选颜色数)
    "QM_COLOR_COUNT": 8,
}
