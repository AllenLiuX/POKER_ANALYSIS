const state = {
  data: null,
  mode: "",
  validOnly: false,
  llmEnabled: localStorage.getItem("wpk.llmReasoning") === "true",
  llmDepth: localStorage.getItem("wpk.llmDepth") === "deep" ? "deep" : "light",
  observerLiveEnabled: localStorage.getItem("wpk.observerLive") !== "false",
  allEquityEnabled: localStorage.getItem("wpk.allEquity") === "true",
  actionLogExpanded: localStorage.getItem("wpk.actionLogExpanded") === "true",
  llmStatus: null,
  serverLiveDecision: null,
  liveDecision: null,
  reasoningInFlight: null,
  reasoningModeInFlight: null,
  reasoningDepthInFlight: null,
  reasoningRequest: null,
  lastAutoSequence: null,
  reasoningResults: new Map(),
  strategyInFlight: null,
  strategyRequest: null,
  strategyResults: new Map(),
  preflopPreview: null,
  preflopPreviewKey: "",
  preflopPreviewInFlight: "",
  equityCurves: new Map(),
  equityInFlight: new Set(),
  equityControllers: new Map(),
  allEquityGeneration: 0,
  allEquityViewKey: "",
  allEquityRequests: new Map(),
  allEquityControllers: new Map(),
  pinnedReasoning: null,
  expandedOpponents: new Set(),
  playerContexts: new Map(),
  playerRanges: new Map(),
  profileReasoning: new Map(),
  profileReasoningInFlight: new Set(),
  profileReasoningRestoreChecked: new Set(),
  assistancePolicy: null,
  opponentNodeSelection: null,
  opponentNodeRequestKey: "",
  opponentNodeCache: new Map(),
  playerRangeMiniCache: new Map(),
  playerRangeMiniInFlight: new Set(),
  historyHands: [],
  historyTotal: null,
  historyHasMore: false,
  historyLoaded: false,
  historyLoading: false,
  historyRequestKey: "",
  historyGeneration: 0,
  historyError: "",
  historyPlayer: null,
  historyMode: "",
  historyQuery: "",
  historyResult: "",
  historyStreet: "",
  historyRevealed: false,
  opponentSearch: "",
  opponentSort: "hands_desc",
  snapshotRefreshInFlight: null,
  profileRefreshKey: "",
};
const suits = { s: "♠", h: "♥", c: "♣", d: "♦" };
const actionNames = {
  small_blind: "小盲", big_blind: "大盲", ante: "前注", raise: "加注",
  bet: "下注", call: "跟注", check: "过牌", fold: "弃牌",
  allin: "全下", all_in: "全下", "all-in": "全下",
};
const tableSeatOrder = [4, 5, 6, 7, 3, 8, 2, 1, 9];
const tableSeatLabels = {
  1: "正下", 2: "左下", 3: "左侧", 4: "左上", 5: "上左",
  6: "上右", 7: "右上", 8: "右侧", 9: "右下",
};
function tableDisplaySeat(seat, heroSeat) {
  const actual = Number(seat);
  const hero = Number(heroSeat);
  if (
    !Number.isInteger(actual) ||
    !Number.isInteger(hero) ||
    actual < 1 ||
    actual > 9 ||
    hero < 1 ||
    hero > 9
  ) return actual;
  return ((actual - hero + 9) % 9) + 1;
}
const streetNames = { preflop: "翻前", flop: "翻牌", turn: "转牌", river: "河牌", showdown: "摊牌" };
const timelineStreets = ["preflop", "flop", "turn", "river"];
const frequencyScopeNames = {
  overall: "整个范围", premium: "顶级牌", strong: "强牌", medium: "中等牌",
  speculative: "投机牌", weak: "弱牌", strong_value: "强价值",
  top_pair: "顶对/超对", showdown: "摊牌价值", strong_draw: "强听牌", air: "空气牌",
};
const squidScenes = {
  1: "开始", 2: "玩家加入", 3: "晋级", 4: "结算", 5: "恢复",
  6: "等待", 7: "关闭等待", 8: "关闭等待", 9: "刷新",
  10: "无人获胜", 11: "结算手牌",
};
const metricNames = {
  vpip: "VPIP", pfr: "PFR", rfi: "RFI", three_bet: "3BET",
  four_bet: "4BET", fold_to_three_bet: "面对 3BET 弃牌",
  flop_cbet: "翻牌 CBET", turn_cbet: "转牌 CBET", river_cbet: "河牌 CBET",
  fold_to_flop_cbet: "面对翻牌 CBET 弃牌", fold_to_turn_cbet: "面对转牌 CBET 弃牌",
  fold_to_river_cbet: "面对河牌 CBET 弃牌",
  fold_to_flop_bet: "面对翻牌进攻弃牌", fold_to_turn_bet: "面对转牌进攻弃牌",
  fold_to_river_bet: "面对河牌进攻弃牌",
  call_vs_flop_bet: "面对翻牌进攻跟注", call_vs_turn_bet: "面对转牌进攻跟注",
  call_vs_river_bet: "面对河牌进攻跟注",
  raise_vs_flop_bet: "面对翻牌进攻再加注", raise_vs_turn_bet: "面对转牌进攻再加注",
  raise_vs_river_bet: "面对河牌进攻再加注",
  fold_to_flop_raise: "面对翻牌加注弃牌", fold_to_turn_raise: "面对转牌加注弃牌",
  fold_to_river_raise: "面对河牌加注弃牌",
  flop_donk: "翻牌领先下注（DONK）", turn_donk: "转牌领先下注（DONK）",
  river_donk: "河牌领先下注（DONK）",
  turn_probe: "转牌刺探下注（PROBE）", river_probe: "河牌刺探下注（PROBE）",
  turn_delayed_cbet: "转牌延迟 CBET",
  fold_to_turn_delayed_cbet: "面对延迟 CBET 弃牌",
  turn_barrel: "转牌继续开火", river_barrel: "河牌继续开火",
  river_triple_barrel: "河牌三枪",
  fold_to_turn_barrel: "面对转牌继续开火弃牌",
  fold_to_river_barrel: "面对河牌继续开火弃牌",
  flop_check_raise: "翻牌过牌加注", turn_check_raise: "转牌过牌加注",
  river_check_raise: "河牌过牌加注",
};
const metricStreetGroups = [
  {
    key: "preflop",
    label: "翻前",
    metrics: ["vpip", "pfr", "rfi", "three_bet", "four_bet", "fold_to_three_bet"],
  },
  {
    key: "flop",
    label: "翻牌",
    metrics: [
      "flop_cbet", "flop_donk", "fold_to_flop_bet",
      "call_vs_flop_bet", "raise_vs_flop_bet",
      "fold_to_flop_cbet", "flop_check_raise", "fold_to_flop_raise",
    ],
  },
  {
    key: "turn",
    label: "转牌",
    metrics: [
      "turn_cbet", "turn_delayed_cbet", "turn_barrel", "turn_probe", "turn_donk",
      "fold_to_turn_bet", "call_vs_turn_bet", "raise_vs_turn_bet",
      "fold_to_turn_cbet", "fold_to_turn_delayed_cbet", "fold_to_turn_barrel",
      "turn_check_raise", "fold_to_turn_raise",
    ],
  },
  {
    key: "river",
    label: "河牌",
    metrics: [
      "river_cbet", "river_barrel", "river_triple_barrel", "river_probe", "river_donk",
      "fold_to_river_bet", "call_vs_river_bet", "raise_vs_river_bet",
      "fold_to_river_cbet", "fold_to_river_barrel",
      "river_check_raise", "fold_to_river_raise",
    ],
  },
];
const rangeLabels = {
  premium_pair: "顶级对子", pair: "口袋对子", strong_ace: "强 A",
  broadway: "高张组合", suited_connector: "同花连张", ace_x: "Ax",
  suited: "同花牌", other: "其他牌", high_card: "高牌/空气",
  draw: "听牌", two_pair_plus: "两对或三条", straight_plus: "顺子以上",
};
const strengthLabels = {
  high_card: "高牌", pair: "一对", draw: "听牌",
  two_pair_plus: "两对 / 三条", straight_plus: "顺子以上",
  two_pair: "两对", trips: "三条", straight: "顺子",
  flush: "同花", full_house: "葫芦", quads: "四条",
  strong_value: "强价值", marginal_showdown: "边缘摊牌价值",
  draws: "听牌", air: "空气", bluff_candidates: "纯 Bluff 候选",
};
const responseActionLabels = {
  fold: "弃牌", check: "过牌", call: "跟注",
  bet: "下注", raise: "加注", all_in: "全下",
};
const responseSizeLabels = {
  tiny: "≤ 25% 池", small: "25–50% 池", medium: "50–75% 池",
  large: "75–100% 池", overbet: "> 100% 池",
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
function renderTimelineActions(street, actions = []) {
  if (!actions.length) return '<p class="action-line empty-action">—</p>';
  const antes = actions.filter(action => normalizedAction(action.action) === "ante");
  const firstAnte = antes[0];
  const collapseAntes = street === "preflop"
    && antes.length > 1
    && antes.every(action => (
      action.amount === firstAnte.amount
      && action.amount_to === firstAnte.amount_to
    ));
  let renderedAnte = false;
  return actions.map(action => {
    if (collapseAntes && normalizedAction(action.action) === "ante") {
      if (renderedAnte) return "";
      renderedAnte = true;
      return `<p class="action-line action-summary">
        <strong>全桌 · ${esc(antes.length)} 人</strong>
        ${esc(actionNames.ante)} ${amount(firstAnte)}
      </p>`;
    }
    return `<p class="action-line">
      <strong>${esc(action.player || `座位 ${action.seat ?? "?"}`)}</strong>
      ${esc(actionNames[action.action] || action.action)} ${amount(action)}
    </p>`;
  }).join("");
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
function normalizedAction(value) {
  const action = String(value || "").toLowerCase().replace("-", "_");
  return action === "allin" ? "all_in" : action;
}
function quickMetric(profile, key) {
  const value = profile?.metrics?.[key]?.mean_pct;
  return value == null ? "—" : Number(value).toFixed(value % 1 ? 1 : 0);
}
const quickStreetMetricSlots = {
  preflop: [
    ["vpip"],
    ["pfr"],
    ["rfi"],
    ["three_bet"],
    ["fold_to_three_bet"],
    ["four_bet"],
  ],
  flop: [
    ["flop_cbet"],
    ["flop_donk"],
    ["fold_to_flop_bet", "fold_to_flop_cbet"],
    ["call_vs_flop_bet"],
    ["raise_vs_flop_bet"],
    ["flop_check_raise", "fold_to_flop_raise"],
  ],
  turn: [
    ["turn_barrel", "turn_cbet"],
    ["turn_probe"],
    ["turn_donk"],
    ["fold_to_turn_bet", "fold_to_turn_cbet", "fold_to_turn_barrel"],
    ["call_vs_turn_bet"],
    ["raise_vs_turn_bet", "turn_check_raise"],
  ],
  river: [
    ["river_barrel", "river_cbet"],
    ["river_probe"],
    ["river_donk"],
    ["fold_to_river_bet", "fold_to_river_cbet", "fold_to_river_barrel"],
    ["call_vs_river_bet"],
    ["raise_vs_river_bet", "river_check_raise"],
  ],
};
const quickMetricNames = {
  vpip: "Voluntarily Put Money In Pot",
  pfr: "Preflop Raise",
  rfi: "Raise First In",
  three_bet: "Three-Bet",
  four_bet: "Four-Bet",
  fold_to_three_bet: "Fold to Three-Bet",
  flop_cbet: "Continuation Bet",
  flop_donk: "Donk Bet",
  fold_to_flop_bet: "Fold vs Bet",
  fold_to_flop_cbet: "Fold vs Continuation Bet",
  call_vs_flop_bet: "Call vs Bet",
  raise_vs_flop_bet: "Raise vs Bet",
  flop_check_raise: "Check-Raise",
  fold_to_flop_raise: "Fold vs Raise",
  turn_barrel: "Turn Barrel",
  turn_cbet: "Continuation Bet",
  turn_probe: "Probe Bet",
  turn_donk: "Donk Bet",
  fold_to_turn_bet: "Fold vs Bet",
  fold_to_turn_cbet: "Fold vs Continuation Bet",
  fold_to_turn_barrel: "Fold vs Turn Barrel",
  call_vs_turn_bet: "Call vs Bet",
  raise_vs_turn_bet: "Raise vs Bet",
  turn_check_raise: "Check-Raise",
  river_barrel: "River Barrel",
  river_cbet: "Continuation Bet",
  river_probe: "Probe Bet",
  river_donk: "Donk Bet",
  fold_to_river_bet: "Fold vs Bet",
  fold_to_river_cbet: "Fold vs Continuation Bet",
  fold_to_river_barrel: "Fold vs River Barrel",
  call_vs_river_bet: "Call vs Bet",
  raise_vs_river_bet: "Raise vs Bet",
  river_check_raise: "Check-Raise",
};
const quickMetricDescriptions = {
  vpip: "自愿入池率：翻前主动投入筹码的比例",
  pfr: "翻前加注率：翻前执行加注的比例",
  rfi: "首入池加注率：前面无人入池时率先加注的比例",
  three_bet: "3BET 率：面对翻前加注时再加注的比例",
  four_bet: "4BET 率：面对 3BET 时再次加注的比例",
  fold_to_three_bet: "面对 3BET 弃牌率",
  flop_cbet: "翻牌持续下注率：作为翻前进攻者在翻牌下注的比例",
  flop_donk: "翻牌领先下注率：不掌握主动权时抢先下注的比例",
  fold_to_flop_bet: "面对翻牌下注时的弃牌率",
  fold_to_flop_cbet: "面对翻牌持续下注时的弃牌率",
  call_vs_flop_bet: "面对翻牌下注时的跟注率",
  raise_vs_flop_bet: "面对翻牌下注时的加注率",
  flop_check_raise: "翻牌过牌后再加注的比例",
  fold_to_flop_raise: "翻牌下注遭加注后的弃牌率",
  turn_barrel: "转牌二枪率：翻牌进攻后在转牌继续下注的比例",
  turn_cbet: "转牌持续下注率",
  turn_probe: "转牌刺探下注率：前一街进攻者过牌后主动下注的比例",
  turn_donk: "转牌领先下注率：不掌握主动权时抢先下注的比例",
  fold_to_turn_bet: "面对转牌下注时的弃牌率",
  fold_to_turn_cbet: "面对转牌持续下注时的弃牌率",
  fold_to_turn_barrel: "面对转牌二枪时的弃牌率",
  call_vs_turn_bet: "面对转牌下注时的跟注率",
  raise_vs_turn_bet: "面对转牌下注时的加注率",
  turn_check_raise: "转牌过牌后再加注的比例",
  river_barrel: "河牌三枪率：连续进攻至河牌的比例",
  river_cbet: "河牌持续下注率",
  river_probe: "河牌刺探下注率：前一街进攻者过牌后主动下注的比例",
  river_donk: "河牌领先下注率：不掌握主动权时抢先下注的比例",
  fold_to_river_bet: "面对河牌下注时的弃牌率",
  fold_to_river_cbet: "面对河牌持续下注时的弃牌率",
  fold_to_river_barrel: "面对河牌三枪时的弃牌率",
  call_vs_river_bet: "面对河牌下注时的跟注率",
  raise_vs_river_bet: "面对河牌下注时的加注率",
  river_check_raise: "河牌过牌后再加注的比例",
};
function quickMetricTitle(key, metric = null) {
  const name = quickMetricNames[key] || metricNames[key] || key;
  const description = quickMetricDescriptions[key] || metricNames[key] || key;
  if (!metric) return `${name} · ${description} · 暂无样本`;
  const deviation = metricDeviation(metric);
  return [
    `${name} · ${description}`,
    `当前 ${metric.mean_pct ?? "—"}%`,
    `牌池 ${metric.population_mean_pct ?? "—"}%`,
    `偏移 ${signedPoints(deviation.delta)}pp · ${deviation.degreeLabel}`,
    `相对 ${deviation.signedRelativePct}% · 赔率 ${deviation.oddsRatio.toFixed(2)}×`,
    `区间 ${metric.low_pct ?? "—"}–${metric.high_pct ?? "—"}%`,
    `样本 n${metric.opportunities || 0}`,
  ].join(" · ");
}
function quickMetricFamily(key) {
  if (key === "vpip") return "entry";
  if (key.startsWith("fold_to_")) return "fold";
  if (key.startsWith("call_vs_")) return "call";
  if (
    key.includes("raise")
    || key === "three_bet"
    || key === "four_bet"
  ) return "raise";
  return "pressure";
}
function quickMetricTagName(key) {
  if (key.startsWith("fold_to_")) return "弃牌倾向";
  if (key.startsWith("call_vs_")) return "跟注倾向";
  if (key.includes("check_raise") || key.startsWith("raise_vs_")) return "反击倾向";
  if (key.includes("barrel") || key.includes("cbet")) return "持续进攻";
  if (key.includes("probe")) return "刺探下注";
  if (key.includes("donk")) return "领先下注";
  return "行动倾向";
}
function signedPoints(value) {
  if (value == null || !Number.isFinite(value)) return "—";
  const rounded = Math.abs(value).toFixed(Math.abs(value) >= 10 ? 0 : 1);
  return `${value > 0 ? "+" : value < 0 ? "−" : "±"}${value === 0 ? "0" : rounded}`;
}
function playerStreetRead(profile, street) {
  const group = metricStreetGroups.find(item => item.key === street) || metricStreetGroups[0];
  const candidates = group.metrics.map(key => ({
    key,
    metric: profile?.metrics?.[key],
  })).filter(item => item.metric?.mean_pct != null && Number(item.metric.opportunities || 0) > 0);
  const strongest = [...candidates].sort((left, right) => {
    const leftDelta = metricDeviation(left.metric).magnitude;
    const rightDelta = metricDeviation(right.metric).magnitude;
    const leftWeight = Math.min(1, Number(left.metric.opportunities || 0) / 20);
    const rightWeight = Math.min(1, Number(right.metric.opportunities || 0) / 20);
    return rightDelta * rightWeight - leftDelta * leftWeight;
  })[0];
  const deviation = metricDeviation(strongest?.metric);
  const delta = deviation.delta;
  const reliableShift = strongest
    && Number(strongest.metric.opportunities || 0) >= 5
    && ["moderate", "strong"].includes(deviation.level);
  const style = profile?.style || {};
  const label = street === "preflop"
    ? style.label || "待分类"
    : reliableShift
      ? `${quickMetricTagName(strongest.key)} · ${deviation.degreeLabel}${delta > 0 ? "偏高" : "偏低"}`
      : candidates.length
        ? "接近牌池"
        : "样本积累中";
  return {
    label,
    strongest,
    delta,
    deviation,
    direction: reliableShift ? (delta > 0 ? "high" : "low") : "same",
    sample: Math.max(0, ...candidates.map(item => Number(item.metric.opportunities || 0))),
  };
}
function quickExploit(read) {
  const key = read.strongest?.key || "";
  const samples = Number(read.strongest?.metric?.opportunities || 0);
  if (samples < 10 || Math.abs(read.delta || 0) < 5) return "";
  if (key.startsWith("fold_to_")) {
    return read.delta > 0 ? "可增加精选诈唬" : "少纯诈唬，扩大薄价值";
  }
  if (key.includes("call_vs_")) {
    return read.delta > 0 ? "扩大价值尺度" : "可增加阻断牌诈唬";
  }
  if (key.includes("raise") || key.includes("cbet") || key.includes("barrel") || key.includes("donk") || key.includes("probe")) {
    return read.delta > 0 ? "扩大强抓诈与诱捕" : "主动线偏窄，谨慎支付";
  }
  return "";
}
function renderQuickMetric(profile, keys) {
  const key = keys.find(candidate => profile?.metrics?.[candidate]?.mean_pct != null);
  if (!key) {
    return `<span class="player-street-metric unavailable"
      title="${esc(quickMetricTitle(keys[0]))}">
      <small>${esc(quickMetricNames[keys[0]] || "数据")}</small><strong>—</strong><em>n0</em>
    </span>`;
  }
  const metric = profile.metrics[key];
  const deviation = metricDeviation(metric);
  const direction = deviation.level === "neutral" ? "same" : deviation.direction;
  const sparse = Number(metric.opportunities || 0) < 10 ? " sparse" : "";
  return `<span class="player-street-metric metric-family-${quickMetricFamily(key)} ${direction} deviation-${deviation.level}${sparse}"
    title="${esc(quickMetricTitle(key, metric))}">
    <small>${esc(quickMetricNames[key] || metricNames[key] || key)}</small>
    <strong>${esc(quickMetric(profile, key))}</strong>
    <em><b>${esc(deviation.degreeLabel)}</b> ${esc(signedPoints(deviation.delta))}<i>pp</i> · n${esc(metric.opportunities || 0)}</em>
  </span>`;
}
function renderPlayerQuickProfile(profile, street = "preflop", isHero = false) {
  const streetLabel = streetNames[street] || street;
  if (isHero && !profile) {
    return `<div class="player-street-profile hero-profile unavailable">
      <div class="player-profile-line">
        <span class="player-style-tag"><b>${esc(streetLabel)}</b>本人席位</span>
      </div>
      <div class="player-profile-empty">本人历史样本积累中；不会混入对手统计</div>
    </div>`;
  }
  if (!profile) {
    return `<div class="player-street-profile unavailable">
      <div class="player-profile-line">
        <span class="player-style-tag"><b>${esc(streetLabel)}</b>新样本</span>
        <span class="player-profile-sample">H 0</span>
      </div>
      <div class="player-profile-empty">暂无该玩家历史核心数据</div>
    </div>`;
  }
  const read = playerStreetRead(profile, street);
  const slots = quickStreetMetricSlots[street] || quickStreetMetricSlots.preflop;
  const exploit = isHero ? "" : quickExploit(read);
  const sizing = profile.sizing?.[street];
  const footer = [
    sizing?.count ? `尺度中位 ${Number(sizing.median_pot || 0).toFixed(2)}P · n${sizing.count}` : "",
    street !== "preflop" ? `AF ${Number(profile.aggression_factor || 0).toFixed(2)}` : "",
  ].filter(Boolean).join(" · ");
  return `<div class="player-street-profile${isHero ? " hero-profile" : ""}">
    <div class="player-profile-line">
      <span class="player-style-tag street-${esc(read.direction)}"
        title="${esc(read.strongest ? quickMetricTitle(read.strongest.key, read.strongest.metric) : "样本积累中")}">
        <b>${esc(streetLabel)}</b><span>${isHero ? "本人 · " : ""}${esc(read.label)}</span>
      </span>
      <span class="player-profile-sample">H ${esc(profile.hands || 0)} · 街 n${esc(read.sample)}</span>
    </div>
    <div class="player-street-metrics">
      ${slots.map(keys => renderQuickMetric(profile, keys)).join("")}
    </div>
    ${exploit || footer ? `<div class="player-street-footer">
      ${exploit ? `<strong>剥削 · ${esc(exploit)}</strong>` : ""}
      ${footer ? `<span>${esc(footer)}</span>` : ""}
    </div>` : ""}
  </div>
  `;
}
function playerRangeMiniPlaceholder(player, options) {
  if (
    player.is_hero ||
    !player.user_id ||
    options.folded ||
    !options.handId
  ) return "";
  if (options.street === "preflop") {
    return `<div class="player-range-mini waiting">
      <div><strong>范围构成</strong><small>暂无翻后数据</small></div>
    </div>`;
  }
  if (options.nodeScope === "live") {
    return `<div class="player-range-mini waiting">
      <div><strong>范围构成</strong><small>点击当前节点范围加载</small></div>
    </div>`;
  }
  const key = [
    player.user_id,
    options.handId,
    options.rangeMiniRevision || options.street,
  ].join("|");
  return `<div class="player-range-mini loading"
    data-range-mini-key="${esc(key)}"
    data-range-mini-user="${esc(player.user_id)}"
    data-range-mini-hand="${esc(options.handId)}">
    <div><strong>范围构成</strong><small>加载中…</small></div>
    <span class="player-range-mini-skeleton"></span>
  </div>`;
}
function renderHandPlayer(player, options = {}) {
  const linkPlayers = options.linkPlayers === true;
  if (!linkPlayers) {
    return `<div class="player${player.is_hero ? " hero" : ""}">
      <div class="player-name">${esc(player.alias || "未知玩家")}${player.is_hero ? " · 我" : ""}</div>
      <div class="player-meta">${settlementMeta(player)}</div>
      <div class="board">${cards(player.hole_cards)}</div>
    </div>`;
  }
  const seat = Number(player.seat);
  const displaySeat = Number(options.displaySeat) || seat;
  const tableLabel = tableSeatLabels[displaySeat] || "未知方位";
  const lastAction = normalizedAction(options.lastAction?.action);
  const folded = options.folded === true;
  const allIn = options.allIn === true;
  const acting = options.acting === true && !folded;
  const status = folded
    ? "已弃牌"
    : allIn
    ? "全下"
    : acting
    ? "行动中"
    : !lastAction || ["ante", "small_blind", "big_blind"].includes(lastAction)
    ? "在局"
    : actionNames[lastAction] || lastAction;
  const statusClass = folded ? "folded" : allIn ? "all-in" : acting ? "acting" : "active";
  const pokerPosition = (
    player.position && !String(player.position).startsWith("Seat ")
  ) ? ` · ${esc(player.position)}` : "";
  const content = `
    <div class="player-head">
      <div class="player-name" title="${esc(player.alias || "未知玩家")}">${esc(player.alias || "未知玩家")}${player.is_hero ? " · 我" : ""}</div>
      <span class="player-status ${statusClass}">${esc(status)}</span>
    </div>
    <div class="player-meta">S${esc(seat)} · ${esc(tableLabel)}${pokerPosition}</div>
    ${renderPlayerQuickProfile(options.profile, options.street, player.is_hero)}
    ${playerRangeMiniPlaceholder(player, options)}
    <div class="board">${cards(player.hole_cards)}</div>
    `;
  const classes = [
    "player",
    `table-seat-${displaySeat}`,
    player.is_hero ? "hero" : "",
    folded ? "folded" : "",
    allIn ? "all-in" : "",
    acting ? "acting" : "",
  ].filter(Boolean).join(" ");
  if (player.is_hero || !player.user_id) {
    return `<div class="${classes}">${content}</div>`;
  }
  const nodeAttributes = [
    options.handId ? `data-hand-id="${esc(options.handId)}"` : "",
    options.nodeSequence != null ? `data-node-sequence="${esc(options.nodeSequence)}"` : "",
    options.nodeStateHash ? `data-node-state-hash="${esc(options.nodeStateHash)}"` : "",
    options.nodeScope ? `data-node-scope="${esc(options.nodeScope)}"` : "",
  ].filter(Boolean).join(" ");
  return `<div class="${classes} player-with-actions">
    ${content}
    <div class="player-card-actions">
      <button class="player-card-action player-node-trigger live-player-link" type="button"
        data-user="${esc(player.user_id)}" data-alias="${esc(player.alias || player.user_id)}"
        ${nodeAttributes}
        aria-label="查看 ${esc(player.alias || player.user_id)} 基于前序行动的当前节点范围与牌力">
        当前节点范围
      </button>
      <button class="player-card-action player-profile-trigger" type="button"
        data-user="${esc(player.user_id)}" data-alias="${esc(player.alias || player.user_id)}"
        aria-label="查看 ${esc(player.alias || player.user_id)} 的完整历史画像">
        历史画像
      </button>
    </div>
  </div>`;
}
function liveEquityDashboardMarkup() {
  return `<section class="live-equity-dashboard" aria-label="所有在局玩家的范围权益曲线">
    <div class="equity-display-controls">
      <span>RANGE EQUITY · LIVE BOARD</span>
      <label class="reasoning-switch">
        <input id="all-equity-toggle" type="checkbox"${state.allEquityEnabled ? " checked" : ""} />
        <span>显示所有在局玩家</span>
      </label>
    </div>
    <div class="live-equity-grid">
      <section id="equity-curve" class="equity-curve compact">
        <small>翻牌后生成当前范围权益曲线。</small>
      </section>
      <section id="all-equity-curves" class="all-equity-curves"${state.allEquityEnabled ? "" : " hidden"}></section>
    </div>
  </section>`;
}
function renderHand(hand, options = {}) {
  if (!hand) return `<div class="empty">等待下一手牌。录制器识别到发牌后，这里会实时出现玩家、公共牌与完整行动线。</div>`;
  const linkPlayers = options.linkPlayers === true;
  const quality = qualityLabel(hand.quality_status);
  const qualityNotice = hand.quality_status === "bad" || hand.quality_status === "partial"
    ? `<div class="quality-notice ${esc(hand.quality_status)}"><strong>${esc(quality)}</strong> · ${esc((hand.quality_reasons || []).join("；") || "牌局记录不完整")} · 已排除统计</div>`
    : "";
  const squid = renderHandSquid(hand.squid_hand);
  const inference = renderRangePredictions(hand.range_predictions);
  const grouped = {};
  const foldedSeats = new Set();
  const allInSeats = new Set();
  const lastActions = new Map();
  const playerActionFingerprints = new Map();
  for (const action of hand.actions || []) {
    (grouped[action.street] ||= []).push(action);
    const seat = Number(action.seat);
    if (!Number.isFinite(seat)) continue;
    const kind = normalizedAction(action.action);
    lastActions.set(seat, action);
    playerActionFingerprints.set(
      seat,
      [
        playerActionFingerprints.get(seat) || "",
        `${action.sequence || 0}:${kind}:${action.amount_to ?? action.amount ?? 0}`,
      ].filter(Boolean).join(","),
    );
    if (kind === "fold") foldedSeats.add(seat);
    if (kind === "all_in") allInSeats.add(seat);
  }
  const profileByUser = new Map(
    (options.opponents || []).map(profile => [String(profile.user_id), profile]),
  );
  if (options.hero?.user_id) {
    profileByUser.set(String(options.hero.user_id), options.hero);
  }
  const actingSeat = Number(options.decision?.subject_seat);
  const rawPlayers = [...(hand.players || [])];
  const heroPlayer = (
    rawPlayers.find(player => player.is_hero) ||
    rawPlayers.find(player => (
      options.hero?.user_id &&
      String(player.user_id || "") === String(options.hero.user_id)
    ))
  );
  const heroSeat = Number(heroPlayer?.seat);
  const players = rawPlayers.sort((left, right) => {
    const leftOrder = tableSeatOrder.indexOf(
      tableDisplaySeat(left.seat, heroSeat),
    );
    const rightOrder = tableSeatOrder.indexOf(
      tableDisplaySeat(right.seat, heroSeat),
    );
    return (leftOrder < 0 ? 99 + Number(left.seat) : leftOrder)
      - (rightOrder < 0 ? 99 + Number(right.seat) : rightOrder);
  });
  const timeline = timelineStreets.map(street => `
    <div class="street${grouped[street]?.length ? "" : " empty-street"}">
      <div class="street-name">${esc(streetNames[street] || street)}</div>
      <div class="street-actions">${renderTimelineActions(street, grouped[street])}</div>
    </div>`).join("");
  const timelineMarkup = linkPlayers
    ? `<details class="action-log-disclosure"${state.actionLogExpanded ? " open" : ""}>
        <summary>
          <span>行动线 LOG</span>
          <small>${esc((hand.actions || []).length)} 条记录</small>
        </summary>
        <div class="timeline">${timeline}</div>
      </details>`
    : `<div class="timeline">${timeline}</div>`;
  const boardStreet = (hand.board || []).length >= 5
    ? "river"
    : (hand.board || []).length === 4
      ? "turn"
      : (hand.board || []).length >= 3
        ? "flop"
        : "preflop";
  return `${qualityNotice}
    <div class="hand-top">
      <div><p class="eyebrow">HAND HISTORY · 第 ${esc(hand.hand_number ?? "?")} 手</p>
        <div class="hand-id">#${esc(hand.hand_id)}</div>
        <small>${esc(hand.played_at_cn || "时间同步中")} · 中国时区</small>
      </div>
      <div class="pot"><small>总底池</small><strong>${esc(hand.pot ?? "—")}</strong></div>
    </div>
    ${linkPlayers ? "" : `<div class="board">${cards(hand.board)}</div>`}
    <div class="players${linkPlayers ? " table-layout" : ""}">
      ${linkPlayers ? `<div class="table-center" role="region" aria-label="公共牌">
        <span>${esc(streetNames[boardStreet] || "BOARD")}</span>
        <div class="board table-board">${(hand.board || []).length ? cards(hand.board) : "<small>等待公共牌</small>"}</div>
        <small>底池 ${esc(hand.pot ?? "—")} · 左 ← 牌桌方位 → 右</small>
      </div>` : ""}
      ${players.map(player => renderHandPlayer(player, {
        linkPlayers,
        displaySeat: tableDisplaySeat(player.seat, heroSeat),
        folded: typeof player.folded === "boolean"
          ? player.folded
          : foldedSeats.has(Number(player.seat)),
        allIn: allInSeats.has(Number(player.seat)),
        acting: Number.isFinite(actingSeat) && Number(player.seat) === actingSeat,
        lastAction: lastActions.get(Number(player.seat)),
        profile: profileByUser.get(String(player.user_id)),
        street: boardStreet,
        handId: options.handId || hand.hand_id,
        rangeMiniRevision: [
          (hand.board || []).join(""),
          playerActionFingerprints.get(Number(player.seat)) || "none",
        ].join(":"),
        nodeSequence: options.nodeSequence,
        nodeStateHash: options.nodeStateHash,
        nodeScope: options.nodeScope,
      })).join("")}
    </div>
    ${linkPlayers ? liveEquityDashboardMarkup() : ""}
    ${squid}
    ${inference}
    ${timelineMarkup}`;
}
function playerRangeMiniMarkup(data) {
  const composition = data.composition || {};
  const values = composition.posterior || {};
  const aggressive = Boolean(composition.aggressive_action);
  const fourthKey = aggressive ? "bluff_candidates" : "air";
  const items = [
    ["strong_value", "强", "强价值"],
    ["marginal_showdown", "摊", "边缘摊牌价值"],
    ["draws", "听", "听牌"],
    [fourthKey, aggressive ? "诈" : "空", aggressive ? "Bluff 候选" : "空气"],
  ].map(([key, shortLabel, label]) => ({
    key,
    shortLabel,
    label,
    value: Math.max(0, Number(values[key] || 0)),
  }));
  if (!items.some(item => item.value > 0)) {
    return `<div><strong>范围构成</strong><small>暂无翻后范围</small></div>`;
  }
  const source = data.range?.history_conditioned
    ? data.range?.production_enabled ? "历史后验" : "历史偏移"
    : data.range?.source === "board_action_heuristic"
      ? "简易估计"
      : "翻前先验";
  const title = items
    .map(item => `${item.label} ${item.value.toFixed(0)}%`)
    .join(" · ");
  return `<div title="${esc(`${title}；点击“当前节点范围”查看依据`)}">
      <strong>范围构成</strong><small>${esc(source)}</small>
    </div>
    <span class="player-range-mini-track">${items.map(item =>
      `<i class="mini-${esc(item.key)}" style="width:${Math.min(100, item.value)}%"></i>`
    ).join("")}</span>
    <span class="player-range-mini-values">${items.map(item =>
      `<b class="mini-${esc(item.key)}">${esc(item.shortLabel)} ${esc(item.value.toFixed(0))}</b>`
    ).join("")}</span>`;
}
function updatePlayerRangeMiniElements(data) {
  const userId = String(data.player?.user_id || "");
  const handId = String(data.node?.hand_id || "");
  if (!userId || !handId) return;
  document.querySelectorAll("[data-range-mini-key]").forEach(element => {
    if (
      element.dataset.rangeMiniUser !== userId ||
      element.dataset.rangeMiniHand !== handId
    ) return;
    const key = element.dataset.rangeMiniKey;
    state.playerRangeMiniCache.set(key, data);
    element.className = "player-range-mini";
    element.innerHTML = playerRangeMiniMarkup(data);
  });
  while (state.playerRangeMiniCache.size > 128) {
    state.playerRangeMiniCache.delete(
      state.playerRangeMiniCache.keys().next().value,
    );
  }
}
function loadPlayerCardRanges(root = document) {
  root.querySelectorAll("[data-range-mini-key]").forEach(element => {
    const key = element.dataset.rangeMiniKey;
    const cached = state.playerRangeMiniCache.get(key);
    if (cached) {
      element.className = "player-range-mini";
      element.innerHTML = playerRangeMiniMarkup(cached);
      return;
    }
    if (state.playerRangeMiniInFlight.has(key)) return;
    state.playerRangeMiniInFlight.add(key);
    const query = new URLSearchParams({
      hand_id: element.dataset.rangeMiniHand,
      phase: "preview",
    });
    fetch(
      `/api/players/${encodeURIComponent(element.dataset.rangeMiniUser)}/range-at-node?${query}`,
    )
      .then(async response => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        updatePlayerRangeMiniElements(data);
      })
      .catch(() => {
        document.querySelectorAll("[data-range-mini-key]").forEach(current => {
          if (current.dataset.rangeMiniKey !== key) return;
          current.className = "player-range-mini unavailable";
          current.innerHTML = `<div><strong>范围构成</strong><small>暂不可用</small></div>`;
        });
      })
      .finally(() => state.playerRangeMiniInFlight.delete(key));
  });
}
function renderEquityCurve(data = null, options = {}) {
  const target = options.target || document.querySelector("#equity-curve");
  if (!target) return;
  const compact = options.compact ?? Boolean(
    target.closest(".live-equity-dashboard"),
  );
  const curveClass = `equity-curve${compact ? " compact" : ""}`;
  if (!data || data.status !== "ok") {
    target.className = curveClass;
    target.innerHTML = `<small>${esc(data?.reason || "翻牌后生成当前范围权益曲线。")}</small>`;
    return;
  }
  const observing = data.decision_subject === "observer";
  const subjectLabel = options.subjectLabel || (observing ? "当前行动者" : "我的");
  const gradientId = `equity-area-gradient-${String(options.subjectSeat ?? "current").replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const left = 36;
  const right = 620;
  const top = 14;
  const bottom = 184;
  const width = right - left;
  const height = bottom - top;
  const chartPoints = (data.points || []).map(point => ({
    ...point,
    x: left + width * Number(point.percentile || 0) / 100,
    y: bottom - height * Number(point.equity_pct || 0) / 100,
  }));
  const line = chartPoints.map(point => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");
  const area = chartPoints.length
    ? `M ${chartPoints[0].x.toFixed(1)} ${bottom} L ${chartPoints.map(point => `${point.x.toFixed(1)} ${point.y.toFixed(1)}`).join(" L ")} L ${chartPoints.at(-1).x.toFixed(1)} ${bottom} Z`
    : "";
  const grid = [0, 25, 50, 75, 100].map(value => {
    const y = bottom - height * value / 100;
    const x = left + width * value / 100;
    return `<line class="${value === 50 ? "equity-mid-line" : "equity-grid-line"}" x1="${left}" y1="${y}" x2="${right}" y2="${y}"></line>
      <line class="equity-grid-line" x1="${x}" y1="${top}" x2="${x}" y2="${bottom}"></line>
      <text class="equity-axis-label" x="3" y="${y + 3}">${value}%</text>
      <text class="equity-axis-label" x="${x}" y="201" text-anchor="middle">${value}</text>`;
  }).join("");
  const samples = chartPoints.map(point => `
    <circle cx="${point.x}" cy="${point.y}" r="3" fill="transparent">
      <title>${esc(`${point.hand} · 权益 ${point.equity_pct}% · 范围权重 ${point.range_weight_pct}%`)}</title>
    </circle>`).join("");
  const hero = data.hero;
  const heroMarker = hero
    ? (() => {
        const x = left + width * Number(hero.percentile || 0) / 100;
        const y = bottom - height * Number(hero.equity_pct || 0) / 100;
        const rightEdge = x > right - 90;
        return `<line class="equity-hero-guide" x1="${x}" y1="${y}" x2="${x}" y2="${bottom}"></line>
          <circle class="equity-hero-dot" cx="${x}" cy="${y}" r="5"></circle>
          <text class="equity-hero-label" x="${x + (rightEdge ? -9 : 9)}" y="${Math.max(top + 9, y - 9)}" text-anchor="${rightEdge ? "end" : "start"}">${esc(`${hero.hand} ${hero.equity_pct}%`)}</text>`;
      })()
    : "";
  const quantiles = data.quantiles || {};
  const stats = compact
    ? `<div class="equity-stats">
        <div class="equity-stat"><small>范围均值</small><strong>${esc(data.range_equity_pct)}%</strong></div>
        <div class="equity-stat"><small>中间 80%</small><strong>${esc(quantiles.p10)}–${esc(quantiles.p90)}%</strong></div>
        <div class="equity-stat"><small>范围组合</small><strong>${esc(data.hero_range?.equivalent_combos)}</strong></div>
      </div>`
    : `<div class="equity-stats">
        <div class="equity-stat"><small>${observing ? "行动者底牌" : "我的手牌"}</small><strong>${esc(hero ? `${hero.hand} · ${hero.equity_pct}%` : "未公开")}</strong></div>
        <div class="equity-stat"><small>范围均值</small><strong>${esc(data.range_equity_pct)}%</strong></div>
        <div class="equity-stat"><small>中间 80% 区间</small><strong>${esc(quantiles.p10)}–${esc(quantiles.p90)}%</strong></div>
        <div class="equity-stat"><small>对手 / 范围组合</small><strong>${esc(data.opponents)} / ${esc(data.hero_range?.equivalent_combos)}</strong></div>
      </div>`;
  target.className = curveClass;
  target.innerHTML = `<div class="equity-curve-head">
      <div>
        <small>BOARD-AWARE · RANGE EQUITY</small>
        <h3>${esc(subjectLabel)}范围权益分布 · ${esc((data.board || []).map(card => cardText(card)).join(" "))}</h3>
      </div>
      <strong>${esc(hero?.equity_pct ?? data.range_equity_pct)}%</strong>
    </div>
    ${stats}
    <svg class="equity-chart" viewBox="0 0 640 210" role="img" tabindex="0" aria-label="${esc(subjectLabel)}范围按权益从低到高排列的曲线；悬浮可查看最近手牌类">
      <defs>
        <linearGradient id="${gradientId}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#d3b55b" stop-opacity=".24"></stop>
          <stop offset="100%" stop-color="#d3b55b" stop-opacity=".015"></stop>
        </linearGradient>
      </defs>
      ${grid}
      <path class="equity-area" d="${area}" style="fill:url(#${gradientId})"></path>
      <polyline class="equity-line" points="${line}"></polyline>
      ${samples}
      ${heroMarker}
      <g class="equity-hover-layer" visibility="hidden">
        <line class="equity-hover-line" y1="${top}" y2="${bottom}"></line>
        <circle class="equity-hover-dot" r="4"></circle>
      </g>
      <text class="equity-axis-label" x="${(left + right) / 2}" y="209" text-anchor="middle">${esc(subjectLabel)}范围权益百分位 →</text>
    </svg>
    <div class="equity-hover-tooltip" hidden></div>
    <div class="equity-curve-note">
      <span>${observing && !hero ? "底牌未公开，仅显示范围 · " : ""}${esc(data.hero_range?.action_line || "未捕获翻前行动")} · ${esc(data.opponent_model)}</span>
      <span>悬浮曲线查看手牌 · ${esc(confidenceLabel(data.confidence))}置信度 · ${esc(data.latency_ms)}ms</span>
    </div>`;
  const chart = target.querySelector(".equity-chart");
  const hoverLayer = target.querySelector(".equity-hover-layer");
  const hoverLine = target.querySelector(".equity-hover-line");
  const hoverDot = target.querySelector(".equity-hover-dot");
  const tooltip = target.querySelector(".equity-hover-tooltip");
  if (chartPoints.length && chart && hoverLayer && tooltip) {
    const showNearestPoint = event => {
      const chartBox = chart.getBoundingClientRect();
      const targetBox = target.getBoundingClientRect();
      const chartX = (
        (event.clientX - chartBox.left) / Math.max(1, chartBox.width) * 640
      );
      const point = chartPoints.reduce(
        (nearest, candidate) => (
          Math.abs(candidate.x - chartX) < Math.abs(nearest.x - chartX)
            ? candidate
            : nearest
        ),
        chartPoints[0],
      );
      hoverLine.setAttribute("x1", point.x);
      hoverLine.setAttribute("x2", point.x);
      hoverDot.setAttribute("cx", point.x);
      hoverDot.setAttribute("cy", point.y);
      hoverLayer.setAttribute("visibility", "visible");
      tooltip.hidden = false;
      tooltip.innerHTML = `<strong>${esc(point.hand)}</strong>
        <span>底池权益 ${esc(point.equity_pct)}%</span>
        <span>范围百分位 ${esc(point.percentile)}%</span>
        <span>行动线权重 ${esc(point.range_weight_pct)}%</span>
        <small>当前 board 可用 ${esc(point.available_combos)} 个组合</small>`;
      const pointLeft = (
        chartBox.left - targetBox.left + point.x / 640 * chartBox.width
      );
      const pointTop = (
        chartBox.top - targetBox.top + point.y / 210 * chartBox.height
      );
      tooltip.style.left = `${pointLeft}px`;
      tooltip.style.top = `${Math.max(70, pointTop)}px`;
      const boundaryBox = (
        target.closest(".live-equity-grid") || target
      ).getBoundingClientRect();
      const pointViewportX = (
        chartBox.left + point.x / 640 * chartBox.width
      );
      const spaceLeft = pointViewportX - boundaryBox.left;
      const spaceRight = boundaryBox.right - pointViewportX;
      tooltip.classList.toggle(
        "flip",
        spaceRight < 190 && spaceLeft > spaceRight,
      );
    };
    const hideHover = () => {
      hoverLayer.setAttribute("visibility", "hidden");
      tooltip.hidden = true;
    };
    chart.addEventListener("pointermove", showNearestPoint);
    chart.addEventListener("pointerleave", hideHover);
    chart.addEventListener("blur", hideHover);
  }
}
function equityCacheKey(decision, hand) {
  if (decision) return `decision:${decisionCacheKey(decision)}`;
  if (!hand) return "hand:none";
  const reveals = (hand.players || [])
    .map(player => `${player.seat}:${(player.hole_cards || []).join("")}`)
    .join("|");
  return [
    "hand",
    hand.hand_id || "unknown",
    (hand.board || []).join(""),
    (hand.actions || []).length,
    reveals,
  ].join(":");
}
function activeEquityPlayers(hand) {
  const foldedSeats = new Set(
    (hand?.actions || [])
      .filter(action => normalizedAction(action.action) === "fold")
      .map(action => Number(action.seat)),
  );
  return (hand?.players || []).filter(player => {
    const seat = Number(player.seat);
    return Number.isInteger(seat) && !player.folded && !foldedSeats.has(seat);
  });
}
function currentEquitySeat(decision, hand, activePlayers) {
  const decisionSeat = Number(decision?.subject_seat ?? decision?.acting_seat);
  if (activePlayers.some(player => Number(player.seat) === decisionSeat)) {
    return decisionSeat;
  }
  const hero = activePlayers.find(player => player.is_hero);
  if (hero) return Number(hero.seat);
  const activeSeats = new Set(activePlayers.map(player => Number(player.seat)));
  const lastActor = [...(hand?.actions || [])].reverse().find(
    action => activeSeats.has(Number(action.seat)),
  );
  return lastActor ? Number(lastActor.seat) : Number(activePlayers[0]?.seat);
}
function abortEquityRequests(controllers, keepKey = null) {
  for (const [key, controller] of controllers) {
    if (key === keepKey) continue;
    controller.abort();
    controllers.delete(key);
  }
}
function abortAllEquityRequests() {
  abortEquityRequests(state.allEquityControllers);
  state.allEquityRequests.clear();
}
async function loadAllEquityCurves(decision, hand) {
  const container = document.querySelector("#all-equity-curves");
  if (!container) return;
  if (!state.allEquityEnabled) {
    abortAllEquityRequests();
    state.allEquityGeneration += 1;
    state.allEquityViewKey = "";
    container.hidden = true;
    container.innerHTML = "";
    return;
  }
  container.hidden = false;
  const board = decision?.board || hand?.board || [];
  const observingSnapshot = (
    !decision &&
    hand?.status === "in_progress" &&
    !(hand.players || []).some(player => player.is_hero)
  );
  if (observingSnapshot && !state.observerLiveEnabled) {
    abortAllEquityRequests();
    state.allEquityGeneration += 1;
    state.allEquityViewKey = "";
    container.innerHTML = `<div class="all-equity-status">请先开启“观战实时范围”。</div>`;
    return;
  }
  if (!hand?.hand_id || hand.status !== "in_progress" || board.length < 3) {
    abortAllEquityRequests();
    state.allEquityGeneration += 1;
    state.allEquityViewKey = "";
    container.innerHTML = `<div class="all-equity-status">翻牌后显示其他在局玩家的范围权益曲线。</div>`;
    return;
  }
  const activePlayers = activeEquityPlayers(hand);
  const subjectSeat = currentEquitySeat(decision, hand, activePlayers);
  const otherPlayers = activePlayers.filter(
    player => Number(player.seat) !== subjectSeat,
  );
  const snapshotKey = equityCacheKey(null, hand);
  const viewKey = [
    snapshotKey,
    subjectSeat,
    otherPlayers.map(player => Number(player.seat)).join(","),
  ].join("|");
  if (
    state.allEquityViewKey === viewKey &&
    otherPlayers.every(player => (
      container.querySelector(`[data-equity-seat="${Number(player.seat)}"]`)
    ))
  ) return;
  abortAllEquityRequests();
  state.allEquityViewKey = viewKey;
  const generation = ++state.allEquityGeneration;
  if (!otherPlayers.length) {
    container.innerHTML = `<div class="all-equity-status">当前没有其他在局玩家。</div>`;
    return;
  }
  container.innerHTML = otherPlayers.map(player => `
    <article class="equity-curve compact loading" data-equity-seat="${esc(player.seat)}">
      <small>正在计算 ${esc(player.alias || `座位 ${player.seat}`)} 的范围权益曲线…</small>
    </article>
  `).join("");
  for (const player of otherPlayers) {
    if (generation !== state.allEquityGeneration) return;
    const seat = Number(player.seat);
    const target = container.querySelector(`[data-equity-seat="${seat}"]`);
    const cacheKey = `${snapshotKey}:subject:${seat}`;
    let data = state.equityCurves.get(cacheKey);
    if (!data) {
      let request = state.allEquityRequests.get(cacheKey);
      let controller = state.allEquityControllers.get(cacheKey);
      if (!request) {
        controller = new AbortController();
        state.allEquityControllers.set(cacheKey, controller);
        request = (async () => {
          const query = new URLSearchParams({
            hand_id: hand.hand_id,
            subject_seat: String(seat),
          });
          const response = await fetch(`/api/equity/current?${query}`, {
            signal: controller.signal,
          });
          const responseData = await response.json();
          if (!response.ok) {
            throw new Error(responseData.detail || `HTTP ${response.status}`);
          }
          state.equityCurves.set(cacheKey, responseData);
          while (state.equityCurves.size > 64) {
            state.equityCurves.delete(state.equityCurves.keys().next().value);
          }
          return responseData;
        })();
        state.allEquityRequests.set(cacheKey, request);
      }
      try {
        data = await request;
      } catch (error) {
        if (error.name === "AbortError") return;
        data = {
          status: "unavailable",
          reason: `${player.alias || `座位 ${seat}`}的权益曲线不可用：${error.message}`,
        };
      } finally {
        if (
          controller &&
          state.allEquityControllers.get(cacheKey) === controller
        ) {
          state.allEquityControllers.delete(cacheKey);
        }
        if (state.allEquityRequests.get(cacheKey) === request) {
          state.allEquityRequests.delete(cacheKey);
        }
      }
    }
    if (generation !== state.allEquityGeneration) return;
    renderEquityCurve(data, {
      target,
      compact: true,
      subjectSeat: seat,
      subjectLabel: `${player.alias || `座位 ${seat}`} · `,
    });
  }
}
async function loadEquityCurve(decision, hand = state.data?.current_hand) {
  const target = document.querySelector("#equity-curve");
  loadAllEquityCurves(decision, hand);
  const board = decision?.board || hand?.board || [];
  const observingSnapshot = (
    !decision &&
    hand?.status === "in_progress" &&
    !(hand.players || []).some(player => player.is_hero)
  );
  if (observingSnapshot && !state.observerLiveEnabled) {
    abortEquityRequests(state.equityControllers);
    renderEquityCurve({
      status: "unavailable",
      reason: "观战实时范围已关闭；开启后会生成当前行动者的范围权益曲线。",
    });
    return;
  }
  if (
    board.length < 3 ||
    (!decision && hand?.status !== "in_progress")
  ) {
    abortEquityRequests(state.equityControllers);
    renderEquityCurve({
      status: "unavailable",
      reason: decision?.decision_subject === "observer" || observingSnapshot
        ? "观战中：翻牌后按当前行动者范围生成权益曲线。"
        : "翻牌后生成当前范围权益曲线。",
    });
    return;
  }
  const cacheKey = equityCacheKey(decision, hand);
  abortEquityRequests(state.equityControllers, cacheKey);
  const cached = state.equityCurves.get(cacheKey);
  if (cached) {
    renderEquityCurve(cached);
    return;
  }
  if (state.equityInFlight.has(cacheKey)) return;
  state.equityInFlight.add(cacheKey);
  const controller = new AbortController();
  state.equityControllers.set(cacheKey, controller);
  target.className = "equity-curve loading";
  target.innerHTML = `<small>正在按当前 board、${observingSnapshot ? "行动者" : "英雄"}行动线和对手范围计算权益分布…</small>`;
  try {
    const query = decision
      ? `sequence=${encodeURIComponent(decision.sequence)}`
      : `hand_id=${encodeURIComponent(hand.hand_id)}`;
    const response = await fetch(
      `/api/equity/current?${query}`,
      { signal: controller.signal },
    );
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    if (data.stale) return;
    state.equityCurves.set(cacheKey, data);
    while (state.equityCurves.size > 32) {
      state.equityCurves.delete(state.equityCurves.keys().next().value);
    }
    if (
      equityCacheKey(state.liveDecision, state.data?.current_hand) === cacheKey
    ) {
      renderEquityCurve(data);
    }
  } catch (error) {
    if (error.name === "AbortError") return;
    if (
      equityCacheKey(state.liveDecision, state.data?.current_hand) === cacheKey
    ) {
      renderEquityCurve({
        status: "unavailable",
        reason: `权益曲线不可用：${error.message}`,
      });
    }
  } finally {
    if (state.equityControllers.get(cacheKey) === controller) {
      state.equityControllers.delete(cacheKey);
    }
    state.equityInFlight.delete(cacheKey);
  }
}
function renderHands(hands = []) {
  document.querySelector("#hands-list").innerHTML = hands.length ? hands.map(hand => `
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
    </article>`).join("") : `<div class="empty-result">当前筛选下没有牌谱</div>`;
  document.querySelectorAll("[data-hand]").forEach(node => {
    node.addEventListener("click", () => openHand(node.dataset.hand));
  });
  renderHistoryStatus();
}
function renderHistoryStatus() {
  const count = document.querySelector("#history-count");
  const clear = document.querySelector("#history-clear-player");
  const more = document.querySelector("#hands-load-more");
  if (!count || !clear || !more) return;
  const shown = state.historyHands.length;
  const total = state.historyTotal;
  const scope = state.historyPlayer
    ? `${state.historyPlayer.alias} · 已亮牌牌谱`
    : (state.validOnly ? "全部有效牌谱" : "全部历史牌谱");
  const activeFilters = [
    state.historyMode ? (state.historyMode === "squid" ? "鱿鱼" : "普通德州") : "",
    state.historyResult ? { won: "盈利", lost: "亏损", even: "持平" }[state.historyResult] : "",
    state.historyStreet ? { preflop: "止步翻前", flop: "止步翻牌", turn: "止步转牌", river: "到达河牌" }[state.historyStreet] : "",
    state.historyRevealed && !state.historyPlayer ? "有对手亮牌" : "",
    state.historyQuery ? `搜索“${state.historyQuery}”` : "",
  ].filter(Boolean);
  count.textContent = state.historyError
    ? `牌谱加载失败：${state.historyError}`
    : `${scope}${activeFilters.length ? ` · ${activeFilters.join(" · ")}` : ""} · 已加载 ${shown}${total == null ? "" : ` / ${total}`}`;
  clear.hidden = !state.historyPlayer;
  more.hidden = !state.historyHasMore;
  more.disabled = state.historyLoading;
  more.textContent = state.historyLoading ? "正在加载…" : "加载更多牌谱";
}
async function loadHistory({ reset = false } = {}) {
  if (reset) {
    state.historyGeneration += 1;
    state.historyRequestKey = `reset-${state.historyGeneration}`;
    state.historyLoading = false;
    state.historyHands = [];
    state.historyTotal = null;
    state.historyHasMore = false;
    state.historyLoaded = false;
  }
  if (state.historyLoading) return;
  const requestKey = [
    state.historyGeneration,
    state.historyMode || "all",
    state.validOnly,
    state.historyPlayer?.userId || "",
    state.historyQuery,
    state.historyResult,
    state.historyStreet,
    state.historyRevealed,
  ].join("|");
  state.historyRequestKey = requestKey;
  state.historyLoading = true;
  state.historyError = "";
  renderHistoryStatus();
  const query = new URLSearchParams({
    limit: state.historyPlayer ? "100" : "50",
    offset: String(state.historyHands.length),
    valid_only: String(state.validOnly),
  });
  if (state.historyMode) query.set("mode", state.historyMode);
  if (state.historyQuery) query.set("q", state.historyQuery);
  if (state.historyResult) query.set("result", state.historyResult);
  if (state.historyStreet) query.set("street", state.historyStreet);
  if (state.historyRevealed) query.set("revealed_only", "true");
  if (state.historyPlayer) {
    query.set("user_id", state.historyPlayer.userId);
    query.set("revealed_only", "true");
  }
  try {
    const response = await fetch(`/api/hands?${query}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    if (state.historyRequestKey !== requestKey) return;
    const known = new Set(state.historyHands.map(hand => hand.hand_id));
    state.historyHands.push(
      ...(data.hands || []).filter(hand => !known.has(hand.hand_id)),
    );
    state.historyTotal = Number(data.total || 0);
    state.historyHasMore = Boolean(data.has_more);
    state.historyLoaded = true;
    renderHands(state.historyHands);
  } catch (error) {
    if (state.historyRequestKey === requestKey) state.historyError = error.message;
  } finally {
    if (state.historyRequestKey === requestKey) {
      state.historyLoading = false;
      renderHistoryStatus();
    }
  }
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
function profileMatchesSearch(player, needle) {
  if (!needle) return true;
  return (
    String(player.alias || "").toLocaleLowerCase().includes(needle) ||
    String(player.user_id || "").toLocaleLowerCase().includes(needle) ||
    (player.is_self && "本人".includes(needle))
  );
}
function renderPlayerProfileRow(player, { isSelf = false } = {}) {
  const userId = String(player.user_id);
  const expanded = state.expandedOpponents.has(userId);
  const title = isSelf
    ? `<strong>${esc(player.alias)} · 本人</strong><br><small>ID ${esc(player.user_id)} · 不计入对手列表</small>`
    : `<strong>${esc(player.alias)}</strong><br><small>ID ${esc(player.user_id)}</small>`;
  return `
    <article class="opponent-row${isSelf ? " hero-row" : ""}${expanded ? " expanded" : ""}">
      <button class="opponent-summary" type="button" aria-expanded="${expanded}" data-user="${esc(userId)}">
        <span>${title}</span>
        <span class="metric"><small>手数</small>${esc(player.hands)}</span>
        ${summaryMetric(player, "vpip")}
        ${summaryMetric(player, "pfr")}
        ${summaryMetric(player, "three_bet")}
        <span class="metric"><small>WTSD</small>${esc(player.wtsd_pct)}%</span>
        <span class="metric"><small>净额</small>${player.net >= 0 ? "+" : ""}${esc(player.net)}</span>
      </button>
      <div class="profile-detail">
        ${renderProfile(player)}
      </div>
    </article>`;
}
function renderOpponents(opponents = []) {
  const needle = state.opponentSearch.trim().toLocaleLowerCase();
  const hero = state.data?.hero;
  const heroRow = hero?.user_id
    ? { ...hero, is_self: true }
    : null;
  const showHero = Boolean(heroRow && profileMatchesSearch(heroRow, needle));
  const visible = opponents.filter(player => profileMatchesSearch(player, needle));
  const numeric = value => Number(value || 0);
  visible.sort((left, right) => {
    if (state.opponentSort === "revealed_desc") {
      return numeric(right.revealed_hands) - numeric(left.revealed_hands);
    }
    if (state.opponentSort === "net_desc") {
      return numeric(right.net) - numeric(left.net);
    }
    if (state.opponentSort === "net_asc") {
      return numeric(left.net) - numeric(right.net);
    }
    if (state.opponentSort === "vpip_desc") {
      return numeric(right.vpip_pct) - numeric(left.vpip_pct);
    }
    if (state.opponentSort === "alias_asc") {
      return String(left.alias || "").localeCompare(String(right.alias || ""), "zh-CN");
    }
    return numeric(right.hands) - numeric(left.hands);
  });
  const count = document.querySelector("#opponent-count");
  if (count) {
    const shown = visible.length + (showHero ? 1 : 0);
    const total = opponents.length + (heroRow ? 1 : 0);
    count.textContent = showHero
      ? `显示 ${shown} / ${total} · 含本人`
      : `显示 ${visible.length} / ${opponents.length}`;
  }
  const rows = [
    ...(showHero ? [renderPlayerProfileRow(heroRow, { isSelf: true })] : []),
    ...visible.map(player => renderPlayerProfileRow(player)),
  ];
  document.querySelector("#opponents-list").innerHTML = rows.length
    ? rows.join("")
    : `<div class="empty-result">${needle ? "没有匹配的玩家" : "没有匹配的对手"}</div>`;
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
  document.querySelectorAll(".profile-revealed-hands").forEach(button => {
    button.addEventListener("click", event => {
      event.stopPropagation();
      openOpponentRevealedHands(
        button.dataset.user,
        button.dataset.alias || button.dataset.user,
      );
    });
  });
}
function summaryMetric(player, key) {
  const metric = player.metrics?.[key];
  if (!metric) return `<span class="metric"><small>${esc(metricNames[key] || key)}</small>—</span>`;
  const deviation = metricDeviation(metric);
  const directionClass = deviation.level === "neutral" ? "same" : deviation.direction;
  const alertClass = deviation.highlight
    ? ` metric-alert deviation-${deviation.direction} deviation-${deviation.level}`
    : "";
  const shift = deviation.level === "neutral"
    ? ""
    : `<b class="summary-shift">${esc(deviation.degreeLabel)} ${esc(deviation.signedDelta)}pp</b>`;
  return `<span class="metric metric-comparison-${directionClass}${alertClass}" title="牌池 ${esc(metric.population_mean_pct)}% · 偏移 ${deviation.signedDelta}pp · 相对 ${deviation.signedRelativePct}% · 赔率 ${deviation.oddsRatio.toFixed(2)}× · ${esc(deviation.degreeLabel)} · 80% 区间 ${esc(metric.low_pct)}–${esc(metric.high_pct)}%">
    <small>${esc(metricNames[key] || key)}</small>${esc(metric.mean_pct)}% <i>(${esc(metric.opportunities)})</i>
    ${shift}
  </span>`;
}
function metricDeviation(metric) {
  const mean = Number(metric?.mean_pct);
  const population = Number(metric?.population_mean_pct);
  const low = Number(metric?.low_pct);
  const high = Number(metric?.high_pct);
  const delta = (
    Number.isFinite(mean) && Number.isFinite(population)
      ? Math.round((mean - population) * 10) / 10
      : 0
  );
  const magnitude = Math.abs(delta);
  const absoluteLevel = magnitude < 3
    ? "neutral"
    : magnitude < 8
      ? "slight"
      : magnitude < 15
        ? "moderate"
        : "strong";
  const boundedMean = Math.min(99.5, Math.max(0.5, mean)) / 100;
  const boundedPopulation = Math.min(99.5, Math.max(0.5, population)) / 100;
  const rawOddsRatio = (
    (boundedMean / (1 - boundedMean)) /
    (boundedPopulation / (1 - boundedPopulation))
  );
  const oddsRatio = Number.isFinite(rawOddsRatio)
    ? Math.max(rawOddsRatio, 1 / rawOddsRatio)
    : 1;
  const oddsLevel = oddsRatio < 1.25
    ? "neutral"
    : oddsRatio < 1.6
      ? "slight"
      : oddsRatio < 2.5
        ? "moderate"
        : "strong";
  const levels = ["neutral", "slight", "moderate", "strong"];
  const level = levels[
    Math.max(levels.indexOf(absoluteLevel), levels.indexOf(oddsLevel))
  ];
  const relativePct = (
    Number.isFinite(population) && population !== 0
      ? Math.round((delta / population) * 1000) / 10
      : 0
  );
  const degreeLabel = {
    neutral: "无明显偏移",
    slight: "轻微",
    moderate: "中度",
    strong: "显著",
  }[level];
  const direction = delta > 0 ? "high" : delta < 0 ? "low" : "same";
  const confidence = String(metric?.confidence || "very_low");
  const intervalExcludesPopulation = (
    Number.isFinite(low) &&
    Number.isFinite(high) &&
    Number.isFinite(population) &&
    (population < low || population > high)
  );
  const reliable = (
    ["medium", "high"].includes(confidence) &&
    intervalExcludesPopulation
  );
  return {
    delta,
    magnitude,
    level,
    absoluteLevel,
    oddsLevel,
    oddsRatio,
    relativePct,
    signedRelativePct: `${relativePct > 0 ? "+" : relativePct < 0 ? "−" : "±"}${Math.abs(relativePct).toFixed(1)}`,
    degreeLabel,
    direction,
    reliable,
    highlight: ["moderate", "strong"].includes(level),
    watch: !reliable && ["moderate", "strong"].includes(level),
    actionable: reliable && level !== "neutral",
    signedDelta: `${delta > 0 ? "+" : delta < 0 ? "−" : "±"}${Math.abs(delta).toFixed(1)}`,
  };
}
function metricSampleLabel(confidence) {
  return {
    none: "无样本",
    very_low: "样本极少",
    low: "样本偏少",
    medium: "样本一般",
    high: "样本充足",
  }[confidence] || "样本待定";
}
function metricExploitAdvice(key, direction) {
  if (key.startsWith("fold_to_")) {
    return direction === "high"
      ? "剥削：在这个节点增加诈唬"
      : "剥削：减少诈唬，扩大薄价值";
  }
  if (key.endsWith("check_raise")) {
    return direction === "high"
      ? "剥削：减少自动下注，用强范围继续"
      : "剥削：扩大持续下注与薄价值";
  }
  if (key.endsWith("_cbet") || key.endsWith("_probe")) {
    return direction === "high"
      ? "剥削：增加跟注或反击，避免过度弃牌"
      : "剥削：其示弱后增加主动夺池";
  }
  const advice = {
    vpip: {
      high: "剥削：扩大隔离与薄价值范围",
      low: "剥削：扩大偷盲，尊重其入池范围",
    },
    pfr: {
      high: "剥削：收紧边缘入池，增加强牌反击",
      low: "剥削：针对跟注入池扩大价值隔离",
    },
    rfi: {
      high: "剥削：扩大强牌 3BET 与合适的防守",
      low: "剥削：扩大偷盲，尊重其主动开池",
    },
    three_bet: {
      high: "剥削：扩大强牌 4BET 与位置内跟注",
      low: "剥削：扩大开池，被 3BET 时提高信用",
    },
    four_bet: {
      high: "剥削：减少轻率 3BET，准备更宽继续",
      low: "剥削：扩大线性 3BET，尊重其 4BET",
    },
  };
  return advice[key]?.[direction] || "";
}
function renderProfileMetric(key, value) {
  const deviation = metricDeviation(value);
  const confidence = String(value.confidence || "very_low");
  const sparse = ["none", "very_low", "low"].includes(confidence);
  const directionClass = deviation.level === "neutral" ? "same" : deviation.direction;
  const directionText = deviation.level === "neutral"
    ? `≈ 接近牌池 ${deviation.signedDelta}pp`
    : `${deviation.delta > 0 ? "↑" : "↓"} ${deviation.degreeLabel}偏${deviation.direction === "high" ? "高" : "低"} ${deviation.signedDelta}pp`;
  const classes = [
    "profile-metric",
    `metric-comparison-${directionClass}`,
    `deviation-${deviation.level}`,
    sparse ? "sample-sparse" : "",
    deviation.highlight ? `deviation-${deviation.direction}` : "",
    deviation.watch ? "deviation-watch" : "",
  ].filter(Boolean).join(" ");
  const deviationBadge = deviation.level === "neutral"
    ? ""
    : `<span class="metric-deviation ${deviation.direction} level-${deviation.level}${deviation.watch ? " watch" : ""}">${esc(deviation.degreeLabel)}偏${deviation.direction === "high" ? "高" : "低"} ${esc(deviation.signedDelta)}pp · 相对 ${esc(deviation.signedRelativePct)}%${deviation.watch ? " · 待验证" : ""}</span>`;
  const exploit = deviation.actionable
    ? metricExploitAdvice(key, deviation.direction)
    : "";
  return `<div class="${classes}">
    <div class="profile-metric-title">
      <small>${esc(metricNames[key] || key)}</small>
      <span class="metric-sample confidence-${esc(confidence)}">${esc(metricSampleLabel(confidence))}</span>
    </div>
    <strong>${esc(value.mean_pct)}%</strong>
    <span>观察 ${esc(value.observed_pct)}% · ${esc(value.successes)}/${esc(value.opportunities)}</span>
    <span>牌池 ${esc(value.population_mean_pct)}% · <b class="metric-direction">${esc(directionText)}</b></span>
    <span>80% 区间 ${esc(value.low_pct)}–${esc(value.high_pct)}%</span>
    ${deviationBadge}
    ${exploit ? `<span class="metric-exploit">${esc(exploit)}</span>` : ""}
  </div>`;
}
function renderMetricStreetGroups(metrics = {}) {
  const knownKeys = new Set(metricStreetGroups.flatMap(group => group.metrics));
  const groups = metricStreetGroups.map(group => ({
    ...group,
    entries: group.metrics
      .filter(key => metrics[key])
      .map(key => [key, metrics[key]]),
  }));
  const otherEntries = Object.entries(metrics).filter(([key]) => !knownKeys.has(key));
  if (otherEntries.length) {
    groups.push({ key: "other", label: "其他", entries: otherEntries });
  }
  return groups.map(group => {
    const sparseCount = group.entries.filter(([, value]) =>
      ["none", "very_low", "low"].includes(String(value.confidence || "very_low"))
    ).length;
    const deviationCount = group.entries.filter(([, value]) =>
      metricDeviation(value).highlight
    ).length;
    const meta = group.entries.length
      ? `${group.entries.length} 项${sparseCount ? ` · ${sparseCount} 项低样本` : ""}${deviationCount ? ` · ${deviationCount} 项中度以上偏移` : ""}`
      : "暂无有效机会";
    return `<section class="metric-street-group street-${esc(group.key)}">
      <div class="metric-street-head">
        <strong>${esc(group.label)}</strong>
        <span>${esc(meta)}</span>
      </div>
      ${group.entries.length
        ? `<div class="profile-metrics">${group.entries.map(([key, value]) => renderProfileMetric(key, value)).join("")}</div>`
        : '<div class="metric-street-empty">样本积累中</div>'}
    </section>`;
  }).join("");
}
function profileReasoningBusy(userId) {
  const prefix = `${userId}|${state.mode || "all"}|`;
  return Array.from(state.profileReasoningInFlight).some(
    key => key.startsWith(prefix),
  );
}
function renderProfile(player) {
  const profileBusy = profileReasoningBusy(player.user_id);
  const metrics = renderMetricStreetGroups(player.metrics || {});
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
    <div class="metric-panel">
      <p class="profile-label">机会率后验 · 按街</p>
      <div class="metric-legend">
        <span class="metric-legend-high">红色 ↑ 高于牌池</span>
        <span class="metric-legend-low">蓝色 ↓ 低于牌池</span>
        <span>按百分点与赔率倍数取较高档：<b>中度 ≥8pp 或 ≥1.6×</b> · <b>显著 ≥15pp 或 ≥2.5×</b></span>
      </div>
      <div class="profile-metric-groups">${metrics}</div>
    </div>
    <div class="position-panel"><p class="profile-label">按位置</p>${positions || "<small>等待位置数据</small>"}</div>
    <div class="sizing-panel"><p class="profile-label">下注尺寸</p>${sizing || "<small>等待尺寸样本</small>"}</div>
    <div class="exploit-panel"><p class="profile-label">漏洞与对抗</p>${tendencies || "<small>当前样本不足，不生成强倾向标签</small>"}
      ${caveats ? `<ul class="profile-caveats">${caveats}</ul>` : ""}
      <div class="profile-action-row">
        <button class="profile-revealed-hands" data-user="${esc(player.user_id)}" data-alias="${esc(player.alias)}" type="button"${player.revealed_hands ? "" : " disabled"}>亮牌牌谱 ${esc(player.revealed_hands || 0)}</button>
        <button class="profile-llm-run" data-user="${esc(player.user_id)}" type="button"${profileBusy ? " disabled" : ""}>${profileBusy ? "LLM 分析中…" : "LLM 深化画像"}</button>
      </div>
      <div class="profile-llm-result${profileBusy ? " loading" : ""}">${profileBusy
        ? "<strong>LLM 正在复核画像…</strong><small>实时数据更新不会中断本次请求。</small>"
        : "<small>仅在需要时手动调用；范围数值仍由本地模型计算。</small>"}</div>
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
function currentOpponentRow(userId) {
  const summary = Array.from(
    document.querySelectorAll(".opponent-summary"),
  ).find(button => button.dataset.user === String(userId));
  return summary?.closest(".opponent-row") || null;
}
function profileSelectionMatches(row, position, line) {
  const panel = row?.querySelector(".preflop-range-panel");
  const selectedPosition = panel?.querySelector(".range-position")?.value;
  const selectedLine = panel?.querySelector(".range-line")?.value;
  return (
    (!selectedPosition || selectedPosition === position) &&
    (!selectedLine || selectedLine === line)
  );
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
    if (!state.profileReasoningRestoreChecked.has(key)) {
      state.profileReasoningRestoreChecked.add(key);
      loadSavedPlayerProfileReasoning(row, userId, position, line, key);
    }
  }
}
async function loadSavedPlayerProfileReasoning(row, userId, position, line, key) {
  const params = new URLSearchParams({ position, line });
  if (state.mode) params.set("mode", state.mode);
  try {
    const response = await fetch(
      `/api/players/${encodeURIComponent(userId)}/profile/analysis?${params}`,
    );
    if (response.status === 404) return;
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    if (state.profileReasoningInFlight.has(key)) return;
    state.profileReasoning.set(key, data);
    const currentRow = currentOpponentRow(userId);
    if (currentRow && profileSelectionMatches(currentRow, position, line)) {
      restoreProfileReasoning(currentRow, userId, position, line);
    }
  } catch (_error) {
    // A persisted profile is optional; local statistics remain available.
  }
}
function profilePatternLabel(pattern) {
  const street = ["flop", "turn", "river"].find(value =>
    String(pattern || "").startsWith(`${value}_`)
  );
  const key = street ? String(pattern).slice(street.length + 1) : String(pattern);
  const labels = {
    donk_lead: "PFA 行动前领打",
    probe_after_pfa_check: "PFA 过牌后 Probe",
    small_bet: "小尺度主动下注",
    overbet: "超池下注",
    draw_continue: "听牌面对下注继续",
    draw_aggression: "听牌主动进攻",
    non_pfa_open_aggression: "非 PFA 未面对下注时主动下注",
    large_one_pair_bet: "一对牌大尺度下注",
  };
  return `${street ? `${streetNames[street]} · ` : ""}${labels[key] || key}`;
}
function renderPlayerProfileAnalysis(result) {
  const analysis = result.analysis || {};
  const evidence = result.evidence || {};
  const persistence = result.persistence || {};
  const savedMillis = Date.parse(persistence.created_at || "");
  const savedAt = Number.isNaN(savedMillis)
    ? ""
    : new Date(savedMillis).toLocaleString("zh-CN", { hour12: false });
  const persistenceText = persistence.saved
    ? `${persistence.stale ? "已恢复历史快照，数据有更新，请重新分析" : (persistence.restored ? "已从 SQLite 恢复" : "已保存到 SQLite")}${savedAt ? ` · ${savedAt}` : ""}`
    : "未持久化";
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
  const postflopPatterns = (evidence.postflop_patterns || []).map(item =>
    `<div class="llm-evidence-row"><strong>${esc(item.evidence_id)}</strong>
      <span>${esc(profilePatternLabel(item.pattern))} ${esc(item.rate_pct)}% vs 牌池 ${esc(item.population_rate_pct)}% · ${item.delta_pp >= 0 ? "+" : ""}${esc(item.delta_pp)}pp · ${esc(item.successes)}/${esc(item.opportunities)}</span>
    </div>`
  ).join("");
  const revealedPatterns = (evidence.revealed_pattern_summary || []).map(item =>
    `<div class="llm-evidence-row"><strong>${esc(item.evidence_id)}</strong>
      <span>${esc(profilePatternLabel(item.pattern))} · ${esc(item.occurrences)} 次 / ${esc(item.hands)} 手牌 · 例 ${esc((item.example_cases || []).join(", ") || "—")}</span>
    </div>`
  ).join("");
  const cases = (evidence.cases || []).map(item => {
    const actions = (item.actions || []).map(action => {
      const details = [
        action.bet_fraction_pct == null ? "" : `下注 ${action.bet_fraction_pct}% pot`,
        action.pot_odds_pct == null ? "" : `赔率 ${action.pot_odds_pct}%`,
        action.spr == null ? "" : `SPR ${action.spr}`,
        (action.pattern_tags || []).map(profilePatternLabel).join("/"),
      ].filter(Boolean).join(" · ");
      return `${streetNames[action.street] || action.street}:${actionNames[action.action] || action.action}${details ? `（${details}）` : ""}`;
    }).join(" → ");
    const strengths = Object.entries(item.strength_by_street || {}).map(([street, value]) => {
      const label = value.preflop_class || value.made_hand;
      const draws = (value.draws || []).join("/");
      const board = (value.board || []).map(cardText).join(" ");
      return `${streetNames[street] || street}:${rangeLabels[label] || label || "—"}${draws ? `+${draws}` : ""}${board ? ` [${board}]` : ""}`;
    }).join(" · ");
    const shownCards = (item.shown_cards || []).map(cardText).join(" ");
    const boardRunout = (item.board_runout || []).map(cardText).join(" ");
    return `<div class="llm-evidence-case">
      <strong>${esc(item.case_id)} · ${esc(item.position)} · ${esc(shownCards || item.shown_hand)} · ${esc(item.preflop_line)}</strong>
      <small>Board ${esc(boardRunout || "—")}</small>
      <span>${esc(actions || "无行动")}</span>
      <small>${esc(strengths)}</small>
    </div>`;
  }).join("");
  const evidenceDetails = deviations || postflopPatterns || revealedPatterns || cases
    ? `<details class="llm-evidence-details">
        <summary>查看发送给 LLM 的证据 · ${esc(evidence.selected_cases || 0)}/${esc(evidence.available_cases || 0)} 手牌</summary>
        ${deviations ? `<p class="profile-label">相对当前牌池偏移</p>${deviations}` : ""}
        ${postflopPatterns ? `<p class="profile-label">离线翻后漏洞模式（全样本）</p>${postflopPatterns}` : ""}
        ${revealedPatterns ? `<p class="profile-label">亮牌样本中的牌力/听牌模式</p>${revealedPatterns}` : ""}
        ${cases ? `<p class="profile-label">匿名具体手牌与行动案例</p>${cases}` : ""}
      </details>`
    : "";
  return `<div class="llm-profile-head">
      <strong>LLM 复核 · ${esc(reasoningConfidence(analysis.confidence))}置信度</strong>
      <span>${esc(analysis.latency_ms ?? "—")}ms</span>
    </div>
    <small class="profile-persistence">${esc(persistenceText)}</small>
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
    state.profileReasoningRestoreChecked.add(key);
    const currentRow = currentOpponentRow(userId);
    if (currentRow && profileSelectionMatches(currentRow, position, line)) {
      restoreProfileReasoning(currentRow, userId, position, line);
    }
  } catch (error) {
    const currentTarget = currentOpponentRow(userId)?.querySelector(
      ".profile-llm-result",
    );
    if (currentTarget) {
      currentTarget.className = "profile-llm-result error";
      currentTarget.textContent = `LLM 画像不可用：${error.message}`;
    }
  } finally {
    state.profileReasoningInFlight.delete(key);
    const currentButton = currentOpponentRow(userId)?.querySelector(
      ".profile-llm-run",
    );
    if (currentButton) {
      currentButton.disabled = false;
      currentButton.textContent = "LLM 深化画像";
    } else if (button && row.isConnected) {
      button.disabled = false;
      button.textContent = "LLM 深化画像";
    }
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
function strategyCellMixStyle(frequencies = {}) {
  const colors = {
    fold: "#202b26",
    check: "#2a5f4d",
    call: "#356f91",
    raise: "#a98231",
    all_in: "#ad4c49",
  };
  const order = ["fold", "check", "call", "raise", "all_in"];
  const entries = order
    .filter(action => Number(frequencies[action] || 0) > 0)
    .map(action => [action, Number(frequencies[action])]);
  if (entries.length <= 1) return "";
  const total = entries.reduce((sum, item) => sum + item[1], 0) || 100;
  let cursor = 0;
  const stops = [];
  for (const [action, frequency] of entries) {
    const start = cursor;
    cursor += 100 * frequency / total;
    stops.push(
      `${colors[action]} ${start.toFixed(1)}%`,
      `${colors[action]} ${cursor.toFixed(1)}%`,
    );
  }
  return ` style="background:linear-gradient(90deg,${stops.join(",")})"`;
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
  const exploitNote = strategy.source === "llm-bounded-exploit-v1"
    ? `<div class="strategy-exploit-note">已应用 ${esc((strategy.applied_frequency_shifts || []).length)} 项受限剥削频率调整 · 可在上方查看基线 → 调整值</div>`
    : "";
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
      const mixStyle = exactAction ? "" : strategyCellMixStyle(item.frequencies);
      const title = exactAction
        ? `${item.hand} · 当前建议 ${actionNames[exactAction] || exactAction} · 范围基线 ${strategyFrequencyText(item.frequencies)}`
        : `${item.hand} · ${strategyFrequencyText(item.frequencies)}`;
      return `<div class="strategy-cell action-${esc(displayAction)}${mixed}${hero}"${mixStyle}
        title="${esc(title)}">
        ${esc(item.hand)}
      </div>`;
    }).join("");
    return `${intro}
      ${exploitNote}
      <div class="strategy-summary">${esc(strategy.summary)}</div>
      <div class="strategy-overall">${renderStrategyFrequencies(strategy.action_mix)}</div>
      <div class="strategy-matrix" aria-label="当前翻前局面的简化全范围动作">${cells}</div>
      <div class="strategy-legend">
        ${renderStrategyFrequencies(strategy.action_mix)}
        <span class="strategy-hero-key">分段颜色：该牌类的混合频率</span>
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
    ${exploitNote}
    <div class="strategy-summary">${esc(strategy.summary)}</div>
    <div class="strategy-buckets">${buckets}</div>
    <div class="strategy-legend">
      <span class="strategy-hero-key">${strategy.hero_bucket ? "白框：当前手牌所属牌力层" : "未看到底牌：按五类牌力展示全范围"}</span>
    </div>
    <small class="strategy-caveat">${esc(strategy.caveat)}</small>
  </section>`;
}
function renderSizingRecommendation(engine = {}) {
  const sizing = engine.sizing_recommendation || {};
  if (
    !sizing.enabled ||
    engine.recommended?.action !== "raise" ||
    sizing.recommended_raise_to == null
  ) return "";
  const source = {
    active_opponent_history: "在场选手个人历史响应",
    population_context: "相似牌池响应后验",
    size_adjusted_heuristic: "通用尺度基线",
  }[sizing.source] || sizing.source;
  const candidates = (sizing.candidates || []).map(item => `
    <span class="money-size-candidate${item.selected ? " selected" : ""}${item.ev_best ? " ev-best" : ""}">
      <b>到 ${esc(item.raise_to)}</b>
      <small>${esc(item.pot_fraction_pct)}%池 · 全弃 ${esc(item.all_fold_pct)}% · EV ${esc(item.robust_ev)}</small>
    </span>`).join("");
  const responses = (sizing.opponent_responses || []).map(item => {
    const sample = Number(item.effective_samples || 0);
    const evidence = item.source === "player_history"
      ? `个人历史 n≈${sample.toFixed(1)}`
      : item.source === "population_context"
      ? `牌池回退 n≈${sample.toFixed(1)}`
      : "通用尺度基线";
    return `<div class="money-size-response">
      <strong>${esc(item.display_name || item.player || "在场对手")}</strong>
      <span>弃 ${esc(item.fold_pct)}% · 跟 ${esc(item.call_pct ?? "—")}% · 再加 ${esc(item.raise_pct ?? "—")}%</span>
      <small>${esc(evidence)} · ${esc(confidenceLabel(item.confidence) || "低")}置信度</small>
    </div>`;
  }).join("");
  return `<section class="money-sizing">
    <div class="money-sizing-head">
      <div><small>DYNAMIC SIZING · ${esc(source)}</small>
        <strong>建议加注到 ${esc(sizing.recommended_raise_to)}</strong></div>
      <span>本次投入约 ${esc(sizing.recommended_pot_fraction_pct)}% 当前底池</span>
    </div>
    <p>${esc(sizing.reason)}</p>
    <div class="money-size-candidates">${candidates}</div>
    ${responses ? `<details><summary>查看在场选手对此尺度的预计反应</summary>
      <div class="money-size-responses">${responses}</div></details>` : ""}
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
  const actionLabel = value => (
    value === "raise" && engine.fit_profile?.facing_bet === false
      ? "下注"
      : (actionNames[value] || value || "等待")
  );
  const action = actionLabel(recommended.action);
  const raise = recommended.raise_to == null ? "" : `到 ${recommended.raise_to}`;
  const squid = engine.squid || {};
  const squidMeta = squid.available
    ? `${squid.hero_squid_count ?? "—"} 条 / 已发 ${squid.awarded_squids ?? "—"} of ${squid.total_squids ?? "—"} · ${squid.calibrated ? `每条 ${squid.squid_value}` : "价值待结算校准"}`
    : (squid.reason || "鱿鱼状态不可用");
  const policies = (engine.policy || []).slice(0, 4).map(item => {
    const name = actionLabel(item.action);
    const size = item.raise_to == null ? "" : ` ${item.raise_to}`;
    const selected = item.id === engine.random_selection?.selected_id ? " selected" : "";
    return `<span class="${selected.trim()}">${esc(name)}${esc(size)} <b>${esc(item.frequency_pct)}%</b></span>`;
  }).join("");
  const randomSelection = engine.random_selection || {};
  const draw = Number(randomSelection.draw_pct);
  const selectedInterval = (randomSelection.intervals || []).find(item => item.selected);
  const mostFrequent = engine.most_frequent || {};
  const mostFrequentName = actionLabel(mostFrequent.action);
  const mostFrequentSize = mostFrequent.raise_to == null ? "" : ` ${mostFrequent.raise_to}`;
  const randomRoll = Number.isFinite(draw) && selectedInterval
    ? (() => {
        const marker = Math.max(0, Math.min(100, draw));
        const markerEdge = marker < 10 ? " edge-left" : marker > 90 ? " edge-right" : "";
        const hitName = actionLabel(selectedInterval.action);
        const hitSize = selectedInterval.raise_to == null ? "" : ` ${selectedInterval.raise_to}`;
        const segments = (randomSelection.intervals || []).map(item => {
          const start = Number(item.start_pct);
          const end = Number(item.end_pct);
          const width = Math.max(0, end - start);
          if (!Number.isFinite(width) || width <= 0) return "";
          const name = actionLabel(item.action);
          return `<span class="money-roll-segment action-${esc(item.action)}${item.selected ? " selected" : ""}"
            style="width:${width}%"
            title="${esc(`${name} ${item.frequency_pct}% · [${start.toFixed(1)}, ${end.toFixed(1)})`)}"></span>`;
        }).join("");
        return `<div class="money-roll">
          <div class="money-roll-copy">
            <span>本次随机数 <b>${esc(draw.toFixed(2))}</b> / 100 · 最高频 ${esc(mostFrequentName)}${esc(mostFrequentSize)} ${esc(mostFrequent.frequency_pct ?? "—")}%</span>
            <strong>落在 [${esc(Number(selectedInterval.start_pct).toFixed(1))}, ${esc(Number(selectedInterval.end_pct).toFixed(1))})：${esc(hitName)}${esc(hitSize)}</strong>
          </div>
          <div class="money-roll-track" aria-label="频率随机数 ${esc(draw.toFixed(2))}，命中 ${esc(hitName)}">
            ${segments}
            <i class="money-roll-marker${markerEdge}" style="left:${marker}%"><b>${esc(draw.toFixed(2))}</b></i>
          </div>
        </div>`;
      })()
    : "";
  const candidates = (engine.candidates || []).slice(0, 4).map(item => {
    const name = actionLabel(item.action);
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
  const reasons = (engine.reasons || [])
    .map(item => `<li>${esc(item)}</li>`)
    .join("");
  return `<section class="money-strategy">
    <div class="money-strategy-head">
      <div>
        <small>离线 solver 拟合 + Money-EV · 按频率随机 · ${esc(reasoningConfidence(engine.confidence))}置信度</small>
        <strong>${esc(action)} ${esc(raise)}</strong>
      </div>
      <span>${esc(engine.latency_ms ?? "—")}ms</span>
    </div>
    ${renderSizingRecommendation(engine)}
    ${reasons ? `<ul class="money-caveats">${reasons}</ul>` : ""}
    <div class="money-policy">${policies}</div>
    ${randomRoll}
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
    return `<code title="${esc(item)}">${esc(detail.display_player || detail.player || "")} · ${esc(metric)} · ${esc(values)}</code>`;
  }).join("");
}
function localizeOpponentLabels(value, analysis = {}) {
  let text = String(value || "");
  const mappings = new Map();
  (analysis.evidence_details || []).forEach(item => {
    if (item.player && item.display_player) mappings.set(item.player, item.display_player);
  });
  (analysis.opponent_reads || []).forEach(item => {
    if (item.player && item.display_player) mappings.set(item.player, item.display_player);
  });
  (analysis.frequency_shifts || []).forEach(item => {
    if (item.player && item.display_player) mappings.set(item.player, item.display_player);
  });
  mappings.forEach((display, player) => {
    text = text.split(player).join(display);
  });
  return text;
}
function renderExploitAnalysis(analysis = null, exploitStrategy = null) {
  if (!analysis) return "";
  const details = new Map(
    (analysis.evidence_details || []).map(item => [item.evidence_id, item])
  );
  const reads = (analysis.opponent_reads || []).map(item => `
    <li><strong>${esc(item.display_player || item.player || "活跃对手")}</strong> · ${esc(localizeOpponentLabels(item.finding, analysis))}
      <div class="exploit-evidence">${evidenceChips(item.evidence, details)}</div>
    </li>`).join("");
  const adjustments = (analysis.range_adjustments || []).map(item => `
    <li>${esc(localizeOpponentLabels(item.adjustment, analysis))}
      <div class="exploit-evidence">${evidenceChips(item.evidence, details)}</div>
    </li>`).join("");
  const shifts = (exploitStrategy?.applied_frequency_shifts || []).map(item => {
    const scope = frequencyScopeNames[item.scope] || item.scope;
    const from = actionNames[item.from_action] || item.from_action;
    const to = actionNames[item.to_action] || item.to_action;
    const capped = Number(item.requested_delta_pp) > Number(item.delta_pp)
      ? ` · 本地由 ${item.requested_delta_pp}pp 限幅到 ${item.delta_pp}pp`
      : "";
    return `<li>
      <strong>${esc(item.display_player || item.player)} · ${esc(scope)}</strong>
      <div>${esc(from)} ${esc(item.baseline_from_pct)}% → ${esc(item.adjusted_from_pct)}%；
        ${esc(to)} ${esc(item.baseline_to_pct)}% → ${esc(item.adjusted_to_pct)}%
        <b>（实际 +${esc(item.applied_delta_pp)}pp）</b></div>
      <small>${esc(localizeOpponentLabels(item.reason, analysis))}${esc(capped)}</small>
      <div class="exploit-evidence">${evidenceChips(item.evidence, details)}</div>
    </li>`;
  }).join("");
  const caveats = (analysis.caveats || []).map(item => `<li>${esc(item)}</li>`).join("");
  return `<section class="live-exploit-analysis">
    <div class="llm-profile-head">
      <strong>针对当前活跃对手 · ${esc(reasoningConfidence(analysis.confidence))}置信度</strong>
      <span>${esc(analysis.latency_ms ?? "—")}ms</span>
    </div>
    ${analysis.summary ? `<p>${esc(localizeOpponentLabels(analysis.summary, analysis))}</p>` : ""}
    ${reads ? `<small>对手判断</small><ul>${reads}</ul>` : ""}
    ${adjustments ? `<small>范围调整</small><ul>${adjustments}</ul>` : ""}
    ${shifts ? `<small>已执行的频率调整（本地限幅）</small><ul class="frequency-shifts">${shifts}</ul>` : ""}
    ${!reads && !adjustments && !shifts ? "<small>当前没有通过样本校验的剥削证据，不强行调整范围。</small>" : ""}
    ${caveats ? `<details><summary>限制条件</summary><ul>${caveats}</ul></details>` : ""}
    <div class="reasoning-meta">${esc(analysis.source || llmModel())} · 仅使用上列证据，不猜未知底牌</div>
  </section>`;
}
function renderInferenceAdvice(advice = {}) {
  const exact = advice.subject === "exact_hand";
  const recommendation = advice.recommendation || {};
  const rangePolicy = advice.range_policy || {};
  const exploitStrategy = rangePolicy.adjusted;
  const strategy = exploitStrategy || rangePolicy.baseline || {};
  const exploits = advice.opponent_exploits || {};
  const exploitAnalysis = {
    summary: exploits.summary,
    confidence: advice.confidence,
    opponent_reads: exploits.reads || [],
    range_adjustments: exploits.range_adjustments || [],
    frequency_shifts: exploits.frequency_shifts || [],
    evidence_details: exploits.evidence_details || [],
    caveats: exploits.caveats || [],
    source: advice.route?.source,
    latency_ms: advice.route?.latency_ms,
  };
  const action = (
    recommendation.action === "raise" &&
    advice.money_strategy?.fit_profile?.facing_bet === false
  )
    ? "下注"
    : (actionNames[recommendation.action] || recommendation.action || "等待建议");
  const raise = recommendation.raise_to == null ? "" : `到 ${recommendation.raise_to}`;
  const reasons = (advice.reasons || []).slice(0, 3).map(item =>
    `<li>${esc(localizeOpponentLabels(item.text, exploitAnalysis))}</li>`
  ).join("");
  const title = exact ? "精确手牌建议" : "观战全范围建议";
  const primary = exact
    ? `${esc(action)} ${esc(raise)}`
    : esc(recommendation.summary || strategy.summary || "按完整范围展示频率");
  const diagnostics = `<details class="inference-diagnostics">
    <summary>推理版本与上下文</summary>
    <div>Engine ${esc(advice.route?.engine_version || "—")}</div>
    <div>Template ${esc(advice.route?.template_id || "—")}</div>
    <div>Context ${esc(advice.context?.version || "—")} · ${esc((advice.context?.hash || "").slice(0, 12))}</div>
    <div>${esc(advice.context?.temporal_quality || "—")}</div>
  </details>`;
  const hasExploit = (
    exploitAnalysis.opponent_reads.length ||
    exploitAnalysis.range_adjustments.length ||
    exploitAnalysis.frequency_shifts.length
  );
  return `${renderAnalysisRoute(advice)}
    <section class="inference-primary ${exact ? "exact" : "range"}">
      <small>${title} · ${esc(reasoningConfidence(advice.confidence))}置信度</small>
      <strong>${primary}</strong>
      ${reasons ? `<ul>${reasons}</ul>` : "<span>当前没有额外的高置信度理由。</span>"}
    </section>
    ${renderMoneyStrategy(advice.money_strategy || {})}
    ${hasExploit ? renderExploitAnalysis(exploitAnalysis, exploitStrategy) : ""}
    ${renderSpotStrategy(strategy, exact ? { recommended_action: recommendation.action } : null)}
    ${diagnostics}`;
}
function renderReasoningAnalysis(result) {
  if (result?.schema_version === "inference-advice-v1") {
    return renderInferenceAdvice(result);
  }
  const analysis = result.analysis;
  const exploitAnalysis = result.exploit_analysis || (
    analysis && (
      (analysis.opponent_reads || []).length ||
      (analysis.range_adjustments || []).length ||
      (analysis.frequency_shifts || []).length
    ) ? analysis : null
  );
  const strategy = renderSpotStrategy(
    result.exploit_strategy || result.strategy || {},
    analysis,
  );
  const money = renderMoneyStrategy(result.money_strategy || {});
  const route = renderAnalysisRoute(result);
  const exploit = renderExploitAnalysis(exploitAnalysis, result.exploit_strategy);
  if (!analysis) {
    return `${route}<div class="strategy-mode-note">
        <strong>未捕获可靠底牌，不猜具体持牌</strong>
        <span>${exploitAnalysis ? "GPT‑5.6 只复核整个范围及对手剥削调整。" : "下面是立即生成的本地范围基线。"}</span>
      </div>${exploit}${money}${strategy}`;
  }
  const action = actionNames[analysis.recommended_action] || analysis.recommended_action || "未给动作";
  const raise = analysis.raise_to == null ? "" : `到 ${esc(analysis.raise_to)}`;
  const factors = (analysis.factors || []).map(item =>
    `<li>${esc(localizeOpponentLabels(item, analysis))}</li>`
  ).join("");
  const risks = (analysis.risks || []).map(item =>
    `<li>风险：${esc(localizeOpponentLabels(item, analysis))}</li>`
  ).join("");
  return `${route}${money}<section class="exact-hand-advice">
    <div class="reasoning-meta">二级规则 / LLM 复核，仅作解释；核心动作以上方 EV 引擎为准</div>
    <div class="reasoning-action">
      <strong>${esc(action)} ${raise}</strong>
      <span class="reasoning-meta">置信度 ${esc(reasoningConfidence(analysis.confidence))}</span>
    </div>
    <div>${esc(localizeOpponentLabels(analysis.summary || "LLM 未返回摘要", analysis))}</div>
    ${factors || risks ? `<ul>${factors}${risks}</ul>` : ""}
    <div class="reasoning-meta">${esc(analysis.source || "unknown")} · ${esc(analysis.latency_ms ?? "—")}ms · 当前手牌建议</div>
  </section>${exploit}${strategy}`;
}
function renderLocalStrategy(result) {
  const target = document.querySelector("#reasoning-result");
  target.className = "reasoning-result";
  if (result.inference?.schema_version === "inference-advice-v1") {
    target.innerHTML = renderInferenceAdvice(result.inference);
    return;
  }
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
function renderPreflopPreview(result) {
  const target = document.querySelector("#reasoning-result");
  const decision = result?.decision || {};
  target.className = "reasoning-result preflop-preview";
  target.innerHTML = `<div class="strategy-mode-note">
      <strong>提前范围预览 · 尚未轮到本人</strong>
      <span>${esc(decision.hero_position || "当前")}位置；前序行动变化后会自动更新。这里只展示范围基线，不生成动作 EV。</span>
    </div>${renderSpotStrategy(result?.baseline || {})}`;
}
function renderDecisionWaiting(decision) {
  const target = document.querySelector("#reasoning-result");
  if (!decision) {
    if (state.preflopPreview) {
      renderPreflopPreview(state.preflopPreview);
      return;
    }
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
    const label = state.preflopPreview
      ? "范围已预览 · 等待本人行动"
      : "等待当前行动节点";
    run.textContent = label;
    localRun.textContent = label;
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
function cancelServerReasoning(request) {
  if (!request?.sequence || !request.stateHash) return;
  fetch("/api/inference/cancel", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    keepalive: true,
    body: JSON.stringify({
      sequence: request.sequence,
      state_hash: request.stateHash,
    }),
  }).catch(() => {});
}
function cancelSupersededLiveRequests(decision) {
  const cacheKey = decision ? decisionCacheKey(decision) : null;
  if (
    state.strategyRequest &&
    state.strategyRequest.cacheKey !== cacheKey
  ) {
    const previous = state.strategyRequest;
    state.strategyRequest = null;
    state.strategyInFlight = null;
    previous.controller.abort();
  }
  if (
    state.reasoningRequest &&
    !state.reasoningRequest.force &&
    state.reasoningRequest.cacheKey !== cacheKey
  ) {
    const previous = state.reasoningRequest;
    state.reasoningRequest = null;
    state.reasoningInFlight = null;
    state.reasoningModeInFlight = null;
    state.reasoningDepthInFlight = null;
    previous.controller.abort();
    cancelServerReasoning(previous);
  }
}
function handleLiveDecision(decision) {
  state.serverLiveDecision = decision || null;
  const observerSuppressed = (
    decision?.decision_subject === "observer" &&
    !state.observerLiveEnabled
  );
  state.liveDecision = observerSuppressed ? null : (decision || null);
  decision = state.liveDecision;
  cancelSupersededLiveRequests(decision);
  if (observerSuppressed) {
    renderEquityCurve({
      status: "unavailable",
      reason: "观战实时范围已关闭；开启后会生成当前行动者的范围权益曲线。",
    });
    loadAllEquityCurves(null, state.data?.current_hand);
  } else if (
    !decision ||
    state.strategyResults.has(decisionCacheKey(decision))
  ) {
    // Local EV owns the live latency budget. Start the heavier equity curve
    // only after the action recommendation is already available.
    loadEquityCurve(decision, state.data?.current_hand);
  }
  const configured = Boolean(state.llmStatus?.configured);
  updateReasoningControls();
  if (renderPinnedReasoning()) return;
  if (observerSuppressed) {
    const target = document.querySelector("#reasoning-result");
    target.className = "reasoning-result";
    target.innerHTML = "<small>观战实时范围已关闭；打开上方“观战实时范围”即可跟随当前行动者显示。</small>";
    return;
  }
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
    local &&
    decisionCanBeReviewed(decision) &&
    (decision.auto_reasoning || decision.local_fast_available) &&
    state.lastAutoSequence !== decision.sequence
  ) {
    runReasoning(false);
  }
}
async function loadPreflopPreview(hand, decision, sequence) {
  const eligible = Boolean(
    hand &&
    hand.status === "in_progress" &&
    !(hand.board || []).length &&
    decision?.decision_subject !== "self",
  );
  const key = eligible ? `${hand.hand_id}:${sequence || 0}` : "";
  if (!eligible) {
    state.preflopPreview = null;
    state.preflopPreviewKey = "";
    state.preflopPreviewInFlight = "";
    return;
  }
  if (state.preflopPreviewKey !== key) {
    state.preflopPreview = null;
    state.preflopPreviewKey = key;
  }
  if (state.preflopPreview || state.preflopPreviewInFlight === key) return;
  state.preflopPreviewInFlight = key;
  try {
    const response = await fetch("/api/strategy/preflop-preview");
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 409) return;
      throw new Error(data.detail || `HTTP ${response.status}`);
    }
    if (
      state.preflopPreviewKey !== key ||
      state.liveDecision?.decision_subject === "self"
    ) return;
    state.preflopPreview = data;
    if (!state.pinnedReasoning && !state.liveDecision) {
      renderPreflopPreview(data);
    }
    updateReasoningControls();
  } catch (_) {
    // The normal live decision view remains available if preview cannot load.
  } finally {
    if (state.preflopPreviewInFlight === key) {
      state.preflopPreviewInFlight = "";
    }
  }
}
function decisionCacheKey(decision) {
  return `${decision?.sequence ?? "none"}:${decision?.state_hash || "legacy"}`;
}
async function runCurrentStrategy(decision) {
  const sequence = decision.sequence;
  const cacheKey = decisionCacheKey(decision);
  if (state.strategyRequest?.cacheKey === cacheKey) return;
  if (state.strategyRequest?.cacheKey !== cacheKey) {
    state.strategyRequest?.controller.abort();
  }
  const controller = new AbortController();
  const strategyRequest = { cacheKey, controller };
  state.strategyRequest = strategyRequest;
  state.strategyInFlight = sequence;
  try {
    const response = await fetch(
      `/api/strategy/current?sequence=${encodeURIComponent(sequence)}`,
      { signal: controller.signal },
    );
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    if (data.stale) return;
    state.strategyResults.set(cacheKey, data);
    while (state.strategyResults.size > 64) {
      state.strategyResults.delete(state.strategyResults.keys().next().value);
    }
    if (
      state.liveDecision?.sequence === sequence &&
      !state.pinnedReasoning
    ) {
      // Re-render immediately even when GPT is still in flight. The live
      // recommendation must not wait for the next SSE snapshot.
      handleLiveDecision(state.liveDecision);
    }
  } catch (error) {
    if (error.name === "AbortError") return;
    // The live action line remains usable even if the experimental EV path is unavailable.
  } finally {
    if (state.strategyRequest === strategyRequest) {
      state.strategyRequest = null;
      state.strategyInFlight = null;
    }
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
  if (state.reasoningRequest?.cacheKey === cacheKey) return;
  if (!force && state.reasoningRequest?.force) return;
  if (
    state.reasoningRequest &&
    state.reasoningRequest.cacheKey !== cacheKey
  ) {
    const previous = state.reasoningRequest;
    previous.controller.abort();
    if (!previous.force) cancelServerReasoning(previous);
  }
  const controller = new AbortController();
  const reasoningRequest = {
    cacheKey,
    controller,
    force,
    sequence,
    stateHash: String(decision.state_hash || ""),
  };
  state.reasoningRequest = reasoningRequest;
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
    const response = await fetch("/api/inference/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({
        sequence,
        state_hash: decision.state_hash || null,
        hand_id: decision.hand_id || null,
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
    if (error.name === "AbortError") return;
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
    if (state.reasoningRequest === reasoningRequest) {
      state.reasoningRequest = null;
      state.reasoningInFlight = null;
      state.reasoningModeInFlight = null;
      state.reasoningDepthInFlight = null;
      updateReasoningControls();
    }
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
  handleLiveDecision(state.serverLiveDecision);
}
function refreshFullSnapshot() {
  if (state.snapshotRefreshInFlight) return state.snapshotRefreshInFlight;
  const suffix = state.mode ? `?mode=${encodeURIComponent(state.mode)}` : "";
  const request = fetch(`/api/snapshot${suffix}`)
    .then(async response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      update(await response.json());
    })
    .catch(() => {})
    .finally(() => {
      if (state.snapshotRefreshInFlight === request) {
        state.snapshotRefreshInFlight = null;
      }
    });
  state.snapshotRefreshInFlight = request;
  return request;
}
function update(data) {
  const included = {
    hands: Object.prototype.hasOwnProperty.call(data, "hands"),
    opponents: Object.prototype.hasOwnProperty.call(data, "opponents"),
    hero: Object.prototype.hasOwnProperty.call(data, "hero"),
    squid: Object.prototype.hasOwnProperty.call(data, "squid"),
    events: Object.prototype.hasOwnProperty.call(data, "events"),
  };
  const previousHand = state.data?.current_hand;
  const incomingHand = data.current_hand;
  const shouldRefreshProfiles = Boolean(
    !included.opponents &&
    previousHand?.hand_id &&
    incomingHand?.hand_id &&
    (
      incomingHand.hand_id !== previousHand.hand_id ||
      (
        previousHand.status === "in_progress" &&
        incomingHand.status !== "in_progress"
      )
    )
  );
  const profileRefreshKey = shouldRefreshProfiles
    ? `${incomingHand.hand_id}:${incomingHand.status || "unknown"}`
    : "";
  data = { ...(state.data || {}), ...data };
  state.data = data;
  state.assistancePolicy = data.assistance_policy || state.assistancePolicy;
  document.querySelector("#sequence").textContent = `#${data.last_sequence || 0}`;
  const displayedHand = data.current_hand || data.hands?.[0];
  const currentHandTarget = document.querySelector("#current-hand");
  currentHandTarget.innerHTML = renderHand(
    displayedHand,
    {
      linkPlayers: true,
      opponents: data.opponents,
      hero: data.hero,
      decision: data.live_decision,
      handId: displayedHand?.hand_id,
      nodeSequence: data.live_decision?.sequence,
      nodeStateHash: data.live_decision?.state_hash,
      nodeScope: displayedHand?.status === "in_progress" ? "live" : "review",
    },
  );
  if (displayedHand?.status !== "in_progress") {
    loadPlayerCardRanges(currentHandTarget);
  }
  const actionLog = currentHandTarget.querySelector(".action-log-disclosure");
  actionLog?.addEventListener("toggle", () => {
    state.actionLogExpanded = actionLog.open;
    localStorage.setItem(
      "wpk.actionLogExpanded",
      String(state.actionLogExpanded),
    );
  });
  const allEquityToggle = currentHandTarget.querySelector("#all-equity-toggle");
  allEquityToggle?.addEventListener("change", () => {
    state.allEquityEnabled = allEquityToggle.checked;
    localStorage.setItem("wpk.allEquity", String(state.allEquityEnabled));
    state.allEquityViewKey = "";
    loadAllEquityCurves(state.liveDecision, state.data?.current_hand);
  });
  if (
    included.hands &&
    !state.historyLoaded &&
    !state.historyLoading &&
    !state.historyPlayer
  ) {
    state.historyHands = data.hands || [];
    state.historyTotal = null;
    state.historyHasMore = false;
    renderHands(state.historyHands);
  }
  if (included.opponents || included.hero) {
    renderOpponents(data.opponents);
  }
  if (included.squid) renderSquid(data.squid);
  if (included.events) renderRaw(data.events);
  loadPreflopPreview(displayedHand, data.live_decision, data.last_sequence);
  handleLiveDecision(data.live_decision);
  if (
    shouldRefreshProfiles &&
    state.profileRefreshKey !== profileRefreshKey
  ) {
    state.profileRefreshKey = profileRefreshKey;
    refreshFullSnapshot();
  }
}
function nodeRangeCacheKey(selection) {
  return [
    selection.userId,
    selection.handId || "current",
    selection.nodeSequence || "latest",
    selection.nodeStateHash || selection.snapshotSequence || "unknown",
  ].join("|");
}
function renderNodeRangeMatrix(data) {
  return (data.range?.matrix || []).map(item => {
    const delta = Number(item.probability_shift_pp || 0);
    const shiftClass = delta >= 0.02
      ? "node-range-up"
      : delta <= -0.02
      ? "node-range-down"
      : "";
    const deltaText = Math.abs(delta) >= 0.01
      ? `${delta > 0 ? "+" : ""}${delta.toFixed(Math.abs(delta) < 0.1 ? 2 : 1)}pp`
      : "持平";
    return `<div class="range-cell ${rangeTier(Number(item.relative_likelihood_pct || 0))} ${shiftClass}${item.in_core_80 ? "" : " outside-core"}"
      title="${esc(`${item.hand} · 当前范围内概率 ${item.probability_pct}% · 剩余组合 ${item.available_combos ?? "—"} · 相对权重 ${item.weight_pct}% · 翻前相对权重 ${item.baseline_pct}% · ${deltaText}`)}">
      <strong>${esc(item.hand)}</strong>
      <small>${esc(item.probability_pct)}%</small>
      ${shiftClass ? `<i>${esc(delta > 0 ? "↑" : "↓")}</i>` : ""}
    </div>`;
  }).join("");
}
function renderNodeStrength(data) {
  const composition = data.composition || {};
  const source = composition.posterior ? composition : (data.strength || {});
  const baseline = source.baseline || {};
  const posterior = source.posterior || {};
  const shifts = source.shifts || {};
  const preferred = composition.posterior
    ? [
        "strong_value",
        "marginal_showdown",
        "draws",
        composition.aggressive_action ? "bluff_candidates" : "air",
      ]
    : ["high_card", "pair", "draw", "two_pair_plus", "straight_plus"];
  const labels = [...new Set(preferred)]
    .filter(key => posterior[key] != null || baseline[key] != null);
  if (!labels.length || data.node?.street === "preflop") {
    return `<div class="node-range-muted">翻牌后才显示 blocker-aware 成牌与听牌分布。</div>`;
  }
  const rows = labels.map(key => {
    const before = Number(baseline[key] || 0);
    const after = Number(posterior[key] || 0);
    const shiftKey = key === "bluff_candidates" ? "air" : key;
    const shift = Number(shifts[shiftKey] || 0);
    const direction = shift > 0.05 ? "up" : shift < -0.05 ? "down" : "flat";
    return `<div class="node-strength-row composition-${esc(key)} shift-${direction}">
      <div><strong>${esc(strengthLabels[key] || key)}</strong>
        <span class="node-strength-values">
          <small>${esc(before.toFixed(1))}%</small>
          <em>→</em>
          <b>${esc(after.toFixed(1))}%</b>
          <i class="${direction}">${esc(`${direction === "up" ? "↑" : direction === "down" ? "↓" : "•"} ${Math.abs(shift).toFixed(1)}pp`)}</i>
        </span>
      </div>
      <div class="node-strength-track" aria-label="${esc(`${strengthLabels[key] || key} ${after.toFixed(1)}%`)}">
        <span class="baseline" style="width:${Math.max(0, Math.min(100, before))}%"></span>
        <span class="posterior" style="width:${Math.max(0, Math.min(100, after))}%"></span>
      </div>
    </div>`;
  }).join("");
  return `<div class="node-composition-legend">
      <span><i class="baseline"></i>左值：仅翻前范围 + 当前牌面</span>
      <span><i class="posterior"></i>右值：再加入本手行动线</span>
      <span><b>↑</b> 升权　<strong>↓</strong> 降权</span>
    </div>
    <div class="node-strength-list">${rows}</div>
    ${renderNodeHistorySource(data)}
    ${composition.interpretation
      ? `<small class="node-composition-note">${esc(composition.interpretation)}</small>`
      : ""}`;
}
function renderNodeHistorySource(data) {
  const preview = data.progressive?.phase === "preview";
  const production = Boolean(data.range?.production_enabled);
  const historyConditioned = Boolean(data.range?.history_conditioned);
  const structural = data.range?.source === "board_action_heuristic";
  const preflop = data.evidence?.preflop || {};
  const updates = (data.evidence?.updates || []).filter(update => !update.heuristic);
  const applied = updates.filter(update => update.applied);
  const playerSamples = applied.reduce(
    (total, update) => total + Number(update.player_samples || 0),
    0,
  );
  const actionSamples = Number(
    preflop.action_opportunities ?? data.range?.action_opportunities ?? 0,
  );
  if (preview && historyConditioned && playerSamples > 0) {
    return `<div class="node-history-source personal"><strong>已应用快速个人历史偏移</strong><span>翻前行动机会 ${esc(actionSamples)} 次 · 翻后个人公开行动样本 n≈${esc(playerSamples.toFixed(0))} · 仅用于展示并向牌池收缩</span></div>`;
  }
  if (preview && historyConditioned) {
    return `<div class="node-history-source pool"><strong>快速历史偏移以牌池为主</strong><span>翻前行动机会 ${esc(actionSamples)} 次；个人公开底牌不足，不作为 Money‑EV 生产后验。</span></div>`;
  }
  if (preview) {
    return `<div class="node-history-source loading"><strong>玩家历史正在加载</strong><span>当前先显示牌面与行动结构预览。</span></div>`;
  }
  if (production && playerSamples > 0) {
    return `<div class="node-history-source personal"><strong>已结合该玩家历史</strong><span>翻前行动机会 ${esc(actionSamples)} 次 · 翻后个人公开行动样本 n≈${esc(playerSamples.toFixed(0))} · 其余向牌池收缩</span></div>`;
  }
  if (production) {
    return `<div class="node-history-source pool"><strong>个人翻后样本不足</strong><span>翻前行动机会 ${esc(actionSamples)} 次；行动线后验主要使用同类牌池历史。</span></div>`;
  }
  if (structural) {
    return `<div class="node-history-source fallback"><strong>个人模型未通过校验</strong><span>翻前历史仍参与范围先验；翻后暂用保守结构回退，不进入 Money‑EV。</span></div>`;
  }
  return `<div class="node-history-source pool"><strong>当前使用翻前历史先验</strong><span>基于该玩家 ${esc(actionSamples)} 次相关行动机会并向牌池收缩。</span></div>`;
}
function renderNodeResponse(data) {
  const model = data.response_model || {};
  const aggregate = model.probabilities_pct || {};
  const actions = Object.keys(aggregate);
  if (!actions.length) {
    return `<div class="node-range-muted">${data.player?.folded
      ? "该玩家已弃牌，节点冻结到其最后一次行动；不再显示下一行动响应。"
      : "当前没有可用的下一行动响应后验。"}</div>`;
  }
  const aggregateBars = actions.map(action => `
    <div class="node-response-action">
      <span>${esc(responseActionLabels[action] || action)}</span>
      <strong>${esc(Number(aggregate[action] || 0).toFixed(1))}%</strong>
      <i style="width:${Math.max(0, Math.min(100, Number(aggregate[action] || 0)))}%"></i>
    </div>`).join("");
  const sizeRows = Object.entries(model.size_curve || {}).map(([bucket, row]) => {
    const probabilities = row.probabilities_pct || {};
    return `<div class="node-size-row">
      <strong>${esc(responseSizeLabels[bucket] || bucket)}</strong>
      ${["fold", "call", "raise"].map(action =>
        `<span>${esc(responseActionLabels[action])} ${esc(Number(probabilities[action] || 0).toFixed(0))}%</span>`
      ).join("")}
      <small>n≈${esc(Number(row.effective_samples || 0).toFixed(1))}</small>
    </div>`;
  }).join("");
  return `<div class="node-response-summary">${aggregateBars}</div>
    ${sizeRows ? `<div class="node-size-curve">
      <div class="node-size-head"><span>面对不同下注尺度的响应</span><small>个人 → 类型/牌池回退</small></div>
      ${sizeRows}
    </div>` : ""}
    <small class="node-model-note">有效样本 n≈${esc(Number(model.effective_samples || 0).toFixed(1))}
      · ${esc(confidenceLabel(model.confidence))}置信度
      · ${model.player_residual_enabled ? "个人残差已通过门控" : "使用牌池基线"}</small>`;
}
function renderNodeCardLoading(title, detail) {
  return `<div class="node-card-loading" role="status" aria-live="polite">
    <i></i>
    <div><strong>${esc(title)}</strong><span>${esc(detail)}</span></div>
  </div>`;
}
function renderNodeEvidence(data) {
  const evidence = data.evidence || {};
  const preflop = evidence.preflop || {};
  const updates = evidence.updates || [];
  const applied = updates.filter(update => update.applied);
  const gate = evidence.range_gate || {};
  return `<div class="node-evidence-stats">
      <div><small>行动机会</small><strong>${esc(preflop.action_opportunities ?? data.range?.action_opportunities ?? 0)}</strong></div>
      <div><small>本人亮牌</small><strong>${esc(preflop.player_revealed_samples ?? 0)}</strong></div>
      <div><small>群体亮牌</small><strong>${esc(preflop.population_revealed_samples ?? 0)}</strong></div>
      <div><small>逐街更新</small><strong>${esc(applied.length)} / ${esc(updates.length)}</strong></div>
    </div>
    <div class="node-update-list">${updates.length ? updates.map(update => `
      <div class="${update.applied ? "applied" : "gated"}">
        <strong>${esc(streetNames[update.street] || update.street)} · ${esc(update.action_path || update.signature || "行动")}</strong>
        <span>${update.applied
          ? `已更新 · ${esc(update.feature_level || "行动模型")} · 权重 ${esc(update.update_weight ?? "—")}`
          : esc(update.reason || "未通过门控")}</span>
      </div>`).join("") : `<small>当前节点没有可应用的翻后行动更新。</small>`}</div>
    <details class="node-gate-detail">
      <summary>模型门控与限制</summary>
      <p>${esc(gate.reason || "行动线门控状态不可用")}</p>
      <ul>${(data.limitations || []).map(item => `<li>${esc(item)}</li>`).join("")}</ul>
    </details>`;
}
function nodeActionLabel(action) {
  const size = action.amount_to != null && Number(action.amount_to) > 0
    ? `到 ${action.amount_to}`
    : action.amount != null && Number(action.amount) > 0
    ? String(action.amount)
    : "";
  return [
    streetNames[action.street] || action.street,
    actionNames[action.action] || action.action,
    size,
  ].filter(Boolean).join(" · ");
}
function renderOpponentNode(data) {
  const target = document.querySelector("#opponent-node-content");
  const title = document.querySelector("#opponent-node-title");
  if (!target || !title) return;
  const player = data.player || {};
  const node = data.node || {};
  const actionLine = player.action_line || [];
  const topShifts = (data.range?.top_class_shifts || [])
    .filter(item => Math.abs(Number(item.delta_pp || 0)) >= 0.1)
    .slice(0, 10);
  const topLikely = [...(data.range?.matrix || [])]
    .sort((left, right) =>
      Number(right.probability_pct || 0)
      - Number(left.probability_pct || 0)
    )
    .slice(0, 12);
  const production = data.range?.production_enabled;
  const structural = data.range?.source === "board_action_heuristic";
  const preview = data.progressive?.phase === "preview";
  const stale = Boolean(node.stale);
  title.textContent = `${player.alias || player.user_id || "对手"} · 节点范围`;
  target.innerHTML = `
    <div class="node-range-status ${stale ? "stale" : preview ? "loading" : production ? "production" : structural ? "heuristic" : "prior"}">
      <div>
        <strong>${stale ? "节点已过期" : preview ? "快速范围已显示 · 正在校准统计后验" : production ? "行动线统计后验" : structural ? "牌面 / ACTION 结构回退" : "翻前先验"}</strong>
        <span>${esc(data.policy?.label || "服务端策略")} · ${esc(node.scope === "live" ? "实时冻结节点" : "历史复盘节点")}${preview ? " · 其余模块独立加载" : ""}</span>
      </div>
      <button class="node-range-refresh" type="button">刷新当前节点</button>
    </div>
    <section class="node-range-hero">
      <div>
        <p class="eyebrow">${esc(node.hand_id)} · #${esc(node.sequence)} · ${esc(streetNames[node.street] || node.street)}</p>
        <h3>${esc(player.alias || player.user_id)} <span>${esc(player.position || "未知位置")}</span></h3>
        <div class="board">${cards(node.board || [])}</div>
        <div class="node-player-action-line">
          <small>该玩家截至此节点的 ACTION</small>
          ${actionLine.length
            ? actionLine.slice(-8).map(action => `<b>${esc(nodeActionLabel(action))}</b>`).join("")
            : "<b>尚无主动行动</b>"}
        </div>
      </div>
      <div class="node-range-frequency">
        <small>${esc(data.range?.line_label || data.range?.line || "行动线")} · 翻前进入频率</small>
        <strong>${esc(data.range?.estimated_range_pct ?? "—")}%</strong>
        <span>当前核心 80%：${esc(data.range?.core_80_class_count ?? "—")} 类 · ${esc(confidenceLabel(data.range?.confidence))}置信度${player.folded ? " · 已弃牌冻结" : ""}</span>
      </div>
    </section>
    <div class="node-range-grid">
      <section class="node-range-card matrix-card">
        <div class="node-card-head"><div><small>169 STARTING CLASSES</small><h4>当前范围</h4></div>
          <span>格内为当前范围内概率 · 箭头为 Action 后位移</span></div>
        <div class="range-matrix node-range-matrix">${renderNodeRangeMatrix(data)}</div>
        <div class="range-legend"><span class="range-tier-1">低频</span><span class="range-tier-3">混合</span><span class="range-tier-5">高频</span></div>
        <div class="node-top-likely">
          <small>当前最可能牌类</small>
          ${topLikely.map(item => `<span>${esc(item.hand)} <b>${esc(item.probability_pct)}%</b></span>`).join("")}
        </div>
        <div class="node-top-shifts">
          <small>${structural ? "牌面 / ACTION 结构位移" : "ACTION 后验位移"}</small>
          ${topShifts.length
            ? topShifts.map(item => `<span class="${Number(item.delta_pp) > 0 ? "up" : "down"}">
                ${esc(item.hand)} ${esc(`${Number(item.delta_pp) > 0 ? "+" : ""}${item.delta_pp}pp`)}
              </span>`).join("")
            : "<span>当前没有可应用的范围位移</span>"}
        </div>
      </section>
      <section class="node-range-card">
        <div class="node-card-head"><div><small>BOARD-AWARE COMPOSITION</small><h4>价值 / 听牌 / Bluff</h4></div><span>当前牌面先验 → Action 后验</span></div>
        ${renderNodeStrength(data)}
      </section>
      <section class="node-range-card response-card">
        <div class="node-card-head"><div><small>CONTEXTUAL RESPONSE</small><h4>下一行动与尺度响应</h4></div><span>不是确定动作</span></div>
        ${preview
          ? renderNodeCardLoading("正在加载行动响应", "计算该玩家在当前街道、位置、SPR 与尺度下的 fold / call / raise 后验。")
          : renderNodeResponse(data)}
      </section>
      <section class="node-range-card evidence-card">
        <div class="node-card-head"><div><small>AUDIT TRAIL</small><h4>证据与门控</h4></div><span>当前手已排除训练</span></div>
        ${preview
          ? renderNodeCardLoading("正在验证历史证据", "时间外检查行动线范围、继续范围和个人残差是否优于牌池回退。")
          : renderNodeEvidence(data)}
      </section>
    </div>
    <footer class="node-range-footer">
      <span>${preview ? "先显示低延迟结构范围；完整统计后验加载后会自动替换。" : structural ? "统计门控未通过：当前使用低置信度牌面/行动结构回退，不进入 Money-EV。" : "这些概率是历史行动证据下的后验范围，不是对确切底牌的识别。"}</span>
      <button class="node-full-profile" type="button"
        data-user="${esc(player.user_id)}" data-alias="${esc(player.alias || player.user_id)}">查看完整历史画像 →</button>
    </footer>`;
  target.querySelector(".node-range-refresh")?.addEventListener("click", () => {
    if (state.opponentNodeSelection) loadOpponentNode(state.opponentNodeSelection, true);
  });
  target.querySelector(".node-full-profile")?.addEventListener("click", event => {
    document.querySelector("#opponent-node-dialog").close();
    const handDialog = document.querySelector("#hand-dialog");
    if (handDialog.open) handDialog.close();
    openOpponentProfile(event.currentTarget.dataset.user, event.currentTarget.dataset.alias);
  });
}
async function loadOpponentNode(selection, force = false) {
  const target = document.querySelector("#opponent-node-content");
  const cacheKey = nodeRangeCacheKey(selection);
  if (!force && state.opponentNodeCache.has(cacheKey)) {
    renderOpponentNode(state.opponentNodeCache.get(cacheKey));
    return;
  }
  const requestKey = `${cacheKey}|${Date.now()}|${Math.random()}`;
  state.opponentNodeRequestKey = requestKey;
  target.innerHTML = `<div class="node-range-loading">
    <strong>正在冻结 ${esc(selection.alias)} 的牌局节点…</strong>
    <span>先加载 169 范围与牌面构成；历史门控和行动响应随后独立完成。</span>
  </div>`;
  const fetchPhase = async phase => {
    const query = new URLSearchParams({ phase });
    if (selection.handId) query.set("hand_id", selection.handId);
    const response = await fetch(
      `/api/players/${encodeURIComponent(selection.userId)}/range-at-node?${query}`,
    );
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    return data;
  };
  const previewPromise = fetchPhase("preview");
  const fullPromise = fetchPhase("full");
  let previewRendered = false;
  try {
    const preview = await previewPromise;
    if (state.opponentNodeRequestKey !== requestKey) return;
    updatePlayerRangeMiniElements(preview);
    renderOpponentNode(preview);
    previewRendered = true;
  } catch (_previewError) {
    // The full request may still succeed and is authoritative.
  }
  try {
    const data = await fullPromise;
    if (state.opponentNodeRequestKey !== requestKey) return;
    if (!data.node?.stale) {
      state.opponentNodeCache.set(cacheKey, data);
      while (state.opponentNodeCache.size > 32) {
        state.opponentNodeCache.delete(state.opponentNodeCache.keys().next().value);
      }
    }
    updatePlayerRangeMiniElements(data);
    renderOpponentNode(data);
  } catch (error) {
    if (state.opponentNodeRequestKey !== requestKey) return;
    if (previewRendered) {
      target.insertAdjacentHTML("afterbegin", `<div class="node-progressive-error">
        完整统计后验加载失败：${esc(error.message)}
        <button class="node-range-retry" type="button">重试</button>
      </div>`);
      target.querySelector(".node-range-retry")?.addEventListener("click", () => {
        loadOpponentNode(selection, true);
      });
      return;
    }
    target.innerHTML = `<div class="node-range-error">
      <strong>节点范围不可用</strong>
      <span>${esc(error.message)}</span>
      <button class="node-range-retry" type="button">重试</button>
    </div>`;
    target.querySelector(".node-range-retry")?.addEventListener("click", () => {
      loadOpponentNode(selection, true);
    });
  }
}
function openOpponentNode(button) {
  const userId = String(button?.dataset.user || "");
  if (!userId) return;
  const selection = {
    userId,
    alias: button.dataset.alias || userId,
    handId: button.dataset.handId || state.data?.current_hand?.hand_id || "",
    nodeSequence: button.dataset.nodeSequence || "",
    nodeStateHash: button.dataset.nodeStateHash || "",
    nodeScope: button.dataset.nodeScope || "review",
    snapshotSequence: state.data?.last_sequence || 0,
  };
  state.opponentNodeSelection = selection;
  const dialog = document.querySelector("#opponent-node-dialog");
  document.querySelector("#opponent-node-title").textContent = `${selection.alias} · 节点范围`;
  if (!dialog.open) dialog.showModal();
  loadOpponentNode(selection);
}
async function openHand(id) {
  const response = await fetch(`/api/hands/${encodeURIComponent(id)}`);
  if (!response.ok) return;
  const hand = await response.json();
  const target = document.querySelector("#dialog-content");
  target.innerHTML = renderHand(hand, {
    linkPlayers: true,
    opponents: state.data?.opponents || [],
    hero: state.data?.hero,
    handId: hand.hand_id,
    nodeScope: "review",
  }) +
    `<details><summary>关联原始事件</summary><pre>${esc(JSON.stringify(hand.raw_events, null, 2))}</pre></details>`;
  loadPlayerCardRanges(target);
  document.querySelector("#hand-dialog").showModal();
}
function activateView(view) {
  document.querySelectorAll(".tab, .view").forEach(node => node.classList.remove("active"));
  document.querySelector(`.tab[data-view="${view}"]`)?.classList.add("active");
  document.querySelector(`#view-${view}`)?.classList.add("active");
  if (view === "hands" && !state.historyLoaded) {
    loadHistory({ reset: true });
  }
}
function openOpponentProfile(userId, alias) {
  const targetUser = String(userId || "");
  if (!targetUser) return;
  state.opponentSearch = targetUser;
  state.expandedOpponents.add(targetUser);
  document.querySelector("#opponent-search").value = targetUser;
  activateView("opponents");
  renderOpponents(state.data?.opponents || []);
  const summary = Array.from(
    document.querySelectorAll(".opponent-summary"),
  ).find(button => button.dataset.user === targetUser);
  if (!summary) {
    document.querySelector("#opponents-list").innerHTML = `
      <div class="empty-result">
        <strong>${esc(alias || targetUser)}</strong><br>
        暂无已完成牌谱样本；记录到有效手牌后即可生成画像与剥削建议。
      </div>`;
    return;
  }
  const row = summary.closest(".opponent-row");
  requestAnimationFrame(() => {
    row.classList.add("profile-jump");
    row.scrollIntoView({ behavior: "smooth", block: "start" });
    summary.focus({ preventScroll: true });
    setTimeout(() => row.classList.remove("profile-jump"), 1800);
  });
}
function syncHistoryControls() {
  document.querySelector("#history-search").value = state.historyQuery;
  document.querySelector("#history-mode").value = state.historyMode;
  document.querySelector("#history-result").value = state.historyResult;
  document.querySelector("#history-street").value = state.historyStreet;
  const revealed = document.querySelector("#history-revealed");
  revealed.value = String(state.historyRevealed);
  revealed.disabled = Boolean(state.historyPlayer);
  document.querySelectorAll(".history-filter-button").forEach(button => {
    button.classList.toggle(
      "active",
      button.dataset.validOnly === String(state.validOnly),
    );
  });
}
function openOpponentRevealedHands(userId, alias) {
  state.historyPlayer = { userId: String(userId), alias: String(alias) };
  state.validOnly = false;
  state.historyMode = state.mode || "";
  state.historyQuery = "";
  state.historyResult = "";
  state.historyStreet = "";
  state.historyRevealed = true;
  syncHistoryControls();
  state.historyLoaded = false;
  activateView("hands");
}
document.querySelector("#dialog-close").addEventListener("click", () => document.querySelector("#hand-dialog").close());
function handleOpponentNodeClick(event) {
  const profileButton = event.target.closest(".player-profile-trigger");
  if (profileButton) {
    const handDialog = document.querySelector("#hand-dialog");
    if (handDialog.open) handDialog.close();
    openOpponentProfile(
      profileButton.dataset.user,
      profileButton.dataset.alias,
    );
    return;
  }
  const button = event.target.closest(".player-node-trigger");
  if (!button) return;
  openOpponentNode(button);
}
document.querySelector("#current-hand").addEventListener("click", handleOpponentNodeClick);
document.querySelector("#dialog-content").addEventListener("click", handleOpponentNodeClick);
document.querySelector("#opponent-node-close").addEventListener("click", () => {
  state.opponentNodeRequestKey = "";
  document.querySelector("#opponent-node-dialog").close();
});
document.querySelector("#opponent-node-dialog").addEventListener("close", () => {
  state.opponentNodeRequestKey = "";
});
document.querySelectorAll(".tab").forEach(tab => tab.addEventListener("click", () => {
  activateView(tab.dataset.view);
}));
let source;
function connectStream() {
  if (source) source.close();
  const suffix = state.mode ? `?mode=${encodeURIComponent(state.mode)}` : "";
  refreshFullSnapshot();
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
  loadHistory({ reset: true });
}));
document.querySelector("#history-clear-player").addEventListener("click", () => {
  state.historyPlayer = null;
  state.historyRevealed = false;
  syncHistoryControls();
  loadHistory({ reset: true });
});
document.querySelector("#hands-load-more").addEventListener("click", () => {
  loadHistory();
});
let historySearchTimer;
document.querySelector("#history-search").addEventListener("input", event => {
  state.historyQuery = event.target.value.trim();
  clearTimeout(historySearchTimer);
  historySearchTimer = setTimeout(() => loadHistory({ reset: true }), 250);
});
for (const [selector, key] of [
  ["#history-mode", "historyMode"],
  ["#history-result", "historyResult"],
  ["#history-street", "historyStreet"],
]) {
  document.querySelector(selector).addEventListener("change", event => {
    state[key] = event.target.value;
    loadHistory({ reset: true });
  });
}
document.querySelector("#history-revealed").addEventListener("change", event => {
  state.historyRevealed = event.target.value === "true";
  loadHistory({ reset: true });
});
document.querySelector("#history-reset").addEventListener("click", () => {
  clearTimeout(historySearchTimer);
  state.historyPlayer = null;
  state.validOnly = false;
  state.historyMode = "";
  state.historyQuery = "";
  state.historyResult = "";
  state.historyStreet = "";
  state.historyRevealed = false;
  syncHistoryControls();
  loadHistory({ reset: true });
});
document.querySelector("#opponent-search").addEventListener("input", event => {
  state.opponentSearch = event.target.value;
  renderOpponents(state.data?.opponents || []);
});
document.querySelector("#opponent-sort").addEventListener("change", event => {
  state.opponentSort = event.target.value;
  renderOpponents(state.data?.opponents || []);
});
const llmToggle = document.querySelector("#llm-toggle");
llmToggle.checked = state.llmEnabled;
llmToggle.addEventListener("change", () => {
  state.llmEnabled = llmToggle.checked;
  localStorage.setItem("wpk.llmReasoning", String(state.llmEnabled));
  handleLiveDecision(state.serverLiveDecision);
});
const observerLiveToggle = document.querySelector("#observer-live-toggle");
observerLiveToggle.checked = state.observerLiveEnabled;
observerLiveToggle.addEventListener("change", () => {
  state.observerLiveEnabled = observerLiveToggle.checked;
  localStorage.setItem("wpk.observerLive", String(state.observerLiveEnabled));
  state.allEquityViewKey = "";
  handleLiveDecision(state.serverLiveDecision);
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
  handleLiveDecision(state.serverLiveDecision);
});
connectStream();
// Let the live snapshot and table layout render before starting this heavier,
// non-live diagnostic query.
window.setTimeout(loadBacktest, 5000);
loadReasoningStatus();
