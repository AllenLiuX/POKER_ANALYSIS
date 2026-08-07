# 算法设计：翻后求解、剥削训练与 LLM 集成

> 版本：v0.1（讨论稿）
> 配套文档：[`ARCHITECTURE.md`](./ARCHITECTURE.md)、[`SCREENSHOT_IMPORT.md`](./SCREENSHOT_IMPORT.md)
> 主题：翻后（postflop）如何拿到可信答案、剥削（exploitative）训练怎么做、LLM（gpt5.6-sol）如何接地使用

---

## 0. 三条主线的关系（一句话总览）

- **求解器 / 预计算**：负责"对不对"——提供频率、EV、equity 等**唯一可信的数字真相**。
- **剥削训练**：在 GTO 基线之上，针对对手漏洞主动偏离，用求解器的 node-locking 生成可信的剥削答案。
- **LLM**：负责"为什么、怎么说人话、怎么针对你"——把数字翻译成教学，把自然语言读牌翻译成可计算的约束。**绝不负责计算数字。**

```
用户自然语言读牌 ──▶ [LLM 解析] ──▶ node-lock 约束
                                        │
                                        ▼
        spot ──▶ [求解器/预计算解集] ──▶ 频率 / EV / equity (真相)
                                        │
                                        ▼
                    [LLM 接地解释] ──▶ 因材施教的教练反馈
                                        │
                    用户历史错题 ──▶ [LLM] ──▶ 个性化训练计划
```

---

## 1. 翻后算法路线（三档）

| 档位 | 方法 | 运行时机 | 精度 | 工程量 | 本产品定位 |
|------|------|----------|------|--------|-----------|
| **① 启发式规则** | texture 分类 + MDF + range advantage | 实时 | 低（近似） | 小 | **MVP 过渡 + 缓存未命中兜底** |
| **② 离线预计算 + 缓存** | 开源 CFR 求解器离线解常见 spot，存 JSON 查表 | 查表（ms） | **高（真 GTO）** | 中 | **最终目标** |
| **③ 实时求解 / 神经网络** | 任意 spot 现场解（CFR 秒级 / ReBeL 式值网络） | 实时（秒级） | 高 | 大 | 远期，非必需 |

**决策（已确认）**：目标走 **②**。MVP 先用 **①** 打通训练闭环与前端，随后用离线预计算把翻后升级为真 GTO；① 保留为兜底。

**核心洞察**：GTO Wizard 这类商业产品的本质就是档位 ②——离线用求解器算好海量 spot，线上查表。开源求解器足以让我们复刻这条路。

---

## 2. 算法基础：CFR 家族

现代扑克求解器的核心都是 **Counterfactual Regret Minimization (CFR)** 及其变体：

- **CFR → CFR+ → Discounted CFR (DCFR) → Predictive CFR+ (PCFR+)**：收敛越来越快。
- **MCCFR**（Monte Carlo CFR，external / outcome sampling）：用采样降低单次迭代成本，适合大博弈/翻前。
- 对**固定范围 + 有限下注尺度**的单个翻后子博弈，CFR 会收敛到纳什均衡（GTO）。
- 收敛质量用 **exploitability（可利用度，单位 bb/100 或 mbb）** 衡量，越低越接近 GTO。

研究向框架（了解/实验用）：**OpenSpiel**（CFR/MCCFR/Deep CFR 全套）；**Deep CFR / ReBeL**（用神经网络逼近，走档位 ③）。

---

## 3. 开源求解器盘点（2026 现状 + 许可证）

> 商业闭源产品，**许可证是第一考量**。求解器选型暂缓，此表用于后续决策。

| 项目 | 语言 | 许可证 | 关键特性 | 适配度 |
|------|------|--------|----------|--------|
| **DCFR-SOLVER** (exinori) | Rust | **MIT** ✅ | 从零实现，10K 迭代 0.016% 可利用度，翻前 MCCFR + 翻后，JSON/HTML 导出，DCFR/CFR+/EGT/QRE | 商业友好，预计算首选候选 |
| **poker_solver** (amaster97) / **TexasSolverLib** | Rust / C++17 | **MIT** ✅ | HUNL 翻前+翻后，可利用度/价值评估，可当库集成（CMake） | 库集成候选 |
| **GTOpen** (MatthewPDingle) | Rust | 待确认 ⚠️ | DCFR + CUDA GPU，**自带 player profiling + 最大剥削**，本地 web UI | 剥削功能对口，需先确认许可证 |
| **TexasSolver** (bupticybee) | C++ | **AGPL-3.0** ⚠️ | 最成熟，2.4k star，对齐 PioSOLVER，JSON dump，跨语言调用 | AGPL 网络条款对闭源不友好 → 建议仅作**离线基准/校验** |
| **OpenSpiel** (DeepMind) | C++/Py | Apache-2.0 | 研究框架，算法齐全 | 学习/原型 |
| **ReBeL** (Meta) | C++/Py | — | 论文做了扑克，但**仅开源 Liar's Dice，扑克代码未放出** | ❌ 不能直接用于扑克 |

**许可证要点**：
- 优先 **MIT**（DCFR-SOLVER / poker_solver）用于线上/预计算。
- **AGPL**（TexasSolver）：作为后端对外提供网络服务通常触发"必须开源"义务；建议仅本地离线跑、作为 GTO 基准校验。
- GTOpen 自带剥削功能很诱人，但**必须先核实许可证**再决定是否采用。

---

## 4. 离线预计算流水线（档位 ② 的落地设计）

```
[定义 spot 网格] ──▶ [MIT 求解器批量求解] ──▶ [解集: 频率/EV/尺度/可利用度]
      │                                              │
  board 抽象 + 常见线                            存 JSON/DB，按抽象键索引
      │                                              │
      └──────────────────────────────────────────────▼
                                   线上查表 + eval7 算 equity → 训练器给真 GTO 反馈
```

### 4.1 状态空间裁剪（否则组合爆炸）

- **Board 抽象**：翻牌面按同花结构/连接性归并为 **1755 个战略等价类**（而非 22100 个原始组合）。转牌/河牌线按常见 runout 采样。
- **动作抽象**：限制下注尺度（如 33% / 66% / 100% / 全下 + 几何尺度），控制博弈树大小。
- **场景网格**：位置组合 × 翻前线（SRP / 3bet pot / 4bet pot）× 常见翻牌面聚类 × 常见下注线。优先覆盖高频 spot。

### 4.2 解集存储格式（示意）

```jsonc
// data/solves/6max_100bb/srp/BTNvsBB/Ts7h2d/root.json
{
  "meta": { "spot": "SRP BTNvsBB", "board": "Ts7h2d", "pot": 6.5, "eff_stack": 97,
            "solver": "dcfr-solver", "iters": 10000, "exploitability_bb100": 0.05 },
  "hero_range_strategy": {
    "AsAd": { "check": 0.15, "bet_33": 0.10, "bet_66": 0.75 },
    "KsQs": { "check": 0.40, "bet_33": 0.35, "bet_66": 0.25 }
    // ... 每手牌的动作频率
  }
}
```

线上只需查表 + `eval7` 补算 equity 做解释，**无实时求解压力**。

### 4.3 ① 启发式引擎（MVP + 兜底）

- Board texture 分类（dry/wet、paired、monotone、connected）。
- 手牌分类（value/marginal/air + draw 类型），基于 `eval7` 强度 + 对假定范围 equity。
- 决策启发式：range advantage → c-bet 频率与尺度；bluff-to-value 比例；防守用 **MDF** + pot odds。
- UI 明确标注"启发式近似，非精确 GTO"。

---

## 5. 剥削训练（产品差异化核心）

**定义**：GTO 是不可被剥削的基线；剥削是针对对手的可识别漏洞，主动偏离 GTO 去榨取更多 EV。

### 5.1 三种算法基础

1. **Node locking（节点锁定）** —— 最实用。把对手某决策点锁成偏离 GTO 的频率（如"面对 c-bet 弃牌 57% > 均衡 50%"），求解器重算英雄最优应对。
2. **Profiles / 频率锁定**（GTO Wizard 做法）：给目标动作挂"虚拟激励"并自动调参，直到命中目标频率，得到"该约束下最小 EV 损失"的策略。
3. **Best Response**：固定对手完整策略，直接算最大剥削（MaxES）。

### 5.2 一个必须让用户理解的 nuance：MinES vs MaxES

- **单点 node lock = MinES（最小剥削）**：只惩罚被锁那条街的漏洞，**假设对手在其它节点仍打完美 GTO**。
- **MaxES（最大剥削）**：需锁定整棵树的对手策略，通常不现实。
- **MinES 更稳健**：不会像满剥削那样产生脆弱、易被反剥削的疯狂策略。
- **产品原则**：如实告诉用户"这是稳健剥削（somewhat exploitative），不是理论满剥削"，并展示"相比 GTO 多赚的 EV"。锁定时**保守取值**（如均衡 50%、观测 57% → 锁 53~54%）以留出方差余量。

### 5.3 对手画像（Opponent Profiles）

预定义画像，每个 = 一组统计倾向：

| 画像 | 特征（示例） | 剥削方向 |
|------|--------------|----------|
| **Nit** | 极紧、fold 过多 | 多诈唬、偷盲、薄价值收敛 |
| **TAG** | 紧凶、接近均衡 | 微调，主要靠位置 |
| **LAG** | 松凶、诈唬多 | 多抓诈、少弃强牌 |
| **Calling Station** | 跟太多、几乎不弃 | 猛薄价值、几乎不诈唬 |
| **Maniac** | 疯狂加注/下注 | 收紧诈唬、用中强牌抓诈 |

倾向参数：VPIP / PFR / 3bet% / fold-to-cbet / check-raise% / aggression factor 等，映射为对应节点的 node-lock 约束。

### 5.4 剥削训练闭环

```
选场景 + 选对手画像
      │
离线已算好的 node-locked 解集  ◀── 每个画像预计算一套
      │
用户选择"如何偏离 GTO"
      │
对照 node-locked 解打分 → 展示 "相比 GTO 多赚/少赚 X bb 的 EV" + 解释
```

---

## 6. LLM 集成（gpt5.6-sol）

### 6.1 铁律（不可违背）

> **数字的唯一真相来源是引擎/求解器，绝不是 LLM。**
> LLM 会把频率、equity、EV 编造得很逼真。LLM 只负责**解释**与**翻译**，不负责**计算正确答案**。

实现方式：**function-calling / tool-use**。LLM 调用我们的 `equity()` / `lookup_solve()` / `solve_nodelock()` 接口拿真数字，再组织语言。gpt5.6-sol 的强推理用在解释、教学、自然语言理解上，数字始终可信可复现。

**Provider 层（复用现有资源）**：优先公网可用的 `model_client` 网关，失败 fallback 到 env 的个人 OpenAI（详见 [`ARCHITECTURE.md`](./ARCHITECTURE.md) §5.4、[`SCREENSHOT_IMPORT.md`](./SCREENSHOT_IMPORT.md) §4）：

| 任务 | 首选（model_client） | Fallback（OpenAI env） |
|------|----------------------|------------------------|
| **文本**（本章的教练/读牌/剥削解释） | `gpt-5.6-sol` | `gpt-5.x` |
| **视觉**（截图解析，见截图文档） | `gemini-flash → gemini-pro → gpt-4o` | `gpt-4o` |

- ⚠️ `gpt-5.6-sol` **不支持视觉**（`supports_vision=False`）；本章的文本推理用它，读图任务必须走 gemini/gpt-4o。
- 密钥经 `.env` 注入，不硬编码、不入 git。

### 6.2 MVP 就要做的两个 LLM 场景（已确认）

**场景 A — 接地 AI 教练（Grounded Coach）**

```
求解器/预计算输出 { 频率, EV, MDF, equity, range advantage }
        │  作为结构化 context 注入
        ▼
   [LLM] ──▶ 因材施教的自然语言讲解 + 多轮苏格拉底式追问
```
- 例："你该 double barrel，因为转牌 K 更偏向你的范围，你有 range advantage，且对手 turn 的跟注范围里……"
- 用户可继续追问"为什么不 check？"，LLM 基于同一份解数据作答，适配用户水平。

**场景 B — 自然语言读牌 → node-lock（剥削训练的灵魂）**

```
用户打字："这哥们面对 c-bet 弃牌太多、从不诈唬加注"
        │
   [LLM 解析] ──▶ 结构化约束 { fold_to_cbet: ↑, raise_freq: ↓ }
        │
   [求解器 node-lock 求解 / 查预计算画像解]
        │
   真 EV 剥削策略 ──▶ [LLM 解释] ──▶ "对这种人你应该……"
```
- 把"用自然语言描述对手"直接变成**可计算的剥削策略**，这是 LLM × 求解器结合的最佳落点。

### 6.3 后续可扩展的 LLM 场景

- **个性化复盘 / 学习计划**：分析用户错题聚合数据 → 生成训练计划（"你 BTN vs 3bet 弃牌过多，本周练这 20 个 spot"）。
- **LLM 假设 + 引擎验证循环**：LLM 提剥削猜想 → 引擎用 best-response 算实际 EV 增益 → 仅呈现验证通过的。
- **手牌历史解析**：解析 PokerStars/GG 格式，结构化后交引擎复盘。
- **WePoker 截图导入**（MVP 功能）：截图 → 近似牌谱 → 偏离标注 → **逐人剥削**，本质是"场景 B（自然语言读牌 → node-lock）"的自动化版本——截图替用户生成对手读牌。完整设计见 [`SCREENSHOT_IMPORT.md`](./SCREENSHOT_IMPORT.md)。
- **场景叙事**：生成对手背景、桌面氛围，增强沉浸感（纯展示层，不影响打分）。

### 6.4 工程注意

- **上下文**：把 spot、范围、解数据以紧凑结构化格式喂给 LLM，避免它"脑补"数字。
- **可复现**：教练解释可缓存；同一 spot 的讲解不必每次重算。
- **成本/延迟**：解释类请求可流式输出；打分本身走引擎（快、免 LLM）。
- **防作弊**：正确答案与打分永远在服务端引擎完成，LLM 输出不参与判分。

---

## 7. 分阶段落地（与 ARCHITECTURE.md 路线图对齐）

| 阶段 | 交付 |
|------|------|
| **Phase 5**（原路线图翻后） | ① 启发式翻后训练器打通闭环 |
| **Phase 7** | 选定 MIT 求解器；搭建离线预计算流水线；覆盖一批高频 spot；翻后升级为真 GTO |
| **Phase 8** | LLM 场景 A（接地教练）接入训练器反馈 |
| **Phase 9** | 对手画像 + node-locked 预计算；剥削训练器 + LLM 场景 B（自然语言读牌） |
| **Phase 10+** | 个性化复盘、假设-验证循环、手牌历史导入 |

---

## 8. 关键风险与取舍

1. **求解器许可证** — 商业产品优先 MIT；AGPL（TexasSolver）仅离线基准；GTOpen 用前先核实许可证。
2. **预计算覆盖率** — 无法穷举所有 spot；靠 board 抽象 + 高频优先 + 启发式兜底。
3. **LLM 数字幻觉** — 用铁律 + tool-use 彻底规避；判分永不经过 LLM。
4. **剥削策略的表达** — 必须讲清 MinES 的"稳健但非满剥削"性质，避免误导用户过度调整。
5. **对手画像的真实性** — 画像需基于合理的 population 统计，node-lock 保守取值留方差余量。
6. **成本与延迟** — LLM 解释可缓存/流式；预计算解集换取线上零求解压力。
