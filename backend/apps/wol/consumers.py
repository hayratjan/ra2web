"""
WOL(Westwood Online)聊天大厅 WebSocket 服务。

协议为"IRC over WebSocket"文本协议,所有消息格式
与前端 network/WolConnection 的解析逻辑一一对应:

客户端 -> 服务器:
    cvers <版本> <SKU>
    pass <Base64 密码> / nick <昵称> / user ...(同一帧三行)
    setlocale <code> / getlocale <nick>
    list <类型> <类型>
    join <频道> [密码]
    joingame <频道> <min> <max> <type> <p4> <p5> <tournament> <p7> [密码](创建)
    joingame <频道> <observer> [密码](加入)
    part/kick/privmsg/page/topic/mode/gameopt/names
    startg <频道> <玩家1,玩家2,...>
    gping <频道> <id> <毫秒>
    ping :<时间戳>

服务器 -> 客户端的回复码见 apps/core/wol_codes.py。

在线连线优化:
- 服务器主动 ping 保活,及时清理死连接并提供真实延迟数据;
- 登录排队(720)避免过载;
- 同名重复登录自动顶号,断线后可立即重连;
- 单连接消息速率限制,防刷屏导致大厅卡顿。
"""
import asyncio
import base64
import logging
import time

from channels.db import database_sync_to_async

from apps.core import wol_codes as codes
from apps.core.consumers import BaseIrcConsumer
from apps.core.irc import (
    escape_channel_name,
    unescape_channel_name,
    user_prefix,
)
from apps.accounts.services import authenticate

from .matchmaker import MATCHMAKER, QueueEntry
from .state import STATE, WolChannel, WolSession

logger = logging.getLogger("wol")

LOGIN_QUEUE_POLL = 5             # 登录排队轮询周期(秒)


class WolConsumer(BaseIrcConsumer):
    """单个 WOL 客户端连接。"""

    KEEPALIVE_INTERVAL = 30

    # ------------------------------------------------------------------
    # 连接生命周期
    # ------------------------------------------------------------------
    async def on_connected(self):
        self.session = WolSession(consumer=self)
        # 登录前的暂存凭据
        self._pending_pass = ""
        self._pending_nick = ""

    async def on_disconnected(self, close_code):
        session = self.session
        MATCHMAKER.remove_session(session)
        if session.is_logged_in():
            async with STATE.lock:
                # 广播离开所有频道
                for chan_name in list(session.channels):
                    channel = STATE.find_channel(chan_name)
                    if channel:
                        await self._channel_part(channel, session, broadcast_self=False)
                if STATE.users.get(session.nick_lower) is session:
                    del STATE.users[session.nick_lower]

    async def on_rtt_sample(self, rtt_ms: int):
        """保活 RTT 用作大厅玩家列表中的延迟展示。"""
        self.session.ping = rtt_ms

    async def on_rate_limited(self):
        nick = self.session.nick or "-"
        await self.send_code(
            codes.ERR_RATE_LIMIT_EXCEEDED, f"{nick} {nick} :Rate limit exceeded"
        )
        await self.close()

    # ------------------------------------------------------------------
    # 专用发送工具
    # ------------------------------------------------------------------
    async def send_page(self, from_nick: str, text: str):
        """以某个系统昵称(如 matchbot)向本连接发送 PAGE 消息。"""
        await self.send_line(
            f":{user_prefix(from_nick)} PAGE {self.session.nick} :{text}"
        )

    async def send_startg(self, game_id: str, timestamp: str):
        """下发开局指令(快速匹配/自定义房间共用)。"""
        url = self._gserv_url()
        await self.send_line(
            f":{self.server} STARTG {self.session.nick} :{url} :{game_id} {timestamp}"
        )

    def _gserv_url(self) -> str:
        """游戏中继服务地址:优先取配置,否则按当前请求 Host 推导。"""
        if self.cfg["GSERV_URL"]:
            return self.cfg["GSERV_URL"]
        headers = dict(self.scope.get("headers") or [])
        host = headers.get(b"host", b"localhost:8000").decode("latin-1")
        proto = headers.get(b"x-forwarded-proto", b"").decode("latin-1")
        scheme = "wss" if proto == "https" or self.scope.get("scheme") == "wss" else "ws"
        return f"{scheme}://{host}/gserv"

    # ------------------------------------------------------------------
    # 消息分发
    # ------------------------------------------------------------------
    async def handle_line(self, line: str):
        parts = line.split(" ")
        command = parts[0].lower()
        handler = {
            "cvers": self.cmd_cvers,
            "pass": self.cmd_pass,
            "nick": self.cmd_nick,
            "user": self.cmd_user,
            "apgar": self.cmd_pass,
            "setlocale": self.cmd_setlocale,
            "getlocale": self.cmd_getlocale,
            "ping": self.reply_ping,
            "pong": self.cmd_pong,
            "list": self.cmd_list,
            "join": self.cmd_join,
            "joingame": self.cmd_joingame,
            "part": self.cmd_part,
            "kick": self.cmd_kick,
            "privmsg": self.cmd_privmsg,
            "page": self.cmd_page,
            "topic": self.cmd_topic,
            "mode": self.cmd_mode,
            "names": self.cmd_names,
            "gameopt": self.cmd_gameopt,
            "startg": self.cmd_startg,
            "gping": self.cmd_gping,
            "quit": self.cmd_quit,
        }.get(command)
        if handler is None:
            nick = self.session.nick or "-"
            await self.send_code(
                codes.ERR_UNKNOWNCOMMAND, f"{nick} {parts[0]} :Unknown command"
            )
            return
        await handler(line, parts)

    # ------------------------------------------------------------------
    # 基础命令
    # ------------------------------------------------------------------
    async def cmd_cvers(self, line, parts):
        """cvers <客户端版本> <SKU>"""
        if len(parts) < 3:
            await self.send_code(codes.ERR_BAD_PARAMS, "- :Need more params")
            return
        version, sku = parts[1], parts[2]
        self.session.client_version = version
        try:
            self.session.sku = int(sku)
        except ValueError:
            self.session.sku = 0
        min_version = self.cfg["MIN_CLIENT_VERSION"]
        if self.session.sku != self.cfg["CLIENT_SKU"] or (
            min_version and not self._version_ok(version, min_version)
        ):
            await self.send_code(
                codes.RPL_CVERS_OUTDATED, "u :Please update your client"
            )
            return
        await self.send_code(codes.RPL_CVERS_OK, "u :ok")

    @staticmethod
    def _version_ok(client: str, minimum: str) -> bool:
        """与前端 WolService.matchVersions 相同的版本比较规则。"""
        try:
            c_major, c_minor, c_patch = client.split(".")
            m_major, m_minor, m_patch = minimum.split(".")
            return (
                c_major == m_major
                and c_minor == m_minor
                and int(c_patch.split("-")[0]) >= int(m_patch)
            )
        except ValueError:
            return False

    async def cmd_pass(self, line, parts):
        self._pending_pass = parts[1] if len(parts) > 1 else ""

    async def cmd_nick(self, line, parts):
        self._pending_nick = parts[1] if len(parts) > 1 else ""

    async def cmd_user(self, line, parts):
        """user 行触发实际登录(凭据来自之前的 pass/nick 行)。"""
        nick = self._pending_nick
        try:
            password = base64.b64decode(self._pending_pass).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            password = ""

        # 登录排队(服务器过载保护)
        position = STATE.online_count() + 1 - self.cfg["MAX_ONLINE_USERS"]
        while position > 0 and not self.closed:
            await self.send_code(
                codes.RPL_LOGIN_QUEUE,
                f"{nick} {position} {position * LOGIN_QUEUE_POLL}",
            )
            await asyncio.sleep(LOGIN_QUEUE_POLL)
            position = STATE.online_count() + 1 - self.cfg["MAX_ONLINE_USERS"]
        if self.closed:
            return

        auth = await database_sync_to_async(authenticate)(nick, password)
        if not auth.ok:
            if auth.error == "Banned":
                await self.send_code(
                    codes.ERR_YOUREBANNEDCREEP,
                    f"{nick} {nick} :You are banned from this server",
                )
            else:
                await self.send_code(
                    codes.RPL_BAD_LOGIN, f"{nick} {nick} :Bad username or password"
                )
            return

        account = auth.account
        async with STATE.lock:
            # 同名顶号:断开旧连接,便于掉线后立即重连
            old = STATE.users.get(account.name_lower)
            if old is not None and old.consumer is not self:
                await old.consumer.close()
                STATE.users.pop(account.name_lower, None)
            self.session.nick = account.name
            self.session.locale = account.locale
            STATE.users[account.name_lower] = self.session

        await database_sync_to_async(account.touch_login)()

        # MOTD(375 起始行也会被前端展示)
        await self.send_code(codes.RPL_MOTDSTART, f"{account.name} :- ra2web")
        for motd_line in self.cfg["MOTD"].splitlines() or [""]:
            await self.send_code(codes.RPL_MOTD, f"{account.name} :- {motd_line}")
        await self.send_code(codes.RPL_ENDOFMOTD, f"{account.name} :- end")

        # 启动保活探测
        self.start_keepalive()

    async def cmd_setlocale(self, line, parts):
        """setlocale <地区代码>"""
        nick = self.session.nick or "-"
        try:
            locale = int(parts[1]) if len(parts) > 1 else 0
        except ValueError:
            locale = 0
        self.session.locale = locale
        if self.session.is_logged_in():
            await database_sync_to_async(self._save_locale)(locale)
        await self.send_code(codes.RPL_SET_LOCALE, f"{nick} {nick}`{locale}`")

    def _save_locale(self, locale: int):
        from apps.accounts.models import Account

        Account.objects.filter(name_lower=self.session.nick_lower).update(locale=locale)

    async def cmd_getlocale(self, line, parts):
        """getlocale <昵称>;回复 params[2] 形如 nick`<locale>`"""
        nick = self.session.nick or "-"
        target = parts[1] if len(parts) > 1 else nick
        target_session = STATE.users.get(target.lower())
        locale = target_session.locale if target_session else 0
        await self.send_code(
            codes.RPL_GET_LOCALE, f"{nick} {target} {target}`{locale}`"
        )

    async def cmd_pong(self, line, parts):
        """客户端对服务器保活 ping 的应答。"""
        await self.handle_pong(parts[1] if len(parts) > 1 else "")

    # ------------------------------------------------------------------
    # 频道命令
    # ------------------------------------------------------------------
    def _require_login(self):
        return self.session.is_logged_in()

    async def cmd_list(self, line, parts):
        """list <类型> <类型>:列出游戏房间(326)与聊天频道(327)。"""
        nick = self.session.nick or "-"
        try:
            wanted_type = int(parts[1]) if len(parts) > 1 else 0
        except ValueError:
            wanted_type = 0
        await self.send_code(codes.RPL_LISTSTART, f"{nick} :Channels")
        async with STATE.lock:
            channels = list(STATE.channels.values())
        for channel in channels:
            escaped = escape_channel_name(channel.name)
            if channel.is_game and channel.channel_type == wanted_type:
                owner = channel.members.get(channel.owner.lower())
                host_ping = owner.ping if owner else 0
                await self.send_code(
                    codes.RPL_GAME_CHANNEL,
                    f"{nick} {escaped} {channel.member_count()} {channel.max_players} "
                    f"{channel.channel_type} {channel.tournament} 0 {host_ping} "
                    f"{channel.flags}::{channel.topic} 0",
                )
            elif not channel.is_game and wanted_type == 0:
                await self.send_code(
                    codes.RPL_CHANNEL,
                    f"{nick} {escaped} {channel.member_count()} 0",
                )
        await self.send_code(codes.RPL_LISTEND, f"{nick} :End of list")

    async def _broadcast(self, channel: WolChannel, line: str, exclude=None):
        for member in list(channel.members.values()):
            if exclude is not None and member is exclude:
                continue
            await member.consumer.send_line(line)

    async def _send_names(self, channel: WolChannel):
        """353/366:发送频道成员列表(OP 前缀 @,附带延迟)。"""
        nick = self.session.nick
        escaped = escape_channel_name(channel.name)
        entries = []
        for member in channel.members.values():
            op = "@" if channel.name.lower() in member.operator_in else ""
            entries.append(f"{op}{member.nick},0,{member.ping}")
        await self.send_code(
            codes.RPL_NAMREPLY, f"{nick} = {escaped} :{' '.join(entries)}"
        )
        await self.send_code(codes.RPL_ENDOFNAMES, f"{nick} {escaped} :End of names")

    async def cmd_join(self, line, parts):
        """join <转义频道名> [密码]:加入聊天频道(大厅/快速匹配)。"""
        if not self._require_login():
            return
        nick = self.session.nick
        if len(parts) < 2:
            await self.send_code(codes.ERR_BAD_PARAMS, f"{nick} :Need more params")
            return
        escaped = parts[1]
        key = parts[2] if len(parts) > 2 else ""
        name = unescape_channel_name(escaped)

        async with STATE.lock:
            channel = STATE.find_channel(name)
            if channel is None:
                # 大厅类频道按需创建(统一使用全局频道密码)
                if name.startswith("#Lob"):
                    channel = STATE.get_or_create_chat_channel(
                        name, key=self.cfg["GLOBAL_CHANNEL_PASS"]
                    )
                else:
                    await self.send_code(
                        codes.ERR_NOSUCHCHANNEL,
                        f"{nick} {nick} {escaped} :No such channel",
                    )
                    return
            if channel.key and key != channel.key:
                await self.send_code(
                    codes.ERR_BADCHANNELKEY, f"{nick} {escaped} :Wrong password"
                )
                return
            if channel.is_full():
                await self.send_code(
                    codes.ERR_CHANNELISFULL, f"{nick} {escaped} :Channel is full"
                )
                return
            channel.members[self.session.nick_lower] = self.session
            self.session.channels.add(name)
            join_line = (
                f":{user_prefix(nick)} JOIN :0,{self.session.ping},0 {escaped}"
            )
            await self._broadcast(channel, join_line)
        await self._send_names(channel)

    async def cmd_joingame(self, line, parts):
        """joingame:创建(7+ 参数)或加入(1-2 参数)游戏房间。"""
        if not self._require_login():
            return
        nick = self.session.nick
        if len(parts) < 3:
            await self.send_code(codes.ERR_BAD_PARAMS, f"{nick} :Need more params")
            return
        escaped = parts[1]
        name = unescape_channel_name(escaped)
        args = parts[2:]

        # 前端创建房间发送 7 个数字参数(+可选密码),加入只有 1-2 个参数
        if len(args) >= 7:
            await self._joingame_create(name, escaped, args)
        else:
            await self._joingame_join(name, escaped, args)

    async def _joingame_create(self, name, escaped, args):
        """创建游戏房间:joingame <chan> <min> <max> <type> <p4> <p5> <trny> <p7> [pass]"""
        nick = self.session.nick
        key = args[7] if len(args) > 7 else ""
        try:
            min_players, max_players, chan_type = int(args[0]), int(args[1]), int(args[2])
            tournament = int(args[5])
        except ValueError:
            await self.send_code(codes.ERR_BAD_PARAMS, f"{nick} :Bad params")
            return
        async with STATE.lock:
            if STATE.find_channel(name) is not None:
                # 同名房间已存在:旧房主已断线则回收重建
                old = STATE.channels[name]
                if old.members:
                    await self.send_code(
                        codes.ERR_CHANNELISFULL, f"{nick} {escaped} :Channel exists"
                    )
                    return
                del STATE.channels[name]
            channel = WolChannel(
                name=name,
                is_game=True,
                key=key,
                owner=nick,
                min_players=min_players,
                max_players=max_players,
                channel_type=chan_type,
                tournament=tournament,
            )
            STATE.channels[name] = channel
            channel.members[self.session.nick_lower] = self.session
            self.session.channels.add(name)
            self.session.operator_in.add(name.lower())
            await self.send_line(
                f":{user_prefix(nick)} JOINGAME {min_players} {max_players} "
                f"{chan_type} {tournament} 0 {self.session.ping} 0 :{escaped}"
            )

    async def _joingame_join(self, name, escaped, args):
        """加入游戏房间:joingame <chan> <observer> [pass]"""
        nick = self.session.nick
        key = args[1] if len(args) > 1 else ""
        async with STATE.lock:
            channel = STATE.find_channel(name)
            if channel is None or not channel.is_game:
                await self.send_code(
                    codes.ERR_GAMEHASCLOSED, f"{nick} {escaped} :Game has closed"
                )
                return
            if channel.key and key != channel.key:
                await self.send_code(
                    codes.ERR_BADCHANNELKEY, f"{nick} {escaped} :Wrong password"
                )
                return
            if channel.is_full():
                await self.send_code(
                    codes.ERR_CHANNELISFULL, f"{nick} {escaped} :Channel is full"
                )
                return
            channel.members[self.session.nick_lower] = self.session
            self.session.channels.add(name)
            join_line = (
                f":{user_prefix(nick)} JOINGAME {channel.min_players} "
                f"{channel.max_players} {channel.channel_type} {channel.tournament} "
                f"0 {self.session.ping} 0 :{escaped}"
            )
            await self._broadcast(channel, join_line)

    async def _channel_part(self, channel: WolChannel, session: WolSession, broadcast_self=True):
        """从频道移除成员并广播 PART(调用方需持有 STATE.lock)。"""
        escaped = escape_channel_name(channel.name)
        part_line = f":{user_prefix(session.nick)} PART {escaped}"
        exclude = None if broadcast_self else session
        await self._broadcast(channel, part_line, exclude=exclude)
        channel.members.pop(session.nick_lower, None)
        session.channels.discard(channel.name)
        session.operator_in.discard(channel.name.lower())
        STATE.drop_channel_if_empty(channel.name)

    async def cmd_part(self, line, parts):
        """part <转义频道名>"""
        if not self._require_login() or len(parts) < 2:
            return
        name = unescape_channel_name(parts[1])
        # 离开快速匹配频道等同于退出匹配队列
        if name.startswith("#Lob"):
            MATCHMAKER.remove_session(self.session)
        async with STATE.lock:
            channel = STATE.find_channel(name)
            if channel and self.session.nick_lower in channel.members:
                await self._channel_part(channel, self.session)

    async def cmd_kick(self, line, parts):
        """kick <频道> <目标1,目标2> :<原因>(仅房主可用)"""
        if not self._require_login() or len(parts) < 3:
            return
        nick = self.session.nick
        name = unescape_channel_name(parts[1])
        targets = parts[2].split(",")
        async with STATE.lock:
            channel = STATE.find_channel(name)
            if channel is None:
                return
            if name.lower() not in self.session.operator_in:
                await self.send_code(
                    codes.ERR_CHANOPRIVSNEEDED,
                    f"{nick} {parts[1]} :You're not channel operator",
                )
                return
            escaped = escape_channel_name(name)
            for target in targets:
                member = channel.members.get(target.lower())
                if member is None:
                    continue
                kick_line = f":{user_prefix(nick)} KICK {escaped} {member.nick}"
                await self._broadcast(channel, kick_line)
                channel.members.pop(member.nick_lower, None)
                member.channels.discard(name)
                member.operator_in.discard(name.lower())
            STATE.drop_channel_if_empty(name)

    async def cmd_privmsg(self, line, parts):
        """privmsg <目标1,目标2> :<内容>;目标可为频道或昵称。"""
        if not self._require_login():
            return
        sep = line.find(" :")
        if sep < 0 or len(parts) < 2:
            return
        text = line[sep + 2 :]
        targets = parts[1].split(",")
        await self._deliver_message("PRIVMSG", targets, text)

    async def cmd_page(self, line, parts):
        """page <目标> :<内容>(跨频道私聊)。"""
        if not self._require_login():
            return
        sep = line.find(" :")
        if sep < 0 or len(parts) < 2:
            return
        text = line[sep + 2 :]
        targets = parts[1].split(",")
        await self._deliver_message("PAGE", targets, text)

    async def _deliver_message(self, verb: str, targets, text: str):
        nick = self.session.nick
        for target in targets:
            if target.startswith("#"):
                name = unescape_channel_name(target)
                async with STATE.lock:
                    channel = STATE.find_channel(name)
                    if channel is None or nick.lower() not in channel.members:
                        continue
                    msg = f":{user_prefix(nick)} {verb} {target} :{text}"
                    await self._broadcast(channel, msg, exclude=self.session)
            elif target.lower() == self.cfg["MATCH_BOT_NAME"].lower():
                await self._handle_matchbot_message(text)
            else:
                member = STATE.users.get(target.lower())
                if member is None:
                    await self.send_code(
                        codes.ERR_NOSUCHNICK, f"- {nick} {target} :No such nick"
                    )
                    continue
                msg = f":{user_prefix(nick)} {verb} {member.nick} :{text}"
                await member.consumer.send_line(msg)

    async def cmd_topic(self, line, parts):
        """topic <频道> :<内容>:保存游戏房间信息串(供 list 使用)。"""
        if not self._require_login() or len(parts) < 2:
            return
        sep = line.find(" :")
        if sep < 0:
            return
        name = unescape_channel_name(parts[1])
        text = line[sep + 2 :]
        async with STATE.lock:
            channel = STATE.find_channel(name)
            if channel and self.session.nick_lower in channel.members:
                channel.topic = text

    async def cmd_mode(self, line, parts):
        """mode <频道> +l <人数>:设置房间人数上限并广播。"""
        if not self._require_login() or len(parts) < 4 or parts[2] != "+l":
            return
        nick = self.session.nick
        name = unescape_channel_name(parts[1])
        try:
            limit = int(parts[3])
        except ValueError:
            return
        async with STATE.lock:
            channel = STATE.find_channel(name)
            if channel is None or name.lower() not in self.session.operator_in:
                return
            channel.max_users = limit
            mode_line = f":{user_prefix(nick)} MODE {parts[1]} +l {limit}"
            await self._broadcast(channel, mode_line)

    async def cmd_names(self, line, parts):
        """names <频道>:返回成员列表。"""
        if not self._require_login() or len(parts) < 2:
            return
        nick = self.session.nick
        name = unescape_channel_name(parts[1])
        async with STATE.lock:
            channel = STATE.find_channel(name)
        if channel is None:
            await self.send_code(
                codes.ERR_NOSUCHCHANNEL, f"{nick} {nick} {parts[1]} :No such channel"
            )
            return
        await self._send_names(channel)

    async def cmd_gameopt(self, line, parts):
        """gameopt <频道|昵称> :<载荷>:游戏选项中继。"""
        if not self._require_login() or len(parts) < 2:
            return
        sep = line.find(" :")
        if sep < 0:
            return
        text = line[sep + 2 :]
        nick = self.session.nick
        target = parts[1]
        if target.startswith("#"):
            name = unescape_channel_name(target)
            async with STATE.lock:
                channel = STATE.find_channel(name)
                if channel is None or nick.lower() not in channel.members:
                    return
                msg = f":{user_prefix(nick)} GAMEOPT {target} :{text}"
                await self._broadcast(channel, msg, exclude=self.session)
        else:
            member = STATE.users.get(target.lower())
            if member is not None:
                await member.consumer.send_line(
                    f":{user_prefix(nick)} GAMEOPT {member.nick} :{text}"
                )

    async def cmd_startg(self, line, parts):
        """
        startg <频道> <玩家1,玩家2,...>:房主开局。

        服务器分配对局 id 与时间戳,向房间所有成员下发 STARTG,
        随后房主在 gserv 上 create,其余玩家 join。
        """
        if not self._require_login() or len(parts) < 3:
            return
        name = unescape_channel_name(parts[1])
        async with STATE.lock:
            channel = STATE.find_channel(name)
            if channel is None or name.lower() not in self.session.operator_in:
                return
            game_id = STATE.next_game_id()
            timestamp = str(int(time.time()))
            for member in list(channel.members.values()):
                await member.consumer.send_startg(game_id, timestamp)

    async def cmd_gping(self, line, parts):
        """gping <频道> <id> <毫秒>:候选游戏服延迟上报(单服部署仅记录)。"""
        return

    async def cmd_quit(self, line, parts):
        await self.send_code(codes.RPL_QUIT, f"{self.session.nick or '-'} :Bye")
        await self.close()

    # ------------------------------------------------------------------
    # 快速匹配
    # ------------------------------------------------------------------
    async def _handle_matchbot_message(self, text: str):
        """处理发往 matchbot 的 privmsg(Match/Stats)。"""
        from apps.core import qm_codes

        bot = self.cfg["MATCH_BOT_NAME"]
        if text.startswith(qm_codes.REQ_MATCH):
            tags = {}
            payload = text[len(qm_codes.REQ_MATCH) :].strip()
            for item in payload.split(","):
                if "=" in item:
                    tag_key, tag_value = item.strip().split("=", 1)
                    tags[tag_key] = tag_value

            def to_int(value, default=-1):
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return default

            mmr = await database_sync_to_async(self._lookup_mmr)()
            entry = QueueEntry(
                session=self.session,
                country_id=to_int(tags.get(qm_codes.TAG_COUNTRY)),
                color_id=to_int(tags.get(qm_codes.TAG_COLOR)),
                ranked=bool(to_int(tags.get(qm_codes.TAG_RANKED), 0)),
                version=tags.get(qm_codes.TAG_VERSION, ""),
                mod_hash=tags.get(qm_codes.TAG_MODHASH, ""),
                mmr=mmr,
            )
            error = await MATCHMAKER.enqueue(entry)
            await self.send_page(bot, error if error else qm_codes.RPL_WORKING)
        elif text == qm_codes.REQ_STATS:
            count, avg_wait = MATCHMAKER.stats()
            await self.send_page(bot, f"{qm_codes.RPL_STATS} {count},{avg_wait}")

    def _lookup_mmr(self) -> int:
        """读取玩家当前赛季 MMR(用于就近匹配)。"""
        from apps.ladder.models import LadderEntry
        from apps.ladder.services import season_number

        entry = (
            LadderEntry.objects.filter(
                account__name_lower=self.session.nick_lower,
                ladder_type="1v1",
                season=season_number(),
            )
            .only("mmr")
            .first()
        )
        return entry.mmr if entry else 1000
