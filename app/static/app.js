/* ReelVault SPA */
"use strict";

const TOKEN = (document.cookie.match(/rv_token=([^;]+)/) || [])[1] ||
  localStorage.getItem("rv_token") || "";
if (!TOKEN) {
  // bootstrap: server sets the cookie on first visit
  fetch("/api/bootstrap").then(r => r.json()).then(d => {
    localStorage.setItem("rv_token", d.token);
    document.cookie = `rv_token=${d.token}; SameSite=Lax`;
    location.reload();
  });
}

const state = { view: "inbox", reels: [], counts: {}, health: null };
const $ = sel => document.querySelector(sel);
const $$ = sel => [...document.querySelectorAll(sel)];
const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtPct = x => Math.round((x ?? 0) * 100);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { "Content-Type": "application/json",
               "Authorization": `Bearer ${TOKEN}`, ...(opts.headers || {}) },
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return res.json();
}
async function apiForm(path, form) {
  const res = await fetch(path, { method: "POST", body: form,
    headers: { "Authorization": `Bearer ${TOKEN}` } });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Upload failed");
  return res.json();
}

function toast(msg, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $("#toast-wrap").append(el);
  setTimeout(() => el.remove(), 4200);
}

/* ------------------------------------------------------------- views */
const VIEWS = {
  inbox:   { title: "Inbox", render: () => renderList({ status: "completed" }) },
  radar:   { title: "Opportunity Radar", render: renderRadar },
  jobs:    { title: "Jobs & Internships", render: () => renderCategory(["Job", "Internship"]) },
  learning:{ title: "Learning", render: () => renderCategory(["Tutorial", "Educational", "AI/ML", "Data Science", "Data Analytics", "Career"]) },
  tools:   { title: "Tools", render: () => renderCategory(["Tool", "Product"]) },
  saved:   { title: "Saved", render: () => renderStarred() },
  processing: { title: "Processing & Failed", render: renderProcessing },
  search:  { title: "Search", render: renderSearch },
  assistant: { title: "Assistant", render: renderAssistant },
  settings:{ title: "Settings", render: renderSettings },
};

function nav(view) {
  state.view = view;
  $$(".nav-btn").forEach(b => b.classList.toggle("active", b.dataset.view === view));
  $("#view-title").textContent = VIEWS[view].title;
  $("#view").innerHTML = `<div class="skeleton" style="height:120px"></div>`;
  VIEWS[view].render().catch(e => {
    $("#view").innerHTML = `<div class="empty"><div class="big">⚠️</div><h3>Couldn't load ${esc(view)}</h3><p>${esc(e.message)}</p></div>`;
  });
}

$$(".nav-btn").forEach(b => b.onclick = () => nav(b.dataset.view));

/* ------------------------------------------------------------ cards */
function cardHTML(r) {
  const cats = r.categories || [];
  const chips = cats.slice(0, 3).map(c => `<span class="chip cat">${esc(c)}</span>`).join("");
  const pri = r.priority >= 30 ? `<span class="chip pri">⏰ urgent</span>` : "";
  const thumb = r.thumb_path ? `/media/thumb/${r.id}` : "";
  return `
  <article class="card" data-id="${r.id}">
    <div class="row">
      <span class="status-pill st-${esc(r.status)}">${esc(r.status)}</span>
      <span style="color:var(--muted);font-size:12px">${esc((r.ingested_at || "").slice(0, 16))}</span>
    </div>
    ${thumb ? `<img class="thumb" loading="lazy" src="${thumb}" alt="">` : ""}
    <h3>${esc(r.title || "(untitled reel)")}</h3>
    <p>${esc(r.summary || "")}</p>
    <div class="chips">${chips}${pri}</div>
    <div class="conf"><span>${fmtPct(r.confidence)}%</span>
      <span class="meter"><i style="width:${fmtPct(r.confidence)}%"></i></span></div>
  </article>`;
}

function bindCards() {
  $$(".card").forEach(c => c.onclick = () => openReel(+c.dataset.id));
}

async function renderList(params) {
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v != null));
  const d = await api(`/api/reels?${qs}`);
  state.reels = d.reels; state.counts = d.counts;
  updateBadges();
  const v = $("#view");
  if (!d.reels.length) {
    v.innerHTML = emptyState();
    return;
  }
  v.innerHTML = `<div class="grid">${d.reels.map(cardHTML).join("")}</div>`;
  bindCards();
}

function emptyState() {
  return `<div class="empty">
    <div class="big">🎬</div>
    <h3>No reels here yet</h3>
    <p>Paste an Instagram Reel URL in the box above and press <b>Add Reel</b>.<br>
    On your phone, use your OS share sheet → <b>ReelVault</b> — it lands here automatically.</p>
    <p style="margin-top:14px"><code>Add Reel → transcribe → OCR → extract → searchable knowledge</code></p>
  </div>`;
}

async function renderRadar() {
  const [d, dash] = await Promise.all([api("/api/radar"), api("/api/dashboard")]);
  const items = d.radar.filter(r => r.status === "completed" || r.priority >= 30);
  const dls = dash.upcoming_deadlines || [];
  const dlHTML = dls.length ? `<div class="stats" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr))">
      ${dls.map(x => `<div class="stat"><b style="font-size:17px">${x.days_left}d</b>
        <span>⏰ ${esc((x.title || "(untitled)").slice(0, 34))}<br>deadline ${esc((x.deadline_iso || "").slice(0, 10))}</span></div>`).join("")}
    </div>` : "";
  if (!items.length && !dlHTML) {
    $("#view").innerHTML = `<div class="empty"><div class="big">⏰</div><h3>Radar is quiet</h3>
      <p>Job posts, internships and deadline-bearing reels get ranked here automatically.</p></div>`;
    return;
  }
  $("#view").innerHTML = `${dlHTML}<div class="stats">
     <div class="stat"><b>${items.length}</b><span>actionable items</span></div>
     <div class="stat"><b>${items.filter(r => r.priority >= 50).length}</b><span>high urgency</span></div>
   </div>
   <div class="grid">${items.map(cardHTML).join("")}</div>`;
  bindCards();
}

async function renderCategory(cats) {
  await renderList({});
  const filtered = state.reels.filter(r =>
    (r.categories || []).some(c => cats.includes(c)));
  $("#view").innerHTML = filtered.length
    ? `<div class="grid">${filtered.map(cardHTML).join("")}</div>`
    : `<div class="empty"><div class="big">🗂️</div><h3>Nothing in this category yet</h3></div>`;
  bindCards();
}

async function renderStarred() {
  const d = await api(`/api/reels?starred=true`);
  $("#view").innerHTML = d.reels.length
    ? `<div class="grid">${d.reels.map(cardHTML).join("")}</div>`
    : `<div class="empty"><div class="big">⭐</div><h3>No starred reels</h3><p>Star a reel from its detail view.</p></div>`;
  bindCards();
}

async function renderProcessing() {
  const d = await api(`/api/reels?limit=200`);
  const active = d.reels.filter(r => ["queued", "processing"].includes(r.status));
  const failed = d.reels.filter(r => r.status === "failed" || r.status === "duplicate");
  updateBadges();
  const adminD = await api("/api/admin/jobs");
  $("#view").innerHTML = `
    <div class="stats">
      <div class="stat"><b>${d.counts.queued || 0 + d.counts.processing || 0}</b><span>in pipeline</span></div>
      <div class="stat"><b>${d.counts.completed || 0}</b><span>completed</span></div>
      <div class="stat"><b>${failed.length}</b><span>failed / merged</span></div>
      <div class="stat"><b>${adminD.dead_jobs}</b><span>dead jobs</span></div>
    </div>
    ${(active.length || failed.length) ? `
    <table class="admin">
      <thead><tr><th>Reel</th><th>Status</th><th>Stage</th><th>Progress</th><th></th></tr></thead>
      <tbody>
        ${[...active, ...failed].map(r => `
          <tr data-id="${r.id}">
            <td>${esc(r.title || `(reel #${r.id})`)}
                ${r.error_code ? `<div class="err">${esc(r.error_code)}</div>` : ""}</td>
            <td><span class="status-pill st-${esc(r.status)}">${esc(r.status)}</span></td>
            <td>${esc(r.current_stage || "")}</td>
            <td style="min-width:130px"><div class="progressbar"><i style="width:${fmtPct(r.progress)}%"></i></div></td>
            <td>${["failed", "duplicate"].includes(r.status)
                  ? `<button class="btn ghost" onclick="retryReel(${r.id}, event)">Retry</button>` : ""}</td>
          </tr>`).join("")}
      </tbody>
    </table>` : `<div class="empty"><div class="big">✅</div><h3>All clear</h3><p>Nothing is stuck or failing.</p></div>`}
    <h4 style="color:var(--muted);margin:26px 0 10px">Recent AI runs</h4>
    <table class="admin">
      <thead><tr><th>Task</th><th>Backend</th><th>Latency</th><th>Tokens in/out</th><th>OK</th></tr></thead>
      <tbody>${adminD.ai_runs.slice(0, 12).map(x => `
        <tr><td>${esc(x.task)}</td><td>${esc(x.model)}</td>
        <td>${x.latency_ms ?? "?"} ms</td><td>${x.tokens_in ?? "-"} / ${x.tokens_out ?? "-"}</td>
        <td>${x.ok ? "✔️" : `<span class="err">${esc((x.error || "").slice(0, 60))}</span>`}</td></tr>`).join("")}
      </tbody>
    </table>`;
}

window.retryReel = async (id, e) => {
  e.stopPropagation();
  try { await api(`/api/admin/retry/${id}`, { method: "POST" }); toast("Retrying…", "ok"); }
  catch (err) { toast(err.message, "error"); }
};

/* ----------------------------------------------------------- search */
async function renderSearch() {
  $("#view").innerHTML = `
    <form id="search-form" style="display:flex;gap:10px;margin-bottom:18px">
      <input id="search-q" style="flex:1" placeholder='Try: "remote data analyst jobs" · "SQL reels" · "AI tools for agents"' autofocus>
      <select id="search-cat"><option value="">Any category</option>
        ${["Job","Internship","Career","Tutorial","Educational","AI/ML","Tool"].map(c => `<option>${c}</option>`).join("")}
      </select>
      <button class="btn primary">Search</button>
    </form>
    <div id="search-results"><div class="empty"><div class="big">🔍</div><h3>Hybrid search</h3>
      <p>Keyword (FTS5) + semantic (local embeddings), blended. Every processed reel is searchable.</p></div></div>`;
  $("#search-form").onsubmit = async e => {
    e.preventDefault();
    const q = $("#search-q").value.trim(); if (!q) return;
    $("#search-results").innerHTML = `<div class="skeleton" style="height:90px"></div>`;
    try {
      const d = await api("/api/search", { method: "POST", body: JSON.stringify({
        query: q, category: $("#search-cat").value || null }) });
      $("#search-results").innerHTML = d.results.length
        ? `<div class="grid">${d.results.map(cardHTML).join("")}</div>`
        : `<div class="empty"><div class="big">🫥</div><h3>No matches</h3><p>Try different words — semantic search understands meaning, not just keywords.</p></div>`;
      bindCards();
    } catch (err) { $("#search-results").innerHTML = `<div class="empty">⚠️ ${esc(err.message)}</div>`; }
  };
}

/* -------------------------------------------------------- assistant */
const SUGGESTIONS = [
  "What jobs have I saved this week?",
  "Which saved jobs are fresher-friendly?",
  "What skills appear most often in my job reels?",
  "Build me a learning roadmap from my educational reels.",
];
async function renderAssistant() {
  $("#view").innerHTML = `
    <div class="chat">
      <div class="chat-log" id="chat-log">
        <div class="msg bot">👋 Ask anything about your saved reels. I answer only from
        your own knowledge base and cite the reels I used.</div>
      </div>
      <div class="sugg">${SUGGESTIONS.map(s => `<button>${esc(s)}</button>`).join("")}</div>
      <form class="chat-input" id="ask-form">
        <input id="ask-q" placeholder="Ask your reel brain…" autocomplete="off">
        <button class="btn primary">Ask</button>
      </form>
    </div>`;
  $$(".sugg button").forEach(b => b.onclick = () => askQuestion(b.textContent));
  $("#ask-form").onsubmit = e => { e.preventDefault(); askQuestion($("#ask-q").value); };

  async function askQuestion(q) {
    q = q.trim(); if (!q) return;
    $("#ask-q").value = "";
    const log = $("#chat-log");
    log.insertAdjacentHTML("beforeend",
      `<div class="msg user">${esc(q)}</div>
       <div class="msg bot" id="thinking">…reading your reels</div>`);
    log.scrollTop = log.scrollHeight;
    try {
      const d = await api("/api/assistant/ask", { method: "POST",
        body: JSON.stringify({ question: q }) });
      $("#thinking").outerHTML = assistantAnswerHTML(d);
      $$(".cite-chip[data-open]").forEach(c =>
        c.onclick = () => openReel(+c.dataset.open));
    } catch (err) {
      $("#thinking").outerHTML = `<div class="msg bot">⚠️ ${esc(err.message)}</div>`;
    }
    log.scrollTop = log.scrollHeight;
  }
}

function assistantAnswerHTML(d) {
  const citedIds = new Set(d.citations.map(c => c.reel_id));
  let html = esc(d.answer)
    .replace(/\[R#(\d+)\]/g, (m, id) => citedIds.has(+id) ? `<b>[R#${id}]</b>` : "")
    .replace(/\n/g, "<br>");
  const cites = d.citations.map(c =>
    `<span class="cite-chip" data-open="${c.reel_id}">🎬 R#${c.reel_id} · ${esc((c.title || "").slice(0, 34))}</span>`).join("");
  const conf = `<div class="conf" style="margin-top:8px"><span>${fmtPct(d.confidence)}% grounded</span>
    <span class="meter"><i style="width:${fmtPct(d.confidence)}%"></i></span></div>`;
  return `<div class="msg bot">${html}<div class="cites">${cites}</div>${conf}</div>`;
}

/* --------------------------------------------------------- settings */
async function renderSettings() {
  const [ready, ingestDocs] = await Promise.all([
    api("/readyz"), api("/healthz"),
  ]);
  const token = TOKEN || "(not set)";
  $("#view").innerHTML = `
    <div class="stats">
      <div class="stat"><b style="font-size:15px;font-family:monospace">${ready.checks.llm ? "🟢 online" : "🔴 offline"}</b><span>SLM (llama-server)</span></div>
      <div class="stat"><b style="font-size:15px">${ready.checks.embedder ? "🟢 ready" : "🔴 down"}</b><span>Embeddings</span></div>
      <div class="stat"><b style="font-size:15px">${ready.checks.db ? "🟢 ok" : "🔴 locked"}</b><span>Database</span></div>
      <div class="stat"><b style="font-size:15px">v${esc(ingestDocs.version)}</b><span>ReelVault</span></div>
    </div>
    <div class="sec" style="max-width:720px;display:flex;flex-direction:column;gap:18px">
      <div>
        <h4>Ingestion methods (all compliant)</h4>
        <ul style="line-height:1.9;color:var(--muted)">
          <li>🔗 <b>Paste URL</b> — top bar, any Instagram reel/post link</li>
          <li>📲 <b>Phone share sheet</b> — install this site as an app (menu → Add to Home screen), then share any Reel → ReelVault</li>
          <li>📁 <b>Watch folder</b> — drop videos into <code>D:\\reelvault\\watch</code>, press Scan below</li>
          <li>⬆️ <b>File upload</b> — ＋ File button in the top bar</li>
        </ul>
        <button class="btn" onclick="scanWatch()">Scan watch folder now</button>
      </div>
      <div>
        <h4>Privacy</h4>
        <p style="color:var(--muted)">Everything runs on this machine — SLM, Whisper, OCR, embeddings.
        Nothing leaves localhost. Delete a reel to purge media, transcript and facts permanently.</p>
      </div>
      <div>
        <h4>API token</h4>
        <code style="user-select:all">${esc(token)}</code>
        <p style="color:var(--muted);font-size:12.5px">Used by the PWA share target & scripts. Treat it like a password.</p>
      </div>
    </div>`;
}
window.scanWatch = async () => {
  try {
    const d = await api("/api/watch-folder/scan", { method: "POST" });
    toast(`Watch folder: added ${d.added.length} reel(s)`, "ok");
  } catch (e) { toast(e.message, "error"); }
};

/* ------------------------------------------------------ reel detail */
let currentReelId = null;
async function openReel(id) {
  currentReelId = id;
  const dlg = $("#reel-dialog");
  $("#dlg-title").textContent = "Loading…";
  $("#dlg-body").innerHTML = `<div class="skeleton" style="height:200px"></div>`;
  dlg.showModal();
  try {
    const r = await api(`/api/reels/${id}`);
    $("#dlg-title").textContent = r.title || `(untitled reel)`;
    $("#dlg-star").textContent = r.starred ? "★ Starred" : "☆ Star";
    $("#dlg-delete").style.display = "";
    $("#dlg-body").innerHTML = detailHTML(r);
    wireDetail(r);
  } catch (e) {
    $("#dlg-body").innerHTML = `<div class="empty">⚠️ ${esc(e.message)}</div>`;
  }
}
window.openReel = openReel;

function factRow(f) {
  const ts = f.evidence_t_s != null ? fmtTs(f.evidence_t_s) : null;
  const srcIcon = { transcript: "🗣️", ocr: "🖥️", caption: "📝", metadata: "⚙️", vision: "👁️" }[f.evidence_source] || "•";
  return `<tr>
    <td class="fact-field">${esc(f.field.replace(/_/g, " "))}</td>
    <td class="fact-val">${esc(f.value)}
      ${f.evidence_quote ? `<span class="evidence">
          <span class="ev-ts">${srcIcon}${ts ? ts : ""}</span>“${esc(f.evidence_quote.slice(0, 160))}”</span>` : ""}
      <span class="fact-actions">
        <button onclick="editFact(${f.id}, '${esc(f.field)}')">Edit</button>
        <button onclick="markFact(${f.id}, true)">Wrong</button>
        <small style="color:var(--muted)">${fmtPct(f.confidence)}%</small>
      </span>
    </td></tr>`;
}

function detailHTML(r) {
  const factsByType = {};
  (r.facts || []).forEach(f => (factsByType[f.schema_type] ||= []).push(f));
  const transcript = (r.transcript || []).map(s =>
    `<div class="transcript-line"><time data-t="${s.start_s}">${fmtTs(s.start_s)}</time><span>${esc(s.text)}</span></div>`).join("");
  const ocr = (r.ocr || []).map(o =>
    `<div class="ocr-box"><time>${fmtTs(o.t_s)}</time> — ${esc(o.text)}</div>`).join("");
  const entities = (r.entities || []).map(e =>
    `<span class="chip">${esc(e.kind)}: ${esc(e.name)}</span>`).join("");
  const sourceUrl = r.source_url
    ? `<a href="${esc(r.source_url)}" target="_blank" rel="noopener noreferrer">Open original ↗</a>` : "";
  const media = r.media_path ? `<video controls preload="none" style="width:100%;border-radius:12px" src="/media/video/${r.id}"></video>` : "";

  return `
  <div class="chips">${(r.categories || []).map(c => `<span class="chip cat">${esc(c)}</span>`).join("")}
    <span class="status-pill st-${esc(r.status)}">${esc(r.status)}</span></div>
  ${media || ""}
  <div class="conf" style="max-width:280px"><span>${fmtPct(r.confidence)}% confidence</span>
    <span class="meter"><i style="width:${fmtPct(r.confidence)}%"></i></span></div>

  <div class="sec"><h4>AI Summary</h4><p style="margin:0">${esc(r.summary || "—")}</p></div>

  ${(r.key_takeaways || []).length ? `<div class="sec"><h4>Key Takeaways</h4>
    <ul class="takeaways">${r.key_takeaways.map(t => `<li>${esc(t)}</li>`).join("")}</ul></div>` : ""}

  ${Object.entries(factsByType).length ? Object.entries(factsByType).map(([type, fs]) => `
    <div class="sec"><h4>Extracted Knowledge — ${esc(type)}</h4>
      <table class="fact-table">${fs.map(factRow).join("")}</table></div>`).join("") : ""}

  ${(r.action_items || []).length ? `<div class="sec"><h4>Action Items</h4>
    <ul class="actions">${r.action_items.map(a => `<li>${esc(a)}</li>`).join("")}</ul>
    ${sourceUrl ? `<p>${sourceUrl}</p>` : ""}</div>` : (sourceUrl ? `<p>${sourceUrl}</p>` : "")}

  ${entities ? `<div class="sec"><h4>Entities</h4><div class="chips">${entities}</div></div>` : ""}
  ${ocr ? `<div class="sec"><h4>On-Screen Text (OCR)</h4>${ocr}</div>` : ""}
  ${transcript ? `<div class="sec"><h4>Transcript</h4>${transcript}</div>` : ""}

  <details class="sec"><h4>Processing trace</h4>
    ${(r.events || []).map(e2 => `<div class="transcript-line"><time>${esc(String(e2.ts).slice(11, 19))}</time>
      <span>[${esc(e2.stage)}] ${esc(e2.message)}</span></div>`).join("")}</details>`;
}

function fmtTs(t) {
  const m = Math.floor(t / 60), s = Math.round(t % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function wireDetail(r) {
  $("#dlg-close").onclick = () => $("#reel-dialog").close();
  $("#dlg-star").onclick = async () => {
    await api(`/api/reels/${r.id}`, { method: "PATCH",
      body: JSON.stringify({ starred: !r.starred }) });
    openReel(r.id); nav(state.view);
  };
  $("#dlg-delete").onclick = async () => {
    if (!confirm("Delete this reel and all extracted knowledge permanently?")) return;
    await api(`/api/reels/${r.id}?purge_media=true`, { method: "DELETE" });
    $("#reel-dialog").close(); nav(state.view); toast("Reel deleted", "ok");
  };
  $$("#dlg-body time[data-t]").forEach(t =>
    t.onclick = () => { /* seek main video */ const v = $("#dlg-body video"); if (v) { v.currentTime = +t.dataset.t; v.play(); } });
}
window.editFact = async (id) => {
  const val = prompt("Correct this value:");
  if (val == null) return;
  await api(`/api/facts/${id}`, { method: "PATCH", body: JSON.stringify({ value: val }) });
  toast("Correction saved — thanks!", "ok"); openReel(currentReelId);
};
window.markFact = async (id, wrong) => {
  if (!wrong) return;
  await api(`/api/facts/${id}`, { method: "PATCH", body: JSON.stringify({ mark_incorrect: true }) });
  toast("Marked incorrect", "ok"); openReel(currentReelId);
};

/* ---------------------------------------------------------- top bar */
$("#quick-add").onsubmit = async e => {
  e.preventDefault();
  const url = $("#quick-url").value.trim();
  try {
    const d = await api("/api/reels", { method: "POST",
      body: JSON.stringify({ url }) });
    if (d.duplicate) toast(`Already in your vault (reel #${d.reel_id})`, "");
    else { toast("Added — pipeline started ⚙️", "ok"); pollProcessing(); }
    $("#quick-url").value = ""; nav("inbox");
  } catch (err) { toast(err.message, "error"); }
};
$("#file-input").onchange = async e => {
  const f = e.target.files[0]; if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  toast("Uploading…");
  try { const d = await apiForm("/api/reels/upload", fd);
    toast(`Uploaded as reel #${d.reel_id}`, "ok"); nav("processing"); }
  catch (err) { toast(err.message, "error"); }
  e.target.value = "";
};

/* ------------------------------------------------------- live bits */
function updateBadges() {
  const inbox = state.counts.completed || 0;
  setBadge("#badge-inbox", inbox > 99 ? "99+" : inbox);
}
function setBadge(sel, n) {
  const el = $(sel);
  if (!el) return;
  el.textContent = n; el.classList.toggle("hidden", !n);
}
let procTimer = null;
function pollProcessing() {
  clearInterval(procTimer);
  procTimer = setInterval(async () => {
    try {
      const d = await api("/api/reels?limit=50");
      state.counts = d.counts;
      const busy = (d.counts.queued || 0) + (d.counts.processing || 0);
      setBadge("#badge-proc", busy);
      if (busy === 0) { clearInterval(procTimer); nav(state.view); toast("All reels processed ✅", "ok"); }
      else if (state.view === "processing") renderProcessing();
    } catch {}
  }, 2500);
}
async function checkHealth() {
  try {
    const r = await fetch("/readyz"); const d = await r.json();
    const pill = $("#health-pill");
    const allOk = d.all_ready;
    pill.className = `pill ${allOk ? "ok" : (d.checks.db ? "warn" : "bad")}`;
    pill.innerHTML = `<span class="dot"></span>${
      allOk ? "AI stack ready" :
      !d.checks.llm ? "SLM offline" : "warming up…"}`;
  } catch { $("#health-pill").className = "pill bad";
    $("#health-pill").innerHTML = `<span class="dot"></span>offline`; }
}

nav("inbox");
checkHealth(); setInterval(checkHealth, 15000);

/* ===================== mobile experience layer ===================== */
const IS_TOUCH = matchMedia("(hover: none)").matches;

// bottom tab bar -> existing views
$$(".tab-btn").forEach(b => b.addEventListener("click", () => {
  $$(".tab-btn").forEach(x => x.classList.toggle("active", x === b));
  nav(b.dataset.view);
}));
// keep tab bar in sync with sidebar nav too
const _origNav = nav;
nav = function (view) { _origNav(view);
  $$(".tab-btn").forEach(x => x.classList.toggle("active", x.dataset.view === view)); };

/* ---------- share-target landing: Instagram → Share → ReelVault ----- */
async function showShareLanding(sharedUrl) {
  let res, ok = false;
  try {
    res = await api("/api/reels", { method: "POST",
      body: JSON.stringify({ url: sharedUrl }) });
    ok = true;
  } catch (e) {
    $("#view").innerHTML = `<div class="share-sheet bad">
      <div class="ring">⚠️</div><h2>Couldn't save</h2>
      <p>${esc(e.message)}</p>
      <p style="margin-top:16px"><button class="btn" onclick="location.href='/'">Back to app</button></p></div>`;
    return;
  }
  const dup = res.duplicate;
  $("#view").innerHTML = `<div class="share-sheet ${ok ? "ok" : ""}">
    <div class="ring">${dup ? "✓" : "🎬"}</div>
    <h2>${dup ? "Already saved" : "Saved!"}</h2>
    <p>${esc(sharedUrl)}</p>
    <p id="share-status">${dup ? "It's already in your library." : "Processing started — you can close this."}</p>
    ${dup ? "" : `<div style="margin-top:18px"><button class="btn primary" onclick="watchShared(${res.reel_id})">Watch it process</button>
    <button class="btn ghost" onclick="location.href='/'">Go to library</button></div>`}
  </div>`;
}
window.watchShared = async function (id) {
  $("#view").innerHTML = `<div id="pt-wrap" style="max-width:430px;margin:0 auto"></div>`;
  pollTimeline(id);
};
function timelineHTML(r) {
  const steps = [
    ["ingest", r.current_stage !== "ingest" || r.progress > .1 ? "done" : "active", "Downloading"],
    ["media", r.progress >= .15 ? (r.progress > .15 ? "done" : "active") : "", "Preparing media"],
    ["transcribe", r.progress >= .35 ? (r.progress > .5 ? "done" : "active") : "", "Transcribing speech"],
    ["ocr", r.progress >= .55 ? (r.progress > .65 ? "done" : "active") : "", "Reading on-screen text"],
    ["classify_extract", r.progress >= .65 ? (r.progress > .85 ? "done" : "active") : "", "Understanding & extracting"],
    ["embed", r.progress >= .85 ? (r.progress >= 1 ? "done" : "active") : "", "Building knowledge"],
    ["finalize", r.status === "completed" ? "done" :
                 (r.status === "duplicate" ? "done" :
                 (r.progress >= .95 ? "active" : "")), "Ready"],
  ];
  if (r.status === "failed") steps.push(["x", "error", r.error_message || "Processing failed"]);
  return `<h2 style="font-size:18px">${esc(r.title || "(untitled reel)")}</h2>
    <ul class="ptimeline">${steps.map(s =>
      `<li class="${s[1]}">${s[2]}</li>`).join("")}</ul>
    ${r.status === "completed"
      ? `<button class="btn primary" style="width:100%;margin-top:10px" onclick="openReel(${r.id}); $('#reel-dialog').showModal()">Open knowledge page</button>`
      : `<p style="color:var(--muted);font-size:12.5px;margin-top:8px">This runs on your PC. You can leave — results appear in your library.</p>`}`;
}
let ptTimer = null;
function pollTimeline(id) {
  clearInterval(ptTimer);
  const tick = async () => {
    try {
      const r = await api(`/api/reels/${id}`);
      const w = $("#pt-wrap");
      if (!w) { clearInterval(ptTimer); return; }
      w.innerHTML = timelineHTML(r);
      if (["completed", "failed", "duplicate"].includes(r.status)) clearInterval(ptTimer);
    } catch { /* network hiccup: keep polling */ }
  };
  tick(); ptTimer = setInterval(tick, 2000);
}

/* ---------- pull to refresh (mobile) ---------- */
(function () {
  let startY = null, pulled = false;
  const ptr = $("#ptr");
  document.addEventListener("touchstart", e => {
    if (window.scrollY <= 0 && $("#view").scrollTop <= 0)
      startY = e.touches[0].clientY; else startY = null;
  }, { passive: true });
  document.addEventListener("touchmove", e => {
    if (startY == null) return;
    const dy = e.touches[0].clientY - startY;
    if (dy > 70 && !pulled) { pulled = true; ptr.classList.add("show"); }
  }, { passive: true });
  document.addEventListener("touchend", () => {
    if (pulled) { nav(state.view); setTimeout(() => ptr.classList.remove("show"), 600); }
    startY = null; pulled = false;
  }, { passive: true });
})();

/* ---------- deep links from the OS share sheet ------------------------
   Server ingests immediately, then redirects to:
     /?shr=ok&rid=N#shared=1   newly saved
     /?shr=dup&rid=N#shared=1  duplicate
     /?shr=err&msg=...         bad link                                          */
window.addEventListener("hashchange", routeShare);
function routeShare() {
  if (!location.hash.includes("shared")) return;
  history.replaceState(null, "", "/");
  const qs = new URLSearchParams(location.search);
  const shr = qs.get("shr");
  const rid = qs.get("rid");
  if (shr === "ok" && rid) {
    $("#view").innerHTML = `<div class="share-sheet ok">
      <div class="ring">🎬</div><h2>Saved!</h2>
      <p>Processing started — you can close this and return to Instagram.</p>
      <div style="margin-top:18px"><button class="btn primary" onclick="watchShared(${rid})">Watch it process</button>
      <button class="btn ghost" onclick="location.href='/'">Go to library</button></div></div>`;
  } else if (shr === "dup" && rid) {
    $("#view").innerHTML = `<div class="share-sheet ok">
      <div class="ring">✓</div><h2>Already saved</h2>
      <p>It's in your library.</p>
      <div style="margin-top:18px"><button class="btn primary" onclick="openReel(${rid});$('#reel-dialog').showModal()">Open it</button></div></div>`;
  } else {
    const msg = qs.get("msg") || "That link didn't look like an Instagram Reel.";
    $("#view").innerHTML = `<div class="share-sheet bad">
      <div class="ring">⚠️</div><h2>Couldn't save</h2><p>${esc(msg)}</p>
      <p style="margin-top:16px"><button class="btn" onclick="location.href='/'">Back to app</button></p></div>`;
  }
}
routeShare();

/* ---------- notifications (opt-in only) ---------- */
document.addEventListener("click", async function once() {
  document.removeEventListener("click", once);
  if (!("Notification" in window) || Notification.permission !== "default") return;
  try { const p = await Notification.requestPermission();
    if (p === "granted") toast("Reminders enabled 🔔", "ok"); } catch {}
}, { once: true });
