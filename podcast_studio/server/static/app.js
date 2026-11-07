/* 播客工坊前端逻辑：原生 JS，无构建链 */
"use strict";

const $ = (id) => document.getElementById(id);
let currentMode = "topic";
let currentStyle = "bedtime";
let currentFormat = "solo";
let currentMood = null;
let profiles = [];
let currentEpId = null;
let pollTimer = null;
const epTitleCache = {};
const BASE_TITLE = document.title;

// ---------- 基础 ----------
// edge 音色显示名:上游 Friendly 是语言名(如 "English (United States)"),对用户没用。
// 已知音色用官方中文名,其余退回 ShortName 的尾段(如 en-US-JennyNeural → Jenny)
const EDGE_NAME_MAP = {
  "zh-CN-XiaoxiaoNeural": "晓晓", "zh-CN-XiaoyiNeural": "晓伊",
  "zh-CN-YunjianNeural": "云健", "zh-CN-YunxiNeural": "云希",
  "zh-CN-YunxiaNeural": "云夏", "zh-CN-YunyangNeural": "云扬",
  "zh-CN-liaoning-XiaobeiNeural": "晓北(辽宁)", "zh-CN-shaanxi-XiaoniNeural": "晓妮(陕西)",
  "zh-HK-HiuMaanNeural": "曉曼(粤)", "zh-HK-HiuGaaiNeural": "曉佳(粤)", "zh-HK-WanLungNeural": "雲龍(粤)",
  "zh-TW-HsiaoChenNeural": "曉臻(台)", "zh-TW-YunJheNeural": "雲哲(台)",
};
function edgeLabel(v) {
  return EDGE_NAME_MAP[v.ShortName] || (v.ShortName || "").split("-").slice(-1)[0].replace(/Neural$/, "") || v.Friendly;
}
function detailText(d, status) {
  // FastAPI 422 的 detail 是数组、异常对象可能是 object——一律转人话,不出现 [object Object]
  if (typeof d === "string" && d) return d;
  if (Array.isArray(d)) return d.map((x) => (x && (x.msg || x.message)) || JSON.stringify(x)).join("；");
  if (d && typeof d === "object") return d.msg || d.message || JSON.stringify(d);
  return `请求失败 (${status})`;
}

async function api(path, opts = {}) {
  const resp = await fetch(path, {
    headers: opts.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...opts,
    body: opts.body instanceof FormData ? opts.body : opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(detailText(data.detail, resp.status));
  return data;
}

function toast(msg) {
  let el = $("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.style.display = "block";
  clearTimeout(el._t);
  el._t = setTimeout(() => (el.style.display = "none"), 3500);
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s ?? "";
  return d.innerHTML;
}

// 键盘可达:卡片类元素补 tabindex/role,Enter/Space 触发
function makeCard(el, onPick) {
  if (!el) return;
  el.setAttribute("tabindex", "0");
  el.setAttribute("role", "button");
  el.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" || ev.key === " " || ev.key === "Spacebar") {
      ev.preventDefault();
      onPick();
    }
  });
}

// 节目状态 → 人话(裸英文 state 不再直接示人)
const STATE_LABELS = {
  done: "已完成", error: "出错", synthesizing: "合成中", awaiting_review: "待审稿",
  awaiting_topic: "待选题", scripting: "写稿中", compliance: "审查中", researching: "选题中",
  created: "排队中", material_title: "起名中",
};
function stateLabel(s) { return t("state." + s, STATE_LABELS[s] || s); }

let lastActiveEpisode = null; // 离开创作页时记住,方便"继续创作"
let bannerTimer = null;

function stopBannerTimer() {
  if (bannerTimer) { clearInterval(bannerTimer); bannerTimer = null; }
}

function renderContinueBanner() {
  stopBannerTimer();
  const old = document.getElementById("continue-banner");
  if (old) old.remove();
  if (!lastActiveEpisode) return;
  // 正在详情页看这个节目 → 横幅多余
  if (!$("view-episode").hidden && currentEpId === lastActiveEpisode) return;
  const build = (st) => {
    const div = document.createElement("div");
    div.id = "continue-banner";
    div.className = "card";
    div.style.cssText = "border-color:var(--accent);margin-bottom:12px;cursor:pointer;padding:14px 20px";
    div.innerHTML = `<div style="display:flex;align-items:center;gap:10px">
      <span style="font-size:20px">▶</span>
      <span style="flex:1"><b>${esc(t("banner.continue"))}</b> — ${esc(st.title || st.input || "")} <span class="hint small">(${esc(stateLabel(st.state))})</span></span>
      <span style="color:var(--accent)">→</span></div>`;
    div.onclick = () => openEpisode(lastActiveEpisode);
    makeCard(div, () => openEpisode(lastActiveEpisode));
    document.querySelector("main").prepend(div); // <main> 顶部,所有视图可见
  };
  const refresh = () => {
    api(`/api/episodes/${lastActiveEpisode}`).then((st) => {
      // 节目到终态 → 横幅自动消失;正在看这个节目 → 也不显示
      if (["done", "error"].includes(st.state) ||
          (!$("view-episode").hidden && currentEpId === lastActiveEpisode)) {
        stopBannerTimer();
        const b = document.getElementById("continue-banner");
        if (b) b.remove();
        return;
      }
      const b = document.getElementById("continue-banner");
      if (b) b.remove();
      build(st);
    }).catch(() => {});
  };
  refresh();
  bannerTimer = setInterval(refresh, 30000); // 30 秒轮询自刷新
}

function showView(name) {
  for (const v of ["new", "episode", "library", "settings"]) {
    $("view-" + v).hidden = v !== name;
    const nav = $("nav-" + v);
    if (nav) nav.classList.toggle("active", v === name);
  }
  if (name !== "episode") stopPoll();
  stopElapsed(); // 离开视图清掉已耗时计表
  if (name === "new") document.title = BASE_TITLE; // 回到新建页去掉 ✅ 前缀
  if (name === "library") loadLibrary();
  if (name === "settings") loadSettings();
  // 离开创作页时记住活跃节目
  if (name !== "episode" && currentEpId) {
    const st = lastEpState;
    if (st && !["done", "error"].includes(st)) lastActiveEpisode = currentEpId;
  }
  renderContinueBanner();
}

function stopPoll() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

function openLibrary() { showView("library"); }
function openSettings() { showView("settings"); }

// ---------- 新建节目 ----------
function setMode(mode) {
  currentMode = mode;
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.mode === mode));
  $("mode-hint").textContent = t(mode === "topic" ? "hint.topic" : "hint.material");
  $("input-text").placeholder = t(mode === "topic" ? "input.topic.ph" : "input.material.ph");
  $("input-text").rows = mode === "material" ? 8 : 2;
}

function setFormat(f) {
  currentFormat = f;
  document.querySelectorAll('[data-format]').forEach((c) => c.classList.toggle("selected", c.dataset.format === f));
  loadVoices(currentFormat);  // 声音随形式联动
}

function renderMoodCards() {
  const box = $("mood-cards");
  if (!box) return;
  const prof = profiles.find((p) => p.id === currentStyle);
  const moods = (prof && prof.moods) || [];
  if (!moods.length) { box.innerHTML = ""; return; }
  if (!currentMood || !moods.find((m) => m.id === currentMood)) currentMood = moods[0].id;
  box.innerHTML = moods.map((m) => `
    <div class="style-card ${m.id === currentMood ? "selected" : ""}" data-mood="${m.id}" onclick="setMood('${m.id}')">
      <div class="t">${esc(m.name)}</div><div class="d">${esc(m.desc)}</div>
    </div>`).join("");
  box.querySelectorAll("[data-mood]").forEach((c) => makeCard(c, () => setMood(c.dataset.mood)));
}

function setMood(id) {
  currentMood = id;
  document.querySelectorAll("[data-mood]").forEach((c) => c.classList.toggle("selected", c.dataset.mood === id));
}

async function loadStructures() {
  try {
    const list = await api(`/api/structures?style=${currentStyle}`);
    const sel = $("structure-select");
    sel.innerHTML = `<option value="">${esc(t("structure.auto"))}</option>` +
      list.map((t) => `<option value="${t.id}">${esc(t.name)}</option>`).join("");
  } catch {}
}

async function loadProfiles() {
  profiles = await api("/api/profiles");
  const box = $("style-cards");
  box.innerHTML = "";
  for (const p of profiles) {
    const div = document.createElement("div");
    div.className = "style-card" + (p.id === currentStyle ? " selected" : "");
    div.innerHTML = `<div class="t">${esc(p.emoji)} ${esc(p.name)}</div><div class="d">${esc(p.desc)}</div>`;
    const pick = () => {
      currentStyle = p.id;
      box.querySelectorAll(".style-card").forEach((c) => c.classList.remove("selected"));
      div.classList.add("selected");
      fillMinutes(p.default_minutes);
      renderMoodCards();   // 基调卡片随类型联动
      loadStructures();     // 结构列表随类型联动
    };
    div.onclick = pick;
    makeCard(div, pick);
    box.appendChild(div);
  }
  fillMinutes((profiles.find((p) => p.id === currentStyle) || profiles[0]).default_minutes);
  renderMoodCards();
  loadStructures();
}

function fillMinutes(def) {
  const sel = $("minutes-select");
  sel.innerHTML = "";
  for (const m of [2, 3, 5, 8, 10, 15]) {
    const o = document.createElement("option");
    o.value = m; o.textContent = t("minutes.opt").replace("{m}", m);
    if (m === def) o.selected = true;
    sel.appendChild(o);
  }
}

async function loadVoices(fmt) {
  const data = await api(`/api/voices${fmt ? `?format=${fmt}` : ""}`);
  const sel = $("voice-select");
  sel.innerHTML = "";
  const group = (label) => {
    const g = document.createElement("optgroup");
    g.label = label;
    sel.appendChild(g);
    return g;
  };
  const add = (g, value, text) => {
    const o = document.createElement("option");
    o.value = value; o.textContent = text;
    g.appendChild(o);
  };

  if (data.clones.length) {
    const g = group("🗣️ 我的声音（克隆）");
    for (const c of data.clones) add(g, c.selector, c.name);
  }
  if ((data.locked || []).length) {
    const gl = group("🔒 未解锁（去设置配置对应 Key）");
    gl.disabled = true;
    for (const g2 of data.locked) {
      const o = document.createElement("option");
      o.disabled = true;
      o.textContent = `${g2.name}（需配置）`;
      gl.appendChild(o);
    }
  }
  const edge = data.builtin.edge || [];
  if (edge.length) {
    const zh = edge.filter((v) => v.Locale.startsWith("zh"));
    const en = edge.filter((v) => v.Locale.startsWith("en") && !v.Locale.startsWith("zh"));
    const ge = group("🆓 edge-tts（免费）");
    for (const v of zh.slice(0, 40)) add(ge, "edge:" + v.ShortName, `${edgeLabel(v)}（中文）`);
    for (const v of en.slice(0, 12)) add(ge, "edge:" + v.ShortName, edgeLabel(v) + "（英文）");
  }
  if (data.builtin.mimo?.length) {
    const gm = group("🔑 MiMo（需 API Key，支持情绪/克隆）");
    for (const v of data.builtin.mimo) add(gm, "mimo:" + v.ShortName, v.Friendly);
  }
  if (data.builtin.volc?.length) {
    const gv = group("🔑 火山豆包（需 app_id + token）");
    for (const v of data.builtin.volc) add(gv, "volc:" + v.ShortName, v.Friendly);
  }
  if (data.builtin.ali?.length) {
    const ga = group("🔑 阿里 CosyVoice（需 DashScope Key）");
    for (const v of data.builtin.ali) add(ga, "ali:" + v.ShortName, v.Friendly);
  }
  if (data.builtin.volcspeech?.length) {
    const gs = group("🎙️ 豆包音色（语音技术 Key·单人）");
    for (const v of data.builtin.volcspeech) add(gs, "volcspeech:" + v.ShortName, v.Friendly);
  }
  if (data.builtin.elevenlabs?.length) {
    const gel = group("🔑 ElevenLabs（国际，需 Key）");
    for (const v of data.builtin.elevenlabs) add(gel, "eleven:" + v.ShortName, v.Friendly);
  }
  if (data.builtin.azure?.length) {
    const gaz = group("🔑 Azure Speech（国际，SSML 情绪）");
    for (const v of data.builtin.azure) add(gaz, "azure:" + v.ShortName, v.Friendly);
  }
  if (data.builtin.volcpodcast?.length) {
    // 双人对谈专用组(E5:后端 dual 模式专门返回,此前前端没有渲染分支,下拉为空)
    const gvp = group("👥 方舟播客（双人对谈）");
    for (const v of data.builtin.volcpodcast) add(gvp, "volcpodcast:" + v.ShortName, v.Friendly);
  }

  // 保留已选值;默认优先晓晓(温暖中文女声)
  const keep = sel.dataset.keep;
  if (keep && sel.querySelector(`option[value="${CSS.escape(keep)}"]`)) sel.value = keep;
  else {
    const preferred = sel.querySelector('option[value="edge:zh-CN-XiaoxiaoNeural"]');
    sel.value = preferred ? preferred.value : sel.querySelector("option")?.value || "";
  }
  sel.dataset.keep = sel.value;
}

async function createEpisode() {
  const errEl = $("create-error");
  errEl.hidden = true;
  const input = $("input-text").value.trim();
  if (!input) {
    errEl.textContent = currentMode === "topic" ? "先输入一个选题范围或问题吧" : "先粘贴素材内容";
    errEl.hidden = false;
    return;
  }
  const btn = $("btn-create");
  btn.disabled = true;
  btn.textContent = t("btn.creating");
  try {
    const data = await api("/api/episodes", {
      method: "POST",
      body: {
        mode: currentMode,
        input,
        style: currentStyle,
        format: currentFormat,
        mood: currentMood,
        pace: $("pace-select").value,
        structure: $("structure-select").value || null,
        voice_id: $("voice-select").value,
        minutes: parseInt($("minutes-select").value, 10),
      },
    });
    $("input-text").value = "";
    openEpisode(data.id);
  } catch (e) {
    errEl.textContent = e.message;
    errEl.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = t("btn.create");
  }
}

// ---------- 节目详情 ----------
function openEpisode(id) {
  currentEpId = id;
  showView("episode");
  renderEpisode({ id, state: "loading" });
  pollEpisode();
}

function pollEpisode(delayMs = 0) {
  stopPoll();
  const tick = async () => {
    if (!currentEpId) return stopPoll();
    try {
      const st = await api(`/api/episodes/${currentEpId}`);
      renderEpisode(st);
      if (["done", "error", "awaiting_topic", "awaiting_review"].includes(st.state)) stopPoll();
    } catch (e) {
      stopPoll();
      toast("加载节目失败：" + e.message);
    }
  };
  if (delayMs > 0) {
    pollTimer = setTimeout(() => { pollTimer = null; tick(); pollTimer ??= setInterval(tick, 1600); }, delayMs);
    return;
  }
  tick();
  pollTimer = setInterval(tick, 1600);
}

// 步骤名走词典（英文界面不再出现中文）；缺键时用第三项兜底
const STEP_DEFS = [
  ["research", "选题"], ["script", "写稿"], ["compliance", "合规审查"], ["synthesis", "合成"],
].map(([id, fallback]) => [id, () => t("step." + id, fallback), fallback]);

function stepStates(st) {
  const s = st.state;
  const order = ["researching", "awaiting_topic", "scripting", "compliance", "awaiting_review", "synthesizing", "done"];
  const idx = (x) => order.indexOf(x);
  const at = idx(s) === -1 ? (s === "error" ? 99 : 0) : idx(s);
  const activeStep =
    s === "researching" ? "research" :
    s === "awaiting_topic" ? "research" :
    s === "scripting" ? "script" :
    s === "compliance" ? "compliance" :
    s === "awaiting_review" ? "compliance" :
    s === "synthesizing" ? "synthesis" : null;
  const map = { research: 0, script: 1, compliance: 2, synthesis: 3 };
  return STEP_DEFS.map(([id, labelFn]) => {
    const label = labelFn();
    if (s === "error" && st.failed_step === (id === "script" ? "script" : id === "compliance" ? "compliance" : id === "synthesis" ? "synthesis" : "research"))
      return { label, cls: "fail" };
    if (map[id] < at) return { label, cls: "done" };
    if (activeStep === id) return { label, cls: s === "awaiting_topic" || s === "awaiting_review" ? "done" : "active" };
    if (s === "done") return { label, cls: "done" };
    return { label, cls: "" };
  });
}

let lastEpState = null;

// ---------- 长流程陪伴:已耗时计时(首次渲染该 state 起表,每秒 mm:ss) ----------
let elapsedTimer = null, elapsedKey = null, elapsedT0 = 0;

function stopElapsed() {
  if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null; }
  elapsedKey = null;
}

function fmtElapsed(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

function startElapsed() {
  const key = `${currentEpId || ""}:${lastEpState || ""}`;
  if (elapsedKey !== key) { elapsedKey = key; elapsedT0 = Date.now(); } // 换了 state 才重新起表
  if (elapsedTimer) clearInterval(elapsedTimer);
  const tick = () => {
    const el = $("state-elapsed");
    if (!el) { stopElapsed(); return; }
    el.textContent = t("state.elapsed").replace("{t}", fmtElapsed(Date.now() - elapsedT0));
  };
  tick();
  elapsedTimer = setInterval(tick, 1000);
}

function renderEpisode(st) {
  window.__currentOrigin = st.origin || "real";
  lastEpState = st.state;
  // done 时标题加 ✅ 提醒(放下手机的家长),其他状态还原
  document.title = st.state === "done" ? "✅ " + BASE_TITLE : BASE_TITLE;
  $("ep-title").textContent = st.title || (st.mode === "material" ? t("title.material.fallback") : st.input || "…");
  if (st.id && st.title) epTitleCache[st.id] = st.title;
  const stepsBox = $("ep-steps");
  stepsBox.innerHTML = "";
  for (const s of stepStates(st)) {
    const el = document.createElement("span");
    el.className = "step " + s.cls;
    el.textContent = s.label + (s.cls === "fail" ? " ✕" : s.cls === "done" ? " ✓" : "");
    stepsBox.appendChild(el);
  }
  const body = $("ep-body");
  const lastLog = (st.log || []).slice(-1)[0]?.msg || "";
  const state = st.state;
  // 非长流程状态一律停表
  if (!["loading", "created", "researching", "scripting", "compliance", "material_title", "synthesizing"].includes(state)) stopElapsed();

  if (state === "loading" || ["created", "researching", "scripting", "compliance", "material_title"].includes(state)) {
    body.innerHTML = `<div class="state-msg working">${esc(lastLog || t("state.working"))}</div>
      <p class="hint small">⏱ <span class="elapsed" id="state-elapsed"></span> · ${esc(t("hint.longrun"))}</p>`;
    startElapsed();
    return;
  }

  if (state === "awaiting_topic") {
    const fcBadge = { reliable: `<span class="badge" style="background:var(--ok-bg);color:var(--ok)">${esc(t("factcheck.reliable"))}</span>`,
                      uncertain: `<span class="badge" style="background:var(--warn-bg);color:var(--warn-ink)">${esc(t("factcheck.uncertain"))}</span>` };
    const srcBlock = (st.sources || []).length
      ? `<p class="hint small">${esc(t("topic.sources"))}${(st.sources).map((r) => esc(r.title) + (r.domain ? ` <code>${esc(r.domain)}</code>` : "")).join(" · ")}</p>`
      : "";
    const cands = st.candidates || [];
    if (!cands.length) {
      // 空候选兜底:不能让用户停在一片空白里(死端回归)
      body.innerHTML = `<p class="hint">${esc(t("topic.empty.hint"))}</p>
        <div class="actions">
          <button class="btn" onclick="retryEpisode()">${esc(t("btn.retopic"))}</button>
          <button class="btn secondary" onclick="backToNewWithMaterial()">${esc(t("btn.useMaterial"))}</button>
        </div>`;
      return;
    }
    body.innerHTML = `<p class="hint">${esc(t("label.candidates"))}</p>${srcBlock}` + cands.map((c) => `
      <div class="cand" data-idx="${c.index}" onclick="selectTopic(${c.index})">
        <div class="t">${esc(c.title)}${c.heat ? `<span class="badge">${esc(t("topic.heat"))} ${esc(c.heat)}</span>` : ""}${fcBadge[c.factcheck] || ""}</div>
        ${c.angle ? `<div class="r">${esc(t("topic.angle"))}${esc(c.angle)}</div>` : ""}
        ${c.reason ? `<div class="r">💡 ${esc(c.reason)}</div>` : ""}
        ${c.factcheck === "uncertain" && c.fc_reason ? `<div class="r" style="color:var(--warn-ink)">🔍 ${esc(c.fc_reason)}</div>` : ""}
      </div>`).join("");
    body.querySelectorAll(".cand").forEach((c) => makeCard(c, () => selectTopic(Number(c.dataset.idx))));
    return;
  }

  if (state === "awaiting_review") {
    const calHint = st.calibrated === false ? `<p class="hint small" style="margin-top:6px">${esc(t("calibrate.hint.review"))}</p>` : "";
    const rep = st.compliance || {};
    const items = rep.items || [];
    body.innerHTML = `
      <label>${esc(t("label.script"))}</label>
      <textarea id="ep-script" rows="14">${esc(st.script || "")}</textarea>
      ${calHint}
      <div class="compliance ${rep.pass === false ? "warn" : "pass"}">
        🛡️ ${esc(t("compliance.title"))}${rep.pass === false ? esc(t("compliance.fail")) : esc(t("compliance.pass"))} ${rep.summary ? "— " + esc(rep.summary) : ""}
        ${items.map((i) => `<div class="item">【${esc(i.dimension)}】${i.pass === false ? "⚠️ " : ""}${esc(i.issues || "")}${i.suggestion ? " → 建议：" + esc(i.suggestion) : ""}</div>`).join("")}
      </div>
      <div class="actions">
        <button class="cta" onclick="approveEpisode()">${esc(t("btn.approve"))}</button>
        <button class="ghost-btn" onclick="saveScript()">${esc(t("btn.saveScript"))}</button>
        <button class="ghost-btn" onclick="regenScript()">${esc(t("btn.regen"))}</button>
      </div>`;
    return;
  }

  if (state === "synthesizing") {
    const sy = st.synthesis || {};
    const pct = sy.total ? Math.round((sy.done / sy.total) * 100) : 0;
    body.innerHTML = `
      <div class="state-msg working">${esc(sy.current || lastLog || "正在合成…")}</div>
      <div class="progress"><div class="bar" style="width:${pct}%"></div></div>
      <p class="hint small">${sy.done || 0} / ${sy.total || "?"} 段 · 声音：${esc(sy.voice || "")}（${esc(sy.provider || "")}）</p>
      <p class="hint small">⏱ <span class="elapsed" id="state-elapsed"></span> · ${esc(t("hint.longrun"))}</p>`;
    startElapsed();
    return;
  }

  if (state === "done") {
    const dur = st.duration_sec ? `⏱ ${Math.floor(st.duration_sec / 60)} 分 ${Math.round(st.duration_sec % 60)} 秒 · ` : "";
    const chunks = (st.synthesis && st.synthesis.total) ? `共 ${st.synthesis.total} 段 · ` : "";
    // P1-2:校准提示与用量合并为一行灰字,不抢主操作的视线
    const calLine = st.calibrated === false ? t("calibrate.hint.done") : "";
    const metaLine = [calLine, usageText(st)].filter(Boolean).join(" · ");
    body.innerHTML = `
      <div class="player">
        <p class="hint">${esc(t("btn.listen"))}${dur}${chunks}声音：${esc(sy_label(st))}</p>
        <audio controls preload="metadata" src="/api/episodes/${st.id}/audio?t=${Date.now()}"></audio>
        ${metaLine ? `<p class="hint small">${metaLine}</p>` : ""}
      </div>
      <div class="actions">
        <button class="cta" onclick="downloadEpisode('${st.id}')">${esc(t("btn.download"))}</button>
        <button class="ghost-btn" onclick="showView('new')">${esc(t("btn.again"))}</button>
      </div>
      <details class="done-fold">
        <summary>${esc(t("done.export.title"))}</summary>
        <div class="actions" style="margin-top:12px">
          <button class="ghost-btn" onclick="exportEpisode('${st.id}')">${esc(t("btn.export"))}</button>
          <button class="ghost-btn" onclick="openLibrary()">${esc(t("btn.toLibrary"))}</button>
        </div>
        <div class="field-row" style="margin-top:14px">
          <label>${esc(t("label.shownotes"))}</label>
          <textarea id="ep-shownotes" rows="8">${esc(st.shownotes || "")}</textarea>
          <div class="actions" style="margin-top:10px">
            <button class="ghost-btn" onclick="copyShownotes()">${esc(t("btn.copy"))}</button>
            <button class="ghost-btn" onclick="saveShownotes()">${esc(t("btn.saveNotes"))}</button>
          </div>
        </div>
      </details>
      <details class="done-fold">
        <summary>${esc(t("label.chunks"))}</summary>
        <div id="ep-chunks" class="hint">分段加载中…</div>
      </details>`;
    loadChunks(st.id);
    return;
  }

  if (state === "error") {
    // 按 failed_step 给「下一步动作」;合成失败时文稿是好的,先让用户把文本拿走
    const stepHints = {
      synthesis: `<button class="btn" onclick="exportEpisode(currentEpId)">⬇️ 先导出文稿（音频没成,稿是好的）</button>`,
      research: `<span class="hint">试试换个更具体的说法再重试。</span>`,
      script: `<span class="hint">写稿失败多为网络/接口问题,重试通常可恢复。</span>`,
      compliance: `<span class="hint">审查没通过,可返回修改文稿后再试。</span>`,
    };
    body.innerHTML = `
      <div class="compliance warn">❌ ${esc(st.error || "出错了")}</div>
      ${stepHints[st.failed_step] || `<span class="hint">重试通常能解决临时性问题;反复失败请展开技术详情。</span>`}
      <div class="actions">
        <button class="cta" onclick="retryEpisode()">${esc(t("btn.retry"))}</button>
        <button class="ghost-btn" onclick="openLibrary()">${esc(t("btn.back"))}</button>
      </div>
      ${st.error_raw ? `<details class="hint small"><summary>技术详情（排查用）</summary><pre style="white-space:pre-wrap">${esc(st.error_raw)}</pre></details>` : ""}`;
    return;
  }
  body.innerHTML = `<div class="state-msg">${esc(lastLog)}</div>`;
}

async function selectTopic(i) {
  try { await api(`/api/episodes/${currentEpId}/select`, { method: "POST", body: { index: i } }); setTimeout(() => pollEpisode(400), 300); }
  catch (e) { toast(e.message); }
}
async function saveScript() {
  try {
    await api(`/api/episodes/${currentEpId}/script`, { method: "POST", body: { text: $("ep-script").value } });
    toast("已保存修改"); pollEpisode();
  } catch (e) { toast(e.message); }
}
async function regenScript() {
  try { await api(`/api/episodes/${currentEpId}/regenerate`, { method: "POST" }); setTimeout(() => pollEpisode(400), 300); }
  catch (e) { toast(e.message); }
}
async function approveEpisode() {
  try { await api(`/api/episodes/${currentEpId}/approve`, { method: "POST" }); setTimeout(() => pollEpisode(400), 300); }
  catch (e) { toast(e.message); }
}
function backToNewWithMaterial() {
  setMode("material");
  showView("new");
  const el = $("input");
  if (el) { el.focus(); }
}
async function retryEpisode() {
  try { await api(`/api/episodes/${currentEpId}/retry`, { method: "POST" }); setTimeout(() => pollEpisode(400), 300); }
  catch (e) { toast(e.message); }
}
async function copyShownotes() {
  const text = $("ep-shownotes").value;
  try { await navigator.clipboard.writeText(text); toast("✅ 已复制到剪贴板"); }
  catch { $("ep-shownotes").select(); document.execCommand("copy"); toast("✅ 已复制"); }
}
async function saveShownotes() {
  try {
    await api(`/api/episodes/${currentEpId}/shownotes`, { method: "POST", body: { text: $("ep-shownotes").value } });
    toast("✅ shownotes 已保存");
  } catch (e) { toast(e.message); }
}

async function loadChunks(epId) {
  const box = $("ep-chunks");
  try {
    const chunks = await api(`/api/episodes/${epId}/chunks`);
    if (!chunks.length) { box.innerHTML = "（无分段）"; return; }
    box.innerHTML = chunks.map((c) => `
      <div class="clone-row">
        <span class="badge-state done">${esc(c.emotion || "正文")}</span>
        <span style="flex:1;font-size:13px;color:var(--ink-soft)">${esc(c.text)} <small>(${c.chars}字)</small></span>
        ${window.__currentOrigin === "demo" ? "" : `<button class="ghost-btn" style="padding:4px 10px" onclick="resynthChunk(${c.index})">${esc(t("btn.rerecord"))}</button>`}
      </div>`).join("");
  } catch (e) { box.textContent = "分段加载失败：" + e.message; }
}

async function resynthChunk(index) {
  try {
    await api(`/api/episodes/${currentEpId}/resynth`, { method: "POST", body: { index } });
    toast(`开始重录第 ${index + 1} 段…`);
    setTimeout(() => pollEpisode(400), 300);
  } catch (e) { toast(e.message); }
}

function usageText(st) {
  const u = st && st.usage;
  if (!u) return "";
  const parts = [];
  if (u.llm_calls) parts.push(`写稿 ${u.llm_calls} 次`);
  if (u.llm_in_chars || u.llm_out_chars) {
    parts.push(`输入 ${u.llm_in_chars || 0} 字 / 输出 ${u.llm_out_chars || 0} 字`);
  }
  if (u.tts_chars) parts.push(`语音 ${u.tts_chars} 字`);
  let line = parts.length ? `📊 本期用量：${parts.join(" · ")}` : "";
  const c = st.usage_cost;
  if (c && c.total) {
    const cur = c.currency === "CNY" ? "¥" : (c.currency || "");
    line += `（约 ${cur}${c.total}）`;
  }
  return line;
}

function minutesLabel(m) {
  return t("minutes.approx", "约 {m} 分钟").replace("{m}", m);
}

function sy_label(st) {
  return (st.synthesis && st.synthesis.voice) || "";
}

// ---------- 我的电台 ----------
async function loadLibrary() {
  window.__library = await api("/api/episodes");
  renderLibraryList();
}

// ---------- P1-5:统一空态 = 一句解释 + 一个主按钮 + 一个次要链接 ----------
function emptyState(icon, text, primaryHtml, secondaryHtml) {
  return `<div class="empty">
    <div class="empty-icon">${icon}</div>
    <p class="empty-text">${text}</p>
    ${primaryHtml}
    ${secondaryHtml ? `<p class="hint small" style="margin-top:12px">${secondaryHtml}</p>` : ""}
  </div>`;
}

function clearLibrarySearch() {
  const el = $("library-search");
  if (el) el.value = "";
  renderLibraryList();
}

function gotoRecording() {
  showView("settings");
  requestAnimationFrame(() => $("rec-btn")?.scrollIntoView({ behavior: "smooth", block: "center" }));
}

function renderLibraryList() {
  const all = window.__library || [];
  const kw = ($("library-search")?.value || "").trim().toLowerCase();
  const list = kw ? all.filter((e) => (e.title || "").toLowerCase().includes(kw)) : all;
  const box = $("library-list");
  if (!all.length) {
    box.innerHTML = emptyState("🎙️", esc(t("library.empty")),
      `<button class="cta empty-cta" onclick="showView('new')">${esc(t("library.empty.cta"))}</button>`,
      `<a href="#" onclick="showDemoShelf();return false">${esc(t("demo.listen"))}</a>`);
    return;
  }
  if (!list.length) {
    box.innerHTML = emptyState("🔍", esc(t("empty.nomatch.text").replace("{kw}", kw)),
      `<button class="cta empty-cta" onclick="clearLibrarySearch()">${esc(t("empty.nomatch.clear"))}</button>`,
      `<a href="#" onclick="showView('new');return false">${esc(t("empty.nomatch.new"))}</a>`);
    return;
  }
  const styleMap = Object.fromEntries(profiles.map((p) => [p.id, p.emoji]));
  const active = list.filter((e) => !e.archived);
  const archived = list.filter((e) => e.archived);
  const real = active.filter((e) => e.origin !== "demo");
  const demos = active.filter((e) => e.origin === "demo");
  const row = (ep, isDemo) => {
    epTitleCache[ep.id] = ep.title;
    const stateText = stateLabel(ep.state);
    const cover = ep.has_cover ? `<img class="ep-cover" src="/api/episodes/${ep.id}/cover" alt="" loading="lazy">` : `<span class="ep-cover ep-cover-ph">${styleMap[ep.style] || "🎙️"}</span>`;
    // P1-4:行内只留 试听/打开,其余动作收进 ⋯ 菜单(功能不减少)
    const menuItems = [
      ep.has_audio ? `<button onclick="downloadEpisode('${ep.id}')">⬇️ 下载 MP3</button>` : "",
      ep.has_audio ? `<button onclick="exportEpisode('${ep.id}')">📦 打包下载(音频+文稿+封面)</button>` : "",
      isDemo ? "" : `<button onclick="renameEpisode('${ep.id}', this.closest('.ep-row').querySelector('.t'))">✏️ 改名</button>`,
      isDemo ? "" : `<button onclick="toggleArchive('${ep.id}', ${!!ep.archived})">${ep.archived ? "📤 取消归档" : "🗃️ 归档（列表里收起，不删除）"}</button>`,
      isDemo ? "" : `<button onclick="deleteEpisode('${ep.id}')">🗑️ 删除</button>`,
    ].filter(Boolean).join("");
    const menu = menuItems
      ? `<details class="row-menu"><summary class="icon-btn" title="更多操作">⋯</summary><div class="menu">${menuItems}</div></details>`
      : "";
    return `<div class="ep-row">
      ${cover}
      <div class="info">
        <div class="t" ${isDemo ? "" : `title="双击改名" ondblclick="renameEpisode('${ep.id}', this)"`}>${isDemo ? `<span class="badge">🎯 示例</span> ` : ""}${esc(ep.title)}</div>
        <div class="m">${esc((ep.created || "").replace("T", " "))} · ${esc(minutesLabel(ep.minutes))} · <span class="badge-state ${ep.state === "done" ? "done" : ep.state === "error" ? "error" : ""}">${stateText}</span></div>
      </div>
      ${ep.has_audio ? `<button class="icon-btn" title="试听" onclick="playFromLibrary('${ep.id}')">▶️</button>` : ""}
      <button class="icon-btn" title="打开" onclick="openEpisode('${ep.id}')">📂</button>
      ${menu}
    </div>`;
  };
  // 示例电台独立分区,不混入时间线;有真实节目后默认折叠
  box.innerHTML =
    real.map((ep) => row(ep, false)).join("") +
    (archived.length ? `<details class="demo-shelf">
      <summary class="hint">🗃️ 已归档（${archived.length} 期 · 不删除，可随时取消归档）</summary>
      ${archived.map((ep) => row(ep, ep.origin === "demo")).join("")}
    </details>` : "") +
    (demos.length ? `<details class="demo-shelf" ${real.length ? "" : "open"}>
      <summary class="hint">🎧 示例电台（${demos.length} 期 · 听听效果,不算你的节目）</summary>
      ${demos.map((ep) => row(ep, true)).join("")}
    </details>` : "");
  // 「先听听示例电台」入口:渲染后展开示例分区并滚到跟前
  if (window.__demoShelfOpen) {
    window.__demoShelfOpen = false;
    const shelf = box.querySelector(".demo-shelf");
    if (shelf) { shelf.open = true; shelf.scrollIntoView({ behavior: "smooth", block: "start" }); }
    else toast(t("library.demo.missing"));
  }
}

function playFromLibrary(id) { openEpisode(id); }

async function renameEpisode(id, el) {
  const current = el.textContent.replace(/^[🌙🎙️]\s*/, "");
  const title = prompt("改个标题:", current);
  if (title === null || !title.trim()) return;
  try {
    await api(`/api/episodes/${id}/rename`, { method: "POST", body: { title: title.trim() } });
    loadLibrary();
  } catch (e) { toast(e.message); }
}

// ---------- 语音输入选题 ----------
let viState = { recorder: null, chunks: [], blob: null, stream: null };

async function voiceInput() {
  const btn = $("voice-input-btn"), status = $("vi-status");
  if (viState.recorder && viState.recorder.state === "recording") {
    viState.recorder.stop();
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia) return toast("此环境不支持录音");
  try { viState.stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
  catch { status.textContent = "拿不到麦克风权限"; return; }
  viState.chunks = []; viState.blob = null;
  viState.recorder = new MediaRecorder(viState.stream);
  viState.recorder.ondataavailable = (e) => viState.chunks.push(e.data);
  viState.recorder.onstop = async () => {
    viState.stream.getTracks().forEach((t) => t.stop());
    viState.blob = new Blob(viState.chunks, { type: viState.recorder.mimeType || "audio/webm" });
    viState.recorder = null;
    btn.textContent = "🎙️ 说一句"; btn.disabled = true;
    status.textContent = "识别中…";
    try {
      const fd = new FormData();
      fd.append("file", viState.blob, "input.webm");
      const resp = await fetch("/api/asr/transcribe", { method: "POST", body: fd });
      const d = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(detailText(d.detail, resp.status));
      $("input-text").value = ($("input-text").value + " " + d.text).trim();
      status.textContent = "✅ " + d.text;
    } catch (e) {
      status.textContent = "";
      toast("语音识别失败：" + e.message);
    } finally { btn.disabled = false; }
  };
  viState.recorder.start();
  btn.textContent = "⏹️ 说完了";
  status.textContent = "录音中…(60 秒内)";
  setTimeout(() => { if (viState.recorder?.state === "recording") viState.recorder.stop(); }, 60000);
}

async function previewVoice() {
  const sel = $("voice-select").value;
  if (sel.startsWith("clone:")) return toast("克隆声音请直接做一期试听效果");
  if (!(sel.startsWith("edge:") || sel.startsWith("mimo:")))
    return toast("在线试听支持 🆓 edge 与 MiMo(需 Key);其余音色配置后直接做一期体验");
  const btn = event?.target;
  if (btn) { btn.disabled = true; btn.textContent = "…"; }
  try {
    const resp = await fetch("/api/voices/preview", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selector: sel }),
    });
    if (!resp.ok) { const d = await resp.json().catch(() => ({})); throw new Error(detailText(d.detail, resp.status)); }
    const blob = await resp.blob();
    new Audio(URL.createObjectURL(blob)).play();
  } catch (e) { toast("试听失败：" + e.message); }
  finally { if (btn) { btn.disabled = false; btn.textContent = "🔊 试听"; } }
}

// ---------- 主题切换(O6 深色模式的手动开关;此前 onclick 挂了未定义函数,按钮是死的) ----------
function toggleTheme() {
  const cur = document.documentElement.getAttribute("data-theme")
    || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = cur === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem("ps-theme", next); } catch (e) {}
  const btn = document.getElementById("theme-toggle");
  if (btn) btn.textContent = next === "dark" ? "🌙" : "☀️";
}

// ---------- 音色墙：一页看遍所有可选声音（D 商业基准 #3） ----------
const VOICE_WALL_META = [
  { key: "edge", prefix: "edge", badge: "free", group: "voices.group.edge" },
  { key: "mimo", prefix: "mimo", badge: "key", group: "voices.group.mimo" },
  { key: "volcspeech", prefix: "volcspeech", badge: "key", group: "voices.group.volcspeech" },
  { key: "volcpodcast", prefix: "volcpodcast", badge: "key", group: "voices.group.volcpodcast" },
  { key: "volc", prefix: "volc", badge: "key", group: "voices.group.volc" },
  { key: "ali", prefix: "ali", badge: "key", group: "voices.group.ali" },
  { key: "elevenlabs", prefix: "eleven", badge: "key", group: "voices.group.elevenlabs" },
  { key: "azure", prefix: "azure", badge: "key", group: "voices.group.azure" },
];
const VOICE_WALL_LIMIT = 24; // 免费组先露脸的数量，其余点「展开全部」

function voiceCardHtml(name, badge, badgeCls, selector, locale, hidden) {
  const meta = locale ? `<code class="vc-locale">${esc(locale)}</code>` : "";
  return `<div class="voice-card"${hidden ? " hidden" : ""}>
    <div class="vc-name">${esc(name)}</div>
    <div class="vc-meta"><span class="vc-badge ${badgeCls}">${esc(badge)}</span>${meta}</div>
    <button class="ghost-btn vc-play" data-sel="${esc(selector)}" onclick="previewWallVoice(this)">${esc(t("voices.wall.preview"))}</button>
  </div>`;
}

function renderVoiceWall() {
  const box = $("voice-wall");
  const expand = $("voice-wall-expand");
  if (!box) return;
  const data = window.__voicesData;
  if (!data) { box.innerHTML = ""; if (expand) expand.hidden = true; return; }
  let html = "";
  const clones = data.clones || [];
  if (clones.length) {
    html += `<div class="vc-group">${esc(t("voices.group.clones"))} · ${clones.length}</div>`;
    html += clones.map((c) => voiceCardHtml(c.name, t("voices.wall.clone"), "clone", c.selector, "", false)).join("");
  }
  const builtin = data.builtin || {};
  let edgeCount = 0;
  for (const m of VOICE_WALL_META) {
    let voices = builtin[m.key] || [];
    if (!voices.length) continue;
    if (m.key === "edge") {
      // 与主下拉一致的排序习惯：中文优先，其次英文，其余殿后
      const zh = voices.filter((v) => (v.Locale || "").startsWith("zh"));
      const en = voices.filter((v) => (v.Locale || "").startsWith("en"));
      const rest = voices.filter((v) => !(v.Locale || "").startsWith("zh") && !(v.Locale || "").startsWith("en"));
      voices = [...zh, ...en, ...rest];
    }
    html += `<div class="vc-group">${esc(t(m.group))} · ${voices.length}</div>`;
    voices.forEach((v, i) => {
      const over = m.key === "edge" && i >= VOICE_WALL_LIMIT;
      if (m.key === "edge") edgeCount++;
      html += voiceCardHtml(m.key === "edge" ? edgeLabel(v) : (v.Friendly || v.ShortName),
        t(m.badge === "free" ? "voices.wall.free" : "voices.wall.needkey"),
        m.badge, m.prefix + ":" + v.ShortName, v.Locale || "", over);
    });
  }
  for (const g of data.locked || []) {
    html += `<div class="voice-card locked">
      <div class="vc-name">🔒 ${esc(g.name)}</div>
      <div class="vc-meta"><span class="vc-badge key">${esc(t("voices.wall.needkey"))}</span></div>
      <div class="vc-unlock">${esc(t("voices.wall.unlock"))}</div>
    </div>`;
  }
  box.innerHTML = html || `<p class="hint">${esc(t("voices.wall.empty"))}</p>`;
  if (expand) expand.hidden = edgeCount <= VOICE_WALL_LIMIT;
}

function expandVoiceWall() {
  document.querySelectorAll("#voice-wall .voice-card[hidden]").forEach((el) => (el.hidden = false));
  const btn = $("voice-wall-expand");
  if (btn) btn.hidden = true;
}

async function previewWallVoice(btn) {
  if (!btn || btn.disabled) return;
  if ((btn.dataset.sel || "").startsWith("clone:")) return toast(t("voices.wall.clone.hint"));
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = "…";
  try {
    const resp = await fetch("/api/voices/preview", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selector: btn.dataset.sel }),
    });
    if (!resp.ok) { const d = await resp.json().catch(() => ({})); throw new Error(detailText(d.detail, resp.status)); }
    const blob = await resp.blob();
    new Audio(URL.createObjectURL(blob)).play();
  } catch (e) { toast(e.message); } // 后端的友好提示原样展示
  finally { btn.disabled = false; btn.textContent = old; }
}

function openVoiceWall() {
  showView("settings");
  const fold = $("voice-wall-fold");
  if (fold) fold.open = true; // 音色墙默认折叠(P1-1),跳转前先展开
  requestAnimationFrame(() => $("voice-wall-card")?.scrollIntoView({ behavior: "smooth", block: "start" }));
}

// ---------- 「先听听示例电台」入口（D #6 收口）：跳电台并展开示例分区 ----------
function showDemoShelf() {
  window.__demoShelfOpen = true;
  showView("library");
  const shelf = document.querySelector("#library-list .demo-shelf");
  if (shelf) shelf.open = true;
}

async function exportEpisode(id) {
  try {
    const resp = await fetch(`/api/episodes/${id}/export`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = (epTitleCache[id] || id) + ".zip";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  } catch (e) { toast("打包失败：" + e.message); }
}

async function downloadEpisode(id) {
  // WebView 可能忽略 <a download>,直接导航到音频会显示二进制"乱码" → 用 blob 强制下载
  try {
    const resp = await fetch(`/api/episodes/${id}/audio`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const blob = await resp.blob();
    const name = (epTitleCache[id] || `podcast-${id}`) + ".mp3";
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  } catch (e) { toast("下载失败：" + e.message); }
}

async function deleteEpisode(id) {
  if (!confirm("确定删除这期节目？（文稿和音频都会删掉）")) return;
  try { await api(`/api/episodes/${id}`, { method: "DELETE" }); loadLibrary(); }
  catch (e) { toast(e.message); }
}

// ---------- 设置 ----------
async function loadSettings() {
  const [cfg, health, voices] = await Promise.all([api("/api/config"), api("/api/health"), api("/api/voices")]);
  window.__voicesData = voices; // 音色墙数据源(每次进设置页刷新)
  renderVoiceWall();
  $("cfg-provider").value = cfg.llm.provider;
  $("cfg-base-url").value = cfg.llm.openai.base_url || "";
  $("cfg-model").value = cfg.llm.openai.model || "";
  $("cfg-api-key").value = cfg.llm.openai.api_key || "";
  $("cfg-api-key").placeholder = cfg.llm.openai.api_key ? "已保存：" + cfg.llm.openai.api_key : "sk-…";
  if (cfg.llm.openai.api_key) $("cfg-api-key").value = "****"; // 占位，保存时原样传回表示不改
  $("cfg-auth-style").value = cfg.llm.openai.auth_style || "api-key";
  $("cfg-ssl-verify").checked = !!cfg.llm.openai.ssl_verify;
  onProviderChange();

  // 定时生成表单
  const bgm = cfg.audio || {};
  if ($("price-currency")) {
    const pr = (cfg.data && cfg.data.pricing) || {};
    $("price-currency").value = pr.currency || "CNY";
    $("price-llm-in").value = String(pr.llm_per_million_in ?? 0);
    $("price-llm-out").value = String(pr.llm_per_million_out ?? 0);
    $("price-tts").value = String(pr.tts_per_kchar ?? 0);
  }
  if ($("bgm-path")) {
    $("bgm-path").value = bgm.bgm_path || "";
    $("bgm-vol").value = String(bgm.bgm_volume ?? 0.12);
  }
  const sched = cfg.schedule || {};
  $("sched-enabled").checked = !!sched.enabled;
  $("sched-time").value = sched.time || "20:30";
  const ssb = $("sched-style");
  ssb.innerHTML = profiles.map((p2) => `<option value="${p2.id}">${p2.emoji} ${esc(p2.name)}</option>`).join("");
  ssb.value = sched.style || "bedtime";
  $("sched-minutes").value = String(sched.minutes || 5);
  // 定时声音下拉独立取全量数据(E7:此前复用主下拉 DOM,可能被 format 过滤成空)
  api("/api/voices").then((vd) => {
    const opts = [];
    (vd.clones || []).forEach((c) => opts.push(`<option value="${esc(c.selector)}">🗣️ 我的声音: ${esc(c.name)}</option>`));
    const builtin = vd.builtin || {};
    const groups = [
      ["edge", "🆓 edge-tts(免费)"],
      ["mimo", "🔑 MiMo"],
      ["volcspeech", "🎙️ 豆包音色(单人)"],
      ["volcpodcast", "👥 方舟播客(双人)"],
      ["volc", "🔑 火山豆包(旧版)"],
      ["ali", "🔑 阿里 CosyVoice"],
      ["elevenlabs", "🔑 ElevenLabs"],
      ["azure", "🔑 Azure Speech"],
    ];
    for (const [key, label] of groups) {
      for (const v of builtin[key] || []) {
        const prefix = key === "elevenlabs" ? "eleven" : key;
        opts.push(`<option value="${esc(prefix + ":" + v.ShortName)}">${label}: ${esc(v.Friendly)}</option>`);
      }
    }
    const sv = $("sched-voice");
    sv.innerHTML = opts.join("");
    sv.value = sched.voice_id || "edge:zh-CN-XiaoxiaoNeural";
    if (!sv.value) sv.selectedIndex = 0;
  }).catch(() => {});
  $("sched-topics").value = (sched.topics || []).join("\n");

  // 预设下拉
  try {
    const presets = await api("/api/llm/presets");
    const psel = $("cfg-preset");
    psel.innerHTML = '<option value="">选择厂商自动填充…</option>' +
      presets.map((p2) => `<option value="${p2.id}">${esc(p2.name)}</option>`).join("");
    window.__llmPresets = Object.fromEntries(presets.map((p2) => [p2.id, p2]));
  } catch {}

  // ASR 字段
  const avc = (cfg.asr || {}).volc || {};
  $("tts-asr-appid").value = avc.app_id || "";
  $("tts-asr-token").value = avc.token || "";

  // 搜索配置
  const sc = cfg.search || {};
  $("search-provider").value = sc.provider || "off";
  $("search-key").value = sc.tavily?.api_key || sc.bocha?.api_key || "";

  const vtts = (cfg.tts || {}).volc || {};
  const atts = (cfg.tts || {}).ali || {};
  $("tts-volc-appid").value = vtts.app_id || "";
  $("tts-volc-token").value = vtts.token || "";
  $("tts-volc-cluster").value = vtts.cluster || "volcano_tts";
  $("tts-ali-key").value = atts.api_key || "";
  $("tts-ali-model").value = atts.model || "cosyvoice-v2";
  const elc = (cfg.tts || {}).elevenlabs || {};
  $("tts-el-key").value = elc.api_key || "";
  $("tts-el-model").value = elc.model || "eleven_multilingual_v2";
  const azc = (cfg.tts || {}).azure || {};
  $("tts-az-key").value = azc.api_key || "";
  $("tts-az-region").value = azc.region || "eastasia";
  const vpc = (cfg.tts || {}).volc_podcast || {};
  $("tts-vp-key").value = vpc.api_key || "";
  $("tts-vp-resource").value = vpc.resource_id || "volc.service_type.10050";

  $("sys-info").innerHTML = `版本 v${health.version} · ffmpeg：${health.ffmpeg ? "✅ 已安装" : "❌ 未找到（合成会失败，请安装 ffmpeg）"}<br>数据目录：${esc(health.data_dir_hint || "~/.podcast-studio/data")}${health.allow_lan ? " · ⚠️ 已允许局域网访问（无账号体系，仅限可信网络）" : ""}`;

  const box = $("clone-list");
  box.innerHTML = (voices.clones || []).map((c) =>
    `<div class="clone-row">🗣️ <b>${esc(c.name)}</b><span class="hint small">（${c.created}）</span>
     <span style="flex:1"></span><button class="icon-btn" onclick="deleteClone('${c.id}')">🗑️</button></div>`
  ).join("") || emptyState("🗣️", esc(t("empty.clone.text")),
    `<button class="cta empty-cta" onclick="gotoRecording()">${esc(t("empty.clone.record"))}</button>`,
    `<a href="#" onclick="openVoiceWall();return false">${esc(t("voice.wall.link"))}</a>`);
}

async function matchKey() {
  const key = $("paste-key").value.trim();
  const box = $("keymatch-report");
  if (!key) return toast("先把 Key 粘进来");
  box.hidden = false;
  box.innerHTML = `<div class="state-msg working">正在拿这把 Key 逐家验证(DeepSeek / Kimi / 通义 / 混元 / OpenAI / 方舟)…</div>`;
  try {
    const r = await api("/api/keys/match", { method: "POST", body: { key } });
    const rows = (r.attempts || []).map((a) =>
      `<div class="clone-row"><span>${a.ok ? "✅" : "❌"} ${esc(a.provider)}</span>
       <span class="hint small" style="flex:1">${esc(a.info)}${a.ms ? ` · ${a.ms}ms` : ""}</span></div>`).join("");
    if (r.matched === "ark") {
      box.innerHTML = `<div class="compliance pass">🎉 认出来了:<b>火山方舟(豆包)</b>。<br>✅ 播客 TTS 已接入——「双人对谈」从此用豆包官方播客音色,不再是微软声音。</div>${rows}
        <p class="hint small">${esc(r.advice || "")}</p>`;
    } else if (r.matched === "ark_llm_pending") {
      window.__pendingArkKey = key; // 会话内暂存,保存成功即清
      box.innerHTML = `<div class="compliance pass">✅ 方舟 Key 有效!但账号下还没有可调用的对话模型。</div>${rows}
        <p class="hint small">${esc(r.advice || "")}</p>
        <div class="field-row">
          <label>模型接入点 ID(ep-开头)或模型 ID</label>
          <input id="ark-model-input" placeholder="ep-2025xxxx-xxxxx 或 doubao-seed-2-1-lite-260915">
        </div>
        <button class="cta" onclick="arkSave()">🚀 连接这个模型</button>`;
    } else if (r.matched) {
      box.innerHTML = `<div class="compliance pass">🎉 认出来了:<b>${esc(r.name)}</b>,已连接并保存——<b>可以生产了!</b></div>${rows}
        <p class="hint small">写作引擎已自动配好，「进阶设置」里可查看细节。</p>`;
    } else {
      box.innerHTML = `<div class="compliance warn">❌ 没有厂商认这把 Key</div>${rows}
        <p class="hint small">${esc(r.advice || "")}</p>`;
    }
    $("paste-key").value = "";
    await loadSettings();
  } catch (e) {
    box.innerHTML = `<div class="compliance warn">❌ ${esc(e.message)}</div>`;
  }
}

async function arkSave() {
  const model = $("ark-model-input").value.trim();
  if (!model) return toast("先填模型接入点 ID 或模型 ID");
  const box = $("keymatch-report");
  box.innerHTML = `<div class="state-msg working">正在验证模型 ${esc(model)}…</div>`;
  try {
    const r = await api("/api/keys/ark-save", {
      method: "POST", body: { key: window.__pendingArkKey, model } });
    window.__pendingArkKey = null;
    box.innerHTML = `<div class="compliance pass">🎉 ${esc(r.message)}(模型:${esc(r.model)})</div>
      <p class="hint small">写作引擎已配好——去「新建节目」直接生产吧。</p>`;
    await loadSettings();
  } catch (e) {
    box.innerHTML = `<div class="compliance warn">❌ ${esc(e.message)}</div>`;
  }
}

function applyPreset() {
  const p2 = (window.__llmPresets || {})[$("cfg-preset").value];
  if (!p2) return;
  $("cfg-provider").value = "openai";
  $("cfg-base-url").value = p2.base_url;
  $("cfg-model").value = p2.model;
  $("cfg-auth-style").value = p2.auth_style || "bearer";
  onProviderChange();
  toast(`已填充 ${p2.name},填入 API Key 后点「测试连接」`);
}

async function detectGateways() {
  const sel = $("cfg-gateway");
  sel.innerHTML = '<option value="">检测中…</option>';
  try {
    const data = await api("/api/gateways");
    const all = (data.gateways || []).flatMap((g) => g.presets.map((p2) => ({ ...p2, from: g.name })));
    if (!all.length) {
      sel.innerHTML = '<option value="">未检测到本地网关</option>';
      return;
    }
    window.__gatewayPresets = all;
    sel.innerHTML = '<option value="">检测到 ' + all.length + ' 个,选择导入…</option>' +
      all.map((p2, i) => `<option value="${i}">${esc(p2.from)}: ${esc(p2.name)}</option>`).join("");
  } catch (e) { sel.innerHTML = '<option value="">检测失败</option>'; toast(e.message); }
}

async function applyGateway() {
  const p2 = (window.__gatewayPresets || {})[$("cfg-gateway").value];
  if (!p2) return;
  try {
    // 明文 Key 只在服务端流转:导入由后端直接写配置,前端只拿脱敏回显
    const r = await api("/api/gateways/import", {
      method: "POST",
      body: { gateway: p2.gateway, name: p2.name },
    });
    $("cfg-provider").value = "openai";
    $("cfg-base-url").value = (r.llm && r.llm.openai && r.llm.openai.base_url) || "";
    $("cfg-model").value = (r.llm && r.llm.openai && r.llm.openai.model) || "";
    $("cfg-api-key").value = "****"; // 占位:测试连接会自动使用已保存的真实 Key
    $("cfg-auth-style").value = "bearer";
    onProviderChange();
    toast(`已从 ${p2.from} 导入「${p2.name}」并保存,点「测试连接」验证`);
  } catch (e) { toast(e.message); }
}

function onProviderChange() {
  const isOpenai = $("cfg-provider").value === "openai";
  $("cfg-openai-fields").style.display = isOpenai ? "" : "none";
  $("cfg-test-row").hidden = !isOpenai;
}

const LLM_TEST_HINTS = {
  config: "配置不完整：请检查 base_url / API Key / 模型名",
  network: "网络不通：检查接口地址、代理或本机网络",
  auth: "认证失败：API Key 无效或没有权限",
  http: "接口返回错误：确认 base_url 以 /v1 结尾、模型名正确",
  rate_limit: "触发限流：稍后再试",
};

async function testLlm() {
  const el = $("cfg-test-result");
  el.textContent = t("wizard.testing");
  try {
    const r = await api("/api/llm/test", {
      method: "POST",
      body: { llm: {
        provider: $("cfg-provider").value,
        openai: {
          base_url: $("cfg-base-url").value, model: $("cfg-model").value,
          api_key: $("cfg-api-key").value, auth_style: $("cfg-auth-style").value,
        },
      }},
    });
    if (r.mock) { el.textContent = "ℹ️ " + r.message; return; }
    if (r.ok) {
      el.textContent = `✅ 连接成功 · 模型 ${r.model} · 延迟 ${r.latency_ms}ms`;
    } else {
      el.textContent = `❌ ${LLM_TEST_HINTS[r.kind] || ""} — ${r.error}`;
    }
  } catch (e) { el.textContent = "❌ " + e.message; }
}

async function savePricing() {
  const msg = $("pricing-msg");
  try {
    await api("/api/config/pricing", {
      method: "POST",
      body: {
        currency: $("price-currency").value.trim() || "CNY",
        llm_per_million_in: parseFloat($("price-llm-in").value) || 0,
        llm_per_million_out: parseFloat($("price-llm-out").value) || 0,
        tts_per_kchar: parseFloat($("price-tts").value) || 0,
      },
    });
    msg.textContent = "✅ 已保存"; msg.hidden = false;
    setTimeout(() => (msg.hidden = true), 2000);
  } catch (e) { toast(e.message); }
}

async function saveBgmConfig() {
  const msg = $("bgm-msg");
  try {
    await api("/api/config/audio", {
      method: "POST",
      body: { bgm_path: $("bgm-path").value.trim(), bgm_volume: parseFloat($("bgm-vol").value) },
    });
    msg.textContent = "✅ 已保存"; msg.hidden = false;
    setTimeout(() => (msg.hidden = true), 2000);
  } catch (e) { toast(e.message); }
}

async function saveConfig() {
  const msg = $("cfg-msg");
  try {
    await api("/api/config", {
      method: "POST",
      body: {
        llm: {
          provider: $("cfg-provider").value,
          openai: {
            base_url: $("cfg-base-url").value,
            model: $("cfg-model").value,
            api_key: $("cfg-api-key").value, // "****" 会被后端忽略
            auth_style: $("cfg-auth-style").value,
            ssl_verify: $("cfg-ssl-verify").checked,
          },
        },
      },
    });
    msg.textContent = "✅ 已保存";
    msg.hidden = false;
    setTimeout(() => (msg.hidden = true), 2000);
  } catch (e) { toast(e.message); }
}

async function saveSearchConfig() {
  const msg = $("search-msg");
  try {
    const provider = $("search-provider").value;
    const key = $("search-key").value;
    const body = { search: { provider } };
    // key 为 "****" 占位时后端不会覆盖
    if (provider === "tavily") body.search.tavily = { api_key: key };
    if (provider === "bocha") body.search.bocha = { api_key: key };
    await api("/api/config", { method: "POST", body });
    msg.textContent = "✅ 搜索配置已保存";
    msg.hidden = false;
    setTimeout(() => (msg.hidden = true), 2000);
  } catch (e) { toast(e.message); }
}

async function saveSchedule() {
  const msg = $("sched-msg");
  try {
    await api("/api/config", {
      method: "POST",
      body: {
        schedule: {
          enabled: $("sched-enabled").checked,
          time: $("sched-time").value || "20:30",
          style: $("sched-style").value,
          minutes: parseInt($("sched-minutes").value, 10) || 5,
          voice_id: $("sched-voice").value,
          mode: "topic",
          topics: $("sched-topics").value.split("\n").map((t) => t.trim()).filter(Boolean),
        },
      },
    });
    msg.textContent = "✅ 定时设置已保存";
    msg.hidden = false;
    setTimeout(() => (msg.hidden = true), 2000);
  } catch (e) { toast(e.message); }
}

async function autoConnect() {
  const box = $("autoconnect-report");
  box.hidden = false;
  box.innerHTML = `<div class="state-msg working">正在逐一尝试本机所有可用连接(网关 / 预设 / 已存配置),最长约 90 秒…</div>`;
  try {
    const r = await api("/api/llm/autoconnect", { method: "POST" });
    const rows = (r.attempts || []).map((a) =>
      `<div class="clone-row"><span>${a.ok ? "✅" : "❌"} <b>${esc(a.name)}</b>${a.model ? ` <small>(${esc(a.model)})</small>` : ""}</span>
       <span class="hint small" style="flex:1">${esc(a.info || "")}${a.ms ? ` · ${a.ms}ms` : ""}</span></div>`).join("");
    if (r.fixed) {
      box.innerHTML = `<div class="compliance pass">🎉 已自动连上:<b>${esc(r.chosen.name)}</b>(${esc(r.chosen.model || "默认模型")})</div>${rows}
        <p class="hint small">配置已写入,现在可以直接「开始制作」出文稿了。</p>`;
      await loadSettings();
    } else {
      box.innerHTML = `<div class="compliance warn">⚠️ 没有能用的连接</div>${rows}
        <p class="hint small">${esc(r.advice || "")}</p>`;
    }
  } catch (e) {
    box.innerHTML = `<div class="compliance warn">❌ ${esc(e.message)}</div>`;
  }
}

async function saveTtsConfig() {
  const msg = $("tts-msg");
  try {
    await api("/api/config", {
      method: "POST",
      body: {
        tts: {
          volc: {
            app_id: $("tts-volc-appid").value,
            token: $("tts-volc-token").value, // "****" 占位不覆盖
            cluster: $("tts-volc-cluster").value,
          },
          ali: {
            api_key: $("tts-ali-key").value,
            model: $("tts-ali-model").value,
          },
          elevenlabs: {
            api_key: $("tts-el-key").value,
            model: $("tts-el-model").value,
          },
          azure: {
            api_key: $("tts-az-key").value,
            region: $("tts-az-region").value,
          },
          volc_podcast: {
            api_key: $("tts-vp-key").value, // "****" 占位不覆盖
            resource_id: $("tts-vp-resource").value,
          },
        },
        asr: {
          volc: {
            app_id: $("tts-asr-appid").value,
            token: $("tts-asr-token").value, // "****" 占位不覆盖
          },
        },
      },
    });
    msg.textContent = "✅ TTS 配置已保存";
    msg.hidden = false;
    setTimeout(() => (msg.hidden = true), 2000);
  } catch (e) { toast(e.message); }
}

// ---------- 浏览器录音 ----------
let recState = { recorder: null, stream: null, chunks: [], blob: null, timer: null, seconds: 0 };

function copyRecScript() {
  // 录音稿里的占位名替换为当前输入的声音名
  const name = ($("clone-name").value || "爸爸").trim();
  const el = document.getElementById("rec-script-box");
  const text = (el ? el.innerText : "").replace(/__/, name);
  navigator.clipboard.writeText(text).then(
    () => toast("✅ 录音稿已复制(名字已替换)"),
    () => toast("复制失败,请手动选择文本"));
}

async function toggleRecord() {
  const btn = $("rec-btn"), status = $("rec-status"), preview = $("rec-preview");
  if (recState.recorder && recState.recorder.state === "recording") {
    recState.recorder.stop();  // onstop 里收尾
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    status.textContent = "此环境不支持录音,请用方式二上传文件";
    return;
  }
  try {
    recState.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    status.textContent = "拿不到麦克风权限,请允许后重试,或用方式二上传";
    return;
  }
  recState.chunks = []; recState.blob = null; recState.seconds = 0;
  recState.recorder = new MediaRecorder(recState.stream);
  recState.recorder.ondataavailable = (e) => recState.chunks.push(e.data);
  recState.recorder.onstop = () => {
    clearInterval(recState.timer);
    recState.stream.getTracks().forEach((t) => t.stop());
    recState.blob = new Blob(recState.chunks, { type: recState.recorder.mimeType || "audio/webm" });
    preview.src = URL.createObjectURL(recState.blob);
    preview.style.display = "";
    btn.textContent = "🎤 重新录";
    status.textContent = `已录 ${recState.seconds}s ✓ 试听满意后点「上传并入库」`;
    recState.recorder = null;
  };
  recState.recorder.start();
  btn.textContent = t("btn.stop", "⏹️ 停止录音");
  status.textContent = "录音中 0s(建议 30-60 秒)";
  recState.timer = setInterval(() => {
    recState.seconds++;
    status.textContent = `录音中 ${recState.seconds}s(建议 30-60 秒)`;
    if (recState.seconds >= 90) recState.recorder.stop();  // 防失控
  }, 1000);
}

async function uploadClone() {
  const name = $("clone-name").value.trim();
  if (!name) return toast("请先填写声音名称");
  const fd = new FormData();
  fd.append("name", name);
  if (recState.blob) {
    fd.append("file", recState.blob, "recording.webm");
  } else {
    const file = $("clone-file").files[0];
    if (!file) return toast("请先录音或选择录音文件");
    fd.append("file", file);
  }
  try {
    const r = await api("/api/voices/clone", { method: "POST", body: fd });
    toast(r.warning ? "✅ 已入库。" + r.warning : "✅ 声音已入库");
    $("clone-name").value = ""; $("clone-file").value = "";
    recState.blob = null;
    $("rec-preview").style.display = "none";
    $("rec-btn").textContent = t("btn.record", "🎤 开始录音");
    await loadVoices();
    loadSettings();
  } catch (e) { toast(e.message); }
}

async function toggleArchive(id, currentlyArchived) {
  try {
    await api(`/api/episodes/${id}/archive`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ archived: !currentlyArchived }),
    });
    toast(currentlyArchived ? "✅ 已取消归档" : "🗃️ 已归档（节目没被删除）");
    openLibrary();
  } catch (e) { toast(e.message); }
}

async function deleteClone(id) {
  if (!confirm("删除这个克隆声音？")) return;
  try { await api(`/api/voices/clone/${id}`, { method: "DELETE" }); loadVoices(); loadSettings(); }
  catch (e) { toast(e.message); }
}

// ---------- 首次运行向导 ----------
let wizardStep = 1;

function showWizard() {
  wizardStep = 1;
  $("wizard").hidden = false;
  document.querySelectorAll(".view").forEach((v) => (v.hidden = true));
  renderWizard();
}

function renderWizard() {
  const body = $("wizard-body");
  const next = $("wizard-next");
  $("wizard-step-ind").textContent = t("wizard.stepnum").replace("{n}", wizardStep);
  next.hidden = false;
  if (wizardStep === 1) {
    body.innerHTML = `
      <h2>${esc(t("wizard.1"))}</h2>
      <p class="hint">${esc(t("wizard.1.hint"))}</p>
      <div class="style-cards" id="wizard-styles"></div>`;
    const box = body.querySelector("#wizard-styles");
    profiles.forEach((p) => {
      const div = document.createElement("div");
      div.className = "style-card" + (p.id === currentStyle ? " selected" : "");
      div.innerHTML = `<div class="t">${esc(p.emoji)} ${esc(p.name)}</div><div class="d">${esc(p.desc)}</div>`;
      const pick = () => {
        currentStyle = p.id;
        box.querySelectorAll(".style-card").forEach((c) => c.classList.remove("selected"));
        div.classList.add("selected");
      };
      div.onclick = pick;
      makeCard(div, pick);
      box.appendChild(div);
    });
    fillMinutes((profiles.find((p) => p.id === currentStyle) || profiles[0]).default_minutes);
  } else if (wizardStep === 2) {
    body.innerHTML = `
      <h2>${esc(t("wizard.2"))}</h2>
      <p class="hint">${esc(t("wizard.2.hint"))}</p>
      <div class="field-row">
        <label for="wz-provider">${esc(t("wizard.engine"))}</label>
        <select id="wz-provider" onchange="document.getElementById('wz-fields').style.display=this.value==='openai'?'':'none'">
          <option value="mock">${esc(t("wizard.engine.mock"))}</option>
          <option value="openai">${esc(t("wizard.engine.openai"))}</option>
        </select>
      </div>
      <div id="wz-fields" style="display:none">
        <div class="field-row two-col">
          <div><label>${esc(t("wizard.url"))}</label><input id="wz-url" placeholder="https://token-plan-cn.xiaomimimo.com/v1"></div>
          <div><label>${esc(t("wizard.model"))}</label><input id="wz-model" placeholder="mimo-v2.5-pro"></div>
        </div>
        <div class="field-row"><label>API Key</label><input id="wz-key" type="password" placeholder="sk-…"></div>
        <button class="ghost-btn" onclick="wizardTest()">${esc(t("btn.test"))}</button>
        <span id="wz-test-result" class="hint"></span>
      </div>`;
  } else {
    body.innerHTML = `
      <h2>${esc(t("wizard.3"))}</h2>
      <p class="hint">${esc(t("wizard.3.hint"))}</p>
      <div class="actions">
        <button class="ghost-btn" onclick="wizardFinish(); openSettings()">${esc(t("wizard.record"))}</button>
        <button class="ghost-btn" onclick="wizardFinish()">${esc(t("wizard.later"))}</button>
      </div>`;
    next.hidden = true;
  }
}

async function wizardTest() {
  const el = $("wz-test-result");
  el.textContent = t("wizard.testing");
  try {
    const r = await api("/api/llm/test", {
      method: "POST",
      body: { llm: {
        provider: $("wz-provider").value,
        openai: {
          base_url: $("wz-url").value, model: $("wz-model").value, api_key: $("wz-key").value,
        },
      }},
    });
    el.textContent = r.mock ? "ℹ️ " + r.message
      : r.ok ? `✅ 连接成功 · 模型 ${r.model} · ${r.latency_ms}ms`
      : `❌ ${r.error}`;
  } catch (e) { el.textContent = "❌ " + e.message; }
}

async function wizardNext() {
  if (wizardStep === 1) { wizardStep = 2; renderWizard(); return; }
  if (wizardStep === 2) {
    // 保存引擎配置(选演示模式则只落一个标志)
    const provider = $("wz-provider").value;
    const payload = { onboarded: true };
    if (provider === "openai") {
      payload.llm = { provider: "openai", openai: {
        base_url: $("wz-url").value, model: $("wz-model").value, api_key: $("wz-key").value,
      }};
    } else {
      payload.llm = { provider: "mock" };
    }
    try { await api("/api/config", { method: "POST", body: payload }); }
    catch (e) { toast(e.message); return; }
    wizardStep = 3; renderWizard(); return;
  }
}

async function wizardSkip() {
  try { await api("/api/config", { method: "POST", body: { onboarded: true } }); } catch {}
  closeWizard();
}

function wizardFinish() { closeWizard(); }

function closeWizard() {
  $("wizard").hidden = true;
  loadVoices().then(() => showView("new"));
}

// ---------- 主题恢复(尽早执行,避免闪主题) ----------
(function initTheme() {
  try {
    const saved = localStorage.getItem("ps-theme");
    if (saved) document.documentElement.setAttribute("data-theme", saved);
    const btn = document.getElementById("theme-toggle");
    const dark = document.documentElement.getAttribute("data-theme") === "dark"
      || (!saved && matchMedia("(prefers-color-scheme: dark)").matches);
    if (btn) btn.textContent = dark ? "🌙" : "☀️";
  } catch (e) {}
})();

// ---------- 启动 ----------
(async function init() {
  try {
    const health = await api("/api/health");
    // 自愈:页面构建号与服务端版本不一致 → 说明缓存了旧前端,硬刷新一次
    if (window.__BUILD__ && health.version && !health.version.startsWith(window.__BUILD__.split(".").slice(0, 2).join("."))) {
      // 版本错配最多硬刷一次;刷完仍不一致就放弃自愈继续加载,避免刷新死循环
      if (!sessionStorage.getItem("ps-reloaded")) {
        sessionStorage.setItem("ps-reloaded", "1");
        location.reload();
        return;
      }
      sessionStorage.removeItem("ps-reloaded");
    } else if (sessionStorage.getItem("ps-reloaded")) {
      sessionStorage.removeItem("ps-reloaded");
    }
    $("ver").textContent = health.version;
    await loadProfiles();
    await loadVoices();
    setMode("topic");
    // 形式卡片(静态 HTML)补键盘可达
    document.querySelectorAll("[data-format]").forEach((c) => makeCard(c, () => setFormat(c.dataset.format)));
    applyI18n();
    // URL 带 #ep-id 时直接打开节目
    if (location.hash.startsWith("#ep-")) openEpisode(location.hash.slice(4));
    if (!health.onboarded) showWizard();
    // 有进行中的节目 → 在新建页显示"继续创作"入口
    api("/api/episodes").then((eps) => {
      const active = eps.find((e) => !["done", "error"].includes(e.state));
      if (active && !location.hash.startsWith("#ep-")) {
        lastActiveEpisode = active.id;
        renderContinueBanner();
      }
    }).catch(() => {});
  } catch (e) {
    document.querySelector("main").innerHTML =
      `<div class="card"><h2>😵 服务没连上</h2><p class="hint">${esc(e.message)}<br>请在终端运行 <code>podcast-studio serve</code> 后刷新本页。</p></div>`;
  }
})();

// ---------- P1-4:点击 ⋯ 菜单外部时收起所有行内菜单 ----------
document.addEventListener("click", (ev) => {
  document.querySelectorAll(".row-menu[open]").forEach((d) => {
    if (!d.contains(ev.target)) d.open = false;
  });
});
