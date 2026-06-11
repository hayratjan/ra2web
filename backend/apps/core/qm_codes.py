"""
快速匹配(matchbot)文本协议常量。

与前端 dist/ra2web.min.js 中 network/qmCodes 模块逐项对应。
客户端通过 privmsg 与 matchbot 通信。
"""

REQ_MATCH = "Match"
REQ_STATS = "Stats"

RPL_WORKING = "Working"
RPL_STATS = "Stats"
RPL_BAD_VERS = "Badvers"
RPL_BAD_HASH = "Badhash"
RPL_MODE_UNAVAIL = "Unavailable"
RPL_ALREADY_QUEUED = "AlreadyQueued"
RPL_MATCHED = "Matched"
RPL_REQUEUE = "Requeue"

TAG_COUNTRY = "COU"
TAG_COLOR = "COL"
TAG_RANKED = "RKD"
TAG_VERSION = "VRS"
TAG_MODHASH = "MOD"
