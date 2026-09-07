# WPK Hand Recorder

本地 Python 工具，通过 Chrome DevTools Protocol（CDP）读取你自己的
[WePoker-H5](https://h5.sxkxys.com/) 标签页所收到的 WebSocket 帧，并把可识别的牌局事件重建为牌谱。

它不安装根证书、不做 HTTPS 中间人代理、不修改或自动点击 WPK 页面。调试端口只绑定
`127.0.0.1`。账号、密码、Cookie、HTTP Header 和 URL 查询参数不会写入录制文件。

## 安装

需要 macOS、Google Chrome 和 Python 3.9+：

```bash
cd wpk_hand_recorder
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

## 使用

### 一条命令启动录制与看板

```bash
wpk-recorder run --data-dir data
```

命令会启动或连接专用 Chrome、开始录制，并在
`http://127.0.0.1:8765/` 打开本地看板。看板包含实时行动线、历史牌谱、对手统计、
鱿鱼时间线和原始桌内事件抽屉。登录和进入牌桌仍由你手动完成。专用配置保存在
`~/.wpk-recorder/chrome-profile`。按 `Ctrl-C` 同时停止录制器和看板。

原来的两步方式 `wpk-recorder browser` + `wpk-recorder capture` 仍可用于诊断。
输出位于：

- `data/hands.sqlite3`：规范化分析库；
- `data/events.jsonl`：完整、已脱敏的桌内 Cocos 事件；
- `data/frames.jsonl`：可审计的帧元数据；
- `data/hands.jsonl`：结构化牌谱；
- `data/live.log`：实时追加的中文 Unicode 行动日志；
- `data/text/*.txt`：每手稳定的中文文本牌谱；
- `data/squid_events.jsonl`：鱿鱼阶段审计流。

看板中的“导出行动 CSV”会导出每个玩家、每街、每个动作一行的分析表。
JSON 导出直接使用 `hands.jsonl` 和 `events.jsonl`。

首次适配新版协议时，可临时保留服务端原始帧：

```bash
wpk-recorder capture --data-dir data --retain-wire --duration 120
wpk-recorder inspect --data-dir data
```

原始二进制是受限诊断材料，虽然目录和文件权限分别设置为 `0700`/`0600`，仍可能包含服务端
下发的账户标识。完成协议映射后立即清除：

```bash
wpk-recorder purge-raw --data-dir data
```

`run --retain-wire` 或 `capture --include-sent --retain-wire` 只应用于短时协议诊断；
它会提高隐私风险。

### 3. 协议映射

录制器同时使用两层信息：

1. CDP 的 WebSocket 帧用于留存完整性哈希和排查协议变化；
2. WPK 客户端已经解开的 Cocos 事件用于可靠读取牌局字段，不复制登录令牌，也不依赖 TLS 解密。

当前已确认的德州事件包括 `dealNotify`、`actionHistoryNotify`、`actionNotify`、
`roundChangeNotify`、`playResultNotify` 和 `updateHistoryData`。牌编码为 `花色*100+点数`：
花色 1/2/3/4 分别是黑桃/红桃/梅花/方片，点数 1 是 A、11/12/13 是 J/Q/K。
`actionScore` 是本次新增投入，`seatScore` 是该街累计到达额；JSON 中分别保存为
`amount` 和 `amount_to`。

底层诊断器仍会依次尝试 JSON、MessagePack、长度前缀、gzip/zlib 和无 schema 的
Protobuf wire 解析。字符串事件名可直接映射；未知数字消息 ID 不会被猜测。

鱿鱼模式使用 `squidGameNotify` / `BattleSquidGameMsg`。`scene` 1–11 原样保留并映射为
开始、加入、晋级、结算、恢复、等待、关闭等待、刷新、无人获胜及结算手牌引用。
`ext`、`users`、`sett` 和 `squidSettHands` 同时保留在原始事件和鱿鱼规范化表中。

复制 `protocol.example.json` 为本地的 `protocol.local.json`，填入从同一版本客户端确认的
消息 ID 和行动 ID，然后重放诊断样本：

```bash
wpk-recorder replay --data-dir data --protocol protocol.local.json
```

`protocol.local.json` 如包含私有协议信息，不应提交或分发。

## 数据正确性

状态机按 hand ID 分手，去除重复事件，并校验：

- 公共牌数量必须是 0、3、4 或 5；
- 行动序号连续；
- 有结算数据时，所有玩家净输赢应平衡；
- 未解出行动的手牌会带 `no actions decoded` 警告。

未知消息和字段会保留统计，但不会被猜成牌局事件。WPK 更新客户端后，应先录一手测试牌，
把页面上的底牌、各街行动、公共牌、底池与文本导出逐项核对；有任何不一致，不应把该批记录用于分析。

## SQLite 数据字典

- `raw_events`：单调序号、时间、table/hand ID、事件名及完整桌内 JSON；
- `hands`、`hand_players`、`actions`、`board_cards`、`results`：一手、玩家快照、
  行动、公共牌和结算；`hands.hand_number` 保存数字局号，`played_at` 统一保存 UTC，
  看板按中国时区显示；行动保留 `action_id`、`amount`、`amount_to`、`stack_after`；
- `players`：稳定 user ID 与最新昵称，用于处理改名；
- `decision_snapshots`：每个行动发生前的底池、有效筹码、SPR、跟注额、位置、
  IP/OOP、翻前主动者、牌面纹理和下注占池比；
- `opportunities`：玩家实际获得的 VPIP、PFR、RFI、3bet/4bet、各街 cbet、
  fold-to-cbet、probe、check-raise 等机会及其选择结果；
- `squid_rounds`、`squid_events`、`squid_settlements`：鱿鱼轮次、阶段和最终分数；
- `hand_squid_players`、`squid_awards`：每手开始/结束时各玩家持有的鱿鱼数，
  以及 scene 3 确认的本手获得者和获得数量；
- `frames`：底层帧哈希及诊断结果，默认没有二进制正文。

VPIP、PFR、RFI、3bet、各街 cbet/fold、激进因子、WTSD、W$SD、WWSF 和净输赢
由规范化表计算。频率使用基于总体牌池先验的 Beta-Binomial 收缩，同时返回观察值、
机会数、80% 近似可信区间和样本置信等级；小样本不会直接显示为确定的 0%/100% 倾向。
昵称用于展示，聚合身份始终使用 user ID。

对手页可切换全部、普通德州和鱿鱼模式。点击玩家行可查看完整机会率、按位置统计、
下注尺寸分布及仅在足够样本下生成的剥削信号。位置和下注前上下文从牌局状态确定性派生；
旧记录缺少按钮位时会标记为 `Unknown`，不会猜测。

历史牌谱可切换“全部牌谱/仅看有效”；每手会显示开局快照中的鱿鱼持有数量，以及
`squidGameNotify scene=3` 确认的本手鱿鱼获得者。无相关事件时不猜测归属。

## 备份与迁移

停止录制后复制整个 `data/` 即可备份。运行中需要备份 SQLite 时使用：

```bash
sqlite3 data/hands.sqlite3 ".backup 'wpk-backup.sqlite3'"
```

不要只复制 `-wal` 文件，也不要把 `data/`、备份、Chrome profile 或诊断帧提交到 Git。

## 隐私边界

工具只应用于你有权访问的牌桌及个人复盘。默认不采集客户端发送帧，并排除 HTTP 请求头、
Cookie、查询参数、认证字段、聊天、好友和支付数据。不要把 `data/`、Chrome profile 或原始帧
上传到代码仓库。

## 工程信息

### 技术栈

- Python 3.9+，使用 `setuptools` 构建并以 `src/` layout 组织包；
- `websockets` 连接 Chrome DevTools Protocol，采集 WebSocket 事件；
- FastAPI + Uvicorn 提供本地 HTTP API 和实时看板；
- SQLite 保存规范化牌局、分析特征和审计数据；
- 原生 HTML、CSS、JavaScript 实现看板，无前端构建步骤；
- pytest、HTTPX 用于状态机、协议解码、特征计算、存储和接口测试。

### 架构与数据流

```text
专用 Chrome / WPK 标签页
          │ CDP + 页面内 Cocos 事件
          ▼
   cdp.py / decoder.py / protocol.py
          │ 规范化事件
          ▼
 state.py / squid.py / quality.py
          │ 手牌、行动、结算、质量告警
          ▼
 storage.py ── SQLite / JSONL / 文本牌谱
          │
          ├── features.py / analytics.py ── 统计与决策特征
          └── server.py / web/ ── 本地 API 与看板
```

`cli.py` 是统一入口，负责 `browser`、`capture`、`inspect`、`replay`、
`purge-raw` 和 `run` 子命令的编排。`models.py` 定义核心数据模型，
`formatting.py` 负责稳定的文本输出，`privacy.py` 负责敏感字段过滤。

### 目录结构

```text
wpk_hand_recorder/
├── src/wpk_recorder/       # 录制、解码、状态机、存储、分析和服务端
│   └── web/                # 无构建步骤的本地看板静态资源
├── tests/                  # 单元测试、接口测试及脱敏协议样本
├── protocol.example.json   # 本地协议映射模板
├── pyproject.toml          # 包元数据、依赖、CLI 与 pytest 配置
└── data/                   # 本地运行数据（已由 Git 忽略）
```

### 本地开发

安装开发依赖并运行测试：

```bash
python -m pip install -e '.[dev]'
pytest
```

查看所有命令及参数：

```bash
wpk-recorder --help
wpk-recorder run --help
```

项目版本和运行时依赖统一维护在 `pyproject.toml`。新增协议样本时必须先脱敏；
生成的数据库、JSONL、文本牌谱、Chrome profile 和 pytest 缓存均不应提交。
