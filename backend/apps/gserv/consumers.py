"""
游戏中继服务(gserv)WebSocket 消费者。

文本协议(与前端 network/GservConnection 一一对应):
    cvers <引擎版本> <API 版本>
    user <昵称> <Base64 密码>
    create <对局id> <时间戳> <gameOpts> <引擎版本> <模组哈希>
    join <对局id> <引擎版本> <模组哈希>
    gameopts                       -> 500 :<gameOpts>
    loaded <百分比> / loadinfo     -> 600 :<载入信息>
    active <0|1>
    taunt <编号>                   -> 803 广播
    privmsg <#all|名字列表> :<内容>
    ping :<时间戳> / pong <令牌>

二进制协议(首字节 0x02):
    [02 01 turn(u32 LE) actions...]  客户端提交某回合动作
    [02 02 turn(u32 LE) hash(u32)]   状态哈希(失步检测)
    [02 03 map...]                   上传地图
    [02 04]                          请求地图 -> [02 02 map...]

锁步聚合:服务器等齐所有活跃玩家某回合的动作后,
按 [02 01 turn u8(人数) (u8 id, u16 len, bytes)...] 广播给全部玩家。

在线连线优化:
- 网络回合时长(802)按全员实测 RTT 自适应调整;
- 回合超时(30s)自动剔除掉线玩家并广播 804,避免全场卡死;
- 载入阶段与对局中保活探测,死连接及时回收;
- 开赛前断线的玩家可重连挂回原槽位。
"""
import asyncio
import base64
import logging
import struct
import time
from typing import Optional

from channels.db import database_sync_to_async

from apps.accounts.services import authenticate
from apps.core import gserv_codes as codes
from apps.core.consumers import BaseIrcConsumer

from .state import STATE, GameInstance, GservPlayer, parse_human_players

logger = logging.getLogger("gserv")

WATCHDOG_INTERVAL = 1            # 回合超时巡检周期(秒)
RATE_UPDATE_INTERVAL = 5         # 速率自适应周期(秒)
MIN_RATE_MILLIS = 40             # 网络回合时长下限
MAX_RATE_MILLIS = 400            # 网络回合时长上限
MAX_INSTANCES_PER_USER = 4       # 单账号同时创建实例上限


class GservConsumer(BaseIrcConsumer):
    """单个 gserv 客户端连接。"""

    KEEPALIVE_INTERVAL = 10
    KEEPALIVE_MISS_LIMIT = 3

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    async def on_connected(self):
        self.nick = ""
        self.api_ok = False
        self.instance: Optional[GameInstance] = None
        self.player: Optional[GservPlayer] = None

    async def on_disconnected(self, close_code):
        if self.instance and self.player:
            async with STATE.lock:
                await self._drop_player("connection closed")

    async def on_rtt_sample(self, rtt_ms: int):
        """RTT 采样供速率自适应与载入信息展示使用。"""
        if self.player is not None:
            self.player.ping = rtt_ms

    # ------------------------------------------------------------------
    # 消息分发
    # ------------------------------------------------------------------
    async def handle_line(self, line: str):
        parts = line.split(" ")
        command = parts[0].lower()
        handler = {
            "cvers": self.cmd_cvers,
            "user": self.cmd_user,
            "create": self.cmd_create,
            "join": self.cmd_join,
            "gameopts": self.cmd_gameopts,
            "loaded": self.cmd_loaded,
            "loadinfo": self.cmd_loadinfo,
            "active": self.cmd_active,
            "taunt": self.cmd_taunt,
            "privmsg": self.cmd_privmsg,
            "ping": self.reply_ping,
            "pong": self.cmd_pong,
        }.get(command)
        if handler is None:
            await self.send_code(codes.RPL_INVALID_PARAMS, "- :Unknown command")
            return
        await handler(line, parts)

    # ------------------------------------------------------------------
    # 文本命令
    # ------------------------------------------------------------------
    async def cmd_cvers(self, line, parts):
        """cvers <引擎版本> <API 版本>"""
        if len(parts) < 3:
            await self.send_code(codes.RPL_NOT_ENOUGH_PARAMS, "- :Need more params")
            return
        if parts[2] != str(self.cfg["GSERV_API_VERSION"]):
            await self.send_code(codes.RPL_CVERS_OUTDATED, "u :Unsupported API version")
            return
        self.api_ok = True
        await self.send_code(codes.RPL_CVERS_OK, "u :ok")

    async def cmd_user(self, line, parts):
        """user <昵称> <Base64 密码>:凭据与 WOL 账号一致。"""
        if len(parts) < 3:
            await self.send_code(codes.RPL_NOT_ENOUGH_PARAMS, "- :Need more params")
            return
        nick = parts[1]
        try:
            password = base64.b64decode(parts[2]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            password = ""
        if self.nick:
            await self.send_code(
                codes.RPL_ALREADY_LOGGED_IN, f"{nick} :Already logged in"
            )
            return
        auth = await database_sync_to_async(authenticate)(
            nick, password, allow_register=False
        )
        if not auth.ok:
            await self.send_code(codes.RPL_BAD_LOGIN, f"{nick} :Bad login")
            return
        self.nick = auth.account.name
        await self.send_code(codes.RPL_LOGGED_IN, f"{self.nick} :Logged in")
        self.start_keepalive()

    async def cmd_create(self, line, parts):
        """create <对局id> <时间戳> <gameOpts> <引擎版本> <模组哈希>"""
        if not self.nick:
            await self.send_code(codes.RPL_NOT_LOGGED_IN, "- :Login first")
            return
        if len(parts) < 6:
            await self.send_code(codes.RPL_NOT_ENOUGH_PARAMS, f"{self.nick} :Need more params")
            return
        game_id, timestamp, opts, version, mod_hash = parts[1:6]
        async with STATE.lock:
            owned = sum(
                1
                for inst in STATE.instances.values()
                if inst.creator.lower() == self.nick.lower() and not inst.closed
            )
            if owned >= MAX_INSTANCES_PER_USER:
                await self.send_code(
                    codes.RPL_INSTANCE_TOO_MANY, f"{self.nick} :Too many instances"
                )
                return
            if STATE.get(game_id) is not None:
                await self.send_code(
                    codes.RPL_INSTANCE_EXISTS, f"{self.nick} :Instance exists"
                )
                return
            instance = GameInstance(
                game_id=game_id,
                timestamp=timestamp,
                opts=opts,
                version=version,
                mod_hash=mod_hash,
                creator=self.nick,
                expected_players=parse_human_players(opts),
            )
            STATE.create(instance)
            await self._attach(instance)
        await self.send_code(
            codes.RPL_INSTANCE_CREATED, f"{self.nick} :Instance created"
        )

    async def cmd_join(self, line, parts):
        """join <对局id> <引擎版本> <模组哈希>"""
        if not self.nick:
            await self.send_code(codes.RPL_NOT_LOGGED_IN, "- :Login first")
            return
        if len(parts) < 4:
            await self.send_code(codes.RPL_NOT_ENOUGH_PARAMS, f"{self.nick} :Need more params")
            return
        game_id, version, mod_hash = parts[1:4]
        async with STATE.lock:
            instance = STATE.get(game_id)
            if instance is None or instance.closed:
                await self.send_code(
                    codes.RPL_INSTANCE_NONEXISTENT, f"{self.nick} :No such instance"
                )
                return
            if instance.version != version or instance.mod_hash != mod_hash:
                await self.send_code(
                    codes.RPL_INSTANCE_VERS_MISMATCH, f"{self.nick} :Version mismatch"
                )
                return
            existing = instance.players.get(self.nick.lower())
            if instance.started and not (existing and existing.disconnected):
                await self.send_code(
                    codes.RPL_INSTANCE_ALREADY_STARTED, f"{self.nick} :Already started"
                )
                return
            if (
                instance.expected_players
                and instance.player_id_for(self.nick) < 0
            ):
                await self.send_code(
                    codes.RPL_INSTANCE_NOT_ALLOWED, f"{self.nick} :Not allowed"
                )
                return
            await self._attach(instance)
        await self.send_code(
            codes.RPL_INSTANCE_CONNECTED, f"{self.nick} :Connected"
        )

    async def _attach(self, instance: GameInstance):
        """把当前连接挂入对局实例(需持有 STATE.lock)。"""
        player = instance.players.get(self.nick.lower())
        if player is None:
            player = GservPlayer(consumer=self, name=self.nick)
            instance.players[self.nick.lower()] = player
        else:
            # 断线重连:复用原玩家槽位
            player.consumer = self
            player.disconnected = False
        player.player_id = instance.player_id_for(self.nick)
        self.instance = instance
        self.player = player

    async def cmd_gameopts(self, line, parts):
        """gameopts -> 500 :<序列化 gameOpts>"""
        if self.instance is None:
            await self.send_code(codes.RPL_NO_INSTANCE, f"{self.nick or '-'} :No instance")
            return
        await self.send_code(
            codes.RPL_GAME_OPTS, f"{self.nick} :{self.instance.opts}"
        )

    async def cmd_loaded(self, line, parts):
        """loaded <百分比>:全员载入完成后广播 700 开赛 + 初始速率。"""
        if self.instance is None or self.player is None or len(parts) < 2:
            return
        try:
            self.player.loaded_percent = int(float(parts[1]))
        except ValueError:
            return
        async with STATE.lock:
            instance = self.instance
            if not instance.started and instance.everyone_loaded():
                instance.started = True
                for player in instance.players.values():
                    if not player.disconnected:
                        await player.consumer.send_code(
                            codes.RPL_GAME_START, f"{player.name} :start"
                        )
                await self._broadcast_rate(instance)
                asyncio.get_event_loop().create_task(self._watchdog(instance))

    async def cmd_loadinfo(self, line, parts):
        """loadinfo -> 600 :<name,status,pct,ping,lagAllowance 列表>"""
        if self.instance is None:
            return
        info = self._serialize_load_info(self.instance)
        await self.send_code(codes.RPL_LOAD_INFO, f"{self.nick} :{info}")

    def _serialize_load_info(self, instance: GameInstance) -> str:
        """与前端 LoadInfoParser 的解析格式(每玩家 5 个值)一致。"""
        now = time.monotonic()
        entries = []
        for name, _country in instance.expected_players:
            player = instance.players.get(name.lower())
            if player is None:
                entries.extend([name, "0", "0", "0", str(codes.TURN_TIMEOUT_MILLIS)])
                continue
            status = "2" if player.disconnected else "1"
            lag_allowance = max(
                0,
                codes.TURN_TIMEOUT_MILLIS - int((now - player.last_action_at) * 1000),
            )
            entries.extend(
                [
                    player.name,
                    status,
                    str(player.loaded_percent),
                    str(player.ping),
                    str(lag_allowance),
                ]
            )
        return ",".join(entries)

    async def cmd_active(self, line, parts):
        """active <0|1>:观察者/暂离切换,影响回合聚合等待名单。"""
        if self.instance is None or self.player is None or len(parts) < 2:
            return
        self.player.active = parts[1] == "1"
        async with STATE.lock:
            await self._flush_turns(self.instance)

    async def cmd_taunt(self, line, parts):
        """taunt <编号> -> 向其他玩家广播 :<from> 803 <to> :<编号>"""
        if self.instance is None or len(parts) < 2:
            return
        for player in self.instance.players.values():
            if player.consumer is not self and not player.disconnected:
                await player.consumer.send_line(
                    f":{self.nick} {codes.RPL_TAUNT} {player.name} :{parts[1]}"
                )

    async def cmd_privmsg(self, line, parts):
        """privmsg <#all|名字列表> :<内容>:对局内聊天中继。"""
        if self.instance is None or len(parts) < 2:
            return
        sep = line.find(" :")
        if sep < 0:
            return
        text = line[sep + 2 :]
        target = parts[1]
        if target == codes.RECIPIENT_ALL:
            for player in self.instance.players.values():
                if player.consumer is not self and not player.disconnected:
                    await player.consumer.send_line(
                        f":{self.nick} PRIVMSG {codes.RECIPIENT_ALL} :{text}"
                    )
            return
        for name in target.split(","):
            player = self.instance.players.get(name.lower())
            if player is not None and player.consumer is not self and not player.disconnected:
                await player.consumer.send_line(
                    f":{self.nick} PRIVMSG {player.name} :{text}"
                )

    async def cmd_pong(self, line, parts):
        """保活应答,采样 RTT 供速率自适应使用。"""
        await self.handle_pong(parts[1] if len(parts) > 1 else "")

    # ------------------------------------------------------------------
    # 二进制协议
    # ------------------------------------------------------------------
    async def handle_binary(self, data: bytes):
        if len(data) < 2 or data[0] != codes.REQ_BIN_PREFIX:
            return
        sub = data[1]
        if sub == codes.REQ_BIN_GAME_ACTIONS:
            await self.bin_game_actions(data[2:])
        elif sub == codes.REQ_BIN_GAME_STATE_HASH:
            await self.bin_state_hash(data[2:])
        elif sub == codes.REQ_BIN_PUT_MAP:
            await self.bin_put_map(data[2:])
        elif sub == codes.REQ_BIN_GET_MAP:
            await self.bin_get_map()

    async def bin_game_actions(self, payload: bytes):
        """客户端提交某回合动作:turn(u32 LE) + 动作字节。"""
        if self.instance is None or self.player is None or len(payload) < 4:
            return
        if self.player.player_id < 0:
            return
        (turn,) = struct.unpack_from("<I", payload, 0)
        actions = payload[4:]
        async with STATE.lock:
            instance = self.instance
            bucket = instance.turn_actions.setdefault(turn, {})
            bucket[self.player.player_id] = actions
            self.player.last_turn = turn
            self.player.last_action_at = time.monotonic()
            await self._flush_turns(instance)

    async def _flush_turns(self, instance: GameInstance):
        """
        广播所有已凑齐的回合(需持有 STATE.lock)。

        每回合需等待全部"活跃"玩家提交;玩家掉线/转观察后
        立刻重新检查,避免剩余玩家被卡住。
        """
        if not instance.started:
            return
        while True:
            next_turn = instance.broadcast_turn + 1
            bucket = instance.turn_actions.get(next_turn)
            active_ids = {p.player_id for p in instance.active_players()}
            if not active_ids:
                return
            if bucket is None or not active_ids.issubset(bucket.keys()):
                return
            frame = bytearray()
            frame.append(codes.RPL_BIN_PREFIX)
            frame.append(codes.RPL_BIN_GAME_ACTIONS)
            frame += struct.pack("<I", next_turn)
            entries = [(pid, bucket[pid]) for pid in sorted(bucket.keys())]
            frame.append(len(entries))
            for pid, actions in entries:
                frame.append(pid)
                frame += struct.pack("<H", len(actions))
                frame += actions
            payload = bytes(frame)
            for player in instance.players.values():
                if not player.disconnected:
                    await player.consumer.send_binary(payload)
            del instance.turn_actions[next_turn]
            instance.broadcast_turn = next_turn

    async def bin_state_hash(self, payload: bytes):
        """状态哈希比对:同回合哈希不一致即广播 801 失步。"""
        if self.instance is None or self.player is None or len(payload) < 8:
            return
        turn, state_hash = struct.unpack_from("<II", payload, 0)
        async with STATE.lock:
            instance = self.instance
            if instance.desync_reported:
                return
            bucket = instance.turn_hashes.setdefault(turn, {})
            bucket[self.player.player_id] = state_hash
            if len(set(bucket.values())) > 1:
                instance.desync_reported = True
                logger.warning("对局 %s 在回合 %s 检测到失步", instance.game_id, turn)
                for player in instance.players.values():
                    if not player.disconnected:
                        await player.consumer.send_code(
                            codes.RPL_GAME_DESYNC, f"{player.name} :Desync"
                        )
            # 只保留最近的哈希记录,防止内存膨胀
            if len(instance.turn_hashes) > 64:
                for old_turn in sorted(instance.turn_hashes)[:-32]:
                    del instance.turn_hashes[old_turn]

    async def bin_put_map(self, payload: bytes):
        """接收房主上传的自定义地图(限 2MB)。"""
        if self.instance is None:
            return
        if len(payload) > codes.MAX_MAP_TRANSFER_BYTES:
            await self.send_code(codes.RPL_MAP_TOO_BIG, f"{self.nick} :Map too big")
            return
        if self.instance.map_data is not None:
            await self.send_code(
                codes.RPL_MAP_ALREADY_SENT, f"{self.nick} :Map already sent"
            )
            return
        self.instance.map_data = payload

    async def bin_get_map(self):
        """下发地图数据:[02 02 <地图字节>]。"""
        if self.instance is None or self.instance.map_data is None:
            return
        await self.send_binary(
            bytes([codes.RPL_BIN_PREFIX, codes.RPL_BIN_MAP_DATA])
            + self.instance.map_data
        )

    # ------------------------------------------------------------------
    # 速率自适应与超时巡检(在线连线优化核心)
    # ------------------------------------------------------------------
    async def _broadcast_rate(self, instance: GameInstance):
        """
        根据全员实测 RTT 计算网络回合时长(毫秒)并广播 802。

        客户端 computeNetworkTurnMillis(rate, turnMs) 会把该值
        向上取整为游戏 tick 的整数倍。
        """
        pings = [p.ping for p in instance.active_players() if p.ping > 0]
        max_ping = max(pings) if pings else 80
        rate = int(min(MAX_RATE_MILLIS, max(MIN_RATE_MILLIS, max_ping * 1.5 + 20)))
        rate = (rate // 10) * 10
        if rate == instance.current_rate and instance.broadcast_turn >= 0:
            return
        instance.current_rate = rate
        turn_no = max(0, instance.broadcast_turn)
        for player in instance.players.values():
            if not player.disconnected:
                await player.consumer.send_code(
                    codes.RPL_NET_RATE, f"{player.name} :{rate},{turn_no}"
                )

    async def _watchdog(self, instance: GameInstance):
        """对局巡检:剔除超时玩家、周期更新速率、回收空实例。"""
        last_rate_update = time.monotonic()
        try:
            while not instance.closed:
                await asyncio.sleep(WATCHDOG_INTERVAL)
                async with STATE.lock:
                    if not any(
                        not p.disconnected for p in instance.players.values()
                    ):
                        STATE.drop_if_empty(instance.game_id)
                        return
                    # 回合等待超时:剔除拖慢全场的掉线玩家
                    next_turn = instance.broadcast_turn + 1
                    bucket = instance.turn_actions.get(next_turn, {})
                    waiting_on = [
                        p
                        for p in instance.active_players()
                        if p.player_id not in bucket
                    ]
                    submitted_any = bool(bucket)
                    now = time.monotonic()
                    if submitted_any:
                        for player in waiting_on:
                            waited_ms = (now - player.last_action_at) * 1000
                            if waited_ms > codes.TURN_TIMEOUT_MILLIS:
                                await self._drop_player_obj(
                                    instance, player, "turn timeout"
                                )
                        await self._flush_turns(instance)
                    # 周期性速率自适应
                    if now - last_rate_update >= RATE_UPDATE_INTERVAL:
                        last_rate_update = now
                        await self._broadcast_rate(instance)
        except asyncio.CancelledError:
            pass

    async def _drop_player(self, reason: str):
        """当前连接对应玩家掉线(需持有 STATE.lock)。"""
        if self.instance and self.player:
            await self._drop_player_obj(self.instance, self.player, reason)

    async def _drop_player_obj(self, instance: GameInstance, player: GservPlayer, reason: str):
        """把玩家标记为掉线并广播 804,随后尝试推进回合。"""
        if player.disconnected:
            return
        player.disconnected = True
        logger.info(
            "玩家 %s 离开对局 %s(%s)", player.name, instance.game_id, reason
        )
        for other in instance.players.values():
            if other is not player and not other.disconnected:
                await other.consumer.send_code(
                    codes.RPL_PLAYER_DISCONNECT, f"{other.name} :{player.name}"
                )
        await self._flush_turns(instance)
        STATE.drop_if_empty(instance.game_id)
