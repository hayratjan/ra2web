"""
天梯 HTTP 接口与前端 WLadderService 的匹配性测试。

前端调用方式(dist/ra2web.min.js network/WLadderService):
    GET  {url}/{sku}/{type}
    POST {url}/{sku}/{type}/{season}/listsearch  {"players":[...]}
    POST {url}/{sku}/{type}/{season}/rungsearch  {"start":n,"count":n}
"""
import json

from django.test import TestCase

from apps.accounts.models import Account
from apps.ladder.models import LadderEntry
from apps.ladder.services import season_number

SKU = 16640


def make_account(name: str) -> Account:
    account = Account(name=name, name_lower=name.lower())
    account.set_password("password123")
    account.save()
    return account


class LadderApiTests(TestCase):
    def setUp(self):
        season = season_number()
        for i, (name, points) in enumerate(
            [("Alice", 300), ("Bob", 200), ("Carol", 100)]
        ):
            account = make_account(name)
            LadderEntry.objects.create(
                account=account,
                ladder_type="1v1",
                season=season,
                points=points,
                mmr=1000 + i,
                wins=3 - i,
                losses=i,
            )

    def test_seasons_structure(self):
        """赛季列表必须是字符串数组,且包含 current。"""
        response = self.client.get(f"/ladder/{SKU}/1v1")
        self.assertEqual(response.status_code, 200)
        seasons = response.json()
        self.assertIsInstance(seasons, list)
        self.assertIn("current", seasons)

    def test_unknown_sku_404(self):
        response = self.client.get("/ladder/99999/1v1")
        self.assertEqual(response.status_code, 404)

    def test_rungsearch_profile_structure(self):
        """rungsearch 返回的档案字段必须满足前端 Ladder 组件消费需求。"""
        response = self.client.post(
            f"/ladder/{SKU}/1v1/current/rungsearch",
            data=json.dumps({"start": 1, "count": 21}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        players = response.json()
        self.assertEqual(len(players), 3)
        first = players[0]
        # 前端用到的字段:name/rank/points/wins/losses/mmr/rankType
        for field in ("name", "rank", "points", "wins", "losses", "mmr"):
            self.assertIn(field, first)
        self.assertEqual(first["name"], "Alice")
        self.assertEqual(first["rank"], 1)
        # 第一名为总司令(rankType=9)
        self.assertEqual(first["rankType"], 9)
        # 名次连续
        self.assertEqual([p["rank"] for p in players], [1, 2, 3])

    def test_rungsearch_pagination(self):
        response = self.client.post(
            f"/ladder/{SKU}/1v1/current/rungsearch",
            data=json.dumps({"start": 2, "count": 2}),
            content_type="application/json",
        )
        players = response.json()
        self.assertEqual([p["name"] for p in players], ["Bob", "Carol"])

    def test_listsearch_found_and_unranked(self):
        """listsearch:上榜玩家带名次;存在但未上榜的玩家 rank=0 无 rankType。"""
        make_account("Dave")
        response = self.client.post(
            f"/ladder/{SKU}/1v1/current/listsearch",
            data=json.dumps({"players": ["Bob", "Dave", "missing"]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        players = {p["name"]: p for p in response.json()}
        self.assertEqual(players["Bob"]["rank"], 2)
        self.assertEqual(players["Dave"]["rank"], 0)
        self.assertNotIn("rankType", players["Dave"])
        self.assertNotIn("missing", players)

    def test_prev_season_alias(self):
        response = self.client.post(
            f"/ladder/{SKU}/1v1/prev/rungsearch",
            data=json.dumps({"start": 1, "count": 10}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])


class FrontendHostingTests(TestCase):
    """同端口前端静态托管测试。"""

    def test_index_served_at_root(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        content = b"".join(response.streaming_content)
        self.assertIn(b"ra2web-root", content)

    def test_static_asset_served(self):
        response = self.client.get("/servers.ini")
        self.assertEqual(response.status_code, 200)

    def test_backend_sources_denied(self):
        """后端源码与数据库不允许通过静态托管泄漏。"""
        for path in (
            "/backend/ra2web_backend/settings.py",
            "/backend/db.sqlite3",
            "/.git/config",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 404, path)

    def test_api_routes_take_priority(self):
        """API 路由优先于静态托管。"""
        response = self.client.get(f"/ladder/{SKU}/1v1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
