# 截图导入：WePoker 牌谱截图 → 近似牌谱 → 偏离标注 → 逐人剥削

> 版本：v0.1（讨论稿）
> 配套文档：[`ARCHITECTURE.md`](./ARCHITECTURE.md)、[`ALGORITHMS.md`](./ALGORITHMS.md)
> 定位：**MVP 功能之一**。把 WePoker（微扑克）对局截图，转成可分析的近似牌谱，标注自己与对手相对 GTO 的偏离，并用 LLM 对每个对手给出针对性剥削建议。

> ⚠️ **优先级说明**：本功能是**差异化亮点 / 辅助功能**，**不是平台核心**。平台核心是**决策训练器 + GTO 引擎 + 剥削训练**（见 [`ARCHITECTURE.md`](./ARCHITECTURE.md) §3、[`ALGORITHMS.md`](./ALGORITHMS.md)）。本功能依赖核心引擎就绪，排在路线图 Phase 6，勿喧宾夺主。

---

## 0. 为什么这是杀手级功能

- WePoker 多用于私局 / 俱乐部，玩家常和**固定牌友**长期对战。
- 本功能把"截图"沉淀成一个**个人化的对手剥削数据库**：截图越多，对每个固定对手的画像越准、剥削越狠。
- 它是 [`ALGORITHMS.md`](./ALGORITHMS.md) 里"自然语言读牌 → node-lock 剥削"链路的**自动化版本**——截图替用户生成对每个对手的读牌，无需手动描述。

---

## 1. 核心难点与设计原则

**好消息（实测真实截图）**：WePoker 的**手牌回放**截图（底部有 `x/x` 播放条）会**逐街标注每个玩家的动作与金额**（如"加注32 / 下注38 / 跟注188 / Allin941"），并给出每人净额——这类是**主路径且可靠**，几乎不需要歧义推理。见 §11 真实样例。

**难点（仅限次要情况）**：纯**结算**截图若只显示最终状态（例如多人都显示 "all-in"），真实下注序列才需要**推理重建**（可能是"一人 raise、一人再 all-in、另一人再 call"）。这是边缘情况，用引擎校验 + 用户确认兜底。

**设计原则**：**引擎给 LLM 的推理"上镣铐"**——LLM 负责视觉识别与序列提议，扑克引擎负责校验合法性与筹码守恒，凡不确定一律标注置信度并交用户确认。延续全局铁律：**数字/合法性的真相来源是引擎，不是 LLM。**

---

## 2. 四阶段流水线

```
截图(可多张)
   │  ① 多模态提取 (gemini/gpt-4o)：只抽"看得见的事实"
   ▼
观测事实 JSON {玩家/位置/筹码/底牌?/公共牌/底池/各人输赢/可见动作标签/截图类型}
   │  ② 引擎约束重建 (LLM 提议 + 扑克引擎校验)
   ▼
近似牌谱 (逐街 action 序列 + 每步置信度)
   │        ├─ 唯一合法解 → 高置信
   │        └─ 多解 / 信息不足 → 低置信，进入用户确认/编辑
   ▼
   │  ③ GTO 偏离标注：每个决策点对照范围表 / 解集
   ▼
标注 hero + 每个对手的偏离点（加权累积到该玩家画像）
   │  ④ 逐人剥削分析 (gpt-5.6-sol，接地在偏离数据 + node-lock)
   ▼
逐对手剥削建议 + 自己的漏洞复盘
```

### 阶段 ①：多模态提取（只抽"看得见的"）

- **模型路由**：`gemini-flash → gemini-pro → gpt-4o`（见 §4；`gpt-5.6-sol` 不支持视觉，不用于读图）。
- **原则**：只提取**可观测事实**，不做推理，降低幻觉。
- **WePoker 中文 UI**：识别 加注 / 跟注 / 全下 / 弃牌 / 过牌 / 大盲 / 小盲 / 底池 / 筹码量 等标签。
- **自动判别截图类型**：
  - `hand_replay`（手牌详情/逐步回放）：含逐步动作 → 提取可靠、置信高。
  - `result_summary`（结算画面）：仅最终状态 → 需重建、置信低。
- **输出**（观测事实 JSON，示意）：

```jsonc
{
  "screenshot_type": "result_summary",
  "board": ["Ts", "7h", "2d", "Kc", "3s"],   // 缺失用 null
  "pot": 210,
  "hero_seat": 3,
  "players": [
    { "seat": 1, "alias": "阿强", "position": "BTN", "stack_end": 0,
      "hole_cards": ["Ah", "Kd"], "net": -100, "visible_actions": ["all-in"] },
    { "seat": 3, "alias": "我",   "position": "BB",  "stack_end": 0,
      "hole_cards": ["Qs", "Qc"], "net": +210, "visible_actions": ["all-in"] }
  ],
  "extraction_confidence": 0.82,
  "notes": "结算画面，无逐步动作，需重建下注序列"
}
```

### 阶段 ②：引擎约束重建（LLM 提议 + 引擎校验）

- LLM 依据观测事实 + 扑克规则，**提议**逐街动作序列。
- 扑克引擎（`poker/` + `pokerkit` 状态机）作为**裁判**校验：
  - **筹码守恒**：Σ各人投入 = 底池；每人投入 ≤ 起始筹码。
  - **动作合法性**：下注/加注金额、行动顺序、位置逻辑合法。
  - **终局一致**：重建后各人筹码/输赢与观测吻合。
- 结果分级：
  - **唯一合法解** → 高置信，直接采用。
  - **多个合法解** → 取"扑克逻辑最可能"者，其余作为候选，标低置信。
  - **信息不足 / 无合法解** → 标记待确认，进入用户编辑。
- **人在环中（human-in-the-loop）**：解析结果渲染为可编辑的手牌回放，用户可修正位置/动作/金额/底牌后再进入分析。避免错误牌谱污染剥削结论。

```jsonc
{
  "street_actions": {
    "preflop": [
      { "seat": 1, "action": "raise", "amount": 6,  "confidence": 0.6 },
      { "seat": 3, "action": "3bet",  "amount": 20, "confidence": 0.5 },
      { "seat": 1, "action": "allin", "amount": 100, "confidence": 0.7 },
      { "seat": 3, "action": "call",  "amount": 80,  "confidence": 0.7 }
    ]
  },
  "reconstruction_status": "multiple_legal",   // unique | multiple_legal | needs_user
  "overall_confidence": 0.63,
  "engine_validated": true
}
```

### 阶段 ③：GTO 偏离标注

- 对重建牌谱的**每个决策点**，对照 GTO 基线：
  - 翻前 → 范围表（`data/ranges/`）。
  - 翻后 → 预计算解集 / 启发式（见 [`ALGORITHMS.md`](./ALGORITHMS.md)）。
- 标注 **hero 与每个对手**的偏离：偏离类型（over-fold / over-call / over-bluff / 跛入 / 尺度错误…）、方向、幅度（EV 损失估计）。
- 每条偏离带置信度（继承重建置信度，低置信偏离弱化权重，不污染画像）。

### 阶段 ④：逐人剥削分析（接地 LLM）

- **画像累积**：同一对手（按 alias / seat + 用户确认的身份）跨多手牌的偏离，聚合成统计倾向（fold-to-cbet、3bet%、跛入率、aggression…）。
- **剥削策略**：把倾向映射为 node-lock 约束 → 求解器/预计算得到剥削应对（见 [`ALGORITHMS.md`](./ALGORITHMS.md) §5）。
- **LLM 生成人话建议**（`gpt-5.6-sol`，接地在偏离数据 + 剥削解）：
  > "对『阿强』：翻前跛入过多且几乎从不 3bet（12 手样本），面对你的隔离加注弃牌 68%。→ 加大隔离尺度、扩大偷盲、河牌薄价值下注更激进、诈唬收敛。"
- 同时输出**用户自己的漏洞复盘**（hero 偏离聚合）。
- **保守原则**：小样本（<30~50 次某 spot 观测）只给"倾向提示"，不给激进剥削结论；剥削是稳健的 MinES，非满剥削（见 [`ALGORITHMS.md`](./ALGORITHMS.md) §5.2）。

---

## 3. LLM 分工（照全局铁律）

| 环节 | 谁负责 | 说明 |
|------|--------|------|
| 视觉识别（截图 → 事实） | **LLM（视觉）** | 只抽可观测事实 |
| 下注序列**提议** | **LLM（文本）** | 提议，不定论 |
| 合法性 / 筹码守恒 / 终局一致 | **引擎** | 唯一裁判，判定真相 |
| 手牌强度 / equity / EV / 偏离幅度 | **引擎** | 数字真相 |
| 剥削策略（node-lock 解） | **求解器/预计算** | 数字真相 |
| 人话建议 / 复盘讲解 | **LLM（文本, gpt-5.6-sol）** | 接地在引擎输出 |

---

## 4. LLM Provider 层（复用 model_client + OpenAI 兜底）

在 `backend/app/llm/` 实现统一的 `LLMProvider` 抽象，**优先公网可用的 `model_client`，再 fallback 到 env 的个人 OpenAI**。

```
backend/app/llm/
├── model_client.py      # vendored（内网/公网网关，gpt-5.6-sol / gemini / gpt-4o）
├── provider.py          # LLMProvider 抽象：text() / vision()
└── config.py            # 从环境变量读 key/host/network（绝不硬编码密钥）
```

**路由策略**：

| 任务 | 首选（model_client） | Fallback（OpenAI env） |
|------|----------------------|------------------------|
| **视觉**（截图解析） | `gemini-flash` → `gemini-pro` → `gpt-4o` | `gpt-4o` |
| **文本**（重建/剥削/教练） | `gpt-5.6-sol` | `gpt-5.x`（如 `gpt-5.5`/`gpt-5.4`） |

- ⚠️ **`gpt-5.6-sol` 的 `supports_vision=False`**——**截图解析绝不能用它**，必须走 gemini/gpt-4o。
- `model_client` 走公网：`network="office"`（或 env `MODEL_CLIENT_NETWORK=office`）。
- **密钥治理**：`model_client` 里内置的网关 AK、以及 OpenAI key，一律迁移到 `.env`（`.gitignore` 已忽略），代码从环境变量读；**任何密钥不进 git**。
- 支持多图：用 `call_model` 传 `images=[...]` 或 `call_model_batch` 批量跑多张/多手牌。

---

## 5. 数据模型（Supabase / PostgreSQL）

> 独立 Supabase 项目；截图文件存 **Supabase Storage**，DB 存结构化结果。

```
uploads                         # 一次上传批次（可含多张截图）
  id (uuid, pk)
  user_id (fk)
  created_at
  status            # 'extracting'|'reconstructing'|'needs_review'|'analyzed'

screenshots
  id (uuid, pk)
  upload_id (fk)
  storage_path      # Supabase Storage 路径
  screenshot_type   # 'hand_replay'|'result_summary'|'unknown'
  extraction_json   # 阶段① 观测事实
  extraction_confidence

parsed_hands                    # 一手重建后的近似牌谱
  id (uuid, pk)
  upload_id (fk)
  user_id (fk)
  hand_json         # 阶段② 逐街动作序列（含置信度）
  reconstruction_status # 'unique'|'multiple_legal'|'needs_user'|'user_edited'
  overall_confidence
  engine_validated  # bool
  created_at

hand_players                    # 一手牌里的每个座位
  id (uuid, pk)
  parsed_hand_id (fk)
  seat, position, alias
  hole_cards, stack_start, stack_end, net
  is_hero (bool)
  opponent_id (fk, nullable)    # 关联到用户确认的对手身份

deviations                      # 阶段③ 偏离点
  id (uuid, pk)
  parsed_hand_id (fk)
  actor (hero | opponent_id)
  street, decision_point
  gto_baseline_json             # 该点 GTO 频率/建议
  actual_action
  deviation_type                # over_fold|over_call|over_bluff|limp|sizing|...
  ev_loss_estimate
  confidence

opponents                       # 用户维护的固定对手
  id (uuid, pk)
  user_id (fk)
  display_name / alias_aliases[]

opponent_profiles               # 阶段④ 聚合画像
  opponent_id (fk)
  sample_size
  tendencies_json               # vpip/pfr/3bet%/fold_to_cbet/agg ...
  exploit_summary               # LLM 生成的人话剥削建议（可缓存）
  updated_at
```

---

## 6. API 设计（草案）

```
POST /api/ingest/upload           multipart：一批截图 → {upload_id, status}
GET  /api/ingest/{upload_id}      轮询解析进度与结果
POST /api/ingest/{hand_id}/confirm  用户提交修正后的近似牌谱 → 触发③④
GET  /api/hands                   我的已解析牌谱列表
GET  /api/hands/{id}              单手牌回放 + 偏离标注
GET  /api/opponents               我的对手列表
GET  /api/opponents/{id}          某对手画像 + 剥削建议
POST /api/opponents/{id}/reexploit  基于最新样本重算剥削
```

- 所有解析/打分/剥削计算在**服务端引擎 + 求解器**完成；LLM 仅解析与解释，不参与判定。

---

## 7. 前端流程（Next.js）

```
上传截图 → 解析中(进度) → 【确认/编辑】可编辑手牌回放 + 置信度提示
        → 偏离标注视图（hero + 对手高亮）
        → 逐对手剥削看板（画像 + LLM 建议 + 样本量）
```

- **确认/编辑**是关键一步：低置信字段高亮，用户一键改位置/动作/金额/底牌。
- 剥削看板按对手聚合，标注样本量与置信度，小样本给"倾向提示"而非硬结论。

---

## 8. 隐私与合规

- 截图含对手 alias（非实名）；仅供用户个人学习使用。
- 明确告知：重建为**近似**，用于学习参考，非真实牌谱回放。
- 存储对手数据遵循最小化原则；提供删除入口。

---

## 9. 分阶段落地

| 阶段 | 交付 |
|------|------|
| **S1** | LLM provider 层（model_client + OpenAI fallback）；单张截图 → 观测事实提取（阶段①） |
| **S2** | 引擎约束重建（阶段②）+ 置信度 + 用户确认/编辑界面 |
| **S3** | GTO 偏离标注（阶段③），接翻前范围表（翻后先启发式） |
| **S4** | 对手画像聚合 + LLM 逐人剥削建议（阶段④） |
| **S5** | 多张/整场批量导入、对手身份合并、剥削看板打磨 |

---

## 10. 关键风险与取舍

1. **重建的固有近似性** — 结算截图信息有限；靠引擎校验 + 置信度 + 用户确认控制质量，绝不把低置信结果当定论。
2. **视觉识别错误** — 中文/花色/金额易错；用户确认环节兜底；关键字段要求高置信否则强制复核。
3. **LLM 数字幻觉** — 铁律 + 引擎裁判彻底规避；判定永不经过 LLM。
4. **小样本剥削误导** — 样本不足只给倾向提示；剥削取稳健 MinES + 保守取值。
5. **密钥安全** — 所有密钥进 `.env`，不入 git；已公开粘贴过的 key 建议轮换。
6. **成本/延迟** — 视觉调用较贵；批量解析异步化 + 结果缓存；解释类可流式。

---

## 11. 真实样例与字段映射（回放类截图）

以一张真实 WePoker 手牌回放截图（`57/57` 最后一帧）为例，说明可提取字段：

- **牌局元信息**：`牌局ID`、盲注 `2/4(1)`（SB/BB/ante）、`底池 2445`、`保险 0`。
- **公共牌**（赢家/摊牌行可见）：翻 `3♠5♠10♠` → 转 `3` → 河 `A`。
- **玩家行**（每行含：头像、昵称、位置标记[大盲/小盲/D]、底牌[摊牌可见 / 否则牌背]、**逐街动作+金额**、右侧净额）：

| 昵称 | 位置 | 底牌 | 逐街动作 | 牌型 | 净额 |
|------|------|------|----------|------|------|
| JayTsui6 | 大盲(BB) | 牌背 | 弃牌 | — | -5 |
| 不会打蓝 | — | 牌背 | 弃牌 | — | -1 |
| BrightDa | — | 牌背 | 跟注32 → 弃牌 | — | -33 |
| 专治抽牌 | — | 牌背 | 弃牌 | — | -5 |
| **先清清兵** | — | 10♣10 | 加注32 → 下注38 → 跟注188 → **Allin941** | 葫芦 | **+1245** |
| 迟到的游 | D(BTN) | 牌背 | 弃牌 | — | -1 |
| **DV999** | 小盲(SB) | A♥4♦ | 跟注32 → 跟注38 → 下注188 → 跟注941 | 两对 | **-1200** |

**引擎校验点（关键）**：
- **净额守恒**：`+1245 -1200 -33 -5 -5 -1 -1 = 0` ✅
- **底池一致**：两主力每街投入 `32+38+188+941=1199`，双方合计 `2398` + BrightDa `32` + 盲注/ante ≈ `2445` ✅
- **逐街动作已显式标注** → 属 `hand_replay`，**高置信、无需歧义重建**。

**提取注意**：
- 位置需综合 `大盲/小盲/D` 标记 + 座位顺序推断其余位置。
- 金额语义（"加注32" 是"到 32"还是"+32"）由引擎按筹码守恒消歧。
- 弃牌玩家不摊牌（牌背），底牌置空；净额仍可用于校验。
- 中文动作词表：加注/下注/跟注/过牌/弃牌/Allin(全下)；花色需识别 ♠♥♣♦ 与四色牌。
