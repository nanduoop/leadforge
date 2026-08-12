const STEPS = [
  { id: "welcome", title: "Welcome", eyebrow: "Step 1 of 6" },
  { id: "setup", title: "Connections", eyebrow: "Step 2 of 6" },
  { id: "business", title: "Your business", eyebrow: "Step 3 of 6" },
  { id: "icp", title: "Target audience", eyebrow: "Step 4 of 6" },
  { id: "review", title: "Review & launch", eyebrow: "Step 5 of 6" },
  { id: "run", title: "Running pipeline", eyebrow: "Step 6 of 6" },
  { id: "results", title: "Results", eyebrow: "Complete" },
];

const COACH = {
  welcome: "LeadForge finds real leads with proof attached — not guesses from a model. I'll guide you through setup in about five minutes.",
  setup: "Connect Firecrawl first — that's what powers web search and page reading. Google Sheets and NeverBounce are optional but useful.",
  business: "Paste your website and I'll read it to pre-fill your brief. Or describe your business in your own words.",
  icp: "These answers define who we search for. Be specific on job titles — that's who we try to reach at each company.",
  review: "Check the brief and discovery preview before spending credits. You can tune limits in Advanced settings.",
  run: "Sit back — I'll show live progress for each stage. Long stages like contacts can take several minutes.",
  results: "Your scored leads are below. Download CSV anytime; Sheets export works if you connected Google.",
};

let stepIndex = 0;
let questions = [];
let pollTimer = null;
let runStartedAt = null;
let logClearedAt = 0;
// The site we have already read through Firecrawl. Re-sending it on every save
// re-scrapes it and bills the user again for a page we already have.
let siteAlreadyRead = null;

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

function setCoach(message) {
  const el = $("#coach-message");
  if (el && el.textContent !== message) {
    el.classList.add("fade");
    setTimeout(() => {
      el.textContent = message;
      el.classList.remove("fade");
    }, 150);
  }
}

function formatElapsed(ms) {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return m > 0 ? `${m}m ${r}s` : `${r}s`;
}

function formatStats(stats) {
  if (!stats || !Object.keys(stats).length) return "";
  return Object.entries(stats)
    .filter(([, v]) => typeof v !== "object")
    .map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`)
    .join(" · ");
}

function renderNav() {
  const nav = $("#step-nav");
  nav.innerHTML = STEPS.slice(0, 6).map((s, i) => `
    <button type="button" class="step-link ${i === stepIndex ? "active" : ""} ${i < stepIndex ? "done" : ""}" data-step="${i}" ${i > stepIndex ? "disabled" : ""}>
      <span class="dot"></span>
      <span>${s.title}</span>
    </button>
  `).join("");
  nav.querySelectorAll(".step-link").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = Number(btn.dataset.step);
      if (idx <= stepIndex && idx !== stepIndex) showStep(idx);
    });
  });
}

function showStep(index) {
  stepIndex = index;
  const step = STEPS[index];
  $("#step-eyebrow").textContent = step.eyebrow;
  $("#step-title").textContent = step.title;
  $$(".panel").forEach((p) => p.classList.add("hidden"));
  $(`#panel-${step.id}`).classList.remove("hidden");
  $("#btn-back").hidden = index === 0;
  $("#btn-next").hidden = step.id === "run";
  if (step.id === "results") $("#btn-next").hidden = true;
  if (step.id === "run") $("#btn-next").textContent = "View results";
  else $("#btn-next").textContent = index === STEPS.length - 2 ? "Start pipeline" : "Continue";
  setCoach(COACH[step.id] || "");
  renderNav();
}

async function loadSetup() {
  const [setup, integrations] = await Promise.all([
    api("/api/setup"),
    api("/api/integrations"),
  ]);

  // The technical checks stay behind a disclosure. They are for whoever set the
  // machine up, not for the person trying to find leads — "dep dns OK" told the
  // user nothing while the detail and fix the API already returns went unused.
  $("#setup-checks").innerHTML = setup.checks.map((c) => `
    <div class="check-row">
      <span class="check-name">${c.name}${c.detail ? `<span class="check-detail">${c.detail}</span>` : ""}</span>
      <span class="badge ${c.status === "ok" ? "ok" : c.status === "FAIL" ? "fail" : "warn"}">${c.status}</span>
      ${c.fix ? `<code class="check-fix">${c.fix}</code>` : ""}
    </div>
  `).join("");

  const failed = setup.checks.filter((c) => c.status !== "ok");
  $("#setup-summary").textContent = failed.length
    ? `System check — ${failed.length} thing(s) need attention`
    : `System check — all ${setup.checks.length} passed`;
  $("#setup-details").open = failed.some((c) => c.status === "FAIL");

  $("#integrations").innerHTML = integrations.integrations.map((i) => `
    <article class="integration">
      <h4>${i.name} <span class="badge ${i.status === "connected" ? "ok" : "warn"}">${i.status.replace(/_/g, " ")}</span></h4>
      <p>${i.description}</p>
      ${i.status !== "connected" && i.link
        ? `<p class="hint">To connect, run this in your terminal:</p><code>${i.link}</code>`
        : ""}
      ${i.upgrade ? `<p class="hint">Need more volume? <a href="${i.upgrade}" target="_blank" rel="noopener">Pricing</a></p>` : ""}
    </article>
  `).join("");

  const connected = integrations.integrations.filter((i) => i.status === "connected").length;
  const optional = integrations.integrations.filter((i) => i.status !== "connected" && !i.required);
  setCoach(setup.ready
    ? `${connected} of ${integrations.integrations.length} services connected — enough to start.`
      + (optional.length ? ` ${optional.map((i) => i.name).join(" and ")} would sharpen results, but you can add them later.` : "")
    : "Firecrawl is the one service LeadForge can't run without. Connect it below, then refresh this page.");

  return setup.ready;
}

async function loadQuestions() {
  const data = await api("/api/intake/questions");
  questions = data.questions;
  const brief = await api("/api/intake/brief");

  $("#icp-form").innerHTML = questions.map((q) => {
    const val = brief.brief.client[q.field] ?? brief.brief.icp[q.field] ?? "";
    const display = Array.isArray(val) ? val.join(", ") : val;
    if (q.multiline) {
      return `<label class="field"><span>${q.prompt}${q.required ? " *" : ""}</span>
        <textarea data-field="${q.field}" rows="3" placeholder="Type your answer…">${display}</textarea></label>`;
    }
    return `<label class="field"><span>${q.prompt}${q.required ? " *" : ""}</span>
      <input data-field="${q.field}" value="${display}" placeholder="Type your answer…"></label>`;
  }).join("");

  if (brief.summary.site) {
    $("#input-site").value = brief.summary.site;
    // Already in the brief, so it has been read before — don't re-scrape on save.
    siteAlreadyRead = brief.summary.site;
  }
}

function collectAnswers() {
  const answers = {};
  $$("#icp-form [data-field]").forEach((el) => {
    answers[el.dataset.field] = el.value.trim();
  });
  return answers;
}

async function saveBrief() {
  const site = $("#input-site").value.trim();
  const body = {
    site: site && site !== siteAlreadyRead ? site : null,
    text: $("#input-text").value.trim() || null,
    answers: collectAnswers(),
  };
  const saved = await api("/api/intake/save", { method: "POST", body: JSON.stringify(body) });
  if (site) siteAlreadyRead = site;
  return saved;
}

async function loadReview(saved) {
  // `saved` is passed in by the caller that already saved. Saving again here
  // would re-scrape the user's website through Firecrawl a second time —
  // billable, and it stalled this step for ~15s with no feedback on screen.
  const s = (saved || await saveBrief()).summary;

  $("#brief-summary").innerHTML = [
    ["Client", s.client_name || s.site || "—"],
    ["Offer", s.offer || "—"],
    ["Industries", (s.industries || []).join(", ") || "—"],
    ["Titles", (s.titles || []).join(", ") || "—"],
    ["Markets", (s.markets || []).join(", ") || "—"],
    ["Signals", (s.signals || []).join(", ") || "—"],
  ].map(([k, v]) => `<div class="summary-item"><strong>${k}</strong>${v}</div>`).join("");

  const gaps = $("#brief-gaps");
  if (s.gaps?.length) {
    gaps.classList.remove("hidden");
    gaps.textContent = `${s.gaps.length} required field(s) still empty: ${s.gaps.join(", ")}. Discovery will run but matches may be weaker.`;
    setCoach(`Almost there — fill in ${s.gaps.join(", ")} for stronger matches, or launch anyway.`);
  } else {
    gaps.classList.add("hidden");
    setCoach("Brief looks complete. Review the discovery preview, then start when ready.");
  }

  try {
    const preview = await api(`/api/discover/preview?limit=${$("#input-limit").value || 60}`);
    $("#discover-preview").innerHTML = `
      <strong>${preview.total_queries}</strong> search queries planned
      (~<strong>${preview.estimated_credits}</strong> Firecrawl credits).<br>
      By path: ${Object.entries(preview.by_path).map(([k, v]) => `${k} (${v})`).join(", ") || "—"}
    `;
  } catch (e) {
    $("#discover-preview").textContent = e.message;
  }
}

function overallPercent(status) {
  const stages = status.stages || [];
  if (!stages.length) return 0;
  const sum = stages.reduce((acc, s) => acc + (s.percent ?? 0), 0);
  return Math.round(sum / stages.length);
}

function renderPipeline(status) {
  const pct = overallPercent(status);
  const summary = status.summary || {};
  const completed = summary.completed_stages ?? status.stages.filter((s) => s.status === "completed").length;

  $("#progress-bar").style.width = `${pct}%`;
  $("#overall-percent").textContent = `${pct}%`;
  $("#progress-stages-label").textContent = `${completed} of ${status.stages.length} stages complete`;

  if (runStartedAt) {
    $("#run-elapsed").textContent = ` · ${formatElapsed(Date.now() - runStartedAt)}`;
  }

  const running = status.stages.find((s) => s.status === "running");
  if (running) {
    const prog = running.progress || {};
    const detail = prog.message || running.hint || "";
    $("#run-subline").textContent = detail;
    setCoach(`Working on ${running.label.toLowerCase()}… ${detail}`);
  } else if (status.overall === "completed") {
    $("#run-subline").textContent = "All stages finished.";
    setCoach("Pipeline complete — your leads are ready to review.");
  } else if (status.overall === "failed") {
    $("#run-subline").textContent = status.pipeline_error || "A stage failed.";
    setCoach("Something went wrong. Check the failed stage below, fix the issue, then re-run that stage.");
  } else {
    $("#run-subline").textContent = "";
  }

  $("#run-lead").textContent = status.running
    ? `Running: ${summary.running_label || running?.label || "pipeline"}`
    : status.overall === "completed"
      ? "Pipeline finished successfully."
      : status.overall === "failed"
        ? "Pipeline stopped — see details below."
        : "Pipeline ready.";

  $("#stage-list").innerHTML = status.stages.map((s, idx) => {
    const stagePct = s.percent ?? 0;
    const statsStr = formatStats(s.stats) || (s.progress?.message) || (s.error ? `Error: ${s.error}` : s.hint);
    const progLabel = s.progress?.total
      ? `${s.progress.current}/${s.progress.total}`
      : "";
    return `
      <details class="stage-card ${s.status}" ${s.status === "running" ? "open" : ""}>
        <summary class="stage-card-header">
          <div class="stage-card-title">
            <span class="stage-num">0${idx + 1}</span>
            <div>
              <strong class="stage-title">${s.label}</strong>
              <p class="stage-hint">${s.hint || ""}</p>
            </div>
          </div>
          <div class="stage-actions">
            ${progLabel ? `<span class="stage-counter">${progLabel}</span>` : ""}
            <span class="badge ${s.status === "completed" ? "ok" : s.status === "failed" ? "fail" : s.status === "running" ? "running" : "pending"}">${s.status}</span>
            <button type="button" class="btn micro rerun-btn" data-stage="${s.id}" ${status.running ? "disabled" : ""}>Re-run</button>
          </div>
        </summary>
        <div class="stage-progress-track" aria-label="${s.label} progress">
          <div class="stage-progress-fill" style="width: ${stagePct}%"></div>
        </div>
        <div class="stage-card-foot">
          <span class="stage-stats">${statsStr}</span>
          <span class="stage-artifact">${s.artifact_exists ? s.artifact : ""}</span>
        </div>
      </details>
    `;
  }).join("");

  $("#stage-list").querySelectorAll(".rerun-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      rerunStage(btn.dataset.stage);
    });
  });

  const events = (status.events || []).slice().reverse();
  $("#event-log").innerHTML = events.map((e) => `
    <li>
      <span class="log-time">${(e.at || "").slice(11, 19)}</span>
      <span class="log-event">${e.event.replace(/_/g, " ")}</span>
      ${e.message ? `<span class="log-msg">${e.message}</span>` : ""}
      ${e.error ? `<span class="log-err">${e.error}</span>` : ""}
    </li>
  `).join("");
}

async function rerunStage(stageId) {
  if (!confirm(`Re-run from "${stageId}"? Downstream stages will reset.`)) return;
  try {
    runStartedAt = Date.now();
    await api("/api/pipeline/stage/rerun", {
      method: "POST",
      body: JSON.stringify({ stage: stageId }),
    });
    pollPipeline();
  } catch (e) {
    alert(`Could not rerun stage ${stageId}: ${e.message}`);
  }
}

async function pollPipeline() {
  try {
    const status = await api("/api/pipeline/status");
    renderPipeline(status);
    if (status.running) {
      pollTimer = setTimeout(pollPipeline, 1000);
    } else if (status.overall === "completed" && stepIndex === STEPS.findIndex((s) => s.id === "run")) {
      setTimeout(() => {
        showStep(STEPS.findIndex((s) => s.id === "results"));
        loadResults();
      }, 800);
    }
  } catch (e) {
    pollTimer = setTimeout(pollPipeline, 2000);
  }
}

async function startPipeline() {
  runStartedAt = Date.now();
  showStep(STEPS.findIndex((s) => s.id === "run"));
  await api("/api/pipeline/start", {
    method: "POST",
    body: JSON.stringify({
      limit: Number($("#input-limit").value) || 60,
      max_companies: Number($("#input-max-companies").value) || 60,
      min_confidence: Number($("#input-min-confidence").value) || 70,
    }),
  });
  pollPipeline();
}

async function loadResults() {
  const data = await api("/api/leads");
  $("#results-summary").textContent = data.count
    ? `${data.count} lead(s) from ${data.stage} stage`
    : "No leads yet. Run the pipeline or adjust thresholds.";

  if (!data.leads.length) {
    $("#leads-table thead").innerHTML = "";
    $("#leads-table tbody").innerHTML = `<tr><td colspan="8" class="empty">No leads to show yet.</td></tr>`;
    return;
  }

  const cols = ["Company", "Contact", "Title", "Email", "Priority", "Fit", "Intent", "Why Now"];
  $("#leads-table thead").innerHTML = `<tr>${cols.map((c) => `<th>${c}</th>`).join("")}</tr>`;
  $("#leads-table tbody").innerHTML = data.leads.map((row) => `
    <tr>${cols.map((c) => `<td>${row[c] ?? ""}</td>`).join("")}</tr>
  `).join("");
}


$("#btn-reset-pipeline").addEventListener("click", async () => {
  if (confirm("Reset pipeline state for a fresh run?")) {
    await api("/api/pipeline/reset", { method: "POST" });
    runStartedAt = null;
    pollPipeline();
  }
});

$("#btn-clear-log").addEventListener("click", () => {
  $("#event-log").innerHTML = "";
});

$("#btn-read-site").addEventListener("click", async () => {
  const site = $("#input-site").value.trim();
  if (!site) {
    $("#site-status").textContent = "Enter a website URL first.";
    $("#site-status").className = "hint warn";
    return;
  }
  $("#site-status").textContent = "Reading website — this usually takes 10–30 seconds…";
  $("#site-status").className = "hint";
  $("#btn-read-site").disabled = true;
  try {
    const data = await api("/api/intake/site", { method: "POST", body: JSON.stringify({ site }) });
    if (data.summary.offer) $("#input-text").value = data.summary.offer;
    const via = data.source ? ` (via ${data.source.replace(/_/g, " ")})` : "";
    $("#site-status").textContent = data.summary.client_name
      ? `Found ${data.summary.client_name}${via} — fields updated below.`
      : `Site read${via}. Review the fields below.`;
    $("#site-status").className = "hint ok";
    setCoach(data.summary.client_name
      ? `I found ${data.summary.client_name}. Tweak anything that looks off in the next step.`
      : "I read your site. Fill in any gaps in the next step.");
    await loadQuestions();
  } catch (e) {
    $("#site-status").textContent = e.message;
    $("#site-status").className = "hint warn";
    setCoach(e.message.includes("rate limit")
      ? "Firecrawl is temporarily rate-limited. Wait a minute, or type your business description manually."
      : "Couldn't read the site — try again or describe your business in the text box.");
  } finally {
    $("#btn-read-site").disabled = false;
  }
});

$("#btn-back").addEventListener("click", () => {
  if (stepIndex > 0) showStep(stepIndex - 1);
});

$("#btn-next").addEventListener("click", async () => {
  const step = STEPS[stepIndex];
  $("#btn-next").disabled = true;
  try {
    if (step.id === "welcome") showStep(1);
    else if (step.id === "setup") {
      const ready = await loadSetup();
      if (!ready) {
        alert("Fix blocking setup issues before continuing. Firecrawl is required.");
        return;
      }
      showStep(2);
      await loadQuestions();
    }
    else if (step.id === "business") {
      if (!$("#input-site").value.trim() && !$("#input-text").value.trim()) {
        setCoach("Add your website or a short business description so I know what to search for.");
        return;
      }
      showStep(3);
    }
    else if (step.id === "icp") {
      setCoach("Saving your brief and reading your website…");
      const saved = await saveBrief();
      showStep(4);
      await loadReview(saved);
    }
    else if (step.id === "review") {
      await startPipeline();
    }
    else if (step.id === "run") {
      showStep(6);
      await loadResults();
    }
  } catch (e) {
    alert(e.message);
  } finally {
    $("#btn-next").disabled = false;
  }
});

loadSetup();
renderNav();
showStep(0);
