"""WSGI 入口(仅 HTTP 接口;WebSocket 请使用 asgi.py)。"""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ra2web_backend.settings")

application = get_wsgi_application()
