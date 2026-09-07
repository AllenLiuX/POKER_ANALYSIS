const state = { data: null, mode: "", validOnly: false };
const suits = { s: "♠", h: "♥", c: "♣", d: "♦" };
const actionNames = {
  small_blind: "小盲", big_blind: "大盲", ante: "前注", raise: "加注",
  bet: "下注", call: "跟注", check: "过牌", fold: "弃牌",
  allin: "全下", "all-in": "全下",
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

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
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
function renderHand(hand) {
  if (!hand) return `<div class="empty">等待下一手牌。录制器识别到发牌后，这里会实时出现玩家、公共牌与完整行动线。</div>`;
  const quality = qualityLabel(hand.quality_status);
  const qualityNotice = hand.quality_status === "bad" || hand.quality_status === "partial"
    ? `<div class="quality-notice ${esc(hand.quality_status)}"><strong>${esc(quality)}</strong> · ${esc((hand.quality_reasons || []).join("；") || "牌局记录不完整")} · 已排除统计</div>`
    : "";
  const squid = renderHandSquid(hand.squid_hand);
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
        <div class="player-meta">座位 ${esc(p.seat)} · ${esc(p.position || "")} · ${p.net == null ? "未结算" : `净额 ${p.net >= 0 ? "+" : ""}${esc(p.net)}`}</div>
        <div class="board">${cards(p.hole_cards)}</div>
      </div>`).join("")}</div>
    ${squid}
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
function qualityLabel(status) {
  return { good: "有效", bad: "坏数据", partial: "不完整", live: "录制中", unknown: "待检查" }[status] || "待检查";
}
function renderOpponents(opponents = []) {
  document.querySelector("#opponents-list").innerHTML = opponents.map(p => `
    <article class="opponent-row">
      <button class="opponent-summary" type="button" aria-expanded="false">
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
    </article>`).join("");
  document.querySelectorAll(".opponent-summary").forEach(button => {
    button.addEventListener("click", () => {
      const row = button.closest(".opponent-row");
      const open = row.classList.toggle("expanded");
      button.setAttribute("aria-expanded", String(open));
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
    <p><strong>${esc(item.label)}</strong> · ${esc(item.exploit)}</p>`).join("");
  return `
    <div><p class="profile-label">机会率后验</p><div class="profile-metrics">${metrics || "尚无决策机会"}</div></div>
    <div><p class="profile-label">按位置</p>${positions || "<small>等待位置数据</small>"}</div>
    <div><p class="profile-label">下注尺寸</p>${sizing || "<small>等待尺寸样本</small>"}</div>
    <div><p class="profile-label">可信剥削信号</p>${tendencies || "<small>当前样本不足，不生成强倾向标签</small>"}</div>`;
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
function update(data) {
  state.data = data;
  document.querySelector("#sequence").textContent = `#${data.last_sequence || 0}`;
  document.querySelector("#current-hand").innerHTML = renderHand(data.hands?.[0]);
  renderEvents(data.events); renderHands(data.hands); renderOpponents(data.opponents);
  renderSquid(data.squid); renderRaw(data.events);
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
connectStream();
