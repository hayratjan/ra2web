"""
WOL WebSocket 服务与前端 WolConnection 的协议匹配性测试。

所有断言使用与前端 dist/ra2web.min.js 相同的正则/解析逻辑,
保证服务端输出能被前端原样消费。
"""
import base64
import re
from unittest.mock import patch

from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.test import TransactionTestCase

from apps.wol.consumers import WolConsumer
from apps.wol.state import STATE

PASSWORD = "password123"


async def recv_line(comm, timeout=3):
    raw = await comm.receive_from(timeout=timeout)
    return raw.rstrip("\r\n")


async def wol_connect_and_login(name: str, locale=None):
    """模拟前端完整登录流程:cvers -> [setlocale] -> pass/nick/user -> MOTD。"""
    comm = WebsocketCommunicator(WolConsumer.as_asgi(), "/wol")
    connected, _ = await comm.connect()
    assert connected
    await comm.send_to(text_data="cvers 0.65.1 16640\r\n")
    reply = await recv_line(comm)
    code = int(reply.split(" ")[1])
    assert code == 700, reply

    if locale is not None:
        # 前端 connectAndLogin 在登录前上报界面语言
        await comm.send_to(text_data=f"setlocale {locale}\r\n")
        reply = await recv_line(comm)
        assert int(reply.split(" ")[1]) == 310, reply

    b64pass = base64.b64encode(PASSWORD.encode()).decode()
    await comm.send_to(
        text_data=(
            f"pass {b64pass}\r\nnick {name}\r\n"
            "user UserName HostName irc.westwood.com :RealName\r\n"
        )
    )
    motd = []
    while True:
        reply = await recv_line(comm)
        code = int(reply.split(" ")[1])
        assert code in (375, 372, 376), reply
        motd.append(reply)
        if code == 376:
            break
    return comm, motd


class WolProtocolTests(TransactionTestCase):
    def tearDown(self):
        # 清理进程级单例,避免用例间串扰
        STATE.users.clear()
        STATE.channels.clear()

    async def test_login_and_motd(self):
        comm, motd = await wol_connect_and_login("Alice")
        # 前端把 375/372 行通过 replace(/^.*:- /,"") 提取为消息
        self.assertTrue(all(re.search(r":- ", line) for line in motd[:-1]))
        await comm.disconnect()

    async def test_bad_password_returns_378(self):
        comm, _ = await wol_connect_and_login("Alice")
        await comm.disconnect()
        comm2 = WebsocketCommunicator(WolConsumer.as_asgi(), "/wol")
        await comm2.connect()
        await comm2.send_to(text_data="cvers 0.65.1 16640\r\n")
        await recv_line(comm2)
        wrong = base64.b64encode(b"wrong-password").decode()
        await comm2.send_to(
            text_data=f"pass {wrong}\r\nnick Alice\r\nuser U H irc :R\r\n"
        )
        reply = await recv_line(comm2)
        self.assertEqual(int(reply.split(" ")[1]), 378)
        await comm2.disconnect()

    async def test_locale_flow_and_localized_motd(self):
        """登录前 setlocale 生效:MOTD 按语言下发并持久化到账号。"""
        from channels.db import database_sync_to_async
        from apps.accounts.models import Account
        from apps.core import wol_locales

        # 英语客户端(locale=2)应收到英文 MOTD
        comm, motd = await wol_connect_and_login("Alice", locale=wol_locales.USA)
        motd_text = "\n".join(motd)
        self.assertIn("Welcome to the ra2web self-hosted server!", motd_text)

        # getlocale 返回 nick`<locale>` 结构(前端按反引号拆分)
        await comm.send_to(text_data="getlocale Alice\r\n")
        reply = await recv_line(comm)
        self.assertEqual(int(reply.split(" ")[1]), 309)
        self.assertEqual(reply.split(" ")[4], f"Alice`{wol_locales.USA}`")
        await comm.disconnect()

        # 语言已持久化到账号
        account = await database_sync_to_async(
            Account.objects.get
        )(name_lower="alice")
        self.assertEqual(account.locale, wol_locales.USA)

        # 简体中文客户端(locale=21)收到默认中文 MOTD
        comm2, motd2 = await wol_connect_and_login("Bob", locale=wol_locales.CHINA)
        self.assertIn("欢迎来到 ra2web 自建服务器!", "\n".join(motd2))
        await comm2.disconnect()

    async def test_ping_pong_format(self):
        """前端 IrcConnection.ping 的应答正则必须匹配。"""
        comm, _ = await wol_connect_and_login("Alice")
        await comm.send_to(text_data="ping :12345\r\n")
        reply = await recv_line(comm)
        self.assertRegex(reply, r"^:[^ ]+ PONG [^ :]+ :12345")
        await comm.disconnect()

    async def test_join_lobby_channel(self):
        """join 回显必须匹配前端 joinChannel 的 replyMatch 正则。"""
        comm, _ = await wol_connect_and_login("Alice")
        chan = "#Lob_45_0"
        await comm.send_to(
            text_data=f"join {chan} {settings.RA2WEB['GLOBAL_CHANNEL_PASS']}\r\n"
        )
        reply = await recv_line(comm)
        self.assertRegex(reply, rf"^:Alice![^ ]+ JOIN :[^ ]+ {chan}$")
        # 自动下发 353/366 成员列表
        names = await recv_line(comm)
        self.assertEqual(int(names.split(" ")[1]), 353)
        end = await recv_line(comm)
        self.assertEqual(int(end.split(" ")[1]), 366)
        await comm.disconnect()

    async def test_join_wrong_password(self):
        comm, _ = await wol_connect_and_login("Alice")
        await comm.send_to(text_data="join #Lob_45_0 badpass\r\n")
        reply = await recv_line(comm)
        self.assertEqual(int(reply.split(" ")[1]), 475)
        await comm.disconnect()

    async def test_create_game_join_chat_and_startg(self):
        """覆盖建房 -> 加入 -> 房间聊天 -> 选项中继 -> 开局全流程。"""
        alice, _ = await wol_connect_and_login("Alice")
        bob, _ = await wol_connect_and_login("Bob")
        chan = "#Alice's_game"

        # Alice 建房(前端 createGame 参数: 1 9 45 0 0 <trny> 0)
        await alice.send_to(text_data=f"joingame {chan} 1 9 45 0 0 0 0\r\n")
        reply = await recv_line(alice)
        self.assertRegex(reply, rf"^:Alice![^ ]+ JOINGAME [^:]+:{chan}$")

        # Alice 设置房间 topic(供 list 使用)
        await alice.send_to(text_data=f"topic {chan} :g16640N39,abc,2,0,0,map,ZGVzYw==,\r\n")

        # Bob list 房间(前端 listGames 解析参数位次)
        await bob.send_to(text_data="list 45 45\r\n")
        start = await recv_line(bob)
        self.assertEqual(int(start.split(" ")[1]), 321)
        entry = await recv_line(bob)
        self.assertEqual(int(entry.split(" ")[1]), 326)
        params = entry.split(" ")[3:]
        self.assertEqual(params[0], chan)              # 频道名
        self.assertEqual(params[1], "1")               # 真人数量
        self.assertEqual(params[3], "45")              # 频道类型
        flags, topic = params[7].split("::")
        self.assertEqual(flags, "128")                 # 无密码
        self.assertTrue(topic.startswith("g16640N39"))
        end = await recv_line(bob)
        self.assertEqual(int(end.split(" ")[1]), 323)

        # Bob 加入(前端 joinGame: joingame <chan> <observer> [pass])
        await bob.send_to(text_data=f"joingame {chan} 0\r\n")
        bob_reply = await recv_line(bob)
        self.assertRegex(bob_reply, rf"^:Bob![^ ]+ JOINGAME [^:]+:{chan}$")
        alice_seen = await recv_line(alice)
        self.assertRegex(alice_seen, rf"^:Bob![^ ]+ JOINGAME [^:]+:{chan}$")
        # 前端 handleJoingame 取第 6 个参数作为 ping
        middle = re.match(r"^:Bob![^ ]+ JOINGAME ([^:]+):", alice_seen).group(1)
        self.assertGreaterEqual(len(middle.strip().split(" ")), 6)

        # 房间聊天:Bob -> 频道,Alice 应收到
        await bob.send_to(text_data=f"privmsg {chan} :hello world\r\n")
        msg = await recv_line(alice)
        self.assertRegex(
            msg, rf"^:Bob![^ ]+ PRIVMSG {re.escape(chan)} :hello world$"
        )

        # 游戏选项中继:Alice -> 频道,Bob 应收到 GAMEOPT
        await alice.send_to(text_data=f"gameopt {chan} :A1\r\n")
        opt = await recv_line(bob)
        self.assertRegex(opt, rf"^:Alice![^ ]+ GAMEOPT {re.escape(chan)} :A1$")

        # 开局:双方都应收到 STARTG,格式匹配前端 handleStartGame 正则
        await alice.send_to(text_data=f"startg {chan} Alice,Bob\r\n")
        startg_re = r"^:[^ ]+ STARTG [^:]+:([^ ]+) :([^ ]+) (\d+)"
        for comm in (alice, bob):
            line = await recv_line(comm)
            match = re.match(startg_re, line)
            self.assertIsNotNone(match, line)
        await alice.disconnect()
        await bob.disconnect()

    async def test_whisper_between_users(self):
        alice, _ = await wol_connect_and_login("Alice")
        bob, _ = await wol_connect_and_login("Bob")
        await alice.send_to(text_data="privmsg Bob :secret\r\n")
        msg = await recv_line(bob)
        # 前端 handlePrivMsg:目标为自己昵称时按私聊处理
        self.assertRegex(msg, r"^:Alice![^ ]+ PRIVMSG Bob :secret$")
        await alice.disconnect()
        await bob.disconnect()

    async def test_part_broadcast(self):
        alice, _ = await wol_connect_and_login("Alice")
        bob, _ = await wol_connect_and_login("Bob")
        key = settings.RA2WEB["GLOBAL_CHANNEL_PASS"]
        for comm in (alice, bob):
            await comm.send_to(text_data=f"join #Lob_45_0 {key}\r\n")
            while True:
                line = await recv_line(comm)
                if " 366 " in line:
                    break
        # 消费 Alice 收到的 Bob JOIN 广播
        line = await recv_line(alice)
        self.assertRegex(line, r"^:Bob![^ ]+ JOIN ")
        await bob.send_to(text_data="part #Lob_45_0\r\n")
        part = await recv_line(alice)
        self.assertRegex(part, r"^:Bob![^ ]+ PART #Lob_45_0$")
        await alice.disconnect()
        await bob.disconnect()


class QuickMatchTests(TransactionTestCase):
    """快速匹配(matchbot)流程测试。"""

    def tearDown(self):
        STATE.users.clear()
        STATE.channels.clear()
        from apps.wol.matchmaker import MATCHMAKER

        MATCHMAKER.queues.clear()

    async def test_quick_match_flow(self):
        map_pool = [
            {
                "name": "tn04t2.map",
                "title": "[2] Official Map",
                "sizeBytes": 50000,
                "digest": "abcdef",
                "official": True,
            }
        ]
        patched = dict(settings.RA2WEB)
        patched.update({"QM_MAP_POOL": map_pool, "QM_COUNTDOWN_SECONDS": 0})
        with patch.object(settings, "RA2WEB", patched):
            alice, _ = await wol_connect_and_login("Alice")
            bob, _ = await wol_connect_and_login("Bob")
            key = settings.RA2WEB["GLOBAL_CHANNEL_PASS"]
            # 前端 joinQueue:加入 "#Lob 50 0" 频道后向 matchbot 发 Match
            for comm in (alice, bob):
                await comm.send_to(text_data=f"join #Lob_50_0 {key}\r\n")
                while True:
                    line = await recv_line(comm)
                    if " 366 " in line:
                        break
            # 消费 Alice 收到的 Bob JOIN 广播
            await recv_line(alice)

            request = "Match COU=-1, COL=-1, VRS=0.65.1, MOD=modhash1, RKD=1"
            await alice.send_to(text_data=f"privmsg matchbot :{request}\r\n")
            working = await recv_line(alice)
            self.assertRegex(working, r"^:matchbot![^ ]+ PAGE Alice :Working$")

            await bob.send_to(text_data=f"privmsg matchbot :{request}\r\n")
            # Bob 入队后立即配对成功:
            # Bob 收到 Working+Matched+STARTG,Alice 收到 Matched+STARTG
            for comm, name, expected in ((bob, "Bob", 3), (alice, "Alice", 2)):
                replies = [await recv_line(comm) for _ in range(expected)]
                joined = "\n".join(replies)
                if name == "Bob":
                    self.assertIn("PAGE Bob :Working", joined)
                self.assertIn("Matched", joined)
                self.assertRegex(joined, r"STARTG [^:]+:[^ ]+ :\d+ \d+")
            # gserv 侧应已预创建对局实例
            from apps.gserv.state import STATE as GSERV_STATE

            self.assertEqual(len(GSERV_STATE.instances), 1)
            instance = next(iter(GSERV_STATE.instances.values()))
            names = [n for n, _c in instance.expected_players]
            self.assertCountEqual(names, ["Alice", "Bob"])
            GSERV_STATE.instances.clear()
            await alice.disconnect()
            await bob.disconnect()
