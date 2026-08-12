#!/usr/bin/env python3
"""
LeadForge web UI — step-by-step wizard over the existing pipeline.

    ./start-ui.sh
    # or: .venv/bin/python ui/server.py

Serves a local wizard at http://127.0.0.1:7842 that walks through setup,
intake, discovery preview, pipeline execution, and results.
"""
import json
import os
import sys
import threading
import traceback
from argparse import Namespace
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

import connectors as C  # noqa: E402
import intake  # noqa: E402
import preflight  # noqa: E402
from output import row_for, COLUMNS  # noqa: E402
from run import ARTIFACT, JOB, RUNNERS, STAGES, Job  # noqa: E402
from schema import load  # noqa: E402

UI = Path(__file__).resolve().parent
STATIC = UI / "static"

app = FastAPI(title="LeadForge", version="1.0.0")

_pipeline_lock = threading.Lock()
_pipeline_state = {
    "running": False,
    "error": None,
    "finished": False,
}

STAGE_LABELS = {
    "intake": "Build brief",
    "discover": "Discover companies",
    "dedup": "Deduplicate",
    "qualify": "Qualify against ICP",
    "contacts": "Find decision makers",
    "verify": "Verify contacts",
    "score": "Score leads",
    "export": "Export results",
}

STAGE_HINTS = {
    "intake": "Turning your answers into a structured search brief.",
    "discover": "Searching job boards, news, and the web across five independent paths.",
    "dedup": "Merging duplicate companies and counting corroborating sources.",
    "qualify": "Filtering to companies that match your ICP before we spend on contacts.",
    "contacts": "Scraping team pages to find decision makers with evidence.",
    "verify": "Running six independent checks — syntax, MX, domain, and more.",
    "score": "Scoring each lead on fit, intent, reachability, and timing.",
    "export": "Writing CSV locally and pushing to Google Sheets if connected.",
}


def stage_percent(stage_info, status):
    """Compute a 0–100 display percent for the UI."""
    prog = stage_info.get("progress") or {}
    if status == "completed":
        return 100
    if status == "failed":
        return prog.get("percent", 0)
    if status == "running":
        if prog.get("total") or prog.get("percent"):
            return prog.get("percent", 0)
        return 8
    return 0

INTEGRATIONS = [
    {
        "id": "firecrawl",
        "name": "Firecrawl",
        "required": True,
        "tier": "free",
        "description": "Web search and structured page extraction.",
        "link": "composio link firecrawl",
        "upgrade": "https://firecrawl.dev/pricing",
    },
    {
        "id": "googlesheets",
        "name": "Google Sheets",
        "required": False,
        "tier": "free",
        "description": "Export leads to a spreadsheet. CSV is always written locally.",
        "link": "composio link googlesheets",
        "upgrade": None,
    },
    {
        "id": "neverbounce",
        "name": "NeverBounce",
        "required": False,
        "tier": "optional",
        "description": "Email verification provider. Free checks run without it.",
        "link": "composio link neverbounce",
        "upgrade": "https://neverbounce.com/pricing",
    },
    {
        "id": "hubspot",
        "name": "HubSpot",
        "required": False,
        "tier": "optional",
        "description": "Push verified contacts to your CRM.",
        "link": "composio link hubspot",
        "upgrade": None,
    },
    {
        "id": "browserbase",
        "name": "Browserbase",
        "required": False,
        "tier": "free",
        "description": "JS-heavy team pages. Set BROWSERBASE_API_KEY in your environment.",
        "link": None,
        "upgrade": "https://browserbase.com/pricing",
    },
    {
        "id": "agent_reach",
        "name": "Agent Reach",
        "required": False,
        "tier": "free",
        "description": "Semantic and social discovery. Install: npm i -g agent-reach",
        "link": None,
        "upgrade": None,
    },
]


class IntakeSiteRequest(BaseModel):
    site: str


class IntakeSaveRequest(BaseModel):
    site: str | None = None
    text: str | None = None
    answers: dict = Field(default_factory=dict)
    reintake: bool = False


class PipelineStartRequest(BaseModel):
    limit: int = 60
    max_companies: int = 60
    min_confidence: int = 70
    min_priority: int = 0
    workers: int = 8
    resume: bool = False
    from_stage: str | None = None
    browser: bool = False


def _integration_status():
    caps = C.available()
    conns = caps.get("_connections", {})
    out = []
    for item in INTEGRATIONS:
        iid = item["id"]
        if iid == "browserbase":
            linked = caps.get("browserbase", False)
            status = "connected" if linked else "not_configured"
        elif iid == "agent_reach":
            linked = caps.get("agent_reach", False)
            status = "connected" if linked else "not_configured"
        else:
            raw = conns.get(iid, [])
            if "ACTIVE" in raw:
                status = "connected"
            elif raw:
                status = "expired"
            else:
                status = "not_connected"
        out.append({**item, "status": status})
    return out


def _job_view():
    job = Job(str(JOB))
    stages = []
    for name in STAGES:
        info = job.state.get("stages", {}).get(name, {})
        status = job.status_from_events(name)
        artifact = ARTIFACT.get(name, "")
        stages.append({
            "id": name,
            "label": STAGE_LABELS[name],
            "hint": STAGE_HINTS[name],
            "status": status,
            "percent": stage_percent(info, status),
            "progress": info.get("progress"),
            "artifact": os.path.relpath(artifact, ROOT) if artifact else None,
            "artifact_exists": os.path.exists(artifact) if artifact else False,
            "stats": {k: v for k, v in info.items()
                      if k not in ("status", "started_at", "finished_at", "failed_at", "progress")
                      and status == "completed"},
            "error": info.get("error") if status == "failed" else None,
        })
    events = job.state.get("events", [])[-40:]
    running = _pipeline_state["running"] or any(s["status"] == "running" for s in stages)
    overall = (
        "running" if running else
        "failed" if any(s["status"] == "failed" for s in stages) else
        "completed" if all(s["status"] == "completed" for s in stages) else
        "idle"
    )
    completed = sum(1 for s in stages if s["status"] == "completed")
    running_stage = next((s for s in stages if s["status"] == "running"), None)
    return {
        "overall": overall,
        "running": running,
        "pipeline_error": _pipeline_state.get("error"),
        "stages": stages,
        "events": events,
        "config": job.state.get("config", {}),
        "summary": {
            "completed_stages": completed,
            "total_stages": len(stages),
            "overall_percent": round(
                (completed / len(stages) * 100) if stages else 0
            ),
            "running_stage": running_stage["id"] if running_stage else None,
            "running_label": running_stage["label"] if running_stage else None,
            "running_message": (running_stage.get("progress") or {}).get("message")
            if running_stage else None,
        },
    }


def _run_pipeline(config: PipelineStartRequest):
    global _pipeline_state
    with _pipeline_lock:
        if _pipeline_state["running"]:
            return
        _pipeline_state = {"running": True, "error": None, "finished": False}

    args = Namespace(
        site=None,
        text=None,
        file=None,
        yes=True,
        resume=config.resume,
        from_stage=None,
        only=None,
        status=False,
        reintake=False,
        limit=config.limit,
        per_query=10,
        paths="",
        workers=config.workers,
        max_companies=config.max_companies,
        min_fit=35,
        min_confidence=config.min_confidence,
        min_priority=config.min_priority,
        browser=config.browser,
        no_paid=False,
        to="auto",
        title=None,
    )

    job = Job(str(JOB))
    if config.from_stage:
        if config.from_stage not in STAGES:
            raise ValueError(f"Unknown stage: {config.from_stage}")
        todo = STAGES[STAGES.index(config.from_stage):]
    elif config.resume:
        todo = [s for s in STAGES if not job.completed(s)]
    else:
        todo = list(STAGES)

    try:
        job.state["config"] = config.model_dump()
        job.emit("LEAD_GEN_STARTED", stages=todo, source="ui")
        job.reset_stages(todo)
        for stage in todo:
            if config.resume and job.completed(stage):
                continue
            job.start(stage)
            try:
                stats = RUNNERS[stage](job, args) or {}
                job.done(stage, **stats)
            except Exception as e:
                job.fail(stage, e)
                _pipeline_state["error"] = str(e)
                traceback.print_exc()
                return
        job.emit("LEAD_GEN_COMPLETED", source="ui")
        _pipeline_state["finished"] = True
    finally:
        _pipeline_state["running"] = False


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/setup")
def api_setup():
    return preflight.run_checks()


@app.get("/api/integrations")
def api_integrations():
    return {"integrations": _integration_status()}


@app.get("/api/intake/questions")
def api_intake_questions():
    return {"questions": intake.questions_for_ui()}


@app.get("/api/intake/brief")
def api_intake_brief():
    brief = intake.load_brief(str(ROOT / "config" / "brief.json"))
    return {"brief": brief, "summary": intake.brief_summary(brief)}


@app.post("/api/intake/site")
def api_intake_site(body: IntakeSiteRequest):
    if not C.is_linked("firecrawl"):
        raise HTTPException(400, "Firecrawl is not connected. Run: composio link firecrawl")
    path = str(ROOT / "config" / "brief.json")
    brief = intake.load_brief(path)
    brief = intake.from_site(body.site, brief)
    intake.save_brief(brief, path)
    summary = intake.brief_summary(brief)
    read = brief.get("meta", {}).get("last_site_read") or {}
    if not read.get("ok"):
        msg = read.get("error") or "Could not read the website."
        if "rate limit" in msg.lower():
            msg = (
                "Firecrawl rate limit hit. Wait about a minute and try again, "
                "or describe your business manually below."
            )
        raise HTTPException(502, msg)
    return {
        "ok": True,
        "brief": brief,
        "summary": summary,
        "source": read.get("source"),
    }


@app.post("/api/intake/save")
def api_intake_save(body: IntakeSaveRequest):
    path = str(ROOT / "config" / "brief.json")
    brief = intake.blank_brief() if body.reintake else intake.load_brief(path)

    if body.text:
        brief = intake.from_text(body.text, brief)
    if body.site:
        if C.is_linked("firecrawl"):
            brief = intake.from_site(body.site, brief)
        else:
            brief["client"]["site"] = intake.normalise(body.site)
    brief = intake.fill_from_answers(brief, body.answers, overwrite=body.reintake)
    intake.save_brief(brief, path)
    return {"brief": brief, "summary": intake.brief_summary(brief)}


@app.get("/api/discover/preview")
def api_discover_preview(limit: int = 60):
    path = str(ROOT / "config" / "brief.json")
    if not os.path.exists(path):
        raise HTTPException(400, "No brief yet. Complete intake first.")
    brief = json.load(open(path))
    import discover
    plan = discover.plan(brief, cap=limit)
    by_path = {}
    for path_name, _query, _cap in plan:
        by_path[path_name] = by_path.get(path_name, 0) + 1
    return {
        "total_queries": len(plan),
        "estimated_credits": len(plan) * 2,
        "by_path": by_path,
        "sample": [{"path": p, "query": q} for p, q, _ in plan[:8]],
    }


@app.get("/api/pipeline/status")
def api_pipeline_status():
    return _job_view()


@app.post("/api/pipeline/start")
def api_pipeline_start(body: PipelineStartRequest):
    if _pipeline_state["running"]:
        raise HTTPException(409, "Pipeline is already running")
    checks = preflight.run_checks()
    if not checks["ready"]:
        raise HTTPException(400, "Setup checks failed. Fix blocking issues first.")
    if not os.path.exists(ARTIFACT["intake"]):
        raise HTTPException(400, "No brief found. Complete intake first.")
    threading.Thread(target=_run_pipeline, args=(body,), daemon=True).start()
    return {"started": True}


@app.get("/api/leads")
def api_leads():
    for stage_file in ("export", "score", "verified", "contacts", "qualified"):
        path = ARTIFACT.get(stage_file)
        if path and os.path.exists(path):
            leads = load(path)
            rows = [dict(zip(COLUMNS, row_for(l))) for l in leads]
            return {
                "stage": stage_file,
                "count": len(rows),
                "leads": rows,
            }
    return {"stage": None, "count": 0, "leads": []}


@app.get("/api/leads/csv")
def api_leads_csv():
    data_dir = ROOT / "data"
    csvs = sorted(data_dir.glob("leads-*.csv"), reverse=True)
    if not csvs:
        raise HTTPException(404, "No CSV export found yet")
    return FileResponse(csvs[0], filename=csvs[0].name, media_type="text/csv")


class RerunStageRequest(BaseModel):
    stage: str


# There is deliberately no model picker here. LeadForge never calls an LLM API
# directly: extraction happens inside Firecrawl (`jsonOptions`) and Browserbase,
# so nothing in this process has a model to choose. A dropdown that wrote to a
# module global nothing else read — offering a "Gemini 3.6 Flash" that does not
# exist — was worse than no control at all.


@app.post("/api/pipeline/stage/rerun")
def api_pipeline_stage_rerun(body: RerunStageRequest):
    if _pipeline_state["running"]:
        raise HTTPException(409, "Pipeline is currently running")
    if body.stage not in STAGES:
        raise HTTPException(400, f"Unknown stage: {body.stage}")
    config = PipelineStartRequest(from_stage=body.stage)
    threading.Thread(target=_run_pipeline, args=(config,), daemon=True).start()
    return {"started": True, "stage": body.stage}


@app.post("/api/pipeline/reset")
def api_pipeline_reset():
    if _pipeline_state["running"]:
        raise HTTPException(409, "Cannot reset while pipeline is running")
    job = Job(str(JOB))
    job.reset_stages(STAGES)
    job.emit("PIPELINE_RESET", source="ui")
    return {"reset": True}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def main():
    import uvicorn
    host = os.environ.get("LEADFORGE_HOST", "127.0.0.1")
    port = int(os.environ.get("LEADFORGE_PORT", "7842"))
    print(f"\nLeadForge UI  →  http://{host}:{port}\n")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
