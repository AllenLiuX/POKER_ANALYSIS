# 德州扑克进阶学习网站 — 架构与实施文档

> 版本：v0.1（讨论稿）
> 目标读者：开发者 / 产品决策者
> 定位：面向公众的 **进阶** 德州扑克决策训练产品（不是入门规则教学）
> 配套文档：[`ALGORITHMS.md`](./ALGORITHMS.md) — 翻后求解、剥削训练、LLM 集成的算法设计
> 配套文档：[`SCREENSHOT_IMPORT.md`](./SCREENSHOT_IMPORT.md) — WePoker 截图导入、近似牌谱重建与逐人剥削

---

## 1. 产品定位与核心理念

**一句话**：帮助已经会玩德州扑克的牌手，通过大量"决策 + 即时反馈"的刻意练习，把翻前/翻后的决策向数学最优（GTO / 高 EV）靠拢。

**入门产品 vs 进阶产品的根本区别**：

- 入门讲**规则**（怎么玩、牌型大小）。
- 进阶讲**决策**（这手牌在这个位置、面对这个动作，fold / call / raise 哪个 EV 最高，为什么）。

因此产品的核心不是"看视频/读文章"，而是一个**量化决策训练闭环**：

```
生成场景 → 用户决策 → 对照权威答案打分 → 用 equity/概念解释"为什么" → 记录进度 → 针对薄弱环节再训练
```

---

## 2. 一个必须先讲清楚的现实判断（决定产品可信度）

训练器要反馈"你的决策对不对"，就必须有一个**权威答案来源**。翻前和翻后差别巨大：

| 阶段 | 权威答案可得性 | 本产品的策略 |
|------|----------------|--------------|
| **翻前 (Preflop)** | 成熟。GTO 范围可用数据表精确表达（RFI / 3bet / vs-3bet 等）。 | **做到高质量、可信**。用公开开源近似范围，UI 明确标注来源。 |
| **翻后 (Postflop)** | 困难。精确 GTO 需实时求解器，开源数据极少且体量巨大。 | **启发式规则近似** + 少量预计算经典场景。UI 明确标注"这是启发式建议，非精确 GTO"。 |

> **诚实原则**：完全精确的 GTO 范围通常是商业产品（如 GTO Wizard）的专有数据。本产品使用**公开可得的开源近似范围**，并在界面上清晰标注数据来源与近似程度，避免版权问题和对用户的误导。

**结论**：MVP 把翻前训练器做到极致、可信；翻后用透明的启发式引擎覆盖最高频场景（c-bet、bluff/value、防守 MDF），并诚实标注其性质。

---

## 3. 功能范围

### 3.0 功能优先级（重要）

- **核心（平台立身之本）**：翻前/翻后**决策训练器** + **GTO 引擎** + **剥削训练**。绝大部分工程与打磨投入应在这里。
- **辅助 / 差异化亮点**：WePoker **截图导入与逐人剥削**。是很强的差异化，但**依赖核心引擎就绪**，排在后置阶段，勿喧宾夺主。
- **支撑**：账号/进度、范围表、胜率工具。

> 判断准则：任何取舍优先保证"训练闭环 + GTO 可信度"的体验；截图功能不得拖慢或复杂化核心。

### 3.1 MVP（第一个可上线版本）

1. **翻前决策训练器**
   - 随机生成场景：牌局类型（6-max 现金 100bb）、英雄位置、对手前置动作、英雄手牌。
   - 用户选择 fold / call / raise(size)。
   - 对照 GTO 范围即时打分（考虑混合频率），用 equity 数据 + 概念标签解释"为什么"。
2. **翻后启发式训练器**
   - 生成翻牌/转牌/河牌场景（含 board、底池、位置、假定对手范围）。
   - 启发式引擎给出建议动作与尺度（c-bet 频率、bluff-to-value、MDF 防守）。
   - 打分 + 解释 + 明确标注"启发式近似"。
3. **交互式范围表**
   - 13×13 网格，按位置 / 动作查看标准 GTO 范围，单元格显示动作频率配色。
4. **账号 + 进度追踪**
   - 注册/登录（Supabase Auth）。
   - 记录每次练习：场景、用户选择、是否正确、EV 损失。
   - 仪表盘：整体正确率、按场景类别的强弱分析、错题本雏形。
5. **胜率 / 赔率小工具**
   - Pot odds、所需胜率、你的手牌/范围对对手范围的胜率（Monte Carlo）。
   - 既是独立工具，也是训练器反馈的底层支撑。
6. **WePoker 截图导入与逐人剥削**（详见 [`SCREENSHOT_IMPORT.md`](./SCREENSHOT_IMPORT.md)）
   - 上传对局截图 → 多模态提取 → 引擎约束重建为**近似牌谱**（含置信度 + 用户确认/编辑）。
   - 标注自己与对手相对 GTO 的偏离，聚合成对手画像。
   - 用 LLM（接地在引擎/求解器输出）对每个对手给出针对性剥削建议。

### 3.2 第二阶段（Post-MVP）

- 手牌历史导入（PokerStars / GG 格式）与逐街回放，标注每个决策点 EV 得失。
- 更细的翻后场景库、转牌/河牌 barreling 训练。
- 概念知识库（MDF、极化/线性范围、blocker、range advantage 等结构化讲解）。
- 数据分析看板：漏洞检测（如"你 BTN 面对 3bet 弃牌过多"）。
- 社交/排行榜、每日挑战、连续打卡。

### 3.3 明确不做（至少短期）

- 真人对战 / 多人牌桌服务器（非学习核心，工程量巨大）。
- 声称"精确 GTO"的翻后求解（无开源数据支撑，会误导用户）。
- 真钱相关的任何功能。

---

## 4. 扑克引擎设计（产品的技术壁垒）

所有扑克逻辑放在后端独立模块 `poker/`，与 Web 层解耦，便于单元测试。

### 4.1 牌与手牌评估

- 用 `eval7`（C 实现，快速）做 7 张牌手牌强度评估与蒙特卡洛胜率。
- 备选/补充：`pokerkit`（完整牌局状态机，用于未来手牌回放）。
- 手牌表示：标准 `As`, `Kd`, `Th` 等；范围用 `eval7` 的范围语法（`AA, AKs, QJo+, ...`）。

### 4.2 翻前范围引擎 `poker/preflop/`

**数据模型**——范围以结构化 JSON 存储，键为场景上下文：

```jsonc
// data/ranges/6max_100bb/rfi/CO.json
{
  "meta": { "format": "6max", "stack": 100, "position": "CO", "action": "RFI",
            "source": "open-source approximation", "notes": "..." },
  "hands": {
    "AA":  { "raise": 1.0, "call": 0.0, "fold": 0.0 },
    "ATs": { "raise": 1.0, "call": 0.0, "fold": 0.0 },
    "A5s": { "raise": 0.75, "call": 0.0, "fold": 0.25 },  // 混合频率
    "K9o": { "raise": 0.0,  "call": 0.0, "fold": 1.0 }
    // ... 169 个手牌类别
  }
}
```

- 覆盖的场景类别（MVP）：RFI（各位置开池）、vs RFI（call / 3bet）、vs 3bet（fold / call / 4bet）。
- 169 手牌类别 = 13 对子 + 78 同花 + 78 非同花的归并表示。

**打分逻辑**（处理混合策略是关键）：

- 纯策略区（某动作频率 ≥ 0.9）：用户选对=满分，选错=按"该动作的 EV 损失"扣分。
- 混合策略区（多个动作都有可观频率）：用户选中任一"高频动作"都算基本正确，给出频率提示（"这手牌 GTO 上 75% raise / 25% fold，两者都可接受，raise 略优"）。
- 反馈附带该手牌 vs 对手范围的 equity，把抽象频率翻译成用户能理解的"为什么"。

### 4.3 翻后启发式引擎 `poker/postflop/`

> 透明、可解释的规则引擎；不假装是求解器。
> **注**：启发式是 MVP 过渡方案与缓存兜底。翻后的最终目标是**离线预计算真 GTO**（求解器 + node-lock 剥削 + LLM 接地教练），完整算法设计见 [`ALGORITHMS.md`](./ALGORITHMS.md)。

- **Board texture 分类**：dry / wet、paired、monotone / two-tone / rainbow、connected 程度、high-card vs low-card。
- **英雄手牌分类**：made-hand 强度（value / marginal / air）+ draw 类型（flush draw / OESD / gutshot / combo draw），通过 `eval7` 评估当前强度 + 对假定对手范围的 equity。
- **决策启发式**：
  - Range advantage → c-bet 频率与尺度（例如 range 优势方在干燥面高频小注）。
  - Bluff-to-value 比例（按下注尺度对应的赔率给出理论 bluff 组合数）。
  - 防守用 **MDF**（Minimum Defense Frequency）与 pot odds 判断 call/fold 门槛。
- **打分**：把用户动作与启发式建议对比，容忍合理区间；解释里给出 equity、MDF、赔率等数字，并标注"启发式近似"。

### 4.4 胜率 / 赔率引擎 `poker/equity/`

- 蒙特卡洛（或已知 board 时精确枚举）计算：手牌 vs 手牌、手牌 vs 范围、范围 vs 范围。
- 输出：胜/平/负概率、equity 热力图数据。
- Pot odds、所需 equity、call 的 EV：`EV_call = equity * (pot + bet) - (1 - equity) * bet`。

### 4.5 场景生成器 `trainer/scenario.py`

- 输入：场景类别（可按用户薄弱项加权抽样）、难度。
- 输出：完整场景对象（位置、盲注、筹码、动作序列、英雄手牌、可选 board）。
- 保证生成的场景在对应范围数据中有权威答案可查。

---

## 5. 技术架构

```
┌────────────────────────┐        HTTPS/JSON        ┌───────────────────────────┐
│  前端 (React + TS)      │  ───────────────────────▶ │  后端 (FastAPI, Python)    │
│  Next.js + Tailwind     │                           │  ├─ api/       路由         │
│  (Vercel 部署)          │  ◀─────────────────────── │  ├─ poker/     扑克引擎     │
│  TanStack Query 数据层  │                           │  │   ├─ eval, equity        │
│  扑克桌/范围网格组件    │                           │  │   ├─ preflop (ranges)    │
└────────────────────────┘                           │  │   └─ postflop (heuristic)│
                                                      │  ├─ trainer/   场景+打分    │
                                                      │  ├─ ingest/    截图→牌谱重建 │
                                                      │  ├─ llm/       provider 抽象 │
                                                      │  └─ db/        Supabase 客户端│
                                                      └──────┬─────────────┬────────┘
                                                             │             │
                                                   ┌─────────▼──┐   ┌──────▼─────────┐
                                                   │  Supabase  │   │  LLM 网关       │
                                                   │  PG + Auth │   │  model_client   │
                                                   │  + Storage │   │  → OpenAI 兜底  │
                                                   └────────────┘   └────────────────┘
```

### 5.1 后端技术选型

| 关注点 | 选择 | 理由 |
|--------|------|------|
| Web 框架 | **FastAPI** | 现代、异步、自动生成 OpenAPI 文档、类型友好 |
| 扑克评估 | **eval7** | C 实现、快、支持范围语法与蒙特卡洛 |
| 牌局状态（未来回放） | **pokerkit** | 完整规则状态机 |
| 数据库 + 认证 | **Supabase**（托管 PostgreSQL + Auth + Storage） | 复用已验证资源，省掉自建 DB / 手写 JWT；截图走 Storage |
| DB 访问 | `supabase-py` / SQLAlchemy（按需） | 直连或 ORM |
| 数据校验 | **Pydantic v2** | 与 FastAPI 天然集成 |
| **LLM** | **`model_client`（公网网关，首选）→ OpenAI（env 兜底）** | 见 §5.4；视觉走 gemini/gpt-4o，文本走 gpt-5.6-sol |
| 测试 | **pytest** | 引擎逻辑必须有充分单测 |
| 包管理 | **uv** 或 pip + `requirements.txt` | 快速、可复现 |

### 5.2 前端技术选型

| 关注点 | 选择 |
|--------|------|
| 框架 | **Next.js**（React + TypeScript，Vercel 部署） |
| 样式 | Tailwind CSS |
| 数据层 | TanStack Query |
| 认证 | Supabase Auth（`@supabase/supabase-js`） |
| 状态 | 轻量（Context / Zustand，按需） |
| 图表 | Recharts（进度仪表盘 / 剥削看板） |

### 5.4 LLM Provider 层（复用现有资源）

优先使用**公网可用**的统一网关模块 `model_client`（gpt-5.6-sol / gemini / gpt-4o），失败再 fallback 到 env 里的**个人 OpenAI**。详细路由与截图解析用法见 [`SCREENSHOT_IMPORT.md`](./SCREENSHOT_IMPORT.md) §4。

| 任务 | 首选（model_client） | Fallback（OpenAI env） |
|------|----------------------|------------------------|
| **视觉**（截图解析） | `gemini-flash → gemini-pro → gpt-4o` | `gpt-4o` |
| **文本**（重建/剥削/教练/讲解） | `gpt-5.6-sol` | `gpt-5.x` |

- ⚠️ `gpt-5.6-sol` **不支持视觉**（`supports_vision=False`），截图解析必须走 gemini/gpt-4o。
- `model_client` 公网走 `network="office"`。
- **密钥治理**：网关 AK / OpenAI key 一律进 `.env`（`.gitignore` 忽略），代码从环境变量读，**任何密钥不进 git**。
- **铁律**：数字/判定的真相来源是引擎/求解器，LLM 只负责解析与解释，不参与判分。

### 5.3 目录结构（建议）

```
poker_analysis/
├── docs/
│   ├── ARCHITECTURE.md
│   ├── ALGORITHMS.md
│   └── SCREENSHOT_IMPORT.md
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/            # 路由：trainer, ranges, equity, progress, ingest, opponents
│   │   ├── poker/          # 纯扑克引擎（无 Web 依赖，可独立测试）
│   │   │   ├── cards.py
│   │   │   ├── evaluate.py
│   │   │   ├── equity.py
│   │   │   ├── preflop/
│   │   │   └── postflop/
│   │   ├── trainer/        # 场景生成 + 打分 + 解释
│   │   ├── ingest/         # 截图→观测事实→引擎约束重建→偏离标注
│   │   ├── llm/            # model_client(vendored) + provider 抽象 + config
│   │   ├── db/             # Supabase 客户端 / 数据访问
│   │   └── schemas/        # Pydantic 模型
│   ├── data/ranges/        # 开源范围 JSON
│   ├── tests/
│   ├── requirements.txt
│   ├── .env.example        # 变量名占位，无真实密钥
│   └── README.md
├── frontend/               # Next.js（Vercel）
│   ├── app/                # App Router 页面：trainer, ranges, dashboard, import, opponents
│   ├── components/         # PokerTable, Card, RangeGrid, ActionBar, FeedbackPanel, HandReplay
│   ├── lib/                # 后端调用 + supabase client
│   ├── package.json
│   └── README.md
├── .gitignore              # 忽略 .env / 密钥
└── README.md
```

---

## 6. 数据模型（Supabase / PostgreSQL）

> 用户与认证由 **Supabase Auth** 提供（`auth.users`），业务表通过 `user_id` 关联。
> 截图导入相关表（`uploads` / `parsed_hands` / `opponents` / `opponent_profiles` 等）见 [`SCREENSHOT_IMPORT.md`](./SCREENSHOT_IMPORT.md) §5。

```
users                           # 业务侧用户档案（关联 Supabase auth.users）
  id (uuid, pk)   → auth.users.id
  display_name
  created_at

practice_sessions
  id (uuid, pk)
  user_id (fk)
  mode           # 'preflop' | 'postflop'
  started_at
  ended_at

attempts                      # 每一次决策
  id (uuid, pk)
  session_id (fk)
  user_id (fk)
  spot_category  # 'RFI' | 'vs_RFI' | 'vs_3bet' | 'cbet' | 'defense' ...
  scenario_json  # 完整场景快照（便于复盘）
  user_action    # 'fold'|'call'|'raise'  (+ size)
  correct        # bool
  ev_loss        # float，偏离最优的 EV 损失（bb）
  created_at

user_stats  (物化/聚合，或用查询实时算)
  user_id, spot_category, attempts, correct, avg_ev_loss  → 驱动"薄弱项"分析
```

---

## 7. API 设计（草案）

```
# 认证：注册/登录由 Supabase Auth (前端 @supabase/supabase-js) 处理
# 后端用 Supabase JWT 校验，从 token 取 user_id
GET  /api/me                     → 当前用户档案

# 训练
POST /api/trainer/next           {mode, categories?, difficulty?} → 场景（不含答案）
POST /api/trainer/answer         {scenario_id, action, size?}     → {correct, ev_loss, explanation, chart_freqs, equity}

# 范围表
GET  /api/ranges                 → 可用范围索引
GET  /api/ranges/{format}/{action}/{position}  → 169 网格 + 频率

# 工具
POST /api/equity                 {hero, villain_range, board?} → {win, tie, lose, equity}
POST /api/potodds                {pot, bet}                    → {required_equity, ...}

# 进度
GET  /api/progress/summary       → 总体正确率、各类别强弱
GET  /api/progress/mistakes      → 错题本

# 截图导入与剥削（详见 SCREENSHOT_IMPORT.md §6）
POST /api/ingest/upload          → {upload_id}
GET  /api/ingest/{upload_id}     → 解析进度与结果
POST /api/ingest/{hand_id}/confirm → 提交修正后的近似牌谱
GET  /api/opponents/{id}         → 对手画像 + 剥削建议
```

**安全要点**：
- `/trainer/next` 返回的场景**不包含答案**；打分在 `/trainer/answer` 服务端完成，避免前端泄露正确答案。
- 认证走 Supabase JWT；后端校验 token 后从中取 `user_id`，不自建密码体系。
- 所有密钥经 `.env` 注入，不硬编码、不入 git。

---

## 8. 前端关键组件

- **PokerTable**：椭圆牌桌，6 个座位，显示位置标签、筹码量、盲注、当前动作高亮、英雄底牌。
- **Card**：SVG/图片牌面渲染，四色牌可选。
- **ActionBar**：fold / call / raise 按钮 + 下注尺度选择（½ pot、¾ pot、pot、all-in）。
- **FeedbackPanel**：对/错、EV 损失、GTO 频率条、equity 数字、概念解释、"启发式近似"标注。
- **RangeGrid**：13×13 交互网格，按动作频率配色（raise 红 / call 绿 / fold 灰的渐变），hover 显示明细。
- **ProgressDashboard**：正确率趋势、各场景类别雷达/柱状图、错题列表。

---

## 9. 开发路线图

**Phase 0 — 脚手架（地基）**
- 后端 FastAPI + 前端 Next.js 跑通，一个 `/health` 与前端首页。
- 建立 `poker/` 模块骨架与 pytest；`.env.example` + `.gitignore`（密钥治理）。
- Supabase 项目（新建）+ LLM provider 层（model_client vendored + OpenAI fallback）打通冒烟。

**Phase 1 — 扑克引擎（核心，先测试后接 Web）**
- 牌评估 + equity（eval7 封装）+ 单元测试。
- 翻前范围数据结构 + 至少一套完整 6-max 100bb 开源近似范围。
- 翻前打分与解释逻辑（含混合频率）。

**Phase 2 — 翻前训练器闭环**
- `/trainer/next` + `/trainer/answer` + 场景生成。
- 前端 PokerTable + ActionBar + FeedbackPanel，完成"练一把→反馈"闭环（先不登录也能玩）。

**Phase 3 — 范围表 + 工具**
- RangeGrid 页面；equity/potodds 小工具页面。

**Phase 4 — 账号 + 进度**
- Supabase Auth 接入、attempts 记录、Dashboard 与错题本。

**Phase 5 — 翻后启发式训练器** ✅（首版：HU 单加注底池·翻牌）
- board texture / 手牌分类 / 启发式决策（c-bet 频率尺度、MDF、赔率、bluff-to-value）+ 打分 + 前端 board 展示（`poker/postflop/`、`/api/trainer/postflop/*`、`/trainer/postflop`）。
- 对手范围取自翻前数据（加注方开池范围 / 跟注方防守跟注范围），equity 用蒙特卡洛估算。
- 后续：转牌/河牌 barreling、更多下注尺度与位置、离线预计算真 GTO 替换启发式。

**Phase 6 — 截图导入与逐人剥削**（详见 [`SCREENSHOT_IMPORT.md`](./SCREENSHOT_IMPORT.md) §9）
- 截图提取 → 引擎约束重建 → 用户确认 → 偏离标注 → 对手画像 + LLM 剥削建议。

**Phase 7 — 打磨 + 部署**
- UI 打磨、空/错状态、部署（前端 Vercel，后端 EC2 + Supabase）。

> 说明：截图导入是承诺的 MVP 功能，但依赖 LLM 层（Phase 0）与翻前引擎（Phase 1）就绪，故排在 Phase 6；可与 Phase 3~5 并行推进其独立部分（提取/重建）。

---

## 10. 关键风险与取舍

1. **GTO 数据的准确性与版权** — 用开源近似并明确标注；不声称精确 GTO。翻后诚实标注"启发式"。
2. **混合策略打分的用户体验** — 必须解释"多个动作都对，只是频率不同"，否则进阶用户会觉得系统错了。
3. **翻后启发式的可信度** — 通过透明展示规则依据（MDF、range advantage、赔率）建立信任，而非黑箱评判。
4. **性能** — 蒙特卡洛 equity 可能较重；缓存常见场景结果，或用足够小的模拟次数 + 精确枚举已知 board。
5. **范围数据的录入成本** — 169×多场景的手工录入量大；考虑用脚本从开源图表数据批量生成/校验。
6. **密钥安全** — LLM 网关 AK / OpenAI key / Supabase key 一律进 `.env`（不入 git）；已公开粘贴过的 key 建议轮换。
7. **截图重建的近似性** — 见 [`SCREENSHOT_IMPORT.md`](./SCREENSHOT_IMPORT.md) §10（引擎校验 + 置信度 + 用户确认兜底）。

---

## 11. 待确认 / 后续讨论

- 具体开源范围数据来源（需评估许可与质量）。
- 是否支持除 6-max 100bb 现金局外的其它牌局类型（MTT/短筹码 push-fold 等）。
- 部署与域名、是否需要邮箱验证/找回密码等账号完整性功能。
