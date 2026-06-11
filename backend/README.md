# ra2web Python 后端(Django)

为网页红警前端(`dist/ra2web.min.js`,Chronodivide 客户端)提供完整的自建联机后端,
与前端无缝对接,无需修改任何前端代码。

## 提供的服务

| 前端配置项(servers.ini) | 协议 | 后端实现 | 说明 |
| --- | --- | --- | --- |
| `wolUrl` | WebSocket | `apps/wol`(`/wol`) | WOL 聊天大厅:登录、频道、房间、聊天、开局、快速匹配 |
| `gservUrl` | WebSocket | `apps/gserv`(`/gserv`) | 游戏中继:锁步动作聚合、地图传输、失步检测、速率自适应 |
| `wladderUrl` | HTTP | `apps/ladder`(`/ladder`) | 天梯:赛季、名次分页、按名字查询 |
| `wgameresUrl` | HTTP | `apps/gameres`(`/wgameres`) | 战绩上报:二进制包解析、排位 Elo 结算 |

## 快速开始

```bash
cd backend
pip install -r requirements.txt
python3 manage.py migrate
python3 -m daphne -b 0.0.0.0 -p 8000 ra2web_backend.asgi:application
```

然后在站点根目录 `servers.ini` 中新增区服(生产环境请使用 wss/https 反向代理):

```ini
[local]
label="我的服务器"
available=yes
gameVersion=0.65
wolUrl="ws://<服务器地址>:8000/wol"
wladderUrl="http://<服务器地址>:8000/ladder"
wgameresUrl="http://<服务器地址>:8000/wgameres"
gservUrl="ws://<服务器地址>:8000/gserv"
wolKeepAliveInGame=yes
```

> 注意:HTTPS 站点必须通过 `wss://`/`https://` 反代后端,
> 否则浏览器会拦截混合内容。

## 接口与前端匹配性核对

以下逐项对照前端打包代码(`dist/ra2web.min.js`)中的网络模块,
所有格式均有自动化测试覆盖(`tests/`,28 个用例)。

### 1. 天梯接口(network/WLadderService)

前端调用:

| 前端方法 | 请求 | 后端路由 |
| --- | --- | --- |
| `getSeasons(type)` | `GET {url}/{sku}/{type}` | `ladder/views.seasons` |
| `listSearch(players, ...)` | `POST {url}/{sku}/{type}/{season}/listsearch`,体 `{"players":[...]}` | `ladder/views.list_search` |
| `rungSearch(start, count, ...)` | `POST {url}/{sku}/{type}/{season}/rungsearch`,体 `{"start":n,"count":n}` | `ladder/views.rung_search` |

数据结构(`apps/core/structures.LadderProfile`,前端 `Ladder`/`RankIndicator` 组件消费):

```json
{"name": "Alice", "rank": 1, "rankType": 9, "points": 300,
 "wins": 3, "losses": 0, "mmr": 1002}
```

- `rankType` 与前端 `PlayerRankType` 枚举一致(0 列兵 ~ 9 总司令),未上榜玩家省略该字段(前端显示 `TXT_UNRANKED`);
- 赛季列表元素为 `"current"`/`"prev"`/历史赛季编号字符串,与前端 `wladderConfig` 的约定一致;
- SKU 固定 `16640`、天梯类型 `1v1`(前端 `WolConfig.allClientSettings`)。

### 2. 战绩上报(network/WGameResService + GameRes)

- 请求:`POST {url}/{sku}`,请求体为战绩二进制包的 Base64,
  `authorization` 头为 `Base64(JSON{nick, pass})`;
- 二进制包字段编码(4 字符字段名 + 大端类型/长度 + 数据)与前端
  `GameRes.toBinary/fromBinary` 完全一致(`apps/gameres/packet.py`);
- 排位结算:`TRNY=1` 且 `PLRS=2` 的对局按 `CMPn`(GameResType:
  Win=256/Loss=512/Resign=528/Disconnect=768/Draw=64)判定胜负,
  按 Elo 更新 `mmr`,同一 `GMID` 仅结算一次。

### 3. WOL 大厅(network/WolConnection / wolCodes)

回复码全部取自前端 `network/wolCodes`(见 `apps/core/wol_codes.py`),关键消息格式:

| 场景 | 服务器消息格式 | 对应前端解析 |
| --- | --- | --- |
| 登录成功 | `375/372/376 :- <MOTD>` | `login()` 收集 MOTD |
| 登录失败 | `378` / `465` / `721`,排队心跳 `720 <nick> <位置> <秒>` | `RPL_BAD_LOGIN` 等 |
| 加入频道 | `:<nick>!u@h JOIN :0,<ping>,<op> <频道>` | `joinChannel` replyMatch / `handleJoin` |
| 建房/加入房 | `:<nick>!u@h JOINGAME <min> <max> <type> <trny> 0 <ping> 0 :<频道>` | `handleJoingame`(第 6 个参数为 ping) |
| 房间列表 | `326 <nick> <频道> <人数> <上限> <类型> <锦标赛> 0 <房主ping> <flags>::<topic> 0` | `listGames` 参数位次,`flags=384` 表示有密码 |
| 成员列表 | `353 <nick> = <频道> :@host,0,<ping> user,0,<ping>` + `366` | `parseNamReply` |
| 聊天/选项 | `:<from>!u@h PRIVMSG/PAGE/GAMEOPT <目标> :<内容>` | `handlePrivMsg` 等 |
| 开局 | `:<srv> STARTG <nick> :<gserv地址> :<对局id> <时间戳>` | `handleStartGame` 正则 |
| 延迟测量 | `ping :<ts>` -> `:<srv> PONG s :<ts>` | `IrcConnection.ping` |

快速匹配(network/qmCodes):加入 `#Lob 50 0` 频道后 `privmsg matchbot :Match COU=.., COL=.., VRS=.., MOD=.., RKD=..`,
机器人回复 `Working`/`Stats n,avg`/`Matched <秒>`/`Requeue`/`Unavailable` 等,
配对成功后由后端在 gserv 预创建对局并向双方下发 `STARTG`。
**注意:需在 `settings.RA2WEB["QM_MAP_POOL"]` 配置地图池后快速匹配才可用。**

### 4. 游戏中继(network/GservConnection / gservCodes)

- 文本命令:`cvers`(API 版本 2)、`user`、`create`、`join`、`gameopts`、
  `loaded`、`loadinfo`、`active`、`taunt`、`privmsg`,回复码见 `apps/core/gserv_codes.py`;
- 二进制帧(首字节 `0x02`,数值小端):

| 方向 | 帧格式 | 说明 |
| --- | --- | --- |
| C→S | `02 01 turn(u32) actions` | 提交某回合动作 |
| S→C | `02 01 turn(u32) n(u8) [id(u8) len(u16) bytes]*n` | 聚合广播(前端 `parseAllPlayerActions`) |
| C→S | `02 02 turn(u32) hash(u32)` | 状态哈希(不一致广播 `801` 失步) |
| C→S / S→C | `02 03 map` / `02 02 map` | 自定义地图上传/下发(限 2MB) |

- `gameOpts` 序列化串与前端 `Serializer.serializeOptions` 一致,
  真人段下标即锁步玩家 id(`apps/gserv/state.parse_human_players`);
- 载入信息(`600`)按前端 `LoadInfoParser` 的 `name,status,pct,ping,lag` 五元组编码。

## 多语言支持

前后端配合实现完整的多语言体验:

- **前端语言切换**(`lib/lang-switch.js`,已在 `index.html` 引入):
  页面右上角提供语言选择器(简体中文/繁體中文/English),
  默认简体中文;选择保存在 `localStorage`,
  通过拦截 `config.ini` 与 `res/locale/*.json` 请求实现切换,
  无需改动打包后的游戏代码;也可用 `window.ra2webLang.set("en-US")` 编程切换;
- **语言上报**:前端登录前发送 `setlocale <地区代码>`
  (代码表见 `apps/core/wol_locales.py`,与前端 `WolLocale` 一致),
  后端登记到会话并持久化到账号;
- **MOTD 多语言下发**:登录公告按上报语言从
  `settings.RA2WEB["MOTD_BY_LOCALE"]` 选择文案,未匹配时回退默认中文 `MOTD`;
- `getlocale` 返回 `nick`\`<locale>\` 结构,与前端解析逻辑一致。

## 在线连线优化

- **速率自适应**:按全员实测 RTT 动态计算网络回合时长并通过 `802` 下发,
  低延迟局面更跟手,高延迟局面不卡顿;
- **回合超时剔除**:某玩家 30 秒未提交动作时广播 `804` 并将其移出等待名单,
  避免一人掉线全场卡死;
- **保活探测**:WOL/gserv 双向 ping,及时回收死连接并提供真实延迟数据
  (大厅玩家列表 ping 显示、匹配与房主选择参考);
- **断线重连**:同名登录自动顶号,gserv 开赛前允许断线玩家重新挂回原槽位;
- **登录排队**:在线人数超限时进入 `720` 排队而非直接拒绝;
- **就近匹配**:快速匹配按 MMR 差值就近配对,等待越久匹配范围逐步放宽;
- **消息限流**:单连接令牌桶限流,防止刷屏拖垮大厅。

## 部署形态

- 单进程 `daphne` 即可同时承载 4 类服务(大厅状态为进程内存);
- 多进程/多机部署时:HTTP 接口(ladder/gameres)可任意水平扩容,
  WOL/gserv 需保持单进程或改造为 Redis 共享状态(`channels-redis`);
- 数据库默认 SQLite,生产建议 PostgreSQL/MySQL(改 `DATABASES` 即可)。

## 测试

```bash
cd backend
python3 manage.py test tests
```

28 个用例覆盖:天梯接口数据结构、战绩包编解码与结算、WOL 登录/频道/建房/
聊天/开局/快速匹配全流程、gserv 建房/加入/锁步聚合/地图传输/失步与版本校验。
