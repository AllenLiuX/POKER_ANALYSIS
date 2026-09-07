const state = {
  data: null,
  mode: "",
  validOnly: false,
  llmEnabled: localStorage.getItem("wpk.llmReasoning") === "true",
  llmDepth: localStorage.getItem("wpk.llmDepth") === "deep" ? "deep" : "light",
  llmStatus: null,
  liveDecision: null,
  reasoningInFlight: null,
  reasoningModeInFlight: null,
  reasoningDepthInFlight: null,
  lastAutoSequence: null,
  reasoningResults: new Map(),
  strategyInFlight: null,
  strategyResults: new Map(),
  pinnedReasoning: null,
  expandedOpponents: new Set(),
  playerContexts: new Map(),
  playerRanges: new Map(),
  profileReasoning: new Map(),
  profileReasoningInFlight: new Set(),
};
const suits = { s: "♠", h: "♥", c: "♣", d: "♦" };
const actionNames = {
  small_blind: "小盲", big_blind: "大盲", ante: "前注", raise: "加注",
  bet: "下注", call: "跟注", check: "过牌", fold: "弃牌",
  allin: "全下", all_in: "全下", "all-in": "全下",
};
const streetNames = { preflop: "翻前", flop: "翻牌", turn: "转牌", river: "河牌", showdown: "摊牌" };
const squidScenes = {
  1: "开始", 2: "玩家加入", 3: "晋级", 4: "结算", 5: "恢复",
  6: "等待", 7: "关闭等待", 8: "关闭等待", 9: "刷新",
  10: "无人获胜", 11: "结算手牌",
};
const metricNames = {
  vpip: "VPIP", pfr: "PFR", rfi: "RFI", three_bet: "3BET",
  four_bet: "4BET", fold_to_three_bet: "弃对 3BET",
  flop_cbet: "翻牌 CBET", turn_cbet: "转牌 CBET", river_cbet: "河牌 CBET",
  fold_to_flop_cbet: "弃对翻牌 CBET", fold_to_turn_cbet: "弃对转牌 CBET",
  flop_probe: "翻牌 PROBE", turn_probe: "转牌 PROBE",
  flop_check_raise: "翻牌过牌加注", turn_check_raise: "转牌过牌加注",
};
const rangeLabels = {
  premium_pair: "顶级对子", pair: "口袋对子", strong_ace: "强 A",
  broadway: "高张组合", suited_connector: "同花连张", ace_x: "Ax",
  suited: "同花牌", other: "其他牌", high_card: "高牌/空气",
  draw: "听牌", two_pair_plus: "两对或三条", straight_plus: "顺子以上",
};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
function llmModel() {
  return state.llmStatus?.model || "远程 LLM";
}
function reasoningDepthName(value) {
  return value === "deep" ? "重推理" : "轻推理";
}
function cardText(card) {
  if (!card || card.length < 2) return esc(card);
  return `${esc(card.slice(0, -1))}${suits[card.slice(-1)] || esc(card.slice(-1))}`;
}
function cards(cards = []) {
  return cards.map(card => {
    const red = /[hd]$/.test(card) ? " red" : "";
    return `<span class="card${red}">${cardText(card)}</span>`;
  }).join("");
}
function amount(action) {
  const pieces = [];
  if (action.amount !== null && action.amount !== undefined) pieces.push(esc(action.amount));
  if (action.amount_to !== null && action.amount_to !== undefined) pieces.push(`到 ${esc(action.amount_to)}`);
  return pieces.length ? `<span class="amount">${pieces.join(" · ")}</span>` : "";
}
function settlementMeta(player) {
  const pieces = [
    `座位 ${esc(player.seat)}`,
    player.position ? esc(player.position) : "",
    player.net == null ? "未结算" : `净额 ${player.net >= 0 ? "+" : ""}${esc(player.net)}`,
  ];
  if (player.insurance_result) pieces.push(`保险 ${player.insurance_result >= 0 ? "+" : ""}${esc(player.insurance_result)}`);
  if (player.fund) pieces.push(`基金费用 ${esc(player.fund)}`);
  return pieces.filter(Boolean).join(" · ");
}
function renderHand(hand) {
  if (!hand) return `<div class="empty">等待下一手牌。录制器识别到发牌后，这里会实时出现玩家、公共牌与完整行动线。</div>`;
  const quality = qualityLabel(hand.quality_status);
  const qualityNotice = hand.quality_status === "bad" || hand.quality_status === "partial"
    ? `<div class="quality-notice ${esc(hand.quality_status)}"><strong>${esc(quality)}</strong> · ${esc((hand.quality_reasons || []).join("；") || "牌局记录不完整")} · 已排除统计</div>`
    : "";
  const squid = renderHandSquid(hand.squid_hand);
  const inference = renderRangePredictions(hand.range_predictions);
  const grouped = {};
  for (const action of hand.actions || []) (grouped[action.street] ||= []).push(action);
  const timeline = Object.entries(grouped).map(([street, actions]) => `
    <div class="street">
      <div class="street-name">${esc(streetNames[street] || street)}</div>
      <div>${actions.map(a => `
        <p class="action-line">
          <strong>${esc(a.player || `座位 ${a.seat ?? "?"}`)}</strong>
          ${esc(actionNames[a.action] || a.action)} ${amount(a)}
        </p>`).join("")}
      </div>
    </div>`).join("");
  return `${qualityNotice}
    <div class="hand-top">
      <div><p class="eyebrow">HAND HISTORY · 第 ${esc(hand.hand_number ?? "?")} 手</p>
        <div class="hand-id">#${esc(hand.hand_id)}</div>
        <small>${esc(hand.played_at_cn || "时间同步中")} · 中国时区</small>
      </div>
      <div class="pot"><small>总底池</small><strong>${esc(hand.pot ?? "—")}</strong></div>
    </div>
    <div class="board">${cards(hand.board)}</div>
    <div class="players">${(hand.players || []).map(p => `
      <div class="player${p.is_hero ? " hero" : ""}">
        <div class="player-name">${esc(p.alias || "未知玩家")}${p.is_hero ? " · 我" : ""}</div>
        <div class="player-meta">${settlementMeta(p)}</div>
        <div class="board">${cards(p.hole_cards)}</div>
      </div>`).join("")}</div>
    ${squid}
    ${inference}
    <div class="timeline">${timeline}</div>`;
}
function eventSummary(event) {
  const data = event.payload?.data || {};
  if (data.actionList?.length) {
    return data.actionList.map(a => `${a.actionType} ${a.actionScore ?? ""}`).join(" / ");
  }
  if (event.event_name === "roundChangeNotify") return `${data.round || ""} · ${data.totalPot ?? ""}`;
  if (event.event_name === "squidGameNotify") return `scene ${data.scene} · ${(data.users || []).length} 人`;
  return event.event_name;
}
function renderEvents(events = []) {
  document.querySelector("#event-stream").innerHTML = events.slice(0, 80).map(event => `
    <li><code>#${esc(event.sequence)}</code><span><strong>${esc(event.event_name)}</strong><br>${esc(eventSummary(event))}</span></li>
  `).join("");
}
function renderHands(hands = []) {
  const visibleHands = state.validOnly
    ? hands.filter(hand => hand.quality_status === "good")
    : hands;
  document.querySelector("#hands-list").innerHTML = visibleHands.map(hand => `
    <article class="hand-row quality-${esc(hand.quality_status || "unknown")}" data-hand="${esc(hand.hand_id)}">
      <div><strong>第 ${esc(hand.hand_number ?? "?")} 手牌</strong>
        <span class="quality-badge">${esc(qualityLabel(hand.quality_status))}</span>
        <br><small>${esc(hand.played_at_cn || "时间同步中")} · CN</small>
        <br><small>#${esc(hand.hand_id)}</small>
        ${(hand.quality_reasons || []).length ? `<br><small class="quality-reason">${esc(hand.quality_reasons.join("；"))}</small>` : ""}
        ${handSquidSummary(hand.squid_hand)}
      </div>
      <div class="board">${cards(hand.board)}</div>
      <div class="metric"><small>行动</small>${esc(hand.actions?.length || 0)}</div>
      <div class="metric"><small>底池</small>${esc(hand.pot ?? "—")}</div>
    </article>`).join("");
  document.querySelectorAll("[data-hand]").forEach(node => {
    node.addEventListener("click", () => openHand(node.dataset.hand));
  });
}
function renderHandSquid(squid = {}) {
  const players = squid.players || [];
  const awards = squid.awards || [];
  if (!players.length && !awards.length) return "";
  return `<section class="hand-squid">
    <p class="profile-label">本手鱿鱼状态</p>
    <div class="squid-holders">${players.map(player => `
      <span class="${player.squid_start > 0 ? "has-squid" : ""}">
        ${esc(player.alias || player.user_id)}：${player.squid_start > 0 ? `鱿鱼 ×${esc(player.squid_start)}` : "无鱿鱼"}
      </span>`).join("")}</div>
    ${awards.length ? `<div class="squid-awards"><strong>本手获得：</strong>${awards.map(award =>
      `${esc(award.alias || award.user_id)} +${esc(award.award_count)}`
    ).join(" · ")}</div>` : ""}
  </section>`;
}
function handSquidSummary(squid = {}) {
  const holders = (squid.players || []).filter(player => player.squid_start > 0);
  const awards = squid.awards || [];
  if (!holders.length && !awards.length) return "";
  return `<div class="hand-squid-summary">
    ${holders.length ? `持有：${holders.map(player => `${esc(player.alias || player.user_id)}×${esc(player.squid_start)}`).join(" · ")}` : ""}
    ${awards.length ? `<br>获得：${awards.map(award => `${esc(award.alias || award.user_id)} +${esc(award.award_count)}`).join(" · ")}` : ""}
  </div>`;
}
function renderRangePredictions(predictions = []) {
  if (!predictions?.length) return "";
  return `<section class="range-inference">
    <p class="profile-label">行动线范围推断 · 研究模型</p>
    <div class="range-grid">${predictions.map(item => {
      const distribution = Object.entries(item.distribution || {}).slice(0, 3);
      const publishable = !["none", "very_low"].includes(item.confidence);
      return `<div class="range-row">
        <strong>${esc(item.alias || item.user_id)} · ${esc(streetNames[item.street] || item.street)}</strong>
        <span>行动线 ${esc(item.action_line || "—")}</span>
        <span>${publishable ? distribution.map(([label, probability]) =>
          `${esc(rangeLabels[label] || label)} ${esc(Math.round(probability * 100))}%`
        ).join(" · ") : "同类行动线样本不足，暂不输出概率"}</span>
        <span>预测 equity ${!publishable || item.expected_equity == null ? "—" : `${esc(Math.round(item.expected_equity * 100))}%`} · 置信度 ${esc(confidenceLabel(item.confidence))} · 标签 ${esc(item.training_samples)} · 同线 ${esc(item.matching_line_samples || 0)}</span>
      </div>`;
    }).join("")}</div>
    <small>只用公开摊牌作为真值；概率不是对未公开底牌的确定识别。</small>
  </section>`;
}
function confidenceLabel(value) {
  return { none: "无", very_low: "很低", low: "低", medium: "中", high: "高" }[value] || value;
}
function qualityLabel(status) {
  return { good: "有效", bad: "坏数据", partial: "不完整", live: "录制中", unknown: "待检查" }[status] || "待检查";
}
function renderOpponents(opponents = []) {
  document.querySelector("#opponents-list").innerHTML = opponents.map(p => {
    const userId = String(p.user_id);
    const expanded = state.expandedOpponents.has(userId);
    return `
    <article class="opponent-row${expanded ? " expanded" : ""}">
      <button class="opponent-summary" type="button" aria-expanded="${expanded}" data-user="${esc(userId)}">
        <span><strong>${esc(p.alias)}</strong><br><small>ID ${esc(p.user_id)}</small></span>
        <span class="metric"><small>手数</small>${esc(p.hands)}</span>
        ${summaryMetric(p, "vpip")}
        ${summaryMetric(p, "pfr")}
        ${summaryMetric(p, "three_bet")}
        <span class="metric"><small>WTSD</small>${esc(p.wtsd_pct)}%</span>
        <span class="metric"><small>净额</small>${p.net >= 0 ? "+" : ""}${esc(p.net)}</span>
      </button>
      <div class="profile-detail">
        ${renderProfile(p)}
      </div>
    </article>`;
  }).join("");
  document.querySelectorAll(".opponent-summary").forEach(button => {
    button.addEventListener("click", () => {
      const row = button.closest(".opponent-row");
      const open = row.classList.toggle("expanded");
      button.setAttribute("aria-expanded", String(open));
      if (open) {
        state.expandedOpponents.add(button.dataset.user);
        loadPlayerContext(row, button.dataset.user);
        loadPlayerRange(row, button.dataset.user);
      } else {
        state.expandedOpponents.delete(button.dataset.user);
      }
    });
    if (button.getAttribute("aria-expanded") === "true") {
      const row = button.closest(".opponent-row");
      loadPlayerContext(row, button.dataset.user);
      loadPlayerRange(row, button.dataset.user);
    }
  });
  document.querySelectorAll(".profile-llm-run").forEach(button => {
    button.addEventListener("click", event => {
      event.stopPropagation();
      runPlayerProfileReasoning(
        button.closest(".opponent-row"),
        button.dataset.user,
      );
    });
  });
}
function summaryMetric(player, key) {
  const metric = player.metrics?.[key];
  if (!metric) return `<span class="metric"><small>${esc(metricNames[key] || key)}</small>—</span>`;
  return `<span class="metric" title="80% 区间 ${esc(metric.low_pct)}–${esc(metric.high_pct)}%">
    <small>${esc(metricNames[key] || key)}</small>${esc(metric.mean_pct)}% <i>(${esc(metric.opportunities)})</i>
  </span>`;
}
function renderProfile(player) {
  const metrics = Object.entries(player.metrics || {}).map(([key, value]) => `
    <div class="profile-metric">
      <small>${esc(metricNames[key] || key)}</small>
      <strong>${esc(value.mean_pct)}%</strong>
      <span>观察 ${esc(value.observed_pct)}% · ${esc(value.successes)}/${esc(value.opportunities)}</span>
      <span>80% 区间 ${esc(value.low_pct)}–${esc(value.high_pct)}%</span>
    </div>`).join("");
  const positions = Object.entries(player.positions || {}).map(([position, values]) => {
    const vpip = values.vpip;
    const rfi = values.rfi;
    return `<div class="position-line"><strong>${esc(position)}</strong>
      <span>VPIP ${vpip ? `${esc(vpip.mean_pct)}% (${esc(vpip.opportunities)})` : "—"}</span>
      <span>RFI ${rfi ? `${esc(rfi.mean_pct)}% (${esc(rfi.opportunities)})` : "—"}</span>
    </div>`;
  }).join("");
  const sizing = Object.entries(player.sizing || {}).map(([street, value]) => `
    <div class="position-line"><strong>${esc(streetNames[street] || street)}</strong>
      <span>中位 ${esc(Math.round(value.median_pot * 100))}% pot</span>
      <span>样本 ${esc(value.count)}</span>
    </div>`).join("");
  const tendencies = (player.tendencies || []).map(item => `
    <p class="exploit-signal"><strong>${esc(item.label)}</strong>
      <span>${esc(item.exploit)}</span>
      <small>${esc(confidenceLabel(item.confidence || "low"))}置信度 · ${esc(item.evidence || "local")}</small>
    </p>`).join("");
  const style = player.style || {};
  const caveats = (player.profile_caveats || []).map(item => `<li>${esc(item)}</li>`).join("");
  return `
    <div class="style-summary">
      <p class="profile-label">风格判断</p>
      <strong class="style-name">${esc(style.label || "样本积累中")}</strong>
      <p>${esc(style.summary || "尚未形成稳定风格标签。")}</p>
      <span class="confidence-chip">${esc(confidenceLabel(style.confidence || "very_low"))}置信度</span>
    </div>
    <div class="metric-panel"><p class="profile-label">机会率后验</p><div class="profile-metrics">${metrics || "尚无决策机会"}</div></div>
    <div class="position-panel"><p class="profile-label">按位置</p>${positions || "<small>等待位置数据</small>"}</div>
    <div class="sizing-panel"><p class="profile-label">下注尺寸</p>${sizing || "<small>等待尺寸样本</small>"}</div>
    <div class="exploit-panel"><p class="profile-label">漏洞与对抗</p>${tendencies || "<small>当前样本不足，不生成强倾向标签</small>"}
      ${caveats ? `<ul class="profile-caveats">${caveats}</ul>` : ""}
      <button class="profile-llm-run" data-user="${esc(player.user_id)}" type="button">LLM 深化画像</button>
      <div class="profile-llm-result"><small>仅在需要时手动调用；范围数值仍由本地模型计算。</small></div>
      <div class="contextual-signals"><small>展开后加载位置、码深、鱿鱼和盈亏状态分布</small></div>
    </div>
    <section class="preflop-range-panel" data-user="${esc(player.user_id)}">
      <div class="range-loading">正在组合全部行动频率与公开亮牌…</div>
    </section>`;
}
async function loadPlayerContext(row, userId) {
  const target = row.querySelector(".contextual-signals");
  if (!target || target.dataset.loaded === "true") return;
  const cached = state.playerContexts.get(userId);
  if (cached) {
    target.innerHTML = cached;
    target.dataset.loaded = "true";
    return;
  }
  const response = await fetch(`/api/players/${encodeURIComponent(userId)}/context`);
  if (!response.ok) return;
  const data = await response.json();
  const entries = Object.entries(data.contexts || {}).filter(([, value]) => value.samples >= 3).slice(0, 10);
  const showdown = data.showdowns || {};
  const showdownText = showdown.samples
    ? `<p class="profile-label">公开摊牌样本 ${esc(showdown.samples)}</p>
       <p>翻前牌类 · ${esc(Object.entries(showdown.preflop_classes || {}).map(([label, count]) => `${rangeLabels[label] || label}:${count}`).join(" / "))}</p>
       <p>平均 equity · ${esc(Object.entries(showdown.average_equity_by_street || {}).map(([street, equity]) => `${streetNames[street] || street}:${Math.round(equity * 100)}%`).join(" / "))}</p>`
    : "<small>没有公开摊牌标签</small>";
  const content = `${showdownText}${entries.length
    ? `<p class="profile-label">上下文行动信号</p>${entries.map(([context, value]) =>
        `<p><strong>${esc(context)}</strong> · n=${esc(value.samples)} · ${esc(Object.entries(value.actions).map(([action, count]) => `${action}:${count}`).join(" / "))}</p>`
      ).join("")}`
    : "<small>上下文样本不足</small>"}`;
  state.playerContexts.set(userId, content);
  target.innerHTML = content;
  target.dataset.loaded = "true";
}
function playerRangeKey(userId, position = "ALL", line = "vpip") {
  return [userId, state.mode || "all", position, line].join("|");
}
async function loadPlayerRange(row, userId, position = "ALL", line = "vpip") {
  const panel = row.querySelector(".preflop-range-panel");
  if (!panel) return;
  const key = playerRangeKey(userId, position, line);
  const cached = state.playerRanges.get(key);
  if (cached) {
    renderPlayerRangePanel(row, cached);
    return;
  }
  panel.innerHTML = `<div class="range-loading">正在推断 ${esc(position)} · ${esc(line)} 范围…</div>`;
  const query = new URLSearchParams({ position, line });
  if (state.mode) query.set("mode", state.mode);
  try {
    const response = await fetch(`/api/players/${encodeURIComponent(userId)}/preflop-range?${query}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    state.playerRanges.set(key, data);
    if (row.isConnected) renderPlayerRangePanel(row, data);
  } catch (error) {
    if (row.isConnected) panel.innerHTML = `<div class="range-error">范围推断不可用：${esc(error.message)}</div>`;
  }
}
function rangeTier(weight) {
  if (weight >= 80) return "range-tier-5";
  if (weight >= 50) return "range-tier-4";
  if (weight >= 25) return "range-tier-3";
  if (weight >= 10) return "range-tier-2";
  if (weight > 0) return "range-tier-1";
  return "range-tier-0";
}
function renderPlayerRangePanel(row, data) {
  const panel = row.querySelector(".preflop-range-panel");
  if (!panel) return;
  const evidence = data.evidence || {};
  const frequency = data.frequency || {};
  const positions = data.options?.positions || [];
  const lines = data.options?.lines || [];
  const cells = (data.matrix || []).map(item => `
    <div class="range-cell ${rangeTier(item.weight_pct)}"
      title="${esc(`${item.hand} · 进入该行动线 ${item.weight_pct}% · 行动线内占比 ${item.conditional_pct}% · 本人亮牌 ${item.player_reveals}`)}">
      <strong>${esc(item.hand)}</strong>
      <small>${esc(item.weight_pct)}%</small>
    </div>`).join("");
  const topHands = (data.top_hands || []).slice(0, 12).map(item =>
    `<span>${esc(item.hand)} <b>${esc(item.weight_pct)}%</b></span>`
  ).join("");
  panel.innerHTML = `
    <div class="range-panel-head">
      <div>
        <p class="profile-label">169 格翻前范围 · BEST EFFORT</p>
        <h3>${esc(data.alias)} · ${esc(data.position === "ALL" ? "全部位置" : data.position)} · ${esc(data.line_label)}</h3>
      </div>
      <div class="range-frequency">
        <small>估计范围宽度</small>
        <strong>${esc(data.estimated_range_pct)}%</strong>
        <span>80% 区间 ${esc(frequency.low_pct)}–${esc(frequency.high_pct)}%</span>
      </div>
    </div>
    <div class="range-controls">
      <label>位置
        <select class="range-position">${positions.map(item =>
          `<option value="${esc(item.value)}"${item.value === data.position ? " selected" : ""}>${esc(item.label)} · 行动 ${esc(item.opportunities)} / 亮牌 ${esc(item.revealed_samples)}</option>`
        ).join("")}</select>
      </label>
      <label>行动线
        <select class="range-line">${lines.map(item =>
          `<option value="${esc(item.value)}"${item.value === data.line ? " selected" : ""}>${esc(item.label)} · 机会 ${esc(item.opportunities)} / 亮牌 ${esc(item.revealed_samples)}</option>`
        ).join("")}</select>
      </label>
      <span class="range-confidence ${esc(data.confidence)}">${esc(confidenceLabel(data.confidence))}置信度</span>
    </div>
    <div class="range-layout">
      <div>
        <div class="range-matrix" aria-label="${esc(`${data.alias} ${data.position} ${data.line_label} 范围矩阵`)}">${cells}</div>
        <div class="range-legend">
          <span class="range-tier-1">低频</span><span class="range-tier-3">混合</span><span class="range-tier-5">高频</span>
        </div>
      </div>
      <aside class="range-evidence">
        <p class="profile-label">证据与解释</p>
        <div class="range-stat"><span>本人亮牌</span><strong>${esc(evidence.player_revealed_samples)}</strong></div>
        <div class="range-stat"><span>群体亮牌</span><strong>${esc(evidence.population_revealed_samples)}</strong></div>
        <div class="range-stat"><span>全部行动机会</span><strong>${esc(evidence.action_opportunities)}</strong></div>
        <div class="range-stat"><span>先验依赖</span><strong>${esc(evidence.prior_dependence_pct)}%</strong></div>
        <p class="profile-label range-top-label">最高频组合</p>
        <div class="range-top-hands">${topHands}</div>
        <small>亮牌有摊牌选择偏差；范围宽度取自全部行动，亮牌只低权重调整形状。</small>
      </aside>
    </div>`;
  const reload = () => loadPlayerRange(
    row,
    data.user_id,
    panel.querySelector(".range-position").value,
    panel.querySelector(".range-line").value,
  );
  panel.querySelector(".range-position").addEventListener("change", reload);
  panel.querySelector(".range-line").addEventListener("change", reload);
  restoreProfileReasoning(row, data.user_id, data.position, data.line);
}
function profileReasoningKey(userId, position, line) {
  return [userId, state.mode || "all", position, line].join("|");
}
function restoreProfileReasoning(row, userId, position, line) {
  const target = row.querySelector(".profile-llm-result");
  if (!target) return;
  const key = profileReasoningKey(userId, position, line);
  const cached = state.profileReasoning.get(key);
  if (cached) {
    target.className = "profile-llm-result";
    target.innerHTML = renderPlayerProfileAnalysis(cached);
  } else if (state.profileReasoningInFlight.has(key)) {
    target.className = "profile-llm-result loading";
    target.innerHTML = "<strong>LLM 正在复核画像…</strong><small>本地范围矩阵不会被远程结果修改。</small>";
  } else {
    target.className = "profile-llm-result";
    target.innerHTML = "<small>仅在需要时手动调用；范围数值仍由本地模型计算。</small>";
  }
}
function renderPlayerProfileAnalysis(result) {
  const analysis = result.analysis || {};
  const evidence = result.evidence || {};
  const vulnerabilities = (analysis.vulnerabilities || []).map(item => {
    if (typeof item === "string") return `<li>${esc(item)}</li>`;
    return `<li><strong>${esc(item.finding || "")}</strong>
      <small>证据 ${esc((item.evidence || []).join(" · "))} · ${esc(reasoningConfidence(item.confidence))}置信度</small>
    </li>`;
  }).join("");
  const counters = (analysis.counter_strategy || []).map(item => `<li>${esc(item)}</li>`).join("");
  const caveats = (analysis.caveats || []).map(item => `<li>${esc(item)}</li>`).join("");
  const deviations = (evidence.population_deviations || []).map(item =>
    `<div class="llm-evidence-row"><strong>${esc(item.evidence_id)}</strong>
      <span>${esc(metricNames[item.metric] || item.metric)} ${item.delta_pp >= 0 ? "+" : ""}${esc(item.delta_pp)}pp · n=${esc(item.opportunities)}</span>
    </div>`
  ).join("");
  const cases = (evidence.cases || []).map(item => {
    const actions = (item.actions || []).map(action =>
      `${streetNames[action.street] || action.street}:${actionNames[action.action] || action.action}${action.size && action.size !== "none" ? `(${action.size})` : ""}`
    ).join(" → ");
    const strengths = Object.entries(item.strength_by_street || {}).map(([street, value]) => {
      const label = value.preflop_class || value.made_hand;
      const draws = (value.draws || []).join("/");
      return `${streetNames[street] || street}:${rangeLabels[label] || label || "—"}${draws ? `+${draws}` : ""}`;
    }).join(" · ");
    return `<div class="llm-evidence-case">
      <strong>${esc(item.case_id)} · ${esc(item.position)} · ${esc(item.shown_hand)} · ${esc(item.preflop_line)}</strong>
      <span>${esc(actions || "无行动")}</span>
      <small>${esc(strengths)}</small>
    </div>`;
  }).join("");
  const evidenceDetails = deviations || cases
    ? `<details class="llm-evidence-details">
        <summary>查看发送给 LLM 的证据 · ${esc(evidence.selected_cases || 0)}/${esc(evidence.available_cases || 0)} 手牌</summary>
        ${deviations ? `<p class="profile-label">相对当前牌池偏移</p>${deviations}` : ""}
        ${cases ? `<p class="profile-label">匿名亮牌与行动案例</p>${cases}` : ""}
      </details>`
    : "";
  return `<div class="llm-profile-head">
      <strong>LLM 复核 · ${esc(reasoningConfidence(analysis.confidence))}置信度</strong>
      <span>${esc(analysis.latency_ms ?? "—")}ms</span>
    </div>
    <p>${esc(analysis.style_summary || "未返回风格摘要")}</p>
    ${vulnerabilities ? `<small>可验证漏洞</small><ul>${vulnerabilities}</ul>` : ""}
    ${counters ? `<small>对抗调整</small><ol>${counters}</ol>` : ""}
    ${caveats ? `<small>保留意见</small><ul>${caveats}</ul>` : ""}
    ${evidenceDetails}`;
}
async function runPlayerProfileReasoning(row, userId) {
  const target = row.querySelector(".profile-llm-result");
  const panel = row.querySelector(".preflop-range-panel");
  const position = panel?.querySelector(".range-position")?.value || "ALL";
  const line = panel?.querySelector(".range-line")?.value || "vpip";
  const key = profileReasoningKey(userId, position, line);
  if (state.profileReasoningInFlight.has(key)) return;
  if (!state.llmStatus?.configured) {
    target.className = "profile-llm-result error";
    target.textContent = "LLM 未配置；本地画像和范围矩阵仍可正常使用。";
    return;
  }
  state.profileReasoningInFlight.add(key);
  restoreProfileReasoning(row, userId, position, line);
  const button = row.querySelector(".profile-llm-run");
  if (button) button.disabled = true;
  try {
    const response = await fetch(`/api/players/${encodeURIComponent(userId)}/profile/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ position, line, mode: state.mode || null }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    state.profileReasoning.set(key, data);
    if (row.isConnected) restoreProfileReasoning(row, userId, position, line);
  } catch (error) {
    if (row.isConnected) {
      target.className = "profile-llm-result error";
      target.textContent = `LLM 画像不可用：${error.message}`;
    }
  } finally {
    state.profileReasoningInFlight.delete(key);
    if (button && row.isConnected) button.disabled = false;
  }
}
function renderSquid(squid = {}) {
  const settlements = new Map();
  for (const item of squid.settlements || []) {
    const list = settlements.get(item.round_id) || [];
    list.push(item); settlements.set(item.round_id, list);
  }
  document.querySelector("#squid-list").innerHTML = (squid.rounds || []).map(round => {
    const latest = round.latest || {};
    const scene = latest.scene || latest.raw?.scene;
    const scores = (settlements.get(round.round_id) || []).map(s =>
      `${esc(s.alias || s.user_id)} ${s.add_score >= 0 ? "+" : ""}${esc(s.add_score ?? 0)}`
    ).join(" · ");
    return `<article class="squid-row">
      <div><strong>${esc(squidScenes[scene] || `阶段 ${scene}`)}</strong><br><small>${esc(round.round_id)}</small></div>
      <div class="metric"><small>状态</small>${esc(round.status)}</div>
      <div class="metric"><small>参与</small>${esc(round.participant_count)} 人</div>
      <div class="metric"><small>结算</small>${scores || "进行中"}</div>
    </article>`;
  }).join("");
}
function renderRaw(events = []) {
  document.querySelector("#raw-list").innerHTML = events.map(event => `
    <details>
      <summary>#${esc(event.sequence)} · ${esc(event.event_name)} · ${esc(event.hand_id || "未绑定牌局")}</summary>
      <pre>${esc(JSON.stringify(event.payload, null, 2))}</pre>
    </details>`).join("");
}
function reasoningConfidence(value) {
  return { low: "低", medium: "中", high: "高" }[value] || "低";
}
function hasKnownCards(decision) {
  return Array.isArray(decision?.hero_cards) && decision.hero_cards.length === 2;
}
function strategyFrequencyText(frequencies = {}) {
  return Object.entries(frequencies)
    .sort((left, right) => right[1] - left[1])
    .map(([action, frequency]) =>
      `${actionNames[action] || action} ${Math.round(frequency)}%`
    ).join(" · ");
}
function renderStrategyFrequencies(frequencies = {}) {
  return Object.entries(frequencies)
    .sort((left, right) => right[1] - left[1])
    .map(([action, frequency]) => `
      <span class="strategy-frequency action-${esc(action)}">
        ${esc(actionNames[action] || action)} <b>${esc(Math.round(frequency))}%</b>
      </span>`).join("");
}
function renderSpotStrategy(strategy = {}, exactAnalysis = null) {
  if (!strategy.kind) return "";
  const spot = strategy.spot || {};
  const street = streetNames[strategy.street] || strategy.street || "当前";
  const odds = Number(spot.required_equity_pct || 0);
  const spotMeta = [
    `${street} · ${spot.hero_position || "未知位置"}`,
    spot.players_in_hand ? `${spot.players_in_hand} 人底池` : "",
    odds > 0 ? `跟注所需约 ${odds}%` : "无需支付跟注额",
  ].filter(Boolean).join(" · ");
  const intro = `<section class="spot-strategy">
    <div class="strategy-spot">
      <span>${esc(spotMeta)}</span>
      <strong>${esc(spot.action_line || strategy.summary || "当前行动线")}</strong>
    </div>`;
  if (strategy.kind === "preflop_matrix") {
    const cells = (strategy.cells || []).map(item => {
      const mixed = Object.keys(item.frequencies || {}).length > 1 ? " mixed" : "";
      const hero = item.hand === strategy.hero_hand ? " is-hero" : "";
      const exactAction = hero && exactAnalysis?.recommended_action;
      const displayAction = exactAction || item.primary_action;
      const title = exactAction
        ? `${item.hand} · 当前建议 ${actionNames[exactAction] || exactAction} · 范围基线 ${strategyFrequencyText(item.frequencies)}`
        : `${item.hand} · ${strategyFrequencyText(item.frequencies)}`;
      return `<div class="strategy-cell action-${esc(displayAction)}${mixed}${hero}"
        title="${esc(title)}">
        ${esc(item.hand)}
      </div>`;
    }).join("");
    return `${intro}
      <div class="strategy-summary">${esc(strategy.summary)}</div>
      <div class="strategy-overall">${renderStrategyFrequencies(strategy.action_mix)}</div>
      <div class="strategy-matrix" aria-label="当前翻前局面的简化全范围动作">${cells}</div>
      <div class="strategy-legend">
        ${renderStrategyFrequencies(strategy.action_mix)}
        <span class="strategy-hero-key">${strategy.hero_hand ? `白框：${esc(strategy.hero_hand)}${exactAnalysis?.recommended_action ? ` 当前${esc(actionNames[exactAnalysis.recommended_action] || exactAnalysis.recommended_action)}` : ""}` : "未看到底牌：展示全范围"}</span>
      </div>
      <small class="strategy-caveat">${esc(strategy.caveat)}</small>
    </section>`;
  }
  const buckets = (strategy.buckets || []).map(item => `
    <div class="strategy-bucket${item.key === strategy.hero_bucket ? " is-hero" : ""}">
      <div class="strategy-bucket-copy">
        <strong>${esc(item.label)}</strong>
        <small>${esc(item.description)}</small>
      </div>
      <div class="strategy-bucket-mix">${renderStrategyFrequencies(item.frequencies)}</div>
    </div>`).join("");
  return `${intro}
    <div class="strategy-summary">${esc(strategy.summary)}</div>
    <div class="strategy-buckets">${buckets}</div>
    <div class="strategy-legend">
      <span class="strategy-hero-key">${strategy.hero_bucket ? "白框：当前手牌所属牌力层" : "未看到底牌：按五类牌力展示全范围"}</span>
    </div>
    <small class="strategy-caveat">${esc(strategy.caveat)}</small>
  </section>`;
}
function renderMoneyStrategy(engine = {}) {
  if (!engine.engine_version) return "";
  if (engine.status === "blocked") {
    return `<section class="money-strategy blocked">
      <strong>EV 引擎已停止建议</strong>
      <span>${esc((engine.reasons || []).join("；") || "当前状态未通过完整性检查")}</span>
    </section>`;
  }
  if (engine.status === "range_only") return "";
  const recommended = engine.recommended || {};
  const action = actionNames[recommended.action] || recommended.action || "等待";
  const raise = recommended.raise_to == null ? "" : `到 ${recommended.raise_to}`;
  const squid = engine.squid || {};
  const squidMeta = squid.available
    ? `${squid.hero_squid_count ?? "—"} 条 / 已发 ${squid.awarded_squids ?? "—"} of ${squid.total_squids ?? "—"} · ${squid.calibrated ? `每条 ${squid.squid_value}` : "价值待结算校准"}`
    : (squid.reason || "鱿鱼状态不可用");
  const policies = (engine.policy || []).slice(0, 4).map(item => {
    const name = actionNames[item.action] || item.action;
    const size = item.raise_to == null ? "" : ` ${item.raise_to}`;
    return `<span>${esc(name)}${esc(size)} <b>${esc(item.frequency_pct)}%</b></span>`;
  }).join("");
  const candidates = (engine.candidates || []).slice(0, 4).map(item => {
    const name = actionNames[item.action] || item.action;
    const size = item.raise_to == null ? "" : `到 ${item.raise_to}`;
    const squidEv = item.squid_ev == null
      ? "未计"
      : `${item.squid_ev >= 0 ? "+" : ""}${item.squid_ev} ${item.squid_ev_unit === "chips" ? "筹码" : "鱿鱼单位"}`;
    return `<div class="money-candidate">
      <strong>${esc(name)} ${esc(size)}</strong>
      <span>牌局EV ${esc(item.chip_ev)} · 鱿鱼边际 ${esc(squidEv)} · 稳健EV ${esc(item.robust_ev)}</span>
    </div>`;
  }).join("");
  const caveats = (engine.caveats || [])
    .map(item => `<li>${esc(item)}</li>`)
    .join("");
  return `<section class="money-strategy">
    <div class="money-strategy-head">
      <div>
        <small>本地 Money-EV 引擎 · ${esc(reasoningConfidence(engine.confidence))}置信度</small>
        <strong>${esc(action)} ${esc(raise)}</strong>
      </div>
      <span>${esc(engine.latency_ms ?? "—")}ms</span>
    </div>
    <div class="money-policy">${policies}</div>
    <div class="money-squid">鱿鱼：${esc(squidMeta)}</div>
    <details>
      <summary>查看候选动作 EV 分解</summary>
      <div class="money-candidates">${candidates}</div>
      ${caveats ? `<ul class="money-caveats">${caveats}</ul>` : ""}
    </details>
  </section>`;
}
function renderAnalysisRoute(result = {}) {
  const route = result.route || {};
  const called = route.llm_called === true;
  const completed = route.llm_completed !== false;
  const source = route.source || (called ? llmModel() : "本地算法");
  const depth = reasoningDepthName(route.reasoning_depth);
  const depthMeta = called
    ? `${depth} · ${route.reasoning_effort || "low"} effort · 最多 ${route.timeout_seconds || "—"}s`
    : "";
  const routeClass = called && !completed ? "fallback" : called ? "remote" : "local";
  const label = called && !completed
    ? `GPT‑5.6 ${depth}未完成 · 已回退本地证据`
    : called
    ? `已调用 GPT‑5.6 · ${depth}`
    : "本地结果 · 未调用 LLM";
  return `<div class="analysis-route ${routeClass}">
    <strong>${label}</strong>
    <span>${esc([source, depthMeta, route.reason].filter(Boolean).join(" · "))}</span>
  </div>`;
}
function evidenceChips(evidence = [], details = new Map()) {
  return evidence.map(item => {
    const detail = details.get(item);
    if (!detail) return `<code>${esc(item)}</code>`;
    const metric = metricNames[detail.metric] || detail.metric;
    const values = [
      detail.mean_pct == null ? "" : `后验 ${detail.mean_pct}%`,
      detail.population_mean_pct == null ? "" : `牌池 ${detail.population_mean_pct}%`,
      detail.opportunities == null ? "" : `n=${detail.opportunities}`,
    ].filter(Boolean).join(" · ");
    return `<code title="${esc(item)}">${esc(detail.player || "")} · ${esc(metric)} · ${esc(values)}</code>`;
  }).join("");
}
function renderExploitAnalysis(analysis = null) {
  if (!analysis) return "";
  const details = new Map(
    (analysis.evidence_details || []).map(item => [item.evidence_id, item])
  );
  const reads = (analysis.opponent_reads || []).map(item => `
    <li><strong>${esc(item.player || "活跃对手")}</strong> · ${esc(item.finding)}
      <div class="exploit-evidence">${evidenceChips(item.evidence, details)}</div>
    </li>`).join("");
  const adjustments = (analysis.range_adjustments || []).map(item => `
    <li>${esc(item.adjustment)}
      <div class="exploit-evidence">${evidenceChips(item.evidence, details)}</div>
    </li>`).join("");
  const caveats = (analysis.caveats || []).map(item => `<li>${esc(item)}</li>`).join("");
  return `<section class="live-exploit-analysis">
    <div class="llm-profile-head">
      <strong>针对当前活跃对手 · ${esc(reasoningConfidence(analysis.confidence))}置信度</strong>
      <span>${esc(analysis.latency_ms ?? "—")}ms</span>
    </div>
    ${analysis.summary ? `<p>${esc(analysis.summary)}</p>` : ""}
    ${reads ? `<small>对手判断</small><ul>${reads}</ul>` : ""}
    ${adjustments ? `<small>范围调整</small><ul>${adjustments}</ul>` : ""}
    ${!reads && !adjustments ? "<small>当前没有通过样本校验的剥削证据，不强行调整范围。</small>" : ""}
    ${caveats ? `<details><summary>限制条件</summary><ul>${caveats}</ul></details>` : ""}
    <div class="reasoning-meta">${esc(analysis.source || llmModel())} · 仅使用上列证据，不猜未知底牌</div>
  </section>`;
}
function renderReasoningAnalysis(result) {
  const analysis = result.analysis;
  const exploitAnalysis = result.exploit_analysis || (
    analysis && (
      (analysis.opponent_reads || []).length ||
      (analysis.range_adjustments || []).length
    ) ? analysis : null
  );
  const strategy = renderSpotStrategy(result.strategy || {}, analysis);
  const money = renderMoneyStrategy(result.money_strategy || {});
  const route = renderAnalysisRoute(result);
  const exploit = renderExploitAnalysis(exploitAnalysis);
  if (!analysis) {
    return `${route}<div class="strategy-mode-note">
        <strong>未捕获可靠底牌，不猜具体持牌</strong>
        <span>${exploitAnalysis ? "GPT‑5.6 只复核整个范围及对手剥削调整。" : "下面是立即生成的本地范围基线。"}</span>
      </div>${exploit}${money}${strategy}`;
  }
  const action = actionNames[analysis.recommended_action] || analysis.recommended_action || "未给动作";
  const raise = analysis.raise_to == null ? "" : `到 ${esc(analysis.raise_to)}`;
  const factors = (analysis.factors || []).map(item => `<li>${esc(item)}</li>`).join("");
  const risks = (analysis.risks || []).map(item => `<li>风险：${esc(item)}</li>`).join("");
  return `${route}${money}<section class="exact-hand-advice">
    <div class="reasoning-meta">二级规则 / LLM 复核，仅作解释；核心动作以上方 EV 引擎为准</div>
    <div class="reasoning-action">
      <strong>${esc(action)} ${raise}</strong>
      <span class="reasoning-meta">置信度 ${esc(reasoningConfidence(analysis.confidence))}</span>
    </div>
    <div>${esc(analysis.summary || "LLM 未返回摘要")}</div>
    ${factors || risks ? `<ul>${factors}${risks}</ul>` : ""}
    <div class="reasoning-meta">${esc(analysis.source || "unknown")} · ${esc(analysis.latency_ms ?? "—")}ms · 当前手牌建议</div>
  </section>${exploit}${strategy}`;
}
function renderLocalStrategy(result) {
  const target = document.querySelector("#reasoning-result");
  target.className = "reasoning-result";
  const money = renderMoneyStrategy(result.money_strategy || {});
  const strategy = renderSpotStrategy(result.baseline || {});
  const route = renderAnalysisRoute({
    route: {
      llm_called: false,
      source: result.money_strategy?.engine_version || result.baseline?.source,
      reason: "实时自动生成的本地范围与 EV 基线",
    },
  });
  target.innerHTML = `${route}${money}${strategy}`;
}
function renderDecisionWaiting(decision) {
  const target = document.querySelector("#reasoning-result");
  if (!decision) {
    target.className = "reasoning-result";
    target.innerHTML = "<small>等待当前行动节点；捕获后即使没有底牌，也可以查看位置与行动线的全范围策略。</small>";
    return;
  }
  const legal = (decision.legal_actions || []).map(action => actionNames[action] || action).join(" / ");
  target.className = "reasoning-result";
  const observed = decision.decision_subject === "observer";
  target.innerHTML = `<strong>${observed ? `观察行动者 · 座位 ${esc(decision.subject_seat ?? "?")}` : "本人行动"} · ${esc(streetNames[decision.street] || decision.street)} · ${esc(decision.players_in_hand)} 人底池</strong>
    <div>${hasKnownCards(decision) ? `当前手牌 ${esc((decision.hero_cards || []).join(" "))}` : "没有可靠底牌：本地显示全范围，GPT‑5.6 可做范围剥削复核"}</div>
    <div>合法动作：${esc(legal || "未知")}</div>
    <div class="reasoning-meta">${esc(decision.auto_reason || "")} · 剩余 ${esc(Math.max(0, Math.round((decision.remaining_ms || 0) / 1000)))}s</div>`;
}
function decisionCanBeReviewed(decision) {
  return Boolean(
    decision &&
    (decision.legal_actions || []).length,
  );
}
function updateReasoningControls() {
  const run = document.querySelector("#llm-run");
  const localRun = document.querySelector("#local-run");
  const clear = document.querySelector("#llm-clear");
  const decision = state.liveDecision;
  const configured = Boolean(state.llmStatus?.configured);
  const available = decisionCanBeReviewed(decision);
  run.disabled = !available || !configured || state.reasoningInFlight !== null;
  localRun.disabled = !available || state.reasoningInFlight !== null;
  document.querySelectorAll(".reasoning-depth-option").forEach(option => {
    option.disabled = state.reasoningInFlight !== null;
  });
  clear.hidden = !state.pinnedReasoning;
  if (state.reasoningInFlight !== null) {
    const remote = state.reasoningModeInFlight !== "local";
    const depth = reasoningDepthName(state.reasoningDepthInFlight);
    run.textContent = remote ? `GPT‑5.6 ${depth}中…` : "等待本地分析完成…";
    localRun.textContent = remote ? "等待 GPT‑5.6…" : "正在生成本地基线…";
  } else if (!decision) {
    run.textContent = "等待当前行动节点";
    localRun.textContent = "等待当前行动节点";
  } else if (!available) {
    run.textContent = "当前节点缺少合法动作";
    localRun.textContent = "当前节点缺少合法动作";
  } else if (!configured) {
    run.textContent = "GPT‑5.6 未配置";
    localRun.textContent = "查看本地范围基线（立即）";
  } else if (
    state.pinnedReasoning &&
    state.pinnedReasoning.sequence !== decision.sequence
  ) {
    run.textContent = `用 GPT‑5.6 ${reasoningDepthName(state.llmDepth)}并锁定新节点`;
    localRun.textContent = "查看并锁定新节点的本地基线";
  } else {
    const depth = reasoningDepthName(state.llmDepth);
    run.textContent = hasKnownCards(decision)
      ? `用 GPT‑5.6 ${depth}：手牌 + 对手剥削`
      : `用 GPT‑5.6 ${depth}：范围 + 对手剥削`;
    localRun.textContent = "查看本地范围基线（立即）";
  }
}
function renderPinnedReasoning() {
  const pinned = state.pinnedReasoning;
  if (!pinned) return false;
  const target = document.querySelector("#reasoning-result");
  const ended = state.liveDecision?.sequence !== pinned.sequence;
  const street = streetNames[pinned.decision?.street] || pinned.decision?.street || "未知街道";
  const note = ended
    ? `已锁定 #${pinned.sequence} · ${street}；牌局已继续，以下内容仅供复盘，不是当前节点建议。`
    : `已锁定当前 #${pinned.sequence} · ${street}；行动变化后结果仍会保留。`;
  if (pinned.status === "loading") {
    const remote = pinned.analysisMode !== "local";
    const depth = reasoningDepthName(pinned.reasoningDepth);
    target.className = "reasoning-result loading pinned";
    target.innerHTML = `<div class="reasoning-lock-note${ended ? " stale" : ""}">${esc(note)}</div>
      <strong>${remote ? `正在调用 ${esc(llmModel())} · ${esc(depth)}…` : "正在生成本地范围基线…"}</strong><br>
      <small>${remote ? (hasKnownCards(pinned.decision) ? `${esc(depth)}会复核当前手牌，并结合活跃对手画像找剥削点。` : `${esc(depth)}在观察模式下不猜底牌，只复核范围和对手剥削调整。`) : "只运行本地范围与 EV 引擎，不会调用 LLM。"}</small>`;
    return true;
  }
  if (pinned.status === "error") {
    target.className = "reasoning-result error pinned";
    target.innerHTML = `<div class="reasoning-lock-note stale">${esc(note)}</div>
      <strong>该节点复核失败</strong><div>${esc(pinned.error || "未知错误")}</div>`;
    return true;
  }
  target.className = "reasoning-result pinned";
  target.innerHTML = `<div class="reasoning-lock-note${ended || pinned.stale ? " stale" : ""}">${esc(note)}</div>
    ${renderReasoningAnalysis(pinned.result || {})}`;
  return true;
}
function handleLiveDecision(decision) {
  state.liveDecision = decision || null;
  const configured = Boolean(state.llmStatus?.configured);
  updateReasoningControls();
  if (renderPinnedReasoning()) return;
  if (!decision) {
    renderDecisionWaiting(null);
    return;
  }
  const cacheKey = decisionCacheKey(decision);
  const cached = state.reasoningResults.get(cacheKey);
  if (cached) {
    const target = document.querySelector("#reasoning-result");
    target.className = "reasoning-result";
    target.innerHTML = renderReasoningAnalysis(cached);
    return;
  }
  const local = state.strategyResults.get(cacheKey);
  if (local && state.reasoningInFlight !== decision.sequence) {
    renderLocalStrategy(local);
  } else {
    renderDecisionWaiting(decision);
  }
  if (
    !local &&
    state.strategyInFlight !== decision.sequence
  ) {
    runCurrentStrategy(decision);
  }
  if (state.reasoningInFlight === decision.sequence) {
    const target = document.querySelector("#reasoning-result");
    target.className = "reasoning-result loading";
    const remote = state.reasoningModeInFlight !== "local";
    if (local) {
      target.innerHTML = `<div class="reasoning-lock-note">本地 EV 已完成；${remote ? `${esc(llmModel())} 正在复核对手剥削。` : "正在锁定本地结果。"}</div>
        ${renderMoneyStrategy(local.money_strategy || {})}
        ${renderSpotStrategy(local.baseline || {})}`;
    } else {
      target.innerHTML = `<strong>决策分析中…</strong><br><small>${remote ? `正在调用 ${esc(llmModel())}。` : "正在运行本地范围与 EV 引擎。"}</small>`;
    }
    return;
  }
  if (
    state.llmEnabled &&
    configured &&
    decisionCanBeReviewed(decision) &&
    (decision.auto_reasoning || decision.local_fast_available) &&
    state.lastAutoSequence !== decision.sequence
  ) {
    runReasoning(false);
  }
}
function decisionCacheKey(decision) {
  return `${decision?.sequence ?? "none"}:${decision?.state_hash || "legacy"}`;
}
async function runCurrentStrategy(decision) {
  const sequence = decision.sequence;
  const cacheKey = decisionCacheKey(decision);
  state.strategyInFlight = sequence;
  try {
    const response = await fetch(`/api/strategy/current?sequence=${encodeURIComponent(sequence)}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    if (data.stale) return;
    state.strategyResults.set(cacheKey, data);
    while (state.strategyResults.size > 64) {
      state.strategyResults.delete(state.strategyResults.keys().next().value);
    }
    if (
      state.liveDecision?.sequence === sequence &&
      state.reasoningInFlight !== sequence &&
      !state.pinnedReasoning
    ) {
      renderLocalStrategy(data);
    }
  } catch (_) {
    // The live action line remains usable even if the experimental EV path is unavailable.
  } finally {
    if (state.strategyInFlight === sequence) state.strategyInFlight = null;
  }
}
async function runReasoning(force, analysisMode = "auto") {
  const decision = state.liveDecision;
  if (
    !decision ||
    !decisionCanBeReviewed(decision) ||
    (analysisMode !== "local" && !state.llmStatus?.configured)
  ) return;
  const sequence = decision.sequence;
  const cacheKey = decisionCacheKey(decision);
  const reasoningDepth = (
    force && analysisMode === "llm" ? state.llmDepth : "light"
  );
  if (!force) state.lastAutoSequence = sequence;
  if (force) {
    state.pinnedReasoning = {
      sequence,
      decision: { ...decision },
      analysisMode,
      reasoningDepth,
      status: "loading",
      result: null,
      stale: false,
    };
  }
  state.reasoningInFlight = sequence;
  state.reasoningModeInFlight = analysisMode;
  state.reasoningDepthInFlight = reasoningDepth;
  handleLiveDecision(decision);
  try {
    const response = await fetch("/api/reasoning/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sequence,
        force,
        analysis_mode: analysisMode,
        reasoning_depth: reasoningDepth,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    if (data.skipped) {
      if (force) {
        state.pinnedReasoning = {
          ...state.pinnedReasoning,
          status: "error",
          error: data.reason || "该节点没有触发复核",
        };
        renderPinnedReasoning();
      } else if (state.liveDecision?.sequence === sequence) {
        renderDecisionWaiting(decision);
      }
      return;
    }
    const changed = decisionCacheKey(state.liveDecision) !== cacheKey;
    if (force) {
      state.reasoningResults.set(cacheKey, data);
      state.pinnedReasoning = {
        ...state.pinnedReasoning,
        status: "done",
        result: data,
        stale: Boolean(data.stale || changed),
      };
      renderPinnedReasoning();
      return;
    }
    if (changed || data.stale) {
      return;
    }
    state.reasoningResults.set(cacheKey, data);
    const target = document.querySelector("#reasoning-result");
    target.className = "reasoning-result";
    target.innerHTML = renderReasoningAnalysis(data);
  } catch (error) {
    if (force) {
      state.pinnedReasoning = {
        ...state.pinnedReasoning,
        status: "error",
        error: `${analysisMode === "local" ? "本地分析" : "GPT‑5.6 推理"}不可用：${error.message}`,
      };
      renderPinnedReasoning();
    } else if (state.liveDecision?.sequence === sequence) {
      const target = document.querySelector("#reasoning-result");
      target.className = "reasoning-result error";
      target.textContent = `${analysisMode === "local" ? "本地分析" : "GPT‑5.6 推理"}不可用：${error.message}`;
    }
  } finally {
    if (state.reasoningInFlight === sequence) state.reasoningInFlight = null;
    state.reasoningModeInFlight = null;
    state.reasoningDepthInFlight = null;
    updateReasoningControls();
  }
}
async function loadReasoningStatus() {
  const target = document.querySelector("#reasoning-service");
  try {
    const response = await fetch("/api/reasoning/status");
    const data = await response.json();
    state.llmStatus = data;
    target.className = `reasoning-service ${data.configured ? "ready" : "error"}`;
    target.textContent = data.configured
      ? `${data.model || "远程 LLM"} · 轻推理 ${data.timeout_seconds}s / 重推理 ${data.deep_timeout_seconds || 20}s`
      : "未配置 API key；请设置 WPK_LLM_API_KEY 后重启";
  } catch (_) {
    target.className = "reasoning-service error";
    target.textContent = "无法读取 LLM 服务状态";
  }
  updateReasoningControls();
  handleLiveDecision(state.liveDecision);
}
function update(data) {
  state.data = data;
  document.querySelector("#sequence").textContent = `#${data.last_sequence || 0}`;
  document.querySelector("#current-hand").innerHTML = renderHand(data.current_hand || data.hands?.[0]);
  renderEvents(data.events); renderHands(data.hands); renderOpponents(data.opponents);
  renderSquid(data.squid); renderRaw(data.events);
  handleLiveDecision(data.live_decision);
}
async function openHand(id) {
  const response = await fetch(`/api/hands/${encodeURIComponent(id)}`);
  if (!response.ok) return;
  const hand = await response.json();
  document.querySelector("#dialog-content").innerHTML = renderHand(hand) +
    `<details><summary>关联原始事件</summary><pre>${esc(JSON.stringify(hand.raw_events, null, 2))}</pre></details>`;
  document.querySelector("#hand-dialog").showModal();
}
document.querySelector("#dialog-close").addEventListener("click", () => document.querySelector("#hand-dialog").close());
document.querySelectorAll(".tab").forEach(tab => tab.addEventListener("click", () => {
  document.querySelectorAll(".tab, .view").forEach(node => node.classList.remove("active"));
  tab.classList.add("active"); document.querySelector(`#view-${tab.dataset.view}`).classList.add("active");
}));
let source;
function connectStream() {
  if (source) source.close();
  const suffix = state.mode ? `?mode=${encodeURIComponent(state.mode)}` : "";
  fetch(`/api/snapshot${suffix}`).then(r => r.json()).then(update).catch(() => {});
  source = new EventSource(`/api/events${suffix}`);
  source.addEventListener("snapshot", event => {
    document.querySelector("#connection-dot").classList.add("online");
    document.querySelector("#connection-label").textContent = "实时";
    update(JSON.parse(event.data));
  });
  source.onerror = () => {
    document.querySelector("#connection-dot").classList.remove("online");
    document.querySelector("#connection-label").textContent = "正在重连";
  };
}
async function loadBacktest() {
  const target = document.querySelector("#inference-backtest");
  try {
    const response = await fetch("/api/inference/backtest");
    const data = await response.json();
    target.innerHTML = `<strong>历史牌力分类器回测（非下方 169 格范围）</strong>
      <span>标签 ${esc(data.labeled_observations)} · 已评分 ${esc(data.scored_observations)}</span>
      <span>Top-1 ${data.top1_accuracy == null ? "—" : `${esc(Math.round(data.top1_accuracy * 100))}%`}</span>
      <span>Brier ${esc(data.mean_brier ?? "—")} · Log loss ${esc(data.mean_log_loss ?? "—")}</span>
      <small>169 格模型使用行动频率约束与亮牌降权；此处旧分类器指标仅作负面对照。</small>`;
  } catch (_) {
    target.textContent = "回测暂不可用";
  }
}
document.querySelectorAll(".mode-button").forEach(button => button.addEventListener("click", () => {
  document.querySelectorAll(".mode-button").forEach(item => item.classList.remove("active"));
  button.classList.add("active");
  state.mode = button.dataset.mode || "";
  connectStream();
}));
document.querySelectorAll(".history-filter-button").forEach(button => button.addEventListener("click", () => {
  document.querySelectorAll(".history-filter-button").forEach(item => item.classList.remove("active"));
  button.classList.add("active");
  state.validOnly = button.dataset.validOnly === "true";
  renderHands(state.data?.hands || []);
}));
const llmToggle = document.querySelector("#llm-toggle");
llmToggle.checked = state.llmEnabled;
llmToggle.addEventListener("change", () => {
  state.llmEnabled = llmToggle.checked;
  localStorage.setItem("wpk.llmReasoning", String(state.llmEnabled));
  handleLiveDecision(state.liveDecision);
});
document.querySelectorAll(".reasoning-depth-option").forEach(button => {
  button.classList.toggle("active", button.dataset.depth === state.llmDepth);
  button.addEventListener("click", () => {
    state.llmDepth = button.dataset.depth === "deep" ? "deep" : "light";
    localStorage.setItem("wpk.llmDepth", state.llmDepth);
    document.querySelectorAll(".reasoning-depth-option").forEach(item => {
      item.classList.toggle("active", item.dataset.depth === state.llmDepth);
    });
    updateReasoningControls();
  });
});
document.querySelector("#local-run").addEventListener("click", () => runReasoning(true, "local"));
document.querySelector("#llm-run").addEventListener("click", () => runReasoning(true, "llm"));
document.querySelector("#llm-clear").addEventListener("click", () => {
  state.pinnedReasoning = null;
  handleLiveDecision(state.liveDecision);
});
connectStream();
loadBacktest();
loadReasoningStatus();
