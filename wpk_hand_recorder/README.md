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

### 可选：实时 LLM 推理复核

看板会先自动秒出本地范围与 Money-EV 基线，这一步不会调用 LLM。用户可单独点击
“用 GPT‑5.6 分析手牌 + 对手剥削”；开启自动模式后，记录器只在检测到复杂本人行动点时
调用已配置的 ModelHub 模型做一次限时复核。每次结果都会明确显示“本地结果 · 未调用 LLM”
或“已调用 GPT‑5.6”、模型来源和耗时，不会自动点击或代替玩家执行动作。
手动复核会锁定点击时的行动节点；即使牌局随后继续，完成结果也会保留并明确标记为复盘内容。
没有可靠底牌时仍可手动调用 GPT‑5.6，但只做整个范围的对手剥削复核，不猜具体持牌或输出
单手牌动作。LLM 给出的每条对手判断和范围调整都必须引用服务端提供的真实统计证据，否则丢弃。
实时复核只发送最多 6 条最有信息量的对手证据、最近 10 个行动和压缩后的范围基线，并要求
短键 JSON 输出以降低 5.6 尾延迟。如果模型未在硬预算内完成、网关失败或 JSON 不完整，服务端
会返回低置信度的 `local-exploit-fallback-v1`，界面明确显示“GPT‑5.6 超时 · 已回退本地证据”，
不会把回退结果冒充 LLM 结论。

API key 只从 `wpk_hand_recorder/.env` 或显式进程环境读取，不写入数据库、日志或前端。
首次配置：

```bash
cp .env.example .env
# 编辑 .env，填写 WPK_LLM_API_KEY
wpk-recorder run --data-dir data
```

`.env` 已被仓库级 `.gitignore` 忽略；显式进程环境优先于文件值。实时决策复核和
深层画像默认统一请求 `gpt-5.6-sol` ModelHub Chat Completions 接口。可选配置：

- `WPK_LLM_MODEL`：ModelHub 部署名，默认 `gpt-5.6-sol`；
- `WPK_LLM_ENDPOINT`：覆盖由模型名自动生成的完整接口 URL；
- `WPK_LLM_TIMEOUT_SECONDS`：轻推理硬超时，默认 12 秒；
- `WPK_LLM_PROFILE_TIMEOUT_SECONDS`：深层画像硬超时，默认 20 秒；
- `WPK_LLM_MAX_COMPLETION_TOKENS`：默认 800，限制在 256–6000；
- `WPK_LLM_REASONING_EFFORT`：默认 `low`，优先满足普通节点延迟；
- `WPK_LLM_DEEP_TIMEOUT_SECONDS`：重推理硬超时，默认并最高为 20 秒；
- `WPK_LLM_DEEP_MAX_COMPLETION_TOKENS`：重推理输出预算，默认 1600；
- `WPK_LLM_DEEP_REASONING_EFFORT`：默认 `high`，可改为 `medium`；
- `MODEL_GATEWAY_KEY`：若未设置 `WPK_LLM_API_KEY`，可复用此环境变量。

实时复核请求只包含匿名座位、本人底牌（如有）、公共牌、合法动作、下注状态、匿名行动线、
位置/码深条件化的贝叶斯机会率、相对当前牌池偏移及收缩后的对手翻前范围摘要；
画像复核包含匿名目标、聚合统计与筛选后的匿名公开亮牌案例。两者都不会发送昵称、稳定
user ID、hand ID、Cookie 或认证信息。
看板可选轻推理或重推理：轻推理最多取 6 条关键对手证据和最近 16 个行动，以 low effort
完成普通节点；重推理最多取 16 条证据和最近 30 个行动，以 high effort 在 20 秒预算内
交叉检查牌型、赔率、SPR、范围和对手偏移。自动复核固定走轻推理；手动复核使用当前所选
档位并锁定行动节点，因此牌局继续后迟到结果仍会作为复盘保留。两档超时或网关失败时都会
明确标记并回退本地证据，不会把本地结果冒充 GPT‑5.6 结论。
系统会先计算牌型、听牌、pot odds、SPR 与对随机单手的中性 equity 基线；只有高确定性的
免费过牌、极端赔率和坚果牌节点才由 `local-rules-v1` 直接返回，其余复杂节点再调用 LLM。
如果当前行动事件没有带出可靠底牌，本地层仍会立即显示当前位置和行动线的简化全范围：
翻前用 169 格颜色区分加注、跟注与弃牌频率，翻后按强价值、顶对/超对、中弱摊牌价值、
强听牌和空气五档展示动作混合；不会猜测具体底牌。此时可另点 GPT‑5.6 按钮，根据当前
活跃对手的样本证据给出范围级调整。有底牌时，同一视图会突出当前手牌，并把本地/LLM 的
具体建议放在范围图上方。该范围是便于快速阅读的确定性基线，不是 GTO solver 解。

### 3. 协议映射

录制器同时使用两层信息：

1. CDP 的 WebSocket 帧用于留存完整性哈希和排查协议变化；
2. WPK 客户端已经解开的 Cocos 事件用于可靠读取牌局字段，不复制登录令牌，也不依赖 TLS 解密。

当前已确认的德州事件包括 `dealNotify`、`actionHistoryNotify`、`actionNotify`、
`roundChangeNotify`、`playResultNotify` 和 `updateHistoryData`。牌编码为 `花色*100+点数`：
花色 1/2/3/4 分别是黑桃/红桃/梅花/方片，点数 1 是 A、11/12/13 是 J/Q/K。
`actionScore` 是本次新增投入，`seatScore` 是该街累计到达额；JSON 中分别保存为
`amount` 和 `amount_to`。实测 `actionId // 1000` 等于该行动所属的数字局号；录制器
使用它在 `dealNotify` 延迟或缺失时提前建立正确手牌边界，避免上一手行动混入下一手。

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
- 行动序号连续且街道不能从翻后退回翻前；
- 有结算数据时，玩家净输赢在扣除保险和基金字段后应平衡；
- 未解出行动的手牌会带 `no actions decoded` 警告。

未知消息和字段会保留统计，但不会被猜成牌局事件。WPK 更新客户端后，应先录一手测试牌，
把页面上的底牌、各街行动、公共牌、底池与文本导出逐项核对；有任何不一致，不应把该批记录用于分析。
启动时会保守修复 action ID 明确属于其他局的污染行动、重复 action ID、强制注街道和
内部序号；无法确认缺失内容的中断牌仍保留为 `partial`，不会伪造成有效牌谱。

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
- `decision_states`：实时与回放共用的版本化行动状态、state hash、质量门控和逐玩家投入快照；
- `strategy_evaluations`：候选动作 EV、推荐混合、置信度及策略引擎版本的审计记录；
- `squid_rounds`、`squid_events`、`squid_settlements`：鱿鱼轮次、阶段和最终分数；
- `hand_squid_players`、`squid_awards`：每手开始/结束时各玩家持有的鱿鱼数，
  以及 scene 3 确认的本手获得者和获得数量；
- `showdown_observations`：只使用公开摊牌生成的监督标签，包括翻前牌类、各街成牌、
  听花/顺听、对随机单个对手的 Monte Carlo equity、位置、码深、鱿鱼数、
  此前累计盈亏和行动线；
- `frames`：底层帧哈希及诊断结果，默认没有二进制正文。

VPIP、PFR、RFI、3bet、各街 cbet/fold、激进因子、WTSD、W$SD、WWSF 和净输赢
由规范化表计算。频率使用基于总体牌池先验的 Beta-Binomial 收缩，同时返回观察值、
机会数、80% 近似可信区间和样本置信等级；小样本不会直接显示为确定的 0%/100% 倾向。
昵称用于展示，聚合身份始终使用 user ID。

对手页可切换全部、普通德州和鱿鱼模式。点击玩家行可查看风格标签、完整机会率、按位置统计、
下注尺寸分布、带证据等级的漏洞/对抗建议，以及按位置和翻前行动线切换的 13×13 起手牌范围。
位置和下注前上下文从牌局状态确定性派生；旧记录缺少按钮位时会标记为 `Unknown`，不会猜测。
“LLM 深化画像”只在手动点击时调用远程接口，负责复核和解释本地事实，不生成或修改范围数字。

历史牌谱可切换“全部牌谱/仅看有效”；每手会显示开局快照中的鱿鱼持有数量，以及
`squidGameNotify scene=3` 确认的本手鱿鱼获得者。无相关事件时不猜测归属。

## 范围推断与回测

`GET /api/players/{user_id}/preflop-range` 提供选手级翻前范围矩阵，可用 `position`
和 `line=vpip|open_raise|three_bet|cold_call|limp` 选择位置与行动线。模型以结构化起手牌强度
和牌池分布为先验，以全部行动机会的 Beta-Binomial 后验确定范围宽度，再用公开亮牌低权重
调整牌型族群的形状。单手公开亮牌只按 0.75 个样本计入，避免偶然亮牌直接主导某个精确组合；
响应同时包含行动机会、本人/群体亮牌数、先验依赖比例、80% 区间和置信等级。样本增加后，
个体证据权重会自动上升。

`POST /api/players/{user_id}/profile/analyze` 使用已配置的 LLM 手动复核当前选择的位置/行动线，
只发送匿名目标、聚合频率、收缩后范围摘要、相对当前牌池的偏移，以及最多 10 个高信息量的
匿名公开亮牌案例。案例包含位置、语义化翻前线、各街行动、尺度桶、牌面纹理和公开牌力，
不包含昵称、稳定 user ID、hand ID 或时间戳。LLM 返回风格解释、最多三项漏洞、最多四项
对抗调整和保留意见；每项漏洞必须引用真实的 `metric:*` 或 `case_*` 证据编号，且置信度受
本地样本量硬上限约束。前端可以展开查看实际发送的偏移与案例。

牌谱详情会按玩家和街道输出粗粒度牌力分布、预测 equity、训练标签数、同类行动线样本数
和置信等级。模型使用位置、IP/OOP、单挑/多人池、下注尺寸、有效码深、公共牌纹理、
鱿鱼持有数、此前累计盈亏及跨街行动线；玩家样本会与总体池样本收缩融合。

`GET /api/inference/backtest` 使用 leave-one-hand-out，返回 Top-1、multi-class Brier score
和 log loss。`GET /api/players/{user_id}/context` 返回位置、码深、鱿鱼和盈亏状态下的行动分布
及公开摊牌牌力摘要。由于弃牌底牌不可见，摊牌样本存在明显选择偏差；置信度很低时前端不会
展示具体范围概率。当前模型是后续接入 GTO 节点先验和更大样本训练的可回测基线，不应直接
作为真钱行动建议。

## 实验性 Money-EV MVP 引擎

`GET /api/strategy/current` 会在本人行动节点上运行本地候选动作评估。目标场景严格限定为
WPK 8/9 人、2/4 盲注、每人 ante 1、深码鱿鱼局；其他配置仍可返回范围基线，但会被标为
低置信和未校准。引擎把结果拆成：

- `chip_ev`：当前牌局从决策点开始的增量筹码 EV；
- `squid_ev`：相对弃牌/过牌参考动作的鱿鱼轮次边际 EV；
- `money_ev`：只有从历史结算校准出每条鱿鱼价值后，才合并前两者；
- `robust_ev`：扣除范围、样本和激进行动的不确定性惩罚后用于排序的值。

实时和回放都通过同一个不可变 `DecisionState` 构造器重建逐玩家筹码、街内/累计投入、
主边池、行动顺序和合法动作。嵌入式 `wpk-recorder run` 优先读取录制器内存快照；
后续行动到达即使旧建议失效，state hash 也用于拒绝迟到结果。每个行动点同时写入
`decision_states`，便于回放核对。

鱿鱼规则按“一轮最多人数 + 4 条、同一玩家可重复获得、发完或只剩一名零鱿鱼玩家时结算”
建模。若结算时实际发出 `S` 条且有 `L` 名零鱿鱼玩家，每名零鱿鱼玩家支付 `S×V`，
持有 `k` 条者获得 `k×L×V`。系统使用 `squidGameNotify scene=3` 重建数量，并用
`scene=4 sett.addScore` 同时检验按实际已发数量或配置总数结算；不能通过零和及误差检查时，
不会把鱿鱼单位 EV 合并进筹码 EV。由于未知单条价值时两种口径可能只相差一个缩放常数，
历史结算无法唯一识别口径会返回 `ambiguous_basis`，必须显式配置后才合并 Money-EV。
跨手模拟使用历史鱿鱼局的有效收池率，并对旧样本衰减、小样本向牌池均值收缩；牌局 Monte
Carlo 分别计算底池份额和“至少并列收池”的获奖概率，不把平分底池 equity 当成获奖概率。
可用以下环境变量显式覆盖已确认的房间规则：

- `WPK_SQUID_VALUE`：每条鱿鱼的筹码价值，必须大于 0；
- `WPK_SQUID_PAYOUT_BASIS=awarded|configured`：按实际已发数量或配置总数结算。

`GET /api/squid/calibration` 提供校准轮数、推断价值和拟合误差；
`GET /api/strategy/evaluations` 返回带 state hash 和引擎版本的影子评估审计记录。
当前版本是可审计 MVP：翻后使用翻前贝叶斯范围、多方 Monte Carlo 和深度受限近似，
不是 8/9 人深码 GTO solver，也不会自动点击。应只在所有参与者知情、平台明确允许的环境中
先做 shadow 验证，不能用短期输赢替代校准误差和状态正确率。

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
 state.py / decision_state.py / squid.py
          │ 手牌、行动、主边池、状态质量
          ▼
 storage.py ── SQLite / JSONL / 文本牌谱
          │
          ├── features.py / analytics.py / opponent_model.py
          ├── squid_value.py / strategy.py ── Money-EV MVP
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
