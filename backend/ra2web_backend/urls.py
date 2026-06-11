"""
HTTP 路由配置。

与前端的对应关系(见 dist/ra2web.min.js network/WLadderService、WGameResService):
- GET  /ladder/<sku>/<ladderType>                         -> 赛季列表
- POST /ladder/<sku>/<ladderType>/<season>/listsearch     -> 按名字查询玩家档案
- POST /ladder/<sku>/<ladderType>/<season>/rungsearch     -> 按名次区间分页查询
- POST /wgameres/<sku>                                    -> 战绩二进制包上报
"""
from django.urls import include, path

urlpatterns = [
    path("ladder/", include("apps.ladder.urls")),
    path("wgameres/", include("apps.gameres.urls")),
]
