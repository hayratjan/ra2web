"""账号认证服务:WOL/gserv/wgameres 三处登录复用同一逻辑。"""
from dataclasses import dataclass
from typing import Optional

from django.conf import settings

from .models import Account, USERNAME_RE


@dataclass
class AuthResult:
    """认证结果。"""

    ok: bool
    account: Optional[Account] = None
    error: str = ""


def authenticate(name: str, password: str, allow_register: Optional[bool] = None) -> AuthResult:
    """
    校验用户名密码;当账号不存在且允许自动注册时创建账号
    (经典 WOL 行为:首次登录即注册)。
    """
    cfg = settings.RA2WEB
    if allow_register is None:
        allow_register = cfg["AUTO_REGISTER"]

    if not name or not USERNAME_RE.match(name):
        return AuthResult(False, error="Invalid username")
    if not (cfg["MIN_USERNAME_LEN"] <= len(name) <= cfg["MAX_USERNAME_LEN"]):
        return AuthResult(False, error="Invalid username length")
    if not (cfg["MIN_PASS_LEN"] <= len(password) <= cfg["MAX_PASS_LEN"]):
        return AuthResult(False, error="Invalid password length")

    account = Account.objects.filter(name_lower=name.lower()).first()
    if account is None:
        if not allow_register:
            return AuthResult(False, error="No such account")
        account = Account(name=name, name_lower=name.lower())
        account.set_password(password)
        account.save()
        return AuthResult(True, account=account)

    if account.banned:
        return AuthResult(False, account=account, error="Banned")
    if not account.check_password(password):
        return AuthResult(False, error="Bad password")
    return AuthResult(True, account=account)
