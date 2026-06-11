"""天梯业务逻辑:赛季计算、排名查询、Elo 结算。"""
import datetime
from typing import Iterable, Optional

from django.conf import settings
from django.db import transaction
from django.db.models import F, Window
from django.db.models.functions import RowNumber

from apps.accounts.models import Account
from apps.core.structures import (
    CURRENT_SEASON,
    PREV_SEASON,
    LadderProfile,
    LadderType,
    compute_rank_type,
)

from .models import LadderEntry

# 赛季纪元:从该日期起按 SEASON_DAYS 切分赛季
SEASON_EPOCH = datetime.date(2024, 1, 1)

# Elo 参数
ELO_K = 32
ELO_BASE_MMR = 1000
# 对外积分:胜 +正比于爆冷程度,负 -较少,保底不为负
POINTS_WIN_BASE = 30
POINTS_LOSS_BASE = 15


def season_number(when: Optional[datetime.datetime] = None) -> int:
    """计算给定时间所属的赛季编号(从 1 开始)。"""
    days = settings.RA2WEB["SEASON_DAYS"]
    date = (when or datetime.datetime.now(datetime.timezone.utc)).date()
    return max(1, (date - SEASON_EPOCH).days // days + 1)


def resolve_season(season: str) -> Optional[int]:
    """把前端传入的赛季别名(current/prev/数字)解析为赛季编号。"""
    if season == CURRENT_SEASON:
        return season_number()
    if season == PREV_SEASON:
        return season_number() - 1
    try:
        return int(season)
    except ValueError:
        return None


def available_seasons(ladder_type: str) -> list:
    """
    返回前端赛季下拉框使用的赛季列表。

    与前端约定:元素为 "current"/"prev"/历史赛季编号字符串,
    前端据此渲染 GUI:LadderCurrent / GUI:LadderPrev / GUI:LadderSeason。
    """
    current = season_number()
    existing = set(
        LadderEntry.objects.filter(ladder_type=ladder_type)
        .values_list("season", flat=True)
        .distinct()
    )
    seasons = [CURRENT_SEASON]
    if (current - 1) in existing:
        seasons.append(PREV_SEASON)
    for number in sorted(existing, reverse=True):
        if number not in (current, current - 1):
            seasons.append(str(number))
    return seasons


def _ranked_queryset(ladder_type: str, season: int):
    """按积分降序、胜场降序为该赛季所有条目编排名次。"""
    return (
        LadderEntry.objects.filter(ladder_type=ladder_type, season=season)
        .select_related("account")
        .annotate(
            rank=Window(
                expression=RowNumber(),
                order_by=[F("points").desc(), F("wins").desc(), F("id").asc()],
            )
        )
    )


def _entry_to_profile(entry, total: int, with_mmr: bool = True) -> LadderProfile:
    """把数据库条目转换为前端 LadderProfile 数据结构。"""
    return LadderProfile(
        name=entry.account.name,
        rank=entry.rank,
        rankType=compute_rank_type(entry.rank, total),
        points=entry.points,
        wins=entry.wins,
        losses=entry.losses,
        mmr=entry.mmr if with_mmr else None,
    )


def rung_search(ladder_type: str, season: str, start: int, count: int) -> list:
    """
    名次区间查询(前端 rungSearch)。

    start 为 1 起始名次,count 为数量(前端取每页数+1 来探测下一页)。
    返回 LadderProfile JSON 数组。
    """
    season_no = resolve_season(season)
    if season_no is None or season_no < 1:
        return []
    qs = _ranked_queryset(ladder_type, season_no)
    total = qs.count()
    start = max(1, start)
    rows = list(qs[start - 1 : start - 1 + max(0, count)])
    return [_entry_to_profile(e, total).to_json() for e in rows]


def list_search(ladder_type: str, season: str, players: Iterable[str]) -> list:
    """
    按名字批量查询(前端 listSearch),最多 MAX_LIST_SEARCH_COUNT 个。

    上榜玩家返回名次档案;未上榜但账号存在的玩家返回
    rank=0 且无 rankType 的档案(前端显示 TXT_UNRANKED)。
    """
    names = [str(p) for p in players][:50]
    lowered = [n.lower() for n in names if n]
    if not lowered:
        return []

    season_no = resolve_season(season)
    if season_no is None or season_no < 1:
        return []

    qs = _ranked_queryset(ladder_type, season_no)
    total = qs.count()
    found = {}
    # Window 注解无法直接按名字过滤(过滤会改变名次),先全量再筛选
    for entry in qs:
        lname = entry.account.name_lower
        if lname in lowered:
            found[lname] = _entry_to_profile(entry, total)

    results = []
    for name in names:
        profile = found.get(name.lower())
        if profile is not None:
            results.append(profile.to_json())
            continue
        account = Account.objects.filter(name_lower=name.lower()).first()
        if account is not None:
            results.append(
                LadderProfile(
                    name=account.name, rank=0, points=0, wins=0, losses=0
                ).to_json()
            )
    return results


def _expected_score(mmr_a: int, mmr_b: int) -> float:
    return 1.0 / (1.0 + 10 ** ((mmr_b - mmr_a) / 400.0))


@transaction.atomic
def record_match(
    winner: Account,
    loser: Account,
    when: Optional[datetime.datetime] = None,
    ladder_type: str = LadderType.SOLO_1V1,
    draw: bool = False,
    loser_disconnected: bool = False,
):
    """
    结算一场排位赛:更新双方 Elo、积分、胜负场。

    由 gameres 应用在收到完整对局战绩后调用。
    """
    season = season_number(when)
    entries = []
    for account in (winner, loser):
        entry, _ = LadderEntry.objects.select_for_update().get_or_create(
            account=account,
            ladder_type=ladder_type,
            season=season,
            defaults={"mmr": ELO_BASE_MMR},
        )
        entries.append(entry)
    win_entry, lose_entry = entries

    expected_win = _expected_score(win_entry.mmr, lose_entry.mmr)
    if draw:
        delta = round(ELO_K * (0.5 - expected_win))
        win_entry.mmr += delta
        lose_entry.mmr -= delta
    else:
        delta = round(ELO_K * (1 - expected_win))
        win_entry.mmr += delta
        lose_entry.mmr -= delta
        win_entry.points += POINTS_WIN_BASE + max(0, round(delta / 2))
        lose_entry.points = max(
            0, lose_entry.points - POINTS_LOSS_BASE + min(0, round(delta / 2))
        )
        win_entry.wins += 1
        lose_entry.losses += 1
        if loser_disconnected:
            lose_entry.disconnects += 1

    win_entry.save()
    lose_entry.save()
    return win_entry, lose_entry
