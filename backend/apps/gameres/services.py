"""战绩入库与排位结算逻辑。"""
import logging

from django.db import IntegrityError, transaction

from apps.accounts.models import Account
from apps.core.structures import GameResType, LadderType
from apps.ladder.services import record_match

from .models import GameReport, RankedMatch
from .packet import GameResPacket

logger = logging.getLogger(__name__)

# 视为"胜利"的完成状态
WIN_STATES = {int(GameResType.WIN)}
# 视为"失败"的完成状态(认输/掉线均算负)
LOSS_STATES = {
    int(GameResType.LOSS),
    int(GameResType.RESIGN),
    int(GameResType.DISCONNECT),
}


def store_report(packet: GameResPacket, reporter: Account) -> GameReport:
    """保存一份战绩上报(同一对局同一上报者只保留首份)。"""
    game_id = packet.get_str("GMID")
    report = GameReport(
        game_id=game_id,
        reporter=reporter,
        tournament=packet.get_bool("TRNY"),
        finished=packet.get_bool("FINI"),
        out_of_sync=packet.get_bool("OOSY"),
        duration=packet.get_int("DURA"),
        map_name=packet.get_str("SCEN"),
        player_count=packet.get_int("PLRS"),
        start_time=packet.get_int("TIME"),
        raw_fields=packet.to_plain_dict(),
    )
    try:
        with transaction.atomic():
            report.save()
    except IntegrityError:
        # 重复上报:返回既有记录
        return GameReport.objects.get(game_id=game_id, reporter=reporter)

    maybe_settle_ranked(packet)
    return report


def maybe_settle_ranked(packet: GameResPacket):
    """
    对 1v1 排位赛(TRNY=1, PLRS=2)进行积分结算。

    以首份能够明确分出胜负(或平局)的上报为准,
    通过 RankedMatch 唯一约束保证只结算一次。
    """
    if not packet.get_bool("TRNY") or packet.get_int("PLRS") != 2:
        return
    if packet.get_bool("OOSY"):
        # 失步对局不计分
        return

    players = packet.players()
    if len(players) != 2:
        return

    game_id = packet.get_str("GMID")
    if not game_id or RankedMatch.objects.filter(game_id=game_id).exists():
        return

    a, b = players
    draw = (
        a["completion"] == int(GameResType.DRAW)
        and b["completion"] == int(GameResType.DRAW)
    )
    winner = loser = None
    if not draw:
        if a["completion"] in WIN_STATES and b["completion"] in LOSS_STATES:
            winner, loser = a, b
        elif b["completion"] in WIN_STATES and a["completion"] in LOSS_STATES:
            winner, loser = b, a
        else:
            # 上报方仍在游戏(对方视角)等不完整状态,等待另一份上报
            return

    def find_account(player):
        return Account.objects.filter(name_lower=player["name"].lower()).first()

    if draw:
        acc_a, acc_b = find_account(a), find_account(b)
        if not acc_a or not acc_b:
            return
        try:
            with transaction.atomic():
                RankedMatch.objects.create(
                    game_id=game_id, winner=acc_a, loser=acc_b, draw=True
                )
                record_match(acc_a, acc_b, draw=True, ladder_type=LadderType.SOLO_1V1)
        except IntegrityError:
            pass
        return

    win_acc, lose_acc = find_account(winner), find_account(loser)
    if not win_acc or not lose_acc:
        logger.warning("排位结算失败:找不到账号 %s/%s", winner["name"], loser["name"])
        return
    try:
        with transaction.atomic():
            RankedMatch.objects.create(game_id=game_id, winner=win_acc, loser=lose_acc)
            record_match(
                win_acc,
                lose_acc,
                ladder_type=LadderType.SOLO_1V1,
                loser_disconnected=loser["completion"] == int(GameResType.DISCONNECT)
                or loser["lost_connection"],
            )
    except IntegrityError:
        pass
