"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const PREFS_KEY = "internmatch:prefs";
const APPLIED_KEY = "internmatch:applied";
const PAGE = 40;

const state = {
  meta: null,
  result: null,
  profile: null,
  ai: null,
  aiLoading: false,
  aiError: null,
  tab: "recommended",
  filters: { q: "", tier: "all", category: "all", hideApplied: false, sort: "odds" },
  shown: PAGE,
  applied: new Set(load(APPLIED_KEY, [])),
};

// ---------------------------------------------------------------- utils
function load(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}
function save(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable; preferences just won't persist */
  }
}
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
function safeUrl(url) {
  return /^https?:\/\//i.test(url || "") ? url : "#";
}
function splitList(text) {
  return (text || "").split(/[,;\n]/).map((s) => s.trim()).filter(Boolean);
}
function ageDays(iso) {
  if (!iso) return null;
  return Math.max(0, (Date.now() - new Date(iso).getTime()) / 86400000);
}
function postedLabel(iso) {
  const d = ageDays(iso);
  if (d === null) return "Date unknown";
  if (d < 1) return "Posted today";
  if (d < 2) return "Posted yesterday";
  return `Posted ${Math.floor(d)} days ago`;
}
function scoreColor(score) {
  return score >= 75 ? "var(--good)" : score >= 55 ? "var(--warn)" : "var(--bad)";
}
function tierClass(tier) {
  return `tier-${String(tier).toLowerCase()}`;
}
async function api(path, options = {}) {
  const res = await fetch(path, options);
  let body = null;
  try {
    body = await res.json();
  } catch {
    /* non-JSON error page */
  }
  if (!res.ok) {
    let detail = body && body.detail;
    if (Array.isArray(detail)) detail = detail.map((d) => d.msg).join("; ");
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return body;
}

// ---------------------------------------------------------------- listings status
function setStatus(listings, error) {
  const el = $("#listingStatus");
  if (error || !listings || !listings.count) {
    el.innerHTML = `<span class="dot error"></span>${esc(error || "No listings loaded")}`;
    el.title = error || "";
    return;
  }
  const when = listings.fetched_at ? new Date(listings.fetched_at) : null;
  const ago = when ? Math.round((Date.now() - when.getTime()) / 60000) : null;
  const agoText = ago === null ? "" : ago < 1 ? "just now" : ago < 60 ? `${ago} min ago` : `${Math.round(ago / 60)} h ago`;
  el.innerHTML = `<span class="dot"></span>${listings.count.toLocaleString()} live internships · updated ${esc(agoText)}`;
  el.title = listings.source_url || "";
}

async function loadMeta() {
  try {
    const meta = await api("/api/meta");
    state.meta = meta;
    setStatus(meta.listings, meta.listings.error && !meta.listings.count ? meta.listings.error : null);
    renderTermChips(meta.terms, meta.default_terms);
    renderCategoryChips(meta.categories);
    applyPrefs();
    if (state.result && state.tab === "ai") renderResults();
  } catch (err) {
    setStatus(null, `Couldn't reach the app server: ${err.message}`);
  }
}

function chip(name, value, label, checked, extra = "") {
  return `<label class="chip-toggle"><input type="checkbox" name="${esc(name)}" value="${esc(value)}" ${checked ? "checked" : ""}><span>${esc(label)}${extra}</span></label>`;
}

function renderTermChips(terms, defaults) {
  const box = $("#termChips");
  if (!terms || !terms.length) {
    box.innerHTML = `<span class="muted small">No term data yet</span>`;
    return;
  }
  const saved = load(PREFS_KEY, null);
  const selected = new Set(saved && saved.target_terms ? saved.target_terms : defaults);
  box.innerHTML = terms
    .filter((t) => t.count >= 3 || selected.has(t.term))
    .map((t) => chip("term", t.term, t.term, selected.has(t.term), ` <small>${t.count}</small>`))
    .join("");
}

function renderCategoryChips(categories) {
  $("#categoryChips").innerHTML = categories.map((c) => chip("category", c, c, false)).join("");
}

// ---------------------------------------------------------------- form
function gradYearOptions() {
  const sel = $("#gradYear");
  const y = new Date().getFullYear();
  for (let i = 0; i <= 6; i++) {
    const opt = document.createElement("option");
    opt.value = String(y + i);
    opt.textContent = String(y + i);
    sel.appendChild(opt);
  }
}

function readProfile() {
  const num = (v) => (v === "" || v === null ? null : Number(v));
  return {
    degree_level: $("#degree").value || null,
    grad_year: num($("#gradYear").value),
    gpa: num($("#gpa").value),
    work_authorization: $("#workAuth").value,
    target_terms: $$('input[name="term"]:checked').map((i) => i.value),
    target_categories: $$('input[name="category"]:checked').map((i) => i.value),
    locations: splitList($("#locations").value),
    remote_ok: $("#remoteOk").checked,
    location_strict: $("#locationStrict").checked,
    extra_skills: splitList($("#extraSkills").value),
    exclude_companies: splitList($("#excludeCompanies").value),
    max_age_days: num($("#maxAge").value),
  };
}

function savePrefs() {
  const p = readProfile();
  save(PREFS_KEY, { ...p, boards: $("#boards").value, enrich: $("#enrich").checked });
}

function applyPrefs() {
  const p = load(PREFS_KEY, null);
  if (!p) return;
  $("#degree").value = p.degree_level || "";
  $("#gradYear").value = p.grad_year ? String(p.grad_year) : "";
  $("#gpa").value = p.gpa ?? "";
  $("#workAuth").value = p.work_authorization || "citizen";
  $("#locations").value = (p.locations || []).join(", ");
  $("#remoteOk").checked = p.remote_ok !== false;
  $("#locationStrict").checked = !!p.location_strict;
  $("#extraSkills").value = (p.extra_skills || []).join(", ");
  $("#excludeCompanies").value = (p.exclude_companies || []).join(", ");
  $("#maxAge").value = p.max_age_days ? String(p.max_age_days) : "";
  $("#boards").value = p.boards || "";
  $("#enrich").checked = p.enrich !== false;
  const cats = new Set(p.target_categories || []);
  $$('input[name="category"]').forEach((i) => (i.checked = cats.has(i.value)));
}

function setupDropzone() {
  const dz = $("#dropzone");
  const input = $("#resumeFile");
  const show = () => {
    const f = input.files && input.files[0];
    dz.classList.toggle("has-file", !!f);
    $("#dzText").innerHTML = f
      ? `<strong>${esc(f.name)}</strong> · ${(f.size / 1024).toFixed(0)} KB`
      : "<strong>Drop your resume here</strong> or click to browse";
  };
  input.addEventListener("change", show);
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, () => dz.classList.remove("drag")));
  dz.addEventListener("drop", (e) => {
    e.preventDefault();
    if (e.dataTransfer.files.length) {
      input.files = e.dataTransfer.files;
      show();
    }
  });
}

async function onSubmit(e) {
  e.preventDefault();
  const file = $("#resumeFile").files[0];
  const text = $("#resumeText").value.trim();
  if (!file && text.length < 50) {
    renderError("Add your resume first: upload a PDF/DOCX/TXT or paste the text.");
    return;
  }
  savePrefs();
  const profile = readProfile();
  const fd = new FormData();
  if (file) fd.append("resume", file);
  else fd.append("resume_text", text);
  fd.append("profile", JSON.stringify(profile));
  fd.append("boards", $("#boards").value);
  fd.append("enrich", $("#enrich").checked ? "true" : "false");
  fd.append("top", "30");

  const btn = $("#analyzeBtn");
  btn.disabled = true;
  btn.textContent = "Analyzing…";
  $("#results").innerHTML = `<div class="loading"><div class="spinner"></div>Grading your resume and scoring thousands of postings…<br><span class="small">Fetching job descriptions for your top matches can take a few seconds.</span></div>`;
  try {
    state.result = await api("/api/analyze", { method: "POST", body: fd });
    state.profile = profile;
    state.ai = null;
    state.aiError = null;
    state.shown = PAGE;
    state.filters = { q: "", tier: "all", category: "all", hideApplied: state.filters.hideApplied, sort: "odds" };
    state.tab = "recommended";
    renderResults();
    if (window.innerWidth < 1080) $("#results").scrollIntoView({ behavior: "smooth" });
  } catch (err) {
    renderError(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Rate my resume & find internships";
  }
}

function renderError(msg) {
  const existing = state.result;
  if (existing) {
    renderResults();
    $("#results").insertAdjacentHTML("afterbegin", `<div class="error-box">${esc(msg)}</div>`);
  } else {
    $("#results").innerHTML = `<div class="error-box">${esc(msg)}</div>`;
  }
}

// ---------------------------------------------------------------- rendering: summary
function ring(score, label) {
  const r = 40;
  const c = 2 * Math.PI * r;
  const off = c * (1 - Math.max(0, Math.min(100, score)) / 100);
  return `<div class="ring"><svg viewBox="0 0 92 92"><circle class="track" cx="46" cy="46" r="${r}"/><circle class="value" cx="46" cy="46" r="${r}" stroke="${scoreColor(score)}" stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}"/></svg><div class="ring-label"><div><b>${score}</b><span>${esc(label)}</span></div></div></div>`;
}

function renderSummary() {
  const { resume, profile, stats } = state.result;
  const verdict = resume.overall >= 85 ? "Excellent" : resume.overall >= 72 ? "Solid" : resume.overall >= 58 ? "Fair" : "Needs work";
  return `
  <div class="summary">
    <div class="card score-card">
      ${ring(resume.overall, "of 100")}
      <div>
        <div class="kicker">Resume score</div>
        <div class="big">${esc(verdict)}<span class="grade">${esc(resume.grade)}</span></div>
        <div class="stat-sub">${resume.improvements.length ? `${resume.improvements.length} fixes found · <a href="#" data-tab="resume">see report</a>` : "No major issues found"}</div>
      </div>
    </div>
    <div class="card">
      <div class="kicker">Candidate strength</div>
      <div class="big">${profile.candidate_strength}<span class="muted" style="font-size:16px;font-weight:500"> / 100</span></div>
      <div class="stat-sub">${esc(profile.level)} · best fit: ${esc(profile.category_fit.slice(0, 2).map((c) => c.category).join(", "))}</div>
    </div>
    <div class="card tiers-card">
      <div class="kicker">${stats.eligible.toLocaleString()} postings you can apply to</div>
      <div class="tier-counts">
        <button class="tier-count likely" data-tier="Likely"><b>${stats.likely}</b><span>Likely</span></button>
        <button class="tier-count target" data-tier="Target"><b>${stats.target}</b><span>Target</span></button>
        <button class="tier-count reach" data-tier="Reach"><b>${stats.reach}</b><span>Reach</span></button>
      </div>
    </div>
  </div>`;
}

function renderTabs() {
  const { recommended, stats } = state.result;
  const tabs = [
    ["recommended", "Recommended", recommended.length],
    ["all", "All matches", stats.eligible],
    ["resume", "Resume report", null],
    ["ai", "AI review", null],
  ];
  return `<nav class="tabs" role="tablist">${tabs
    .map(([id, label, n]) => `<button class="tab ${state.tab === id ? "active" : ""}" data-tab="${id}" role="tab">${label}${n !== null ? `<span class="count">${n.toLocaleString()}</span>` : ""}</button>`)
    .join("")}</nav>`;
}

// ---------------------------------------------------------------- rendering: jobs
function filteredMatches() {
  const source = state.tab === "recommended" ? state.result.recommended : state.result.matches;
  const f = state.filters;
  const q = f.q.toLowerCase();
  let rows = source.filter((m) => {
    if (f.tier !== "all" && m.tier !== f.tier) return false;
    if (f.category !== "all" && m.posting.category !== f.category) return false;
    if (f.hideApplied && state.applied.has(m.posting.id)) return false;
    if (q) {
      const hay = `${m.posting.company} ${m.posting.title} ${m.posting.locations.join(" ")} ${m.matched_skills.join(" ")}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  if (f.sort === "fit") rows = [...rows].sort((a, b) => b.match_score - a.match_score || b.likelihood - a.likelihood);
  else if (f.sort === "newest") rows = [...rows].sort((a, b) => (ageDays(a.posting.date_posted) ?? 999) - (ageDays(b.posting.date_posted) ?? 999));
  return rows;
}

function jobCard(m) {
  const p = m.posting;
  const age = ageDays(p.date_posted);
  const locs = p.locations.length > 3 ? `${p.locations.slice(0, 3).join(" · ")} +${p.locations.length - 3}` : p.locations.join(" · ");
  const applied = state.applied.has(p.id);
  const ai = state.ai && state.ai.job_assessments.find((j) => j.posting_id === p.id);
  const tierBg = `bg-${m.tier.toLowerCase()}`;
  return `
  <article class="job ${applied ? "applied" : ""}" data-id="${esc(p.id)}">
    <div class="job-main">
      <div class="job-head">
        <span class="tier ${tierClass(m.tier)}">${esc(m.tier)}</span>
        <div>
          <h4><a href="${esc(safeUrl(p.url))}" target="_blank" rel="noopener">${esc(p.title)}</a></h4>
          <div class="company">${esc(p.company)}</div>
        </div>
      </div>
      <div class="job-meta">
        ${locs ? `<span>${esc(locs)}</span>` : ""}
        <span class="${age !== null && age <= 7 ? "fresh" : ""}">${esc(postedLabel(p.date_posted))}</span>
        ${p.terms.length ? `<span>${esc(p.terms.join(", "))}</span>` : ""}
        <span>${esc(p.category)}${m.focus ? ` · ${esc(m.focus)}` : ""}</span>
      </div>
      <div class="chips">
        ${m.matched_skills.slice(0, 7).map((s) => `<span class="chip have">${esc(s)}</span>`).join("")}
        ${m.missing_skills.slice(0, 4).map((s) => `<span class="chip need" title="Mentioned for this role but not on your resume">+ ${esc(s)}</span>`).join("")}
      </div>
      ${ai ? `<div class="ai-note"><b>AI: ${esc(ai.verdict)}.</b> ${esc(ai.reasoning)}</div>` : ""}
      <details class="why">
        <summary>Why this rating</summary>
        <ul>
          ${m.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}
          ${m.warnings.map((w) => `<li class="warn">${esc(w)}</li>`).join("")}
        </ul>
      </details>
    </div>
    <div class="job-side">
      <div class="metric"><span>Interview odds</span><b>${m.likelihood}</b></div>
      <div class="meter" title="Relative odds score (0–100), used for ranking. Not a literal probability."><span class="${tierBg}" style="width:${m.likelihood}%"></span></div>
      <div class="metric"><span>Resume fit</span><b>${m.match_score}%</b></div>
      <a class="btn primary small" href="${esc(safeUrl(p.url))}" target="_blank" rel="noopener">Apply ↗</a>
      <label class="applied-toggle"><input type="checkbox" data-applied="${esc(p.id)}" ${applied ? "checked" : ""}> Applied</label>
    </div>
  </article>`;
}

function renderJobs() {
  const rows = filteredMatches();
  const f = state.filters;
  const cats = [...new Set(state.result.matches.map((m) => m.posting.category))].sort();
  const tierBtn = (t) => `<button type="button" data-filter-tier="${t}" class="${f.tier === t ? "active" : ""}">${t === "all" ? "All" : t}</button>`;
  const intro = state.tab === "recommended"
    ? `Your best shots: the highest-odds roles you're eligible for, at most two per company. Apply to the fresh ones first.`
    : `Every posting you're eligible for, ranked by interview odds${state.result.matches.length < state.result.stats.eligible ? ` (top ${state.result.matches.length} shown)` : ""}.`;
  return `
  <p class="list-meta">${intro}</p>
  <div class="toolbar">
    <input type="text" id="q" placeholder="Search company, role, skill…" value="${esc(f.q)}">
    <div class="segmented">${["all", "Likely", "Target", "Reach"].map(tierBtn).join("")}</div>
    <select id="catFilter"><option value="all">All categories</option>${cats.map((c) => `<option ${f.category === c ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>
    <select id="sortSel">
      <option value="odds" ${f.sort === "odds" ? "selected" : ""}>Best odds</option>
      <option value="fit" ${f.sort === "fit" ? "selected" : ""}>Best fit</option>
      <option value="newest" ${f.sort === "newest" ? "selected" : ""}>Newest</option>
    </select>
    <label class="toggle"><input type="checkbox" id="hideApplied" ${f.hideApplied ? "checked" : ""}> Hide applied</label>
    <button class="btn ghost small" id="exportBtn" type="button">Export CSV</button>
  </div>
  <div id="jobList">
    ${rows.length ? rows.slice(0, state.shown).map(jobCard).join("") : `<div class="card muted">No postings match these filters.</div>`}
  </div>
  ${rows.length > state.shown ? `<div class="more"><button class="btn ghost" id="moreBtn" type="button">Show more (${(rows.length - state.shown).toLocaleString()} left)</button></div>` : ""}`;
}

function exportCsv() {
  const rows = filteredMatches();
  const cols = ["Tier", "Odds", "Fit", "Company", "Title", "Category", "Locations", "Terms", "Posted", "Matched skills", "Missing skills", "URL", "Applied"];
  const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const lines = [cols.map(q).join(",")];
  for (const m of rows) {
    const p = m.posting;
    lines.push([m.tier, m.likelihood, m.match_score, p.company, p.title, p.category, p.locations.join("; "), p.terms.join("; "),
      p.date_posted ? p.date_posted.slice(0, 10) : "", m.matched_skills.join(", "), m.missing_skills.join(", "), p.url,
      state.applied.has(p.id) ? "yes" : ""].map(q).join(","));
  }
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "internship-matches.csv";
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ---------------------------------------------------------------- rendering: resume report
const GROUP_LABELS = {
  language: "Languages", web: "Web", backend: "Backend", mobile: "Mobile", data: "Data", ml: "ML / AI",
  cloud: "Cloud & DevOps", tools: "Tools", systems: "Systems", cs: "CS fundamentals", hardware: "Hardware",
  quant: "Quant & math", product: "Product & design", security: "Security", other: "Other",
};

function bar(label, score, detail, right) {
  return `<div class="bar-row"><div class="bar-top"><span>${esc(label)}</span><span>${right ?? score}</span></div><div class="bar"><span style="width:${score}%;background:${scoreColor(score)}"></span></div>${detail ? `<div class="bar-detail">${esc(detail)}</div>` : ""}</div>`;
}

function renderReport() {
  const { resume: r, profile: p } = state.result;
  const d = r.detected;
  const yn = (b) => (b ? `<span class="yes">✓</span>` : `<span class="no">✗</span>`);
  const groups = Object.entries(r.skills).sort((a, b) => b[1].length - a[1].length);
  return `
  <div class="grid-2">
    <div class="card"><h3>Score breakdown <span class="grade">${esc(r.grade)} · ${r.overall}</span></h3>
      ${r.subscores.map((s) => bar(`${s.label} (${Math.round(s.weight * 100)}%)`, s.score, s.detail)).join("")}
    </div>
    <div class="card"><h3>Where you fit</h3>
      ${p.category_fit.map((c) => bar(c.category, c.fit, null, `${c.fit}`)).join("")}
      ${p.notes.length ? `<ul class="notes">${p.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
    </div>
  </div>
  <div class="grid-2">
    <div class="card"><h3>What's working</h3>
      ${r.strengths.length ? `<ul class="list good">${r.strengths.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>` : `<p class="muted">Work through the fixes and your strengths will show here.</p>`}
    </div>
    <div class="card"><h3>Fix these first</h3>
      ${r.improvements.length ? `<ul class="list fix">${r.improvements.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>` : `<p class="muted">Nothing major. Nice work.</p>`}
    </div>
  </div>
  ${r.weak_bullets.length ? `<div class="card" style="margin-bottom:16px"><h3>Bullets to rewrite</h3>
    ${r.weak_bullets.map((b) => `<div class="bullet-fb"><q>${esc(b.text)}</q><ul>${b.issues.map((i) => `<li>${esc(i)}</li>`).join("")}</ul></div>`).join("")}
  </div>` : ""}
  <div class="grid-2">
    <div class="card"><h3>What we detected</h3>
      <dl class="kv">
        <dt>Level</dt><dd>${esc(d.level)}</dd>
        <dt>Degree</dt><dd>${esc(d.degree || "not found")}${d.major ? `, ${esc(d.major)}` : ""}</dd>
        <dt>Graduation</dt><dd>${esc(d.grad_year || "not found")}</dd>
        <dt>GPA</dt><dd>${esc(d.gpa ?? "not listed")}</dd>
        <dt>Length</dt><dd>${r.stats.pages ? `${r.stats.pages} page(s), ` : ""}${r.stats.word_count} words, ${r.stats.bullets} bullets</dd>
        <dt>Experience</dt><dd>${r.stats.roles} role(s), ${r.stats.internships} internship(s), ${r.stats.projects} project(s)</dd>
        <dt>Contact</dt><dd>${yn(r.contact.email)} email ${yn(r.contact.phone)} phone ${yn(r.contact.linkedin)} LinkedIn ${yn(r.contact.github || r.contact.portfolio)} GitHub/site</dd>
      </dl>
      <p class="small muted" style="margin:12px 0 0">Something wrong? Set it in “About you” and re-run.</p>
    </div>
    <div class="card"><h3>Skills found <span class="muted small">${r.skill_count}</span></h3>
      ${groups.map(([g, list]) => `<div class="skill-group"><h5>${esc(GROUP_LABELS[g] || g)}</h5><div class="chips" style="margin:0">${list.map((s) => `<span class="chip">${esc(s)}</span>`).join("")}</div></div>`).join("") || `<p class="muted">No recognizable skills found.</p>`}
    </div>
  </div>`;
}

// ---------------------------------------------------------------- rendering: AI
function renderAI() {
  const meta = state.meta || { ai: { available: false } };
  if (!meta.ai.available) {
    return `<div class="card"><h3>AI review with Claude</h3>
      <p class="muted">Get a recruiter-style critique, line-by-line bullet rewrites, and a second opinion on your top matches.</p>
      <p>To turn it on, get an API key from <a href="https://console.anthropic.com/" target="_blank" rel="noopener">console.anthropic.com</a>, then restart the app with it set:</p>
      <pre class="card" style="padding:10px 12px;overflow:auto;box-shadow:none">export ANTHROPIC_API_KEY=sk-ant-...\ninternmatch serve</pre>
      <p class="small muted">Everything else in this app works without it.</p></div>`;
  }
  const intro = `<div class="card" style="margin-bottom:16px"><div class="ai-intro"><div><h3 style="margin:0">AI review with Claude</h3>
    <p>Sends your resume text and your top ${Math.min(20, state.result.recommended.length)} recommended postings to ${esc(meta.ai.model)} for a recruiter-style critique. Takes about a minute.</p></div>
    <button class="btn primary" id="aiBtn" type="button" ${state.aiLoading ? "disabled" : ""}>${state.aiLoading ? "Reviewing…" : state.ai ? "Run again" : "Run AI review"}</button></div>
    ${state.aiError ? `<div class="error-box" style="margin:14px 0 0">${esc(state.aiError)}</div>` : ""}</div>`;
  if (state.aiLoading) return intro + `<div class="loading"><div class="spinner"></div>Claude is reading your resume…</div>`;
  if (!state.ai) return intro;
  const a = state.ai;
  const byId = Object.fromEntries(state.result.recommended.map((m) => [m.posting.id, m]));
  return intro + `
  <div class="summary" style="grid-template-columns:auto 1fr">
    <div class="card score-card">${ring(a.score, "AI score")}</div>
    <div class="card"><div class="kicker">Recruiter's take</div><p style="margin:6px 0 0;font-size:15px">${esc(a.summary)}</p></div>
  </div>
  <div class="grid-2">
    <div class="card"><h3>Strengths</h3><ul class="list good">${a.strengths.map((s) => `<li>${esc(s)}</li>`).join("")}</ul></div>
    <div class="card"><h3>Weaknesses</h3><ul class="list fix">${a.weaknesses.map((s) => `<li>${esc(s)}</li>`).join("")}</ul></div>
  </div>
  ${a.bullet_rewrites.length ? `<div class="card" style="margin-bottom:16px"><h3>Suggested rewrites</h3>${a.bullet_rewrites.map((b) => `<div class="rewrite"><div class="before">${esc(b.original)}</div><div class="after">${esc(b.improved)}</div><div class="whyline">${esc(b.why)}</div></div>`).join("")}<p class="small muted">Replace any [bracketed placeholders] with your real numbers.</p></div>` : ""}
  <div class="grid-2">
    <div class="card"><h3>Next steps</h3><ul class="list fix">${a.next_steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ul></div>
    <div class="card"><h3>Keywords to add (if true)</h3><div class="chips" style="margin:0">${a.keywords_to_add.map((k) => `<span class="chip">${esc(k)}</span>`).join("") || `<span class="muted">None</span>`}</div></div>
  </div>
  ${a.job_assessments.length ? `<div class="card"><h3>Second opinion on your matches</h3>${a.job_assessments.map((j) => {
    const m = byId[j.posting_id];
    if (!m) return "";
    return `<div class="assess"><span class="tier ${tierClass(j.verdict)}">${esc(j.verdict)}</span><span class="t"><a href="${esc(safeUrl(m.posting.url))}" target="_blank" rel="noopener">${esc(m.posting.title)}</a> · ${esc(m.posting.company)}</span><span class="r">${esc(j.reasoning)}</span></div>`;
  }).join("")}</div>` : ""}`;
}

async function runAI() {
  state.aiLoading = true;
  state.aiError = null;
  renderResults();
  try {
    state.ai = await api("/api/ai-review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        resume_text: state.result.resume_text,
        profile: state.profile,
        report: state.result.resume,
        matches: state.result.recommended.slice(0, 20),
      }),
    });
  } catch (err) {
    state.aiError = err.message;
  } finally {
    state.aiLoading = false;
    if (state.tab === "ai") renderResults();
  }
}

// ---------------------------------------------------------------- main render + events
function renderResults() {
  if (!state.result) return;
  let body;
  if (state.tab === "resume") body = renderReport();
  else if (state.tab === "ai") body = renderAI();
  else body = renderJobs();
  $("#results").innerHTML = renderSummary() + renderTabs() + body;
  const warn = state.result.stats.board_errors;
  if (warn && state.tab !== "resume" && state.tab !== "ai") {
    $("#results").insertAdjacentHTML("afterbegin", `<div class="error-box">Some company boards couldn't be loaded: ${esc(warn)}</div>`);
  }
}

function rerenderJobsKeepingFocus() {
  const q = $("#q");
  const pos = q ? q.selectionStart : null;
  renderResults();
  const q2 = $("#q");
  if (q2 && pos !== null) {
    q2.focus();
    q2.setSelectionRange(pos, pos);
  }
}

function bindResultEvents() {
  const root = $("#results");
  root.addEventListener("click", (e) => {
    const tab = e.target.closest("[data-tab]");
    if (tab) {
      e.preventDefault();
      state.tab = tab.dataset.tab;
      state.filters.tier = "all";
      state.shown = PAGE;
      renderResults();
      return;
    }
    const tierCount = e.target.closest("[data-tier]");
    if (tierCount) {
      state.tab = "all";
      state.filters.tier = tierCount.dataset.tier;
      state.shown = PAGE;
      renderResults();
      return;
    }
    const tierFilter = e.target.closest("[data-filter-tier]");
    if (tierFilter) {
      state.filters.tier = tierFilter.dataset.filterTier;
      state.shown = PAGE;
      renderResults();
      return;
    }
    if (e.target.closest("#moreBtn")) {
      state.shown += PAGE;
      renderResults();
    } else if (e.target.closest("#exportBtn")) {
      exportCsv();
    } else if (e.target.closest("#aiBtn")) {
      runAI();
    }
  });
  root.addEventListener("input", (e) => {
    if (e.target.id === "q") {
      state.filters.q = e.target.value;
      state.shown = PAGE;
      rerenderJobsKeepingFocus();
    }
  });
  root.addEventListener("change", (e) => {
    const t = e.target;
    if (t.id === "catFilter") state.filters.category = t.value;
    else if (t.id === "sortSel") state.filters.sort = t.value;
    else if (t.id === "hideApplied") state.filters.hideApplied = t.checked;
    else if (t.dataset.applied) {
      const id = t.dataset.applied;
      if (t.checked) state.applied.add(id);
      else state.applied.delete(id);
      save(APPLIED_KEY, [...state.applied]);
      const card = t.closest(".job");
      if (card) card.classList.toggle("applied", t.checked);
      if (!state.filters.hideApplied) return;
    } else return;
    state.shown = PAGE;
    renderResults();
  });
}

async function onRefresh() {
  const btn = $("#refreshBtn");
  btn.disabled = true;
  $("#listingStatus").innerHTML = `<span class="dot pending"></span>Downloading latest postings…`;
  try {
    const status = await api("/api/refresh", { method: "POST" });
    setStatus(status);
    await loadMeta();
  } catch (err) {
    setStatus(null, err.message);
  } finally {
    btn.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  gradYearOptions();
  setupDropzone();
  bindResultEvents();
  $("#form").addEventListener("submit", onSubmit);
  $("#refreshBtn").addEventListener("click", onRefresh);
  loadMeta();
});
