"""
游戏中继(gserv)进程级内存状态。

每个对局对应一个 GameInstance:
- 由房主 create(自定义房间)或匹配机器人预创建(快速匹配);
- 保存序列化的 gameOpts,joiner 通过 gameopts 命令获取;
- 锁步动作按回合聚合后统一广播。
"""
import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, Optional


def parse_human_players(opts: str) -> list:
    """
    从序列化 gameOpts 中解析真人玩家名单(含观察者)。

    序列化格式(前端 network/gameopt/Serializer.serializeOptions):
        <设置段>:<真人段>:@:<AI 段>,
    真人段为每 8 个逗号分隔值一组:name,countryId,colorId,startPos,teamId,0,0,0
    返回 [(name, country_id), ...],列表下标即锁步协议中的玩家 id。
    """
    sections = opts.split(":")
    if len(sections) < 2:
        return []
    tokens = sections[1].split(",")
    players = []
    for i in range(0, len(tokens) - 7, 8):
        name = tokens[i]
        if not name:
            continue
        try:
            country = int(tokens[i + 1])
        except ValueError:
            country = -1
        players.append((name, country))
    return players


@dataclass
class GservPlayer:
    """对局内的单个连接。"""

    consumer: "object"
    name: str
    player_id: int = -1            # 锁步协议玩家 id(gameOpts 真人下标)
    loaded_percent: int = 0
    active: bool = True            # active 0 表示观察者/暂离,不参与回合聚合
    ping: int = 0
    last_turn: int = -1            # 最近提交的回合号
    last_action_at: float = field(default_factory=time.monotonic)
    disconnected: bool = False

    @property
    def name_lower(self) -> str:
        return self.name.lower()


@dataclass
class GameInstance:
    """单个对局实例。"""

    game_id: str
    timestamp: str
    opts: str                       # 序列化 gameOpts
    version: str                    # 引擎版本
    mod_hash: str
    creator: str = ""
    expected_players: list = field(default_factory=list)   # [(name, countryId)]
    players: Dict[str, GservPlayer] = field(default_factory=dict)  # 小写名 -> 玩家
    started: bool = False
    closed: bool = False
    map_data: Optional[bytes] = None
    # 回合号 -> {player_id: 动作字节}
    turn_actions: Dict[int, Dict[int, bytes]] = field(default_factory=dict)
    broadcast_turn: int = -1        # 已广播到的回合号
    # 回合号 -> {player_id: 状态哈希},失步检测用
    turn_hashes: Dict[int, Dict[int, int]] = field(default_factory=dict)
    desync_reported: bool = False
    current_rate: int = 0           # 最近下发的网络回合时长(毫秒)
    created_at: float = field(default_factory=time.monotonic)

    def player_id_for(self, name: str) -> int:
        for idx, (pname, _country) in enumerate(self.expected_players):
            if pname.lower() == name.lower():
                return idx
        # gameOpts 解析失败的兜底:按加入顺序分配 id,避免锁步停摆
        if not self.expected_players:
            existing = self.players.get(name.lower())
            if existing is not None and existing.player_id >= 0:
                return existing.player_id
            used = {p.player_id for p in self.players.values()}
            candidate = 0
            while candidate in used:
                candidate += 1
            return candidate
        return -1

    def active_players(self) -> list:
        return [
            p
            for p in self.players.values()
            if p.active and not p.disconnected and p.player_id >= 0
        ]

    def everyone_loaded(self) -> bool:
        joined = [p for p in self.players.values() if not p.disconnected]
        if len(joined) < len(self.expected_players):
            return False
        return all(p.loaded_percent >= 100 for p in joined)


class GservState:
    """gserv 全局状态(进程级单例)。"""

    def __init__(self):
        self.lock = asyncio.Lock()
        self.instances: Dict[str, GameInstance] = {}

    def get(self, game_id: str) -> Optional[GameInstance]:
        return self.instances.get(game_id)

    def create(self, instance: GameInstance) -> bool:
        if instance.game_id in self.instances:
            return False
        self.instances[instance.game_id] = instance
        return True

    def drop_if_empty(self, game_id: str):
        instance = self.instances.get(game_id)
        if instance and not any(
            not p.disconnected for p in instance.players.values()
        ):
            instance.closed = True
            del self.instances[game_id]


STATE = GservState()
