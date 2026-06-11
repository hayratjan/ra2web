"""
WOL 地区代码(与前端 network/WolLocale 枚举一致)。

前端登录前会发送 setlocale <代码>,代码由 localeCodeMap
从界面语言(ISO 代码)映射而来,服务端据此实现多语言下发。
"""

UNKNOWN = 0
OTHER = 1
USA = 2
CANADA = 3
UK = 4
GERMANY = 5
FRANCE = 6
SPAIN = 7
NETHERLANDS = 8
BELGIUM = 9
AUSTRIA = 10
SWITZERLAND = 11
ITALY = 12
DENMARK = 13
SWEDEN = 14
NORWAY = 15
FINLAND = 16
ISRAEL = 17
SOUTH_AFRICA = 18
JAPAN = 19
SOUTH_KOREA = 20
CHINA = 21
SINGAPORE = 22
TAIWAN = 23
MALAYSIA = 24
AUSTRALIA = 25
NEW_ZEALAND = 26
BRAZIL = 27
THAILAND = 28
ARGENTINA = 29
PHILIPPINES = 30
GREECE = 31
IRELAND = 32
POLAND = 33
PORTUGAL = 34
MEXICO = 35
RUSSIA = 36
TURKEY = 37

# 与前端 localeCodeMap 一致:界面语言(ISO) -> WOL 地区代码
ISO_TO_WOL = {
    "en-US": USA,
    "en-GB": UK,
    "de-DE": GERMANY,
    "es-ES": SPAIN,
    "fr-FR": FRANCE,
    "it-IT": ITALY,
    "ja-JP": JAPAN,
    "ko-KR": SOUTH_KOREA,
    "nl-NL": NETHERLANDS,
    "pl-PL": POLAND,
    "pt-BR": BRAZIL,
    "pt-PT": PORTUGAL,
    "ru-RU": RUSSIA,
    "zh-CN": CHINA,
    "zh-TW": TAIWAN,
}
