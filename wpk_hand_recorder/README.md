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

实时入口由 `unified-inference-v1` 统一编排，返回固定的 `inference-advice-v1`：
本人节点为 `exact_hand`，包含格式化动作、尺度和最多三条理由；观战节点为 `full_range`，
只返回完整范围策略及频率调整。两种模式共享同一套 context 版本、证据校验、路由元数据和
前端展示；旧 `/api/reasoning/analyze` 暂时保留兼容，新客户端使用 `/api/inference/analyze`。

看板会先自动秒出本地范围与 Money-EV 基线，这一步不会调用 LLM。用户可单独点击
“用 GPT‑5.6 分析手牌 + 对手剥削”；开启自动模式后，记录器只在检测到复杂本人行动点时
调用已配置的 ModelHub 模型做一次限时复核。每次结果都会明确显示“本地结果 · 未调用 LLM”
或“已调用 GPT‑5.6”、模型来源和耗时，不会自动点击或代替玩家执行动作。
“观战实时范围”开关默认开启并保存在当前浏览器；未入座观战时会跟随当前行动者刷新范围和
动作建议，关闭后停止实时展示。本人已入座时，对手行动节点仍会被服务端过滤。
实时牌局中的非本人玩家卡片提供两个独立入口：“当前节点范围”打开冻结节点侧栏，显示该玩家
截至此刻的 check/call/bet/raise 行动线、当前 169 后验范围、成牌/听牌分布、行动前后位移及
下一行动与尺度响应；“历史画像”才跳到长期对手数据。历史牌谱详情复用这两个入口，但节点范围
固定到所选手牌，不会静默切换到最新实时牌局。
翻牌后未弃牌的对手卡片会异步显示“强价值 / 摊牌价值 / 听牌 / 空气或 Bluff 候选”四段简条；
分类按底牌相对公共牌带来的提升计算，公共牌自身的两对、顺子或同花不会再让全员显示强牌。
轻量摘要会对个人公开行动样本做保守牌池收缩，但只用于展示，不会绕过 Money-EV 的生产门控。
实时右栏不再重复显示低信息量事件流；完整事件仍保留在“原始事件”页，牌局 Action 保留在牌谱中。
九人桌玩家卡片按 WPK 牌桌方位排列（上排 4–7、两侧 3/8、下排 2–1–9），并标注座位方位；
弃牌状态优先读取客户端座位组件的实时 `isFold`，取不到时才按行动线推导；弃牌玩家会降色并
显示“已弃牌”，行动中和全下也有独立状态色。卡片内同时压缩显示风格标签、
历史手数及 VPIP、PFR、3BET，方便实战中扫视。
手动复核会锁定点击时的行动节点；即使牌局随后继续，完成结果也会保留并明确标记为复盘内容。
没有可靠底牌时仍可手动调用 GPT‑5.6，但只做整个范围的对手剥削复核，不猜具体持牌或输出
单手牌动作。LLM 给出的每条对手判断、范围调整和频率转移都必须引用服务端提供的真实统计证据，
否则丢弃。频率转移采用 `范围桶 + 转出动作 + 转入动作 + 百分点 + 证据` 的结构化协议；
本地层再按样本数和置信度限幅并保持每个范围桶总和为 100%，不会直接执行 LLM 的任意数字。
实时复核只发送最多 6 条最有信息量的对手证据、整手牌截至该节点的完整行动线和压缩后的范围基线，并要求
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
- `WPK_ASSISTANCE_MODE`：服务端强制能力策略，默认 `operator_approved_live`；也可通过
  `run|dashboard --assistance-mode` 显式设置。四档为 `operator_approved_live`（实时范围、
  equity、建议、画像和历史范围）、`post_session`（只允许手后画像与历史范围）、
  `static_history`（只允许确定性的静态历史范围）和 `capture_only`（只录制，不开放分析输出）。
  前端开关不能扩大该策略授权；
- `WPK_LLM_ENDPOINT`：覆盖由模型名自动生成的完整接口 URL；
- `WPK_LLM_TIMEOUT_SECONDS`：轻推理硬超时，默认 12 秒；
- `WPK_LLM_PROFILE_TIMEOUT_SECONDS`：深层画像硬超时，默认 30 秒；
- `WPK_LLM_MAX_COMPLETION_TOKENS`：默认 800，限制在 256–6000；
- `WPK_LLM_PROFILE_MAX_COMPLETION_TOKENS`：画像独立输出预算，默认 2000；首次 JSON
  被截断或格式损坏时，会用剩余画像超时预算自动做一次精简重试；
- `WPK_LLM_REASONING_EFFORT`：默认 `low`，优先满足普通节点延迟；
- `WPK_LLM_DEEP_TIMEOUT_SECONDS`：重推理硬超时，默认并最高为 20 秒；
- `WPK_LLM_DEEP_MAX_COMPLETION_TOKENS`：重推理输出预算，默认 2000；
- `WPK_LLM_DEEP_REASONING_EFFORT`：默认 `medium`，20 秒内比 `high` 更容易完整返回结构化 JSON；
  也可显式改为 `high`；
- `WPK_INFERENCE_EXACT_TEMPLATE`：生产精确手牌模板，默认 `exact-action-v1`；
- `WPK_INFERENCE_RANGE_TEMPLATE`：生产观战范围模板，默认 `full-range-v1`；
- `WPK_INFERENCE_ALLOW_EXPERIMENTAL`：默认 `false`，防止候选模板误入实时生产；
- `MODEL_GATEWAY_KEY`：若未设置 `WPK_LLM_API_KEY`，可复用此环境变量。

实时复核请求只包含匿名座位、本人底牌（非观战时必须有）、该节点完整公共牌、合法动作、下注状态、
整手牌截至该节点的匿名完整行动线、
位置/码深条件化的贝叶斯机会率、相对当前牌池偏移及收缩后的对手翻前范围摘要；
画像复核包含匿名目标、聚合统计、本地计算的 donk/probe/尺度模式，以及筛选后的公开亮牌
案例；案例保留实际 hole cards、逐街 board、SPR、pot odds、IP/OOP、PFA 状态和实际下注占池比。
不会直接发送高噪声 raw events。两者都不会发送昵称、稳定
user ID、hand ID、Cookie 或认证信息。LLM 只看到 `seat_*`；返回后由服务端在本地映射回
当前昵称，因此看板显示玩家名而不是匿名座位号。
看板可选轻推理或重推理：两档都发送整手完整行动线；轻推理最多取 6 条关键对手证据，以 low effort
完成普通节点；重推理最多取 16 条证据，以 medium effort 在 20 秒预算内
交叉检查牌型、赔率、SPR、范围和对手偏移。自动复核固定走轻推理；手动复核使用当前所选
档位并锁定行动节点；服务端会按 `sequence + state_hash + hand_id` 回读冻结上下文，因此即使
牌局已推进或结束，迟到请求和结果仍可作为复盘保留。两档超时或网关失败时都会
明确标记并回退本地证据，不会把本地结果冒充 GPT‑5.6 结论。
通过校验的结构化调整会生成 `llm-bounded-exploit-v1` 范围：前端同时显示基线频率、
限幅后的频率及实际变化百分点，但仍只提供建议，不会自动点击牌桌。
系统会先计算牌型、听牌、pot odds、SPR 与对随机单手的中性 equity 基线；只有高确定性的
免费过牌、极端赔率和坚果牌节点才由 `local-rules-v1` 直接返回，其余复杂节点再调用 LLM。
如果当前行动事件没有带出可靠底牌，本地层仍会立即显示当前位置和行动线的简化全范围：
翻前用 169 格分段颜色显示每类牌的加注、跟注与弃牌混合频率，翻后按强价值、强顶对、弱踢脚顶对、
中弱摊牌价值、强听牌、弱听牌和空气七档展示动作混合；不会猜测具体底牌。离线
`public-solver-fit-v3` 会根据 2–9 人桌、在局人数、标准化位置、limper 人数、limped/SRP/3bet/4bet 底池、
牌面湿度、SPR 和街道动态调整频率；下注尺度同时约束 Money-EV，深码弱牌不会再因
未条件化的宽范围 equity 被建议全下。此时可另点 GPT‑5.6 按钮，根据当前
活跃对手的样本证据给出范围级调整。有底牌时，同一视图会突出当前手牌，并把本地/LLM 的
具体建议放在范围图上方。拟合依据包括 GTO Wizard 的公开翻后/多人池/ante 教学、
MIT 许可的 Greenline 6-max 图表，以及 TexasSolver 可复现的求解接口；未复制商业
solver 私有频率。该范围是可审计的 solver-inspired 基线，不是当前节点的精确 GTO 解。

翻牌后实时页还会通过 `GET /api/equity/current` 生成当前 board 的英雄范围权益曲线。英雄范围
按本人已捕获的翻前 raise/call/check 行动条件化；曲线把 169 类起手牌按对当前活跃对手范围的
底池权益从低到高排列，并标记本人实际手牌、范围均值及 P10–P90。对手有足够历史时使用其
贝叶斯范围，并优先采用已经通过时间外门控的逐街行动线后验；未通过门控时回退翻前范围，
缺少范围时再明确回退均匀随机牌。节点侧栏按底牌相对公共牌的贡献把后验拆成强价值、
边缘摊牌价值、私人听牌和空气；
只有最近动作为下注或加注时，空气和听牌才分别标为纯 bluff 与半 bluff 候选，不把候选误报为
确定意图。权益曲线会处理 board/手牌 blocker，但仍属于低延迟 Monte Carlo 近似，而不是当前
节点的精确 solver 解。
开启“观战实时范围”时同样计算当前行动者的范围；行动者会从对手集合中排除，未公开底牌时
只显示范围曲线和范围均值，不生成虚假的精确手牌标记。行动间隙或全下等待阶段即使没有
`live_decision`，接口也会从当前 `in_progress` 手牌快照选取本人或最近行动者继续生成曲线。
打开“显示所有在局玩家”后，牌桌九宫格下方以最多 3×3 的紧凑网格放置本人或当前行动者及
其余未弃牌玩家的独立权益小图；每张图使用该玩家自己的范围百分位轴，避免把不同范围错误
叠线比较。行动线 Log 默认折叠，可按需展开，并在实时刷新时保留当前展开状态。

每次实时节点会把匿名 `inference-context-v1` 冻结到 SQLite；推理运行另存模型、模板、
prompt/context hash、延迟和校验状态，不保存昵称、user ID 或认证信息。离线比较已注册模板：

```bash
wpk-recorder eval-templates --data-dir data \
  --template exact-action-v1 \
  --template exact-action-evidence-v2 \
  --depth deep --limit 50 --repeats 2
```

报告包含合法结构通过率、超时率、p50/p95、重复稳定性、行为一致率，以及可计算时的
Money-EV regret。玩家实际动作只作为行为参考，不被当作策略正确答案；候选模板必须通过
显式环境配置晋升，前端不提供任意 prompt 编辑。

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
- 有结算数据时，会校验扣除保险和基金字段后的玩家净输赢；绝对差额不超过 10
  视为正常抽水，超过 10 才标记为坏数据并排除统计；
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
  donk、标准 probe、延迟 cbet、连续开火、check-raise，以及面对进攻时弃牌/跟注/再加注的
  机会及其选择结果；
- `decision_states`：实时与回放共用的版本化行动状态、state hash、质量门控和逐玩家投入快照；
- `strategy_evaluations`：候选动作 EV、推荐混合、置信度及策略引擎版本的审计记录；
- `inference_contexts`：按行动点冻结的匿名、版本化 LLM context；
- `inference_runs`：模板/model/prompt/context hash、结构校验、延迟和统一 Advice 输出；
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
实时决策另使用统一的上下文 Dirichlet 模型估计
`P(fold/call/raise | 玩家, 街道, 位置, 人数, SPR, IP/OOP, 面对动作, 尺寸, 牌面)`；
模型不依赖“紧/松/凶/被动”预设类型，而是学习玩家相对同模式牌池先验的残差，并随近期样本衰减。
个人 residual 按“街道 × 是否面对下注”的时间外 log loss 独立门控；未通过的节点只使用上下文
牌池后验，避免一个玩家翻前预测有效就错误放开其全部翻后偏移。
每个响应画像还比较最近 90 分钟（不足时取最近行动）与此前长期分布，输出 Dirichlet 收缩后的
策略漂移和 Jensen-Shannon divergence。prequential 回测只在实际触发漂移的观察上比较
“近期混合后验”与长期玩家/牌池后验；至少 20 次且 log loss 提升超过 0.002 后，近期分布才以
最高 30% 权重有限混入所有下注尺寸响应曲线，否则仍仅作诊断。
昵称用于展示，聚合身份始终使用 user ID。

对手页可切换全部、普通德州和鱿鱼模式。点击玩家行可查看风格标签、完整机会率、按位置统计、
下注尺寸分布、带证据等级的漏洞/对抗建议，以及按位置和翻前行动线切换的 13×13 起手牌范围。
机会率按翻前、翻牌、转牌和河牌分组，每项同时显示机会数、样本等级、牌池均值、后验偏移及
80% 区间；红色上箭头表示高于牌池，蓝色下箭头表示低于牌池。中高置信且区间已经越过牌池
基准的指标会高亮并给出节点级剥削提示；低样本即使数值偏移很大也只标记“待验证”，避免把
偶然的 0%/100% 当作稳定漏洞。

翻后名称按行动线严格区分：`DONK` 是非翻前主动者在主动者尚未行动、且主动权没有在前街被
放弃时领先下注；`PROBE` 只指翻前主动者在上一街过牌到底后，防守者在转牌或河牌率先下注；
`延迟 CBET` 指翻前主动者翻牌过牌后于转牌下注；`继续开火/三枪` 要求主动者在前街已有下注。
“面对转牌加注弃牌”表示玩家已经面对转牌二次进攻时选择弃牌，不是“弃牌并转牌加注”。
面对各街进攻的弃牌、跟注和再加注共享同一机会口径，三项可直接组成响应频率拆分。

列表支持按昵称或 user ID 搜索，并按手数、亮牌数、净输赢、VPIP 或昵称排序；每名玩家的
“亮牌牌谱”按钮会切换到历史页，只列出该玩家公开过底牌的手牌。
位置和下注前上下文从牌局状态确定性派生；旧记录缺少按钮位时会标记为 `Unknown`，不会猜测。
“LLM 深化画像”只在手动点击时调用远程接口，负责复核和解释本地事实，不生成或修改范围数字。
每项漏洞必须引用牌池偏移、离线模式或具体匿名案例；离线层严格区分 PFA 行动前 donk 与
PFA 过牌后的 probe，并把亮牌样本中的追听牌、听牌进攻、一对大尺度和强牌小尺度作为候选信号。
这些亮牌模式带有摊牌选择偏差，只能与全样本统计交叉验证，不能由单手牌直接定性。

历史牌谱通过 `GET /api/hands` 分页读取，不再受实时快照最近 30 手的上限限制，并可切换
“全部牌谱/仅看有效”或继续加载更多。分页接口支持按牌局/玩家关键词、普通德州/鱿鱼、
本人盈亏、最深街道及是否有对手公开底牌组合筛选；每手会显示开局快照中的鱿鱼持有数量，以及
`squidGameNotify scene=3` 确认的本手鱿鱼获得者。无相关事件时不猜测归属。

## 范围推断与回测

`GET /api/players/{user_id}/range-at-node` 提供实时与复盘共用的单玩家节点响应。未传 `hand_id`
时读取当前进行中手牌；传入 `hand_id` 时按手牌状态由服务端判定实时或历史能力，不能把进行中
手牌伪装成历史请求绕过策略。响应冻结 `hand_id + sequence + state_hash`，包含 169 类矩阵、
翻前基线与行动线后验的牌力分布/位移、尺度响应曲线、样本、门控、限制及当前服务端策略。
前端会并行请求 `phase=preview` 与完整结果：预览先应用个人/牌池亮牌行动频率的保守收缩，
并明确标为仅展示；行动响应与三组历史门控独立加载，完整统计后验返回后自动替换。公开行动
记录和全局门控按数据版本缓存，门控只回测最近 160 个有亮牌的手牌，避免历史增长阻塞看板。
已弃牌玩家冻结到其最后一次行动和当时可见公共牌；活动玩家固定到请求节点。若调用方携带的
`state_hash` 已过期，接口返回 409；计算期间节点变化则显式返回 `node.stale=true`。
服务端按 `state_hash + user_id + model_version + policy` 缓存，当前手始终从训练查询中排除。

矩阵格显示该起手牌类在当前 169 后验范围内、扣除公共牌和已知手牌 blocker 后的归一化概率
（全矩阵合计约 100%），颜色表示相对权重，核心 80% 以外的类别降色；右上角的 57.8% 一类数字仅是翻前进入该行动线的频率，
不再称为“当前范围宽度”。这些值不是确切底牌识别，也不输出花色组合级伪精度。逐手时间外
门控优先使用精确路径与尺度；高维层未过门但更粗的 action-family 层已验证时，会明确回退到
该粗层继续按每街 Action 更新。所有统计层都未过门时，节点面板使用明确标为低置信度的
`board_action_heuristic`，按每街牌面、check/call/bet/raise 和尺度做保守结构回退；它绝不把
`preview_weights` 冒充正式概率，也不会进入 Money-EV。
范围构成中的左值是“该玩家翻前历史范围投射到当前牌面”，右值是在此基础上再加入本手截至
节点的逐街 Action 后验；两侧都按底牌相对公共牌的实际增益分类。面板会明确标注个人公开行动
样本是否实际参与、主要回退牌池，还是
统计门控未通过而只显示结构预览。升权使用暖色箭头，降权使用冷色箭头，不代表好坏评价。
公共牌和已知本人底牌作为 blocker 参与牌力分布；观战且没有可靠本人底牌时不会猜 blocker。

实时建议中的 169 格翻前基线使用 `public-solver-fit-v4`。范围宽度按桌人数、位置、总前注和
有效码深调整；范围形状额外区分同花与非同花：8/9 人深码优先同花 Ax、同花大牌和可实现权益
的连张，削减容易被支配的边缘非同花牌。若桌上有足量 3bet 后验显示持续高压，会同时收窄
边缘开池并削减难以承受再加注的低同花连张，而不是把“松凶局”等同于无条件多玩投机牌。
鱿鱼模式只在轮次状态确认本人尚无鱿鱼时扩大范围；剩余无鱿鱼玩家不超过三人时提高压力，
但扩张优先高牌、阻断牌和同花 Ax，不会平推所有同花垃圾牌。该形状参考公开 8/9 人深码、
ante cash 与 stand-up/squid 资料做透明启发式拟合，不冒充对应房间、尺度和奖励参数的精确解。

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
本地样本量硬上限约束。画像调用强制使用 JSON 响应模式；通过校验的结果按玩家、模式、位置、
行动线和上下文 hash 写入 SQLite 的 `profile_analyses`，刷新或重启后由
`GET /api/players/{user_id}/profile/analysis` 自动恢复。新增样本导致上下文变化时，旧结果
会标记为历史快照；再次点击深化画像才会生成并保存新上下文结果。前端可以展开查看实际发送的
偏移与案例。

牌谱详情会按玩家和街道输出粗粒度牌力分布、预测 equity、训练标签数、同类行动线样本数
和置信等级。模型使用位置、IP/OOP、单挑/多人池、下注尺寸、有效码深、公共牌纹理、
鱿鱼持有数、此前累计盈亏及跨街行动线；玩家样本会与总体池样本收缩融合。实时范围从
169 类翻前后验开始，按每街观察到的 check/call/bet/raise 行动线，以公开摊牌学得且受限幅的
牌力 likelihood ratio 逐步更新；保留座位与玩家身份，不会把多人范围按宽度重排。每次更新
同时输出前后牌力分布、变化最大的起手牌类和范围熵，方便审计行动线究竟把哪些组合上调或下调。
尺寸 tell 被分解为 `P(action | strength) × P(size | action,strength)`，避免把行动本身的预测力
误算成尺寸价值。任一尺寸层至少 12 条训练观察且包含两个尺寸桶时才生成强收缩 preview；
精确路径不足时依次回退到“动作家族 × 尺寸”“街道 × 尺寸”、纯行动路径和动作家族。
尺寸门控直接比较“行动+尺寸”与“仅行动”，只有自身至少 20 个时间外观察且增量 log loss
确有提升才进入实时 EV。翻前路径细分仍需要至少 60 条同类观察，防止高维交叉特征过拟合。

激进行动的每个候选尺寸还会为最可能继续的具体对手构造 `call ∪ raise` 条件范围，而不是统一
扣减 hero equity。模型把当前 169 类范围按公开牌力下的 call/raise likelihood 重新加权，并输出
条件前后牌力分布、范围熵、变化最大的牌类、预期加注人数和至少一人加注概率。
`continuation_range_gate` 按街道比较学习模型与旧的通用继续范围收紧代理；至少 20 个时间外
观察且 log loss 提升后才替换旧启发式。尺寸本身仍需额外超过纯行动条件模型，否则各尺寸共享
同一个已验证继续范围。即使门控通过，范围 likelihood ratio 也不会满额应用：实际权重由样本数
和时间外增益共同决定并封顶 75%，避免二十余个边缘样本直接重写整个 caller range。
用于显式再加注分支时，call 与 raise 还要各自在至少 12 个时间外公开底牌样本上同时超过
牌力先验和旧继续范围启发式，并分别计算应用权重；综合门控通过并不会自动授权稀疏的 raise
子模型。如果综合 `call ∪ raise` 未通过、但 call 子模型单独通过，普通被跟注 EV 仍可只启用
已验证的 call range，同时保留再加注风险罚分。
call 与 raise 现在分别重加权范围，而不是共享一个笼统的继续范围。系统也会从有效的非 all-in
raise 记录学习 raise-to 倍数（过滤小于 1.5x 或大于 6x 的异常值），在对数空间按相似上下文
收缩到牌池，再分别对“牌池尺寸优于固定默认值”和“玩家 residual 优于牌池”做逐手时间外门控。
只有 call range、raise range 和 raise size 同时通过门控，候选动作才显式展开
fold / call / re-raise 三分支，并在被加注后比较 hero fold 与 call 的增量 EV；否则继续使用原有
有上限的 `raise_risk_penalty`，不会让稀疏的 raise 样本伪装成精确博弈树。

多人底池的“所有人弃牌”不再强制等于各玩家弃牌率相乘。记录器从历史 bet/raise 后的完整响应
序列提取真实 all-fold 标签，按街道、剩余对手数、进攻类型和尺寸建立 Beta 收缩后验，再把
经验联合概率与边际独立概率的差异转换成有界 log-odds 校准。该校准按“街道 × 对手数桶”
分别做时间切分 log loss 与 Brier 双门控；未通过的节点严格回退到独立模型。

`GET /api/inference/backtest` 使用 leave-one-hand-out，返回 Top-1、multi-class Brier score
和 log loss，并附带“前 70% 预热、后 30% 逐手先评分再学习”的 `action_line_gate`。只有行动线模型在至少
20 个时间外观察上取得更低 log loss，对应特征层的隐藏范围更新才允许进入 Money-EV；未通过的
精确路径/尺度层先回退到已通过门控的 action-family 粗层，粗层也未通过才回退翻前/牌池先验。
响应还包含 `action_response_gate`，按街道和是否面对下注比较个体 residual
与纯上下文牌池模型的时间外 log loss/Top-1；数值层始终保持先验收缩，只有单节点达到
medium/high 后才生成显式剥削指令，回测报告用于继续校准偏移上限，而不是用样本内 HUD
命中率自证有效。`GET /api/players/{user_id}/context` 返回位置、码深、鱿鱼和盈亏状态下的
行动分布及公开摊牌牌力摘要。由于弃牌底牌不可见，摊牌样本存在明显选择偏差，因此更新强度
被收缩和限幅，不能用 Top-1 或短期输赢单独决定启用。
节点级 player residual 通过后，还会为每名达到 20 个时间外观察的玩家执行嵌套安全门：
前 12 个观察只决定 residual 与牌池模型的优劣方向，后续至少 8 个观察独立确认。只有两个
阶段都显示玩家模型持续更差时才对该玩家回退到牌池上下文；方向不一致时保留已通过节点级
门控且有先验收缩的 residual，避免小样本双向挑选反而降低时间外准确率。审计输出包含逐玩家
状态、确认段 log loss 和安全门相对原节点策略的增益。

前 70% 时间段只作为验证预热集；后 30% 按手执行 prequential 流程：先用此前数据预测并计分，
再把该手加入扩张训练窗。因此每条后段样本在评分时严格时间外，但标签出现后不会被永久浪费。
节点通过门控后，实时后验使用全部 `excluded_from_stats=0` 的已完成、质量合格牌谱重新估计。
当前尚未结束的手牌、坏牌谱和被排除手牌不会参与拟合，当前手的公开底牌也会按 `hand_id`
再次显式排除，避免目标泄漏。

## 实验性 Money-EV MVP 引擎

`GET /api/strategy/current` 会在本人行动节点上运行本地候选动作评估。算法支持 2–9 人、
不同码深和普通/鱿鱼模式；WPK 8/9 人、2/4 盲注、每人 ante 1 的深码鱿鱼局拥有额外规则
校准。其他配置使用同一后验接口和牌池回退，不再因为缺少鱿鱼校准而被无条件降为低置信。
引擎把结果拆成：

- `chip_ev`：当前牌局从决策点开始的增量筹码 EV；
- `squid_ev`：相对弃牌/过牌参考动作的鱿鱼轮次边际 EV；
- `money_ev`：只有从历史结算校准出每条鱿鱼价值后，才合并前两者；
- `robust_ev`：扣除范围、样本和激进行动的不确定性惩罚后用于排序的值。

最终建议不再固定取最高频动作。服务端会对当前频率策略生成一次 `0–100` 随机数，
按各动作的累计频率区间抽样，并返回随机数、命中区间、命中动作和最高频动作供审计；
看板用分段频率条和游标显示本次落点。同一 `sequence + state hash` 行动节点复用同一次
抽样，避免本地结果与稍后完成的 LLM 复核给出不同随机动作。

实时和回放都通过同一个不可变 `DecisionState` 构造器重建逐玩家筹码、街内/累计投入、
主边池、行动顺序和合法动作。嵌入式 `wpk-recorder run` 优先读取录制器内存快照；
后续行动到达即使旧建议失效，state hash 也用于拒绝迟到结果。每个行动点同时写入
`decision_states`，便于回放核对。
WPK 的 `minRaiseScore` / `maxRaiseScore` 表示本次新增投入，并非最终 raise-to；协议层会
加上当前街已投入额后再交给 EV 引擎。短码玩家不足常规最小加注时，若服务端只给出
`ALL_IN` 而没有 `RAISE`，会按合法的不足额全下处理，不再误报上下限冲突。

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
当前 `money-ev-dynamic-sizing-v13` 会比较约 30%、50%、75%、100% 和 125% 当前底池
（受合法最小/最大加注额约束）的加注候选，让按玩家/尺寸条件化且通过安全门的响应后验直接影响候选动作 EV。
当最终动作是加注时，看板会显示动态 raise-to、各候选的全员弃牌率和稳健 EV，并列出在场选手
对该尺度的预计弃牌/跟注/再加注概率及有效样本；个人模型未通过门控时明确回退到相似牌池后验或通用基线。
在行动线范围模型通过时间外门后使用逐街后验，并仅在独立子门控全部通过时展开再加注分支；
多方 Monte Carlo 和未来街仍是深度受限近似，
不是 8/9 人深码 GTO solver，也不会自动点击。WPK 对这类实时辅助的公开政策目前未得到项目内
可核验材料确认；默认模式保持兼容不代表平台允许。应由操作者先确认房间规则，并只在所有参与者
知情、平台明确允许的环境中启用 `operator_approved_live`；否则使用 `post_session`、
`static_history` 或 `capture_only`。不能用短期输赢替代校准误差、概率校准和状态正确率。

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
