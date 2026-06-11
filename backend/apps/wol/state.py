"""
WOL 大厅进程级内存状态。

单进程部署(daphne)下所有连接共享此注册表;
多进程部署需改造为 Redis 等共享存储。
"""
import asyncio
import itertools
import time
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class WolChannel:
    """聊天/游戏频道。"""

    name: str                      # 未转义名称(含空格)
    is_game: bool = False
    key: str = ""                  # 频道密码
    owner: str = ""                # 创建者昵称(游戏频道房主)
    topic: str = ""                # 原始 topic 串(gXXN39,... 格式)
    min_players: int = 1
    max_players: int = 8
    channel_type: int = 45         # 前端 getClientChannelType()
    tournament: int = 0
    max_users: int = 0             # MODE +l 设置;0 表示不限制
    members: Dict[str, "WolSession"] = field(default_factory=dict)  # 小写昵称 -> 会话

    @property
    def flags(self) -> int:
        """326 列表回复中的 flags(384=有密码,128=开放)。"""
        return 384 if self.key else 128

    def member_count(self) -> int:
        return len(self.members)

    def is_full(self) -> bool:
        limit = self.max_users or (self.max_players if self.is_game else 0)
        return bool(limit) and self.member_count() >= limit


@dataclass
class WolSession:
    """单个已连接客户端的会话状态。"""

    consumer: "object"             # WolConsumer 实例
    nick: str = ""                 # 登录后的昵称(原始大小写)
    sku: int = 0
    client_version: str = ""
    locale: int = 0
    ping: int = 0                  # 最近一次测量的 RTT(毫秒)
    operator_in: set = field(default_factory=set)  # 拥有 OP 的频道(小写名)
    channels: set = field(default_factory=set)     # 已加入频道(原始名)
    connected_at: float = field(default_factory=time.monotonic)

    @property
    def nick_lower(self) -> str:
        return self.nick.lower()

    def is_logged_in(self) -> bool:
        return bool(self.nick)


class WolState:
    """WOL 大厅全局状态(进程级单例)。"""

    def __init__(self):
        self.lock = asyncio.Lock()
        self.users: Dict[str, WolSession] = {}      # 小写昵称 -> 会话
        self.channels: Dict[str, WolChannel] = {}   # 原始频道名 -> 频道
        self._game_id_seq = itertools.count(int(time.time()) % 100000 * 10)

    # ------------------------------------------------------------------
    def next_game_id(self) -> str:
        return str(next(self._game_id_seq))

    def find_channel(self, name: str) -> Optional[WolChannel]:
        return self.channels.get(name)

    def get_or_create_chat_channel(self, name: str, key: str = "") -> WolChannel:
        channel = self.channels.get(name)
        if channel is None:
            channel = WolChannel(name=name, is_game=False, key=key)
            self.channels[name] = channel
        return channel

    def drop_channel_if_empty(self, name: str):
        channel = self.channels.get(name)
        if channel is not None and not channel.members:
            del self.channels[name]

    def online_count(self) -> int:
        return len(self.users)


# 进程级单例
STATE = WolState()
