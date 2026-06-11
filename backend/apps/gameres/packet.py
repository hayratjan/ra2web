"""
战绩二进制包(GameRes packet)编解码。

二进制布局与前端 network/gameres/GameRes 的 toBinary/fromBinary 一致:

    uint16(BE) 总长度(正文 + 4)
    uint16     0
    重复字段:
        4 字节 ASCII 字段名
        uint16(BE) 类型(1=Byte 2=Boolean 5=Time 6=Int 7=String)
        uint16(BE) 元素数/长度
        数据(Byte/Time/Int: uint32 BE;Boolean: 4 字节取首字节;
             String: 以 0 结尾,按 4 字节对齐填充)

字段名说明(节选,完整见前端 GameRes.toFlat):
    GMID 对局 id、SNAM 上报者、TRNY 排位赛、PLRS 真人玩家数、
    SCEN 地图名、DURA 时长(秒)、OOSY 是否失步、FINI 是否完赛、
    NAMn 玩家名、CMPn 完成状态(GameResType)、CTYn 国家、TIDn 队伍。
"""
import struct
from dataclasses import dataclass, field

TYPE_BYTE = 1
TYPE_BOOLEAN = 2
TYPE_TIME = 5
TYPE_INT = 6
TYPE_STRING = 7

# 单局玩家数上限(RA2 最多 8 人,留余量),防止恶意 PLRS 导致循环放大
MAX_PACKET_PLAYERS = 16
# 战绩包字段数上限,防止畸形包撑爆内存
MAX_PACKET_FIELDS = 512


class GameResPacketError(ValueError):
    """战绩包格式错误。"""


@dataclass
class GameResPacket:
    """解析后的战绩包:fields 保留原始字段,便于完整入库。"""

    fields: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # 解析
    # ------------------------------------------------------------------
    @classmethod
    def from_binary(cls, data: bytes) -> "GameResPacket":
        # 所有底层解析错误(越界/截断)统一转为 GameResPacketError,
        # 避免畸形数据把 struct.error/IndexError 泄漏到视图层变成 500
        try:
            return cls._from_binary_unsafe(data)
        except (struct.error, IndexError, OverflowError) as exc:
            raise GameResPacketError(f"Malformed packet: {exc}") from exc

    @classmethod
    def _from_binary_unsafe(cls, data: bytes) -> "GameResPacket":
        if len(data) < 4:
            raise GameResPacketError("Packet too short")
        (total_len,) = struct.unpack_from(">H", data, 0)
        (zero,) = struct.unpack_from("<H", data, 2)
        if zero != 0:
            raise GameResPacketError("Second word should be 0")
        body_len = total_len - 4
        if body_len < 0 or len(data) < total_len:
            raise GameResPacketError("Packet length mismatch")

        fields = {}
        pos = 4
        end = 4 + body_len
        # 与前端 fromBinary 循环条件一致:position <= bodyLen - 4
        while pos <= end - 4:
            if len(fields) >= MAX_PACKET_FIELDS:
                raise GameResPacketError("Too many fields")
            name = data[pos : pos + 4].decode("ascii", "replace").rstrip("\x00")
            pos += 4
            (ftype,) = struct.unpack_from(">H", data, pos)
            pos += 2
            (count,) = struct.unpack_from(">H", data, pos)
            pos += 2
            if ftype == TYPE_BYTE or ftype in (TYPE_TIME, TYPE_INT):
                (value,) = struct.unpack_from(">I", data, pos)
                pos += 4
            elif ftype == TYPE_BOOLEAN:
                value = bool(data[pos])
                pos += 4
            elif ftype == TYPE_STRING:
                size = 4 * ((count + 3) // 4)
                raw = data[pos : pos + size]
                pos += size
                value = raw.split(b"\x00", 1)[0].decode("latin-1")
            else:
                # 未知类型:按长度跳过(与前端行为一致)
                pos += count
                continue
            fields[name] = (ftype, value)
        return cls(fields=fields)

    # ------------------------------------------------------------------
    # 生成(测试与机器人上报使用)
    # ------------------------------------------------------------------
    def to_binary(self) -> bytes:
        body = bytearray()
        for name, (ftype, value) in self.fields.items():
            if len(name) > 4:
                raise GameResPacketError(f"字段名 {name} 超过 4 字符")
            body += name.encode("ascii").ljust(4, b"\x00")
            body += struct.pack(">H", ftype)
            if ftype == TYPE_BYTE:
                body += struct.pack(">H", 1)
                body += struct.pack(">I", int(value))
            elif ftype == TYPE_BOOLEAN:
                body += struct.pack(">H", 1)
                body += bytes([1 if value else 0, 0, 0, 0])
            elif ftype in (TYPE_TIME, TYPE_INT):
                body += struct.pack(">H", 4)
                body += struct.pack(">I", int(value))
            elif ftype == TYPE_STRING:
                text = str(value)
                size = len(text) + 1
                body += struct.pack(">H", size)
                body += text.encode("latin-1").ljust(4 * ((size + 3) // 4), b"\x00")
            else:
                raise GameResPacketError(f"未知字段类型 {ftype}")
        return struct.pack(">H", len(body) + 4) + b"\x00\x00" + bytes(body)

    # ------------------------------------------------------------------
    # 便捷取值
    # ------------------------------------------------------------------
    def get_int(self, name: str, default: int = 0) -> int:
        ftype_value = self.fields.get(name)
        if not ftype_value:
            return default
        ftype, value = ftype_value
        return int(value) if ftype in (TYPE_BYTE, TYPE_TIME, TYPE_INT) else default

    def get_bool(self, name: str) -> bool:
        ftype_value = self.fields.get(name)
        return bool(ftype_value and ftype_value[0] == TYPE_BOOLEAN and ftype_value[1])

    def get_str(self, name: str, default: str = "") -> str:
        ftype_value = self.fields.get(name)
        if not ftype_value or ftype_value[0] != TYPE_STRING:
            return default
        return str(ftype_value[1])

    def player_field(self, name: str, index: int):
        """按玩家序号读取 NAMn/CMPn 等字段。"""
        return self.fields.get(f"{name}{index}")

    def players(self) -> list:
        """提取所有玩家的关键信息(数量强制截断,防循环放大攻击)。"""
        result = []
        count = min(self.get_int("PLRS"), MAX_PACKET_PLAYERS)
        for i in range(count):
            name_field = self.player_field("NAM", i)
            if not name_field:
                continue
            result.append(
                {
                    "index": i,
                    "name": str(name_field[1]),
                    "completion": self.get_int(f"CMP{i}"),
                    "country": self.get_int(f"CTY{i}"),
                    "side": self.get_int(f"SID{i}"),
                    "team": self.get_int(f"TID{i}"),
                    "color": self.get_int(f"COL{i}"),
                    "lost_connection": self.get_bool(f"LCN{i}"),
                }
            )
        return result

    def to_plain_dict(self) -> dict:
        """转换为可 JSON 序列化的 {字段: 值} 字典(入库存档用)。"""
        return {k: v for k, (_t, v) in self.fields.items()}
