"""战绩存储模型。"""
from django.db import models

from apps.accounts.models import Account


class GameReport(models.Model):
    """
    单个客户端上报的一份战绩(同一对局每个玩家各上报一份)。

    raw_fields 保留完整解析结果,关键字段单独建列便于查询。
    """

    game_id = models.CharField("对局 id", max_length=64, db_index=True)
    reporter = models.ForeignKey(
        Account, on_delete=models.CASCADE, verbose_name="上报者"
    )
    tournament = models.BooleanField("排位赛", default=False)
    finished = models.BooleanField("完赛", default=False)
    out_of_sync = models.BooleanField("失步", default=False)
    duration = models.IntegerField("时长(秒)", default=0)
    map_name = models.CharField("地图", max_length=255, blank=True)
    player_count = models.IntegerField("真人数", default=0)
    start_time = models.BigIntegerField("开局时间戳", default=0)
    raw_fields = models.JSONField("完整字段", default=dict)
    created_at = models.DateTimeField("上报时间", auto_now_add=True)

    class Meta:
        verbose_name = "战绩上报"
        verbose_name_plural = verbose_name
        unique_together = ("game_id", "reporter")


class RankedMatch(models.Model):
    """已结算的排位对局,防止同一对局重复结算。"""

    game_id = models.CharField("对局 id", max_length=64, unique=True)
    winner = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name="ranked_wins",
        verbose_name="胜者",
        null=True,
    )
    loser = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name="ranked_losses",
        verbose_name="败者",
        null=True,
    )
    draw = models.BooleanField("平局", default=False)
    settled_at = models.DateTimeField("结算时间", auto_now_add=True)

    class Meta:
        verbose_name = "排位结算"
        verbose_name_plural = verbose_name
