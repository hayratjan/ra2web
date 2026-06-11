"""HTTP 中间件:为前端跨域请求开放 CORS。"""
from django.http import HttpResponse


def cors_middleware(get_response):
    """前端站点(静态托管)与后端通常不同源,需要放开 CORS。"""

    def middleware(request):
        if request.method == "OPTIONS":
            response = HttpResponse(status=204)
        else:
            response = get_response(request)
        response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        # 前端 wgameres 上报使用自定义 authorization 头(Base64 凭据)
        response["Access-Control-Allow-Headers"] = "authorization, content-type"
        response["Access-Control-Max-Age"] = "86400"
        return response

    return middleware
