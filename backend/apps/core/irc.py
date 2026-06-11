"""
IRC 协议工具函数。

频道名转义规则与前端 network/IrcProtocol 模块完全一致:
空格 -> "_"、"%" -> "%%"、"_" -> "%_"、"\b" -> "%b"、
"\n" -> "%n"、"\r" -> "%r"、":" -> "%="、"," -> "%-"。
"""

MAX_CHANNELNAME_LEN = 30
CHAN_OP_PREFIX = "@"


def escape_channel_name(name: str) -> str:
    """转义频道名(与前端 IrcProtocol.escapeChannelName 一致)。"""
    out = []
    for ch in name:
        if ch == " ":
            out.append("_")
        elif ch == "%":
            out.append("%%")
        elif ch == "_":
            out.append("%_")
        elif ch == "\b":
            out.append("%b")
        elif ch == "\n":
            out.append("%n")
        elif ch == "\r":
            out.append("%r")
        elif ch == ":":
            out.append("%=")
        elif ch == ",":
            out.append("%-")
        else:
            out.append(ch)
    return "".join(out)


def unescape_channel_name(name: str) -> str:
    """反转义频道名(与前端 IrcProtocol.unescapeChannelName 一致)。"""
    out = []
    i = 0
    while i < len(name):
        ch = name[i]
        i += 1
        if ch == "%":
            nxt = name[i] if i < len(name) else ""
            i += 1
            out.append(
                {"b": "\b", "n": "\n", "r": "\r", "=": ":", "-": ","}.get(nxt, nxt)
            )
        elif ch == "_":
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def user_prefix(nick: str) -> str:
    """构造形如 nick!user@host 的消息前缀。"""
    return f"{nick}!u@h"
