"""
前后端共用数据结构定义(后端权威版本)。

所有跨服务复用的数据结构集中在此,确保:
- HTTP 接口(ladder/gameres)
- WebSocket 服务(wol/gserv)
使用同一套字段命名,且与前端解析逻辑一一对应。
"""
from dataclasses import asdict, dataclass
from enum import IntEnum
from typing import Optional


class PlayerRankType(IntEnum):
    """军衔类型(与前端 network/PlayerRankType 枚举顺序一致)。"""

    PRIVATE = 0          # 列兵
    CORPORAL = 1         # 下士
    SERGEANT = 2         # 中士
    LIEUTENANT = 3       # 中尉
    MAJOR = 4            # 少校
    COLONEL = 5          # 上校
    BRIG_GENERAL = 6     # 准将
    GENERAL = 7          # 上将
    FIVE_STAR_GENERAL = 8  # 五星上将
    COMMANDER_IN_CHIEF = 9  # 总司令


class LadderType:
    """天梯类型(与前端 network/wladderConfig.LadderType 一致)。"""

    SOLO_1V1 = "1v1"

    ALL = (SOLO_1V1,)


# 赛季别名(与前端 wladderConfig 一致)
CURRENT_SEASON = "current"
PREV_SEASON = "prev"
MAX_LIST_SEARCH_COUNT = 50


@dataclass
class LadderProfile:
    """
    天梯玩家档案。

    前端消费字段(Ladder 组件/RankIndicator 组件):
    name、rank、rankType(可空)、points、wins、losses、
    mmr(可空)、bonusPool(可空)。
    未上榜玩家 rankType 为空,前端显示 TXT_UNRANKED。
    """

    name: str
    rank: int
    points: int
    wins: int
    losses: int
    rankType: Optional[int] = None
    mmr: Optional[int] = None
    bonusPool: Optional[int] = None

    def to_json(self) -> dict:
        """序列化为前端期望的 JSON 对象(省略空字段)。"""
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}


def compute_rank_type(rank: int, total: int) -> Optional[int]:
    """
    按名次占比计算军衔图标。

    与官方服务器一致的分布无公开资料,这里采用按比例阶梯:
    第 1 名总司令,前 1% 五星上将,之后按比例递减,
    未上榜(rank<=0)返回 None。
    """
    if rank <= 0 or total <= 0:
        return None
    if rank == 1:
        return int(PlayerRankType.COMMANDER_IN_CHIEF)
    ratio = rank / total
    ladder = [
        (0.01, PlayerRankType.FIVE_STAR_GENERAL),
        (0.05, PlayerRankType.GENERAL),
        (0.10, PlayerRankType.BRIG_GENERAL),
        (0.20, PlayerRankType.COLONEL),
        (0.35, PlayerRankType.MAJOR),
        (0.50, PlayerRankType.LIEUTENANT),
        (0.70, PlayerRankType.SERGEANT),
        (0.85, PlayerRankType.CORPORAL),
    ]
    for threshold, rank_type in ladder:
        if ratio <= threshold:
            return int(rank_type)
    return int(PlayerRankType.PRIVATE)


class GameResType(IntEnum):
    """单局完成状态(与前端 network/gameres/GameResType 一致)。"""

    CONNECTION_LOST = 2
    PLAYING = 8
    DRAW = 64
    WIN = 256
    LOSS = 512
    RESIGN = 528
    DISCONNECT = 768
