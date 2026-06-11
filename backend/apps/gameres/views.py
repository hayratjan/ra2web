"""
战绩上报 HTTP 接口。

前端 network/WGameResService.sendGameResPacket:
    POST {wgameresUrl}/{sku}
    headers: authorization: Base64(JSON{nick, pass})
    body:    Base64(战绩二进制包)
成功返回 2xx 即可(前端不解析响应体)。
"""
import base64
import json
import logging

from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.accounts.services import authenticate

from . import services
from .packet import GameResPacket, GameResPacketError

logger = logging.getLogger(__name__)

# 战绩包(Base64 后)大小上限:正常包不足 64KB,留足余量
MAX_BODY_BYTES = 256 * 1024


@csrf_exempt
@require_POST
def submit(request, sku: int):
    """接收战绩包,校验凭据后入库并按需结算排位积分。"""
    if sku != settings.RA2WEB["CLIENT_SKU"]:
        return HttpResponseNotFound("Unknown SKU")

    # 大小限制前置,避免恶意大包进入解码流程
    content_length = int(request.headers.get("Content-Length") or 0)
    if content_length > MAX_BODY_BYTES or len(request.body) > MAX_BODY_BYTES:
        return HttpResponseBadRequest("Packet too large")

    auth_header = request.headers.get("Authorization", "")
    try:
        credentials = json.loads(base64.b64decode(auth_header).decode("utf-8"))
        nick, password = credentials["nick"], credentials["pass"]
    except (ValueError, KeyError, TypeError):
        return HttpResponse("Bad credentials", status=401)

    auth = authenticate(nick, password, allow_register=False)
    if not auth.ok:
        return HttpResponse("Bad credentials", status=401)

    try:
        packet_bytes = base64.b64decode(request.body, validate=True)
        packet = GameResPacket.from_binary(packet_bytes)
    except (ValueError, GameResPacketError) as exc:
        logger.warning("战绩包解析失败(%s): %s", nick, exc)
        return HttpResponseBadRequest("Bad packet")

    # 上报者必须是对局玩家之一(SNAM 与凭据一致)
    snam = packet.get_str("SNAM")
    if snam.lower() != auth.account.name_lower:
        return HttpResponseBadRequest("Reporter mismatch")

    try:
        services.store_report(packet, auth.account)
    except ValueError as exc:
        logger.warning("战绩包字段非法(%s): %s", nick, exc)
        return HttpResponseBadRequest("Bad packet")
    return HttpResponse(status=200)
