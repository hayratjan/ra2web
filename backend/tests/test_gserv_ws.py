"""
gserv WebSocket 服务与前端 GservConnection 的协议匹配性测试。

覆盖:登录、建房/加入、gameopts、载入与开赛(700)、
初始速率(802)、锁步动作聚合广播、地图传输、聊天与嘲讽。
"""
import base64
import struct

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase

from apps.accounts.models import Account
from apps.core import gserv_codes as codes
from apps.gserv.consumers import GservConsumer
from apps.gserv.state import STATE, parse_human_players

PASSWORD = "password123"
VERSION = "0.65.1"
MOD_HASH = "modhash1"

# 与前端 Serializer.serializeOptions 格式一致的测试 gameOpts
GAME_OPTS = (
    "0,0,2,10000,10,1,0,0,1,0,1,0,bWFw,8,1,50000,tn04t2.map,abcdef"
    ":Alice,0,0,-1,-1,0,0,0,Bob,4,1,-1,-1,0,0,0"
    ":@:,"
)


async def recv_line(comm, timeout=3):
    raw = await comm.receive_from(timeout=timeout)
    if isinstance(raw, bytes):
        return raw
    return raw.rstrip("\r\n")


def make_account(name: str):
    account = Account(name=name, name_lower=name.lower())
    account.set_password(PASSWORD)
    account.save()
    return account


async def gserv_login(name: str):
    """模拟前端 connectToServerInstance 前半段:connect + cvers + user。"""
    comm = WebsocketCommunicator(GservConsumer.as_asgi(), "/gserv")
    connected, _ = await comm.connect()
    assert connected
    await comm.send_to(text_data=f"cvers {VERSION} {codes.API_VERSION}\r\n")
    reply = await recv_line(comm)
    assert int(reply.split(" ")[1]) == codes.RPL_CVERS_OK, reply
    b64 = base64.b64encode(PASSWORD.encode()).decode()
    await comm.send_to(text_data=f"user {name} {b64}\r\n")
    reply = await recv_line(comm)
    assert int(reply.split(" ")[1]) == codes.RPL_LOGGED_IN, reply
    return comm


class GservProtocolTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        STATE.instances.clear()

    def tearDown(self):
        STATE.instances.clear()

    def test_parse_human_players(self):
        """gameOpts 真人段解析:下标即锁步玩家 id。"""
        players = parse_human_players(GAME_OPTS)
        self.assertEqual(players, [("Alice", 0), ("Bob", 4)])

    async def _setup_match(self):
        """Alice 建房、Bob 加入,双方载入完成,返回两个连接。"""
        await database_sync_to_async(make_account)("Alice")
        await database_sync_to_async(make_account)("Bob")
        alice = await gserv_login("Alice")
        bob = await gserv_login("Bob")

        await alice.send_to(
            text_data=f"create g100 1718000000 {GAME_OPTS} {VERSION} {MOD_HASH}\r\n"
        )
        reply = await recv_line(alice)
        assert int(reply.split(" ")[1]) == codes.RPL_INSTANCE_CREATED, reply

        await bob.send_to(text_data=f"join g100 {VERSION} {MOD_HASH}\r\n")
        reply = await recv_line(bob)
        assert int(reply.split(" ")[1]) == codes.RPL_INSTANCE_CONNECTED, reply

        # joiner 获取 gameOpts(前端 gameOpts() -> 500)
        await bob.send_to(text_data="gameopts\r\n")
        reply = await recv_line(bob)
        parts = reply.split(" ")
        assert int(parts[1]) == codes.RPL_GAME_OPTS
        opts = " ".join(parts[3:]).lstrip(":")
        assert opts == GAME_OPTS, opts

        # 双方载入完成 -> 700 开赛 + 802 初始速率
        await alice.send_to(text_data="loaded 100\r\n")
        await bob.send_to(text_data="loaded 100\r\n")
        for comm in (alice, bob):
            start = await recv_line(comm)
            assert f" {codes.RPL_GAME_START} " in start, start
            rate = await recv_line(comm)
            assert f" {codes.RPL_NET_RATE} " in rate, rate
            # 前端解析:e[3].slice(1).split(",") -> [rate, turnNo]
            rate_str, turn_str = rate.split(" ")[3].lstrip(":").split(",")
            assert int(rate_str) > 0 and int(turn_str) == 0
        return alice, bob

    async def test_full_game_flow_and_action_relay(self):
        alice, bob = await self._setup_match()

        # 双方提交第 0 回合动作(格式: [02 01 u32turn payload])
        alice_actions = b"\x01\x05\x02\x00AA"
        bob_actions = b"\x01\x06\x01\x00B"
        await alice.send_to(
            bytes_data=bytes([2, 1]) + struct.pack("<I", 0) + alice_actions
        )
        await bob.send_to(
            bytes_data=bytes([2, 1]) + struct.pack("<I", 0) + bob_actions
        )

        # 双方都应收到聚合帧,且能按前端 parseAllPlayerActions 解析
        for comm in (alice, bob):
            frame = await recv_line(comm)
            self.assertIsInstance(frame, bytes)
            self.assertEqual(frame[0], codes.RPL_BIN_PREFIX)
            self.assertEqual(frame[1], codes.RPL_BIN_GAME_ACTIONS)
            (turn,) = struct.unpack_from("<I", frame, 2)
            self.assertEqual(turn, 0)
            count = frame[6]
            self.assertEqual(count, 2)
            pos = 7
            parsed = {}
            for _ in range(count):
                player_id = frame[pos]
                (length,) = struct.unpack_from("<H", frame, pos + 1)
                parsed[player_id] = frame[pos + 3 : pos + 3 + length]
                pos += 3 + length
            self.assertEqual(parsed[0], alice_actions)
            self.assertEqual(parsed[1], bob_actions)

        await alice.disconnect()
        await bob.disconnect()

    async def test_chat_and_taunt_relay(self):
        alice, bob = await self._setup_match()

        # #all 聊天:前端正则 ^:([A-Za-z0-9-_]+) PRIVMSG ([A-Za-z0-9-_#']+) :(.*)
        await alice.send_to(text_data="privmsg #all :gg\r\n")
        msg = await recv_line(bob)
        self.assertEqual(msg, ":Alice PRIVMSG #all :gg")

        # 定向(队内)消息:接收方看到的目标是自己的昵称
        await alice.send_to(text_data="privmsg Bob :team msg\r\n")
        msg = await recv_line(bob)
        self.assertEqual(msg, ":Alice PRIVMSG Bob :team msg")

        # 嘲讽:前端解析 from=e[0], tauntNo=e[3]
        await alice.send_to(text_data="taunt 5\r\n")
        msg = await recv_line(bob)
        self.assertEqual(msg, f":Alice {codes.RPL_TAUNT} Bob :5")

        await alice.disconnect()
        await bob.disconnect()

    async def test_map_transfer(self):
        await database_sync_to_async(make_account)("Alice")
        await database_sync_to_async(make_account)("Bob")
        alice = await gserv_login("Alice")
        bob = await gserv_login("Bob")
        await alice.send_to(
            text_data=f"create g200 1718000001 {GAME_OPTS} {VERSION} {MOD_HASH}\r\n"
        )
        await recv_line(alice)
        await bob.send_to(text_data=f"join g200 {VERSION} {MOD_HASH}\r\n")
        await recv_line(bob)

        map_bytes = b"MAPDATA-0123456789"
        await alice.send_to(bytes_data=bytes([2, 3]) + map_bytes)
        # 用一次带应答的命令确认上传已被处理(同连接内消息按序处理)
        await alice.send_to(text_data="loadinfo\r\n")
        await recv_line(alice)
        await bob.send_to(bytes_data=bytes([2, 4]))
        frame = await recv_line(bob)
        self.assertIsInstance(frame, bytes)
        # 前端 sendBinCommand:e[0]=前缀, e[1]=RPL_BIN_MAP_DATA, data=e[2:]
        self.assertEqual(frame[0], codes.RPL_BIN_PREFIX)
        self.assertEqual(frame[1], codes.RPL_BIN_MAP_DATA)
        self.assertEqual(frame[2:], map_bytes)
        await alice.disconnect()
        await bob.disconnect()

    async def test_join_version_mismatch(self):
        await database_sync_to_async(make_account)("Alice")
        await database_sync_to_async(make_account)("Bob")
        alice = await gserv_login("Alice")
        await alice.send_to(
            text_data=f"create g300 1718000002 {GAME_OPTS} {VERSION} {MOD_HASH}\r\n"
        )
        await recv_line(alice)
        bob = await gserv_login("Bob")
        await bob.send_to(text_data="join g300 9.9.9 otherhash\r\n")
        reply = await recv_line(bob)
        self.assertEqual(
            int(reply.split(" ")[1]), codes.RPL_INSTANCE_VERS_MISMATCH
        )
        await alice.disconnect()
        await bob.disconnect()

    async def test_join_nonexistent_instance(self):
        await database_sync_to_async(make_account)("Bob")
        bob = await gserv_login("Bob")
        await bob.send_to(text_data=f"join missing {VERSION} {MOD_HASH}\r\n")
        reply = await recv_line(bob)
        self.assertEqual(
            int(reply.split(" ")[1]), codes.RPL_INSTANCE_NONEXISTENT
        )
        await bob.disconnect()

    async def test_loadinfo_format(self):
        """loadinfo 必须能被前端 LoadInfoParser(每玩家 5 个值)解析。"""
        alice, bob = await self._setup_match()
        await alice.send_to(text_data="loadinfo\r\n")
        reply = await recv_line(alice)
        parts = reply.split(" ")
        self.assertEqual(int(parts[1]), codes.RPL_LOAD_INFO)
        info = parts[3].lstrip(":")
        values = info.split(",")
        self.assertEqual(len(values) % 5, 0)
        self.assertEqual(values[0], "Alice")
        self.assertEqual(values[5], "Bob")
        await alice.disconnect()
        await bob.disconnect()
