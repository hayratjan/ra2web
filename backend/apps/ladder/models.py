"""天梯数据模型。"""
from django.db import models

from apps.accounts.models import Account


class LadderEntry(models.Model):
    """
    单个赛季内某玩家在某天梯类型下的战绩与积分。

    points 为对外展示积分,mmr 为内部 Elo 匹配分;
    与前端 LadderProfile 数据结构对应(rank/rankType 实时计算)。
    """

    account = models.ForeignKey(Account, on_delete=models.CASCADE, verbose_name="账号")
    ladder_type = models.CharField("天梯类型", max_length=8, default="1v1")
    season = models.IntegerField("赛季编号")
    points = models.IntegerField("积分", default=0)
    mmr = models.IntegerField("匹配分", default=1000)
    wins = models.IntegerField("胜场", default=0)
    losses = models.IntegerField("负场", default=0)
    disconnects = models.IntegerField("断线场次", default=0)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "天梯条目"
        verbose_name_plural = verbose_name
        unique_together = ("account", "ladder_type", "season")
        indexes = [
            models.Index(fields=["ladder_type", "season", "-points"]),
        ]

    def __str__(self):
        return f"{self.account.name} S{self.season} {self.ladder_type}: {self.points}"
