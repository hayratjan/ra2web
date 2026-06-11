"""
HTTP 路由配置。

与前端的对应关系(见 dist/ra2web.min.js network/WLadderService、WGameResService):
- GET  /ladder/<sku>/<ladderType>                         -> 赛季列表
- POST /ladder/<sku>/<ladderType>/<season>/listsearch     -> 按名字查询玩家档案
- POST /ladder/<sku>/<ladderType>/<season>/rungsearch     -> 按名次区间分页查询
- POST /wgameres/<sku>                                    -> 战绩二进制包上报
- 其余路径                                                 -> 前端静态站点(可选)

静态托管使得前端与后端可共用同一个端口部署(ws/http 同源,
不存在浏览器混合内容限制)。
"""
import mimetypes
import os
from pathlib import Path

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation
from django.http import FileResponse, Http404
from django.urls import include, path, re_path
from django.utils._os import safe_join

urlpatterns = [
    path("ladder/", include("apps.ladder.urls")),
    path("wgameres/", include("apps.gameres.urls")),
]


# 禁止通过静态托管访问的目录(后端源码、数据库、版本库等敏感内容)
FRONTEND_DENYLIST = ("backend", ".git", ".github")


def frontend(request, path=""):
    """托管前端静态文件;根路径返回 index.html。

    使用 FileResponse 流式输出并声明 Accept-Ranges,
    便于大文件(如 fully-music.exe)断点续传。
  """
    root = settings.FRONTEND_ROOT
    if not root or not os.path.isdir(root):
        raise Http404("Frontend hosting disabled")
    if path in ("", "/"):
        path = "index.html"
    first_segment = path.lstrip("/").split("/", 1)[0].lower()
    if first_segment in FRONTEND_DENYLIST:
        raise Http404("Not served")
    try:
        fullpath = Path(safe_join(root, path))
    except SuspiciousFileOperation:
        raise Http404("Not served") from None
    if not fullpath.is_file():
        raise Http404("Not found")
    content_type, _ = mimetypes.guess_type(str(fullpath))
    response = FileResponse(
        fullpath.open("rb"),
        content_type=content_type or "application/octet-stream",
    )
    response["Accept-Ranges"] = "bytes"
    response["Content-Disposition"] = f'inline; filename="{fullpath.name}"'
    return response


if settings.FRONTEND_ROOT and os.path.isdir(settings.FRONTEND_ROOT):
    urlpatterns.append(re_path(r"^(?P<path>.*)$", frontend, name="frontend"))
