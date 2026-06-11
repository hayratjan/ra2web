"""
战绩上报接口与前端 WGameResService/GameRes 的匹配性测试。

二进制包按前端 GameRes.toBinary 的格式构造,
验证服务端解析、入库与排位积分结算。
"""
import base64
import json

from django.test import TestCase

from apps.accounts.models import Account
from apps.core.structures import GameResType
from apps.gameres.models import GameReport, RankedMatch
from apps.gameres.packet import (
    GameResPacket,
    TYPE_BOOLEAN,
    TYPE_INT,
    TYPE_STRING,
    TYPE_TIME,
)
from apps.ladder.models import LadderEntry

SKU = 16640


def make_account(name: str, password="password123") -> Account:
    account = Account(name=name, name_lower=name.lower())
    account.set_password(password)
    account.save()
    return account


def build_packet(reporter: str, winner: str, loser: str, tournament=True) -> bytes:
    """构造一份 1v1 战绩包(字段与前端 toFlat 一致)。"""
    packet = GameResPacket()
    fields = {
        "GMID": (TYPE_STRING, "game-1001"),
        "SNAM": (TYPE_STRING, reporter),
        "TRNY": (TYPE_BOOLEAN, tournament),
        "PLRS": (TYPE_INT, 2),
        "FINI": (TYPE_BOOLEAN, True),
        "OOSY": (TYPE_BOOLEAN, False),
        "DURA": (TYPE_INT, 600),
        "SCEN": (TYPE_STRING, "tn04t2.map"),
        "TIME": (TYPE_TIME, 1718000000),
        "VERS": (TYPE_STRING, "0.65.1"),
        "NAM0": (TYPE_STRING, winner),
        "CMP0": (TYPE_INT, int(GameResType.WIN)),
        "CTY0": (TYPE_INT, 0),
        "SID0": (TYPE_INT, 0),
        "TID0": (TYPE_INT, 0),
        "LCN0": (TYPE_BOOLEAN, False),
        "NAM1": (TYPE_STRING, loser),
        "CMP1": (TYPE_INT, int(GameResType.LOSS)),
        "CTY1": (TYPE_INT, 4),
        "SID1": (TYPE_INT, 1),
        "TID1": (TYPE_INT, 1),
        "LCN1": (TYPE_BOOLEAN, False),
    }
    packet.fields.update(fields)
    return packet.to_binary()


def auth_header(nick: str, password="password123") -> str:
    return base64.b64encode(
        json.dumps({"nick": nick, "pass": password}).encode()
    ).decode()


class GameResApiTests(TestCase):
    def setUp(self):
        self.alice = make_account("Alice")
        self.bob = make_account("Bob")

    def post_packet(self, packet: bytes, nick="Alice"):
        return self.client.post(
            f"/wgameres/{SKU}",
            data=base64.b64encode(packet),
            content_type="text/plain",
            headers={"authorization": auth_header(nick)},
        )

    def test_packet_roundtrip(self):
        """编解码往返必须一致(模拟前端 toBinary -> 服务端 fromBinary)。"""
        raw = build_packet("Alice", "Alice", "Bob")
        parsed = GameResPacket.from_binary(raw)
        self.assertEqual(parsed.get_str("GMID"), "game-1001")
        self.assertEqual(parsed.get_int("PLRS"), 2)
        self.assertTrue(parsed.get_bool("TRNY"))
        players = parsed.players()
        self.assertEqual(players[0]["name"], "Alice")
        self.assertEqual(players[1]["completion"], int(GameResType.LOSS))

    def test_submit_and_settle(self):
        """上报成功后入库,并完成排位积分结算。"""
        response = self.post_packet(build_packet("Alice", "Alice", "Bob"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(GameReport.objects.count(), 1)
        match = RankedMatch.objects.get(game_id="game-1001")
        self.assertEqual(match.winner, self.alice)
        self.assertEqual(match.loser, self.bob)

        win_entry = LadderEntry.objects.get(account=self.alice)
        lose_entry = LadderEntry.objects.get(account=self.bob)
        self.assertEqual(win_entry.wins, 1)
        self.assertEqual(lose_entry.losses, 1)
        self.assertGreater(win_entry.points, 0)
        self.assertGreater(win_entry.mmr, lose_entry.mmr)

    def test_duplicate_settle_once(self):
        """双方各上报一次,只结算一场。"""
        self.post_packet(build_packet("Alice", "Alice", "Bob"), nick="Alice")
        self.post_packet(build_packet("Bob", "Alice", "Bob"), nick="Bob")
        self.assertEqual(RankedMatch.objects.count(), 1)
        self.assertEqual(LadderEntry.objects.get(account=self.alice).wins, 1)

    def test_bad_credentials_rejected(self):
        response = self.client.post(
            f"/wgameres/{SKU}",
            data=base64.b64encode(build_packet("Alice", "Alice", "Bob")),
            content_type="text/plain",
            headers={"authorization": auth_header("Alice", "wrongpass")},
        )
        self.assertEqual(response.status_code, 401)

    def test_reporter_mismatch_rejected(self):
        """SNAM 与登录凭据不符时拒绝(防伪造他人战绩)。"""
        response = self.post_packet(build_packet("Bob", "Alice", "Bob"), nick="Alice")
        self.assertEqual(response.status_code, 400)

    def test_casual_game_not_ranked(self):
        """非排位赛(TRNY=0)不结算积分。"""
        response = self.post_packet(
            build_packet("Alice", "Alice", "Bob", tournament=False)
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(RankedMatch.objects.count(), 0)
