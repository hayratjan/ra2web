"""玩家账号模型:WOL 登录、gserv 登录、wgameres 上报共用同一套凭据。"""
import re

from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone

# 与前端 WolConnection 的消息正则一致:昵称只允许字母数字与 - _
USERNAME_RE = re.compile(r"^[A-Za-z0-9\-_]+$")


class Account(models.Model):
    """玩家账号(用户名不区分大小写,展示时保留原始大小写)。"""

    name = models.CharField("用户名", max_length=15, unique=True)
    name_lower = models.CharField("用户名小写", max_length=15, unique=True, db_index=True)
    password_hash = models.CharField("密码哈希", max_length=128)
    locale = models.IntegerField("地区代码", default=0)
    banned = models.BooleanField("封禁", default=False)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    last_login_at = models.DateTimeField("最近登录", null=True, blank=True)

    class Meta:
        verbose_name = "玩家账号"
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.name

    def set_password(self, raw: str):
        self.password_hash = make_password(raw)

    def check_password(self, raw: str) -> bool:
        return check_password(raw, self.password_hash)

    def touch_login(self):
        self.last_login_at = timezone.now()
        self.save(update_fields=["last_login_at"])
