"""
安全与健壮性回归测试。

覆盖本次安全审查修复的问题:
- 畸形/恶意战绩包(截断、PLRS 放大、超大请求体);
- WOL 登录防爆破与 topic 协议注入防护;
- gserv 回合号窗口限制(内存泄漏防护)与登录限次。
"""
import base64
import struct

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import TestCase, TransactionTestCase

from apps.accounts.models import Account
from apps.gameres.packet import (
    GameResPacket,
    GameResPacketError,
    MAX_PACKET_PLAYERS,
    TYPE_INT,
    TYPE_STRING,
)
from apps.gserv.consumers import GservConsumer
from apps.gserv.state import STATE as GSERV_STATE
from apps.wol.consumers import WolConsumer, MAX_LOGIN_ATTEMPTS
from apps.wol.state import STATE as WOL_STATE

from .test_gserv_ws import GAME_OPTS, MOD_HASH, VERSION, gserv_login, recv_line
from .test_wol_ws import wol_connect_and_login

PASSWORD = "password123"
SKU = 16640


def make_account(name: str) -> Account:
    account = Account(name=name, name_lower=name.lower())
    account.set_password(PASSWORD)
    account.save()
    return account


class GameResPacketSecurityTests(TestCase):
    def test_truncated_packet_raises_packet_error(self):
        """截断数据必须抛 GameResPacketError 而非底层 struct 异常。"""
        # 声明长度大于实际数据
        bad = struct.pack(">H", 100) + b"\x00\x00" + b"\x01"
        with self.assertRaises(GameResPacketError):
            GameResPacket.from_binary(bad)

    def test_garbage_field_data_raises_packet_error(self):
        """字段区越界读取必须被兜底转换为 GameResPacketError。"""
        body = b"PLRS" + struct.pack(">H", TYPE_INT) + struct.pack(">H", 4)
        # 缺少 4 字节值,造成 struct 越界
        raw = struct.pack(">H", len(body) + 4 + 4) + b"\x00\x00" + body
        with self.assertRaises(GameResPacketError):
            GameResPacket.from_binary(raw + b"\x00")

    def test_plrs_bomb_is_capped(self):
        """PLRS 写入超大值时玩家解析必须截断,防循环放大。"""
        packet = GameResPacket()
        packet.fields["PLRS"] = (TYPE_INT, 4_000_000_000)
        packet.fields["NAM0"] = (TYPE_STRING, "Alice")
        players = packet.players()
        self.assertLessEqual(len(players), MAX_PACKET_PLAYERS)

    def test_oversized_body_rejected(self):
        """超大请求体直接 400,不进入解码流程。"""
        make_account("Alice")
        import json

        auth = base64.b64encode(
            json.dumps({"nick": "Alice", "pass": PASSWORD}).encode()
        ).decode()
        response = self.client.post(
            f"/wgameres/{SKU}",
            data=b"A" * (300 * 1024),
            content_type="text/plain",
            headers={"authorization": auth},
        )
        self.assertEqual(response.status_code, 400)

    def test_invalid_base64_rejected(self):
        make_account("Alice")
        import json

        auth = base64.b64encode(
            json.dumps({"nick": "Alice", "pass": PASSWORD}).encode()
        ).decode()
        response = self.client.post(
            f"/wgameres/{SKU}",
            data=b"!!!not-base64!!!",
            content_type="text/plain",
            headers={"authorization": auth},
        )
        self.assertEqual(response.status_code, 400)


class WolSecurityTests(TransactionTestCase):
    def tearDown(self):
        WOL_STATE.users.clear()
        WOL_STATE.channels.clear()

    async def test_login_bruteforce_closes_connection(self):
        """连续错误密码达到上限后服务器必须断开连接。"""
        await database_sync_to_async(make_account)("Alice")
        comm = WebsocketCommunicator(WolConsumer.as_asgi(), "/wol")
        await comm.connect()
        await comm.send_to(text_data="cvers 0.65.1 16640\r\n")
        await recv_line(comm)
        wrong = base64.b64encode(b"wrong-password").decode()
        for i in range(MAX_LOGIN_ATTEMPTS):
            await comm.send_to(
                text_data=f"pass {wrong}\r\nnick Alice\r\nuser U H irc :R\r\n"
            )
            reply = await recv_line(comm)
            self.assertEqual(int(reply.split(" ")[1]), 378)
        # 第 MAX_LOGIN_ATTEMPTS 次失败后连接应被关闭
        output = await comm.receive_output(timeout=3)
        self.assertEqual(output["type"], "websocket.close")
        await comm.disconnect()

    async def test_topic_injection_sanitized(self):
        """topic 中的空格/控制字符必须被剔除,防 326 列表协议注入。"""
        comm, _ = await wol_connect_and_login("Alice")
        chan = "#Alice's_game"
        await comm.send_to(text_data=f"joingame {chan} 1 9 45 0 0 0 0\r\n")
        await recv_line(comm)
        # 恶意 topic:带空格与伪造参数
        await comm.send_to(
            text_data=f"topic {chan} :evil topic 999 :fake-injection\r\n"
        )
        # 用带应答的命令确认 topic 已处理
        await comm.send_to(text_data="ping :1\r\n")
        await recv_line(comm)
        channel = WOL_STATE.find_channel("#Alice's game")
        self.assertIsNotNone(channel)
        self.assertNotIn(" ", channel.topic)
        self.assertNotIn("\r", channel.topic)
        await comm.disconnect()


class GservSecurityTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        GSERV_STATE.instances.clear()

    def tearDown(self):
        GSERV_STATE.instances.clear()

    async def test_login_bruteforce_returns_104_and_closes(self):
        """gserv 登录失败达到上限回复 104 并断开。"""
        await database_sync_to_async(make_account)("Alice")
        comm = WebsocketCommunicator(GservConsumer.as_asgi(), "/gserv")
        await comm.connect()
        await comm.send_to(text_data="cvers 0.65.1 2\r\n")
        await recv_line(comm)
        wrong = base64.b64encode(b"wrong-password").decode()
        replies = []
        for _ in range(3):
            await comm.send_to(text_data=f"user Alice {wrong}\r\n")
            replies.append(await recv_line(comm))
        self.assertIn(" 104 ", replies[-1])
        output = await comm.receive_output(timeout=3)
        self.assertEqual(output["type"], "websocket.close")
        await comm.disconnect()

    async def test_turn_flood_outside_window_ignored(self):
        """超出合法窗口的回合号必须被丢弃,防 turn_actions 内存膨胀。"""
        await database_sync_to_async(make_account)("Alice")
        await database_sync_to_async(make_account)("Bob")
        alice = await gserv_login("Alice")
        bob = await gserv_login("Bob")
        await alice.send_to(
            text_data=f"create g900 1718000000 {GAME_OPTS} {VERSION} {MOD_HASH}\r\n"
        )
        await recv_line(alice)
        await bob.send_to(text_data=f"join g900 {VERSION} {MOD_HASH}\r\n")
        await recv_line(bob)
        await alice.send_to(text_data="loaded 100\r\n")
        await bob.send_to(text_data="loaded 100\r\n")
        for comm in (alice, bob):
            await recv_line(comm)  # 700 开赛
            await recv_line(comm)  # 802 速率

        # 提交一个天文数字回合号(应被丢弃)
        await alice.send_to(
            bytes_data=bytes([2, 1]) + struct.pack("<I", 999_999_999) + b"x"
        )
        # 用带应答命令确认已处理
        await alice.send_to(text_data="loadinfo\r\n")
        await recv_line(alice)

        instance = GSERV_STATE.get("g900")
        self.assertIsNotNone(instance)
        self.assertNotIn(999_999_999, instance.turn_actions)
        await alice.disconnect()
        await bob.disconnect()
