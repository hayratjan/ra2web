"""
快速匹配机器人(matchbot)。

客户端流程(前端 QuickGameScreen):
1. join "#Lob <qmChanId> 0"(全局密码)
2. privmsg matchbot :Match COU=x, COL=y, VRS=v, MOD=h, RKD=0|1
3. 收到 "Working" 进入等待;每 5 秒 privmsg matchbot :Stats
4. 收到 "Matched <秒>" 倒计时,然后等待 WOL STARTG 消息
5. 收到 STARTG 后连接 gserv join 对局

服务器职责:
- 校验版本/模组哈希;按 MMR 就近配对(在线连线优化);
- 在本机 gserv 预创建对局实例(写入序列化 gameOpts);
- 倒计时结束后通过 WOL 向双方下发 STARTG。
"""
import asyncio
import base64
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Optional

from django.conf import settings

from apps.core import qm_codes
from apps.gserv.state import STATE as GSERV_STATE
from apps.gserv.state import GameInstance

logger = logging.getLogger(__name__)

# 与前端 game/gameopts/constants 一致的"随机"占位值
RANDOM_ID = -1
NO_TEAM_ID = -1
RANDOM_START_POS = -1


@dataclass
class QueueEntry:
    """匹配队列中的一名玩家。"""

    session: "object"              # WolSession
    country_id: int
    color_id: int
    ranked: bool
    version: str
    mod_hash: str
    mmr: int = 1000
    enqueued_at: float = field(default_factory=time.monotonic)


def _encode_utf16_base64(text: str) -> str:
    """与前端 Base64.encode(utf16ToBinaryString(text)) 一致的编码。"""
    raw = bytearray()
    for ch in text:
        code = ord(ch)
        raw.append(code & 0xFF)
        raw.append((code >> 8) & 0xFF)
    return base64.b64encode(bytes(raw)).decode("ascii")


def serialize_game_opts(opts: dict, humans: list, ai_count: int = 0) -> str:
    """
    生成序列化 gameOpts 字符串(与前端 Serializer.serializeOptions 一致)。

    humans: [{"name","countryId","colorId","startPos","teamId"}]
    """
    head = ",".join(
        str(v)
        for v in [
            "0",
            "0",
            6 - opts["gameSpeed"],
            opts["credits"],
            opts["unitCount"],
            opts["shortGame"],
            opts["superWeapons"],
            opts["buildOffAlly"],
            opts["mcvRepacks"],
            opts["cratesAppear"],
            opts["gameMode"],
            opts["hostTeams"],
            _encode_utf16_base64(opts["mapTitle"]),
            opts["maxSlots"],
            1 if opts["mapOfficial"] else 0,
            opts["mapSizeBytes"],
            opts["mapName"],
            opts["mapDigest"],
        ]
    )
    humans_part = ",".join(
        f"{h['name']},{h['countryId']},{h['colorId']},{h['startPos']},{h['teamId']},0,0,0"
        for h in humans
    )
    ai_part = ",".join("0,-1,-1,-1,-1" for _ in range(ai_count))
    return f"{head}:{humans_part}:@:{ai_part},"


class Matchmaker:
    """单进程内存版匹配器。"""

    def __init__(self):
        # (ladder_type, ranked) -> [QueueEntry]
        self.queues = {}
        self.recent_wait_times = []   # 最近完成匹配的等待秒数,用于 Stats

    # ------------------------------------------------------------------
    def queue_for(self, ladder_type: str, ranked: bool) -> list:
        return self.queues.setdefault((ladder_type, ranked), [])

    def remove_session(self, session) -> bool:
        """玩家离开队列(离开频道/断线时调用)。"""
        removed = False
        for queue in self.queues.values():
            before = len(queue)
            queue[:] = [e for e in queue if e.session is not session]
            removed = removed or len(queue) != before
        return removed

    def stats(self) -> tuple:
        """返回 (排队人数, 平均等待秒数或 -1)。"""
        count = sum(len(q) for q in self.queues.values())
        if not self.recent_wait_times:
            return count, -1
        avg = int(sum(self.recent_wait_times) / len(self.recent_wait_times))
        return count, avg

    # ------------------------------------------------------------------
    async def enqueue(self, entry: QueueEntry, ladder_type: str = "1v1") -> Optional[str]:
        """
        入队并尝试配对。

        返回 None 表示已入队(回复 Working 由调用方处理),
        否则返回 qm_codes 错误字符串。
        """
        cfg = settings.RA2WEB
        if not cfg["QM_MAP_POOL"]:
            return qm_codes.RPL_MODE_UNAVAIL
        min_version = cfg["MIN_CLIENT_VERSION"]
        if min_version and entry.version != min_version:
            return qm_codes.RPL_BAD_VERS

        queue = self.queue_for(ladder_type, entry.ranked)
        if any(e.session is entry.session for e in queue):
            return qm_codes.RPL_ALREADY_QUEUED
        queue.append(entry)
        await self.try_match(ladder_type, entry.ranked)
        return None

    async def try_match(self, ladder_type: str, ranked: bool):
        """按 MMR 就近原则配对队列中的玩家(在线连线优化)。"""
        queue = self.queue_for(ladder_type, ranked)
        while len(queue) >= 2:
            first = queue[0]
            waited = time.monotonic() - first.enqueued_at
            # 版本与模组必须一致才能同场对局
            candidates = [
                e
                for e in queue[1:]
                if e.version == first.version and e.mod_hash == first.mod_hash
            ]
            if not candidates:
                return
            # 直接选 MMR 最接近的对手;队列在入队/重排时触发,
            # 始终保证可配对的玩家不会滞留
            best = min(candidates, key=lambda e: abs(e.mmr - first.mmr))
            queue.remove(first)
            queue.remove(best)
            self.recent_wait_times.append(int(waited))
            self.recent_wait_times = self.recent_wait_times[-20:]
            await self.start_match(first, best, ranked)

    async def start_match(self, a: QueueEntry, b: QueueEntry, ranked: bool):
        """创建 gserv 实例并通知双方。"""
        cfg = settings.RA2WEB
        game_map = random.choice(cfg["QM_MAP_POOL"])
        opts = dict(cfg["QM_GAME_SETTINGS"])
        opts.update(
            {
                "mapTitle": game_map.get("title", game_map["name"]),
                "mapName": game_map["name"],
                "mapDigest": game_map.get("digest", ""),
                "mapSizeBytes": game_map.get("sizeBytes", 0),
                "mapOfficial": game_map.get("official", True),
            }
        )

        used_colors = set()

        def resolve_player(entry: QueueEntry) -> dict:
            country = entry.country_id
            if country == RANDOM_ID:
                country = random.randrange(cfg["QM_COUNTRY_COUNT"])
            color = entry.color_id
            if color == RANDOM_ID or color in used_colors:
                color = random.choice(
                    [c for c in range(cfg["QM_COLOR_COUNT"]) if c not in used_colors]
                )
            used_colors.add(color)
            return {
                "name": entry.session.nick,
                "countryId": country,
                "colorId": color,
                "startPos": RANDOM_START_POS,
                "teamId": NO_TEAM_ID,
            }

        humans = [resolve_player(a), resolve_player(b)]
        opts_str = serialize_game_opts(opts, humans)

        from apps.wol.state import STATE as WOL_STATE

        game_id = WOL_STATE.next_game_id()
        timestamp = str(int(time.time()))
        instance = GameInstance(
            game_id=game_id,
            timestamp=timestamp,
            opts=opts_str,
            version=a.version,
            mod_hash=a.mod_hash,
            creator=cfg["MATCH_BOT_NAME"],
            expected_players=[(h["name"], h["countryId"]) for h in humans],
        )
        async with GSERV_STATE.lock:
            GSERV_STATE.create(instance)
        # 预创建实例若 3 分钟内未开赛且无人连接则回收,防止泄漏
        asyncio.get_event_loop().create_task(self._cleanup_stale(game_id, 180))

        countdown = cfg["QM_COUNTDOWN_SECONDS"]
        for entry in (a, b):
            await entry.session.consumer.send_page(
                cfg["MATCH_BOT_NAME"], f"{qm_codes.RPL_MATCHED} {countdown}"
            )

        asyncio.get_event_loop().create_task(
            self._send_start_after(countdown, a, b, game_id, timestamp)
        )

    async def _cleanup_stale(self, game_id: str, delay_seconds: int):
        """回收始终未开赛且无人连接的预创建实例。"""
        await asyncio.sleep(delay_seconds)
        async with GSERV_STATE.lock:
            instance = GSERV_STATE.get(game_id)
            if instance and not instance.started and not instance.players:
                instance.closed = True
                GSERV_STATE.instances.pop(game_id, None)

    async def _send_start_after(self, countdown: int, a, b, game_id: str, timestamp: str):
        """倒计时结束后向双方下发 STARTG;若有人掉线则让对方重新排队。"""
        await asyncio.sleep(countdown)
        cfg = settings.RA2WEB
        sessions = [a.session, b.session]
        alive = [s for s in sessions if s.consumer.is_open()]
        if len(alive) < 2:
            async with GSERV_STATE.lock:
                GSERV_STATE.instances.pop(game_id, None)
            for session in alive:
                await session.consumer.send_page(
                    cfg["MATCH_BOT_NAME"], qm_codes.RPL_REQUEUE
                )
                entry = a if session is a.session else b
                entry.enqueued_at = time.monotonic()
                queue = self.queue_for("1v1", entry.ranked)
                queue.insert(0, entry)
                await self.try_match("1v1", entry.ranked)
            return
        for session in sessions:
            await session.consumer.send_startg(game_id, timestamp)


MATCHMAKER = Matchmaker()
