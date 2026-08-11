const STEPS = [
  { id: "welcome", title: "Welcome", eyebrow: "Step 1 of 6" },
  { id: "setup", title: "Connections", eyebrow: "Step 2 of 6" },
  { id: "business", title: "Your business", eyebrow: "Step 3 of 6" },
  { id: "icp", title: "Target audience", eyebrow: "Step 4 of 6" },
  { id: "review", title: "Review & launch", eyebrow: "Step 5 of 6" },
  { id: "run", title: "Running pipeline", eyebrow: "Step 6 of 6" },
  { id: "results", title: "Results", eyebrow: "Complete" },
];

let stepIndex = 0;
let questions = [];
let pollTimer = null;

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

function renderNav() {
  const nav = $("#step-nav");
  nav.innerHTML = STEPS.slice(0, 6).map((s, i) => `
    <div class="step-link ${i === stepIndex ? "active" : ""} ${i < stepIndex ? "done" : ""}">
      <span class="dot"></span>
      <span>${s.title}</span>
    </div>
  `).join("");
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
  renderNav();
}

async function loadSetup() {
  const [setup, integrations] = await Promise.all([
    api("/api/setup"),
    api("/api/integrations"),
  ]);

  $("#setup-checks").innerHTML = setup.checks.map((c) => `
    <div class="check-row">
      <span>${c.name}</span>
      <span class="badge ${c.status === "ok" ? "ok" : c.status === "FAIL" ? "fail" : "warn"}">${c.status}</span>
    </div>
  `).join("");

  $("#integrations").innerHTML = integrations.integrations.map((i) => `
    <article class="integration">
      <h4>${i.name} <span class="badge ${i.status === "connected" ? "ok" : "warn"}">${i.status.replace("_", " ")}</span></h4>
      <p>${i.description}</p>
      ${i.link ? `<code>${i.link}</code>` : ""}
      ${i.upgrade ? `<p class="hint">Upgrade: <a href="${i.upgrade}" target="_blank" rel="noopener">${i.upgrade}</a></p>` : ""}
    </article>
  `).join("");

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
        <textarea data-field="${q.field}" rows="3">${display}</textarea></label>`;
    }
    return `<label class="field"><span>${q.prompt}${q.required ? " *" : ""}</span>
      <input data-field="${q.field}" value="${display}"></label>`;
  }).join("");

  if (brief.summary.site) $("#input-site").value = brief.summary.site;
}

function collectAnswers() {
  const answers = {};
  $$("#icp-form [data-field]").forEach((el) => {
    answers[el.dataset.field] = el.value.trim();
  });
  return answers;
}

async function saveBrief() {
  const body = {
    site: $("#input-site").value.trim() || null,
    text: $("#input-text").value.trim() || null,
    answers: collectAnswers(),
  };
  return api("/api/intake/save", { method: "POST", body: JSON.stringify(body) });
}

async function loadReview() {
  const saved = await saveBrief();
  const s = saved.summary;

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
    gaps.textContent = `${s.gaps.length} required field(s) still empty: ${s.gaps.join(", ")}. Discovery will run but matches may be weak.`;
  } else {
    gaps.classList.add("hidden");
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

function renderPipeline(status) {
  const done = status.stages.filter((s) => s.status === "completed").length;
  const pct = Math.round((done / status.stages.length) * 100);
  $("#progress-bar").style.width = `${pct}%`;
  $("#run-lead").textContent = status.running
    ? "Pipeline is running. You can leave this tab open to watch progress."
    : status.overall === "completed"
      ? "Pipeline finished successfully."
      : status.overall === "failed"
        ? `Pipeline failed: ${status.pipeline_error || "see stage below"}`
        : "Ready to start.";

  $("#stage-list").innerHTML = status.stages.map((s) => `
    <div class="stage-row">
      <span>${s.label}</span>
      <span class="badge ${s.status === "completed" ? "ok" : s.status === "failed" ? "fail" : s.status === "running" ? "running" : "pending"}">${s.status}</span>
    </div>
  `).join("");

  $("#event-log").innerHTML = (status.events || []).slice().reverse().map((e) => `
    <li>${(e.at || "").slice(11, 19)} · ${e.event}</li>
  `).join("");
}

async function pollPipeline() {
  const status = await api("/api/pipeline/status");
  renderPipeline(status);
  if (status.running) {
    pollTimer = setTimeout(pollPipeline, 2000);
  } else if (status.overall === "completed") {
    showStep(STEPS.findIndex((s) => s.id === "results"));
    loadResults();
  }
}

async function startPipeline() {
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
    ? `${data.count} lead(s) from stage: ${data.stage}`
    : "No leads yet. Run the pipeline or lower your thresholds.";

  if (!data.leads.length) return;

  const cols = ["Company", "Contact", "Title", "Email", "Priority", "Fit", "Intent", "Why Now"];
  $("#leads-table thead").innerHTML = `<tr>${cols.map((c) => `<th>${c}</th>`).join("")}</tr>`;
  $("#leads-table tbody").innerHTML = data.leads.map((row) => `
    <tr>${cols.map((c) => `<td>${row[c] ?? ""}</td>`).join("")}</tr>
  `).join("");
}

$("#btn-read-site").addEventListener("click", async () => {
  const site = $("#input-site").value.trim();
  if (!site) return;
  $("#site-status").textContent = "Reading website…";
  try {
    const data = await api("/api/intake/site", { method: "POST", body: JSON.stringify({ site }) });
    if (data.summary.offer) $("#input-text").value = data.summary.offer;
    $("#site-status").textContent = data.summary.client_name
      ? `Found: ${data.summary.client_name}`
      : "Site read. Fill any remaining fields below.";
    await loadQuestions();
  } catch (e) {
    $("#site-status").textContent = e.message;
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
    else if (step.id === "business") showStep(3);
    else if (step.id === "icp") {
      await saveBrief();
      showStep(4);
      await loadReview();
    }
    else if (step.id === "review") await startPipeline();
    else if (step.id === "run") {
      showStep(STEPS.findIndex((s) => s.id === "results"));
      await loadResults();
    }
    else showStep(Math.min(stepIndex + 1, STEPS.length - 1));
  } catch (e) {
    alert(e.message);
  } finally {
    $("#btn-next").disabled = false;
  }
});

showStep(0);
loadSetup().catch(() => {});
