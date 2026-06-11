"""
天梯 HTTP 接口。

与前端 network/WLadderService 的调用一一对应:
- getSeasons : GET  /ladder/<sku>/<ladderType>
- listSearch : POST /ladder/<sku>/<ladderType>/<season>/listsearch
               body: {"players": ["name", ...]}
- rungSearch : POST /ladder/<sku>/<ladderType>/<season>/rungsearch
               body: {"start": 1, "count": 21}
返回值均为 LadderProfile JSON 数组(或赛季字符串数组)。
"""
import json

from django.conf import settings
from django.http import HttpResponseBadRequest, HttpResponseNotFound, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.core.structures import LadderType, MAX_LIST_SEARCH_COUNT

from . import services


def _check_request(sku: int, ladder_type: str):
    """校验 SKU 与天梯类型,返回错误响应或 None。"""
    if sku != settings.RA2WEB["CLIENT_SKU"]:
        return HttpResponseNotFound("Unknown SKU")
    if ladder_type not in LadderType.ALL:
        return HttpResponseNotFound("Unknown ladder type")
    return None


def _parse_body(request):
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return None


@require_GET
def seasons(request, sku: int, ladder_type: str):
    """赛季列表(前端 getSeasons)。"""
    error = _check_request(sku, ladder_type)
    if error:
        return error
    return JsonResponse(services.available_seasons(ladder_type), safe=False)


@csrf_exempt
@require_POST
def list_search(request, sku: int, ladder_type: str, season: str):
    """按名字批量查询玩家档案(前端 listSearch)。"""
    error = _check_request(sku, ladder_type)
    if error:
        return error
    body = _parse_body(request)
    if body is None or not isinstance(body.get("players"), list):
        return HttpResponseBadRequest("Expected JSON body with 'players' array")
    players = body["players"][:MAX_LIST_SEARCH_COUNT]
    return JsonResponse(
        services.list_search(ladder_type, season, players), safe=False
    )


@csrf_exempt
@require_POST
def rung_search(request, sku: int, ladder_type: str, season: str):
    """按名次区间分页查询(前端 rungSearch)。"""
    error = _check_request(sku, ladder_type)
    if error:
        return error
    body = _parse_body(request)
    if body is None:
        return HttpResponseBadRequest("Expected JSON body")
    try:
        start = int(body.get("start", 1))
        count = int(body.get("count", 20))
    except (TypeError, ValueError):
        return HttpResponseBadRequest("'start'/'count' must be integers")
    count = max(0, min(count, 100))
    return JsonResponse(
        services.rung_search(ladder_type, season, start, count), safe=False
    )
