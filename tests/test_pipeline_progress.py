#!/usr/bin/env python3
"""Tests for pipeline progress tracking and UI status derivation."""
import sys
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "ui"))

import run
from server import stage_percent, _job_view, STAGES


def test_job_progress_updates_percent(tmp_path):
    job_file = tmp_path / "job.json"
    job = run.Job(str(job_file))
    job.start("discover")
    job.progress("discover", 5, 10, message="halfway")

    info = job.state["stages"]["discover"]
    assert info["progress"]["current"] == 5
    assert info["progress"]["total"] == 10
    assert info["progress"]["percent"] == 50
    assert info["progress"]["message"] == "halfway"


def test_status_from_events_ignores_previous_run(tmp_path):
    """Stale completions from an earlier run must not show as completed."""
    job_file = tmp_path / "job.json"
    job = run.Job(str(job_file))

    job.emit("LEAD_GEN_STARTED", stages=run.STAGES)
    job.done("verify")
    job.done("score")
    job.done("export")

    job.emit("LEAD_GEN_STARTED", stages=run.STAGES)
    job.start("contacts")

    assert job.status_from_events("verify") == "pending"
    assert job.status_from_events("score") == "pending"
    assert job.status_from_events("export") == "pending"
    assert job.status_from_events("contacts") == "running"


def test_reset_clears_derived_status(tmp_path):
    """Reset must clear the derived view, not just the stages dict.

    Status is read from the event log, so clearing `stages` alone left every
    completion of the previous run showing green forever — the reset button
    emitted an event and changed nothing a user could see.
    """
    job_file = tmp_path / "job.json"
    job = run.Job(str(job_file))

    job.emit("LEAD_GEN_STARTED", stages=run.STAGES)
    job.done("score")
    job.done("export")
    assert job.status_from_events("export") == "completed"

    job.reset_stages(run.STAGES)
    job.emit("PIPELINE_RESET", source="ui")

    for stage in run.STAGES:
        assert job.status_from_events(stage) == "pending", stage


def test_reset_leaves_overall_idle(monkeypatch, tmp_path):
    """After a reset the run screen must not claim the pipeline finished."""
    job_file = tmp_path / "data" / "job.json"
    job_file.parent.mkdir(parents=True)
    job = run.Job(str(job_file))
    job.emit("LEAD_GEN_STARTED", stages=run.STAGES)
    job.done("score")
    job.done("export")
    job.reset_stages(run.STAGES)
    job.emit("PIPELINE_RESET", source="ui")

    monkeypatch.setattr("server.JOB", str(job_file))
    monkeypatch.setattr("server._pipeline_state", {"running": False, "error": None, "finished": False})

    view = _job_view()
    assert view["overall"] == "idle"
    assert view["summary"]["completed_stages"] == 0


def test_carried_over_stages_are_not_reported_pending(monkeypatch, tmp_path):
    """A partial re-run must not show reused upstream stages as pending."""
    job_file = tmp_path / "data" / "job.json"
    job_file.parent.mkdir(parents=True)
    job = run.Job(str(job_file))

    todo = ["score", "export"]
    job.emit("LEAD_GEN_STARTED", stages=todo)
    job.reset_stages(todo)
    for stage in run.STAGES:
        if stage not in todo:
            job.emit(f"{stage.upper()}_COMPLETED", reused=True)
    job.done("score")
    job.done("export")

    monkeypatch.setattr("server.JOB", str(job_file))
    monkeypatch.setattr("server._pipeline_state", {"running": False, "error": None, "finished": False})

    view = _job_view()
    assert view["overall"] == "completed"
    assert all(s["status"] == "completed" for s in view["stages"])


def test_stage_percent_completed_and_running():
    assert stage_percent({"progress": {"percent": 40}}, "completed") == 100
    assert stage_percent({"progress": {"percent": 40}}, "failed") == 40
    assert stage_percent({"progress": {"percent": 65, "total": 10}}, "running") == 65
    assert stage_percent({}, "running") == 8
    assert stage_percent({}, "pending") == 0


def test_job_view_includes_progress_fields(monkeypatch, tmp_path):
    job_file = tmp_path / "data" / "job.json"
    job_file.parent.mkdir(parents=True)
    job = run.Job(str(job_file))
    job.emit("LEAD_GEN_STARTED", stages=run.STAGES)
    job.start("discover")
    job.progress("discover", 3, 12, message="3 queries done")

    monkeypatch.setattr("server.JOB", str(job_file))
    monkeypatch.setattr("server._pipeline_state", {"running": True, "error": None, "finished": False})

    view = _job_view()
    discover = next(s for s in view["stages"] if s["id"] == "discover")
    assert discover["percent"] == 25
    assert discover["progress"]["message"] == "3 queries done"
    assert "summary" in view
    assert view["summary"]["running_stage"] == "discover"


def test_contacts_enrich_progress_callback():
    from contacts import enrich
    from schema import Lead

    leads = [Lead(company=f"Co{i}", domain=f"co{i}.com") for i in range(4)]
    icp = {"target_titles": ["CEO"]}
    calls = []

    def on_progress(current, total, found):
        calls.append((current, total, found))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("contacts.find_for", lambda *a, **k: [])
        enrich(leads, icp, workers=2, on_progress=on_progress)

    assert len(calls) == 4
    assert calls[-1][0] == 4
    assert calls[-1][1] == 4


def test_verify_leads_progress_callback():
    from verify import verify_leads
    from schema import Lead

    leads = [
        Lead(company="A", domain="a.com", contact={"email": "a@a.com", "name": "A"}),
        Lead(company="B", domain="b.com", contact={"email": "b@b.com", "name": "B"}),
    ]
    calls = []

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "verify.verify_one",
            lambda *a, **k: {"confidence": 80, "checks": {"syntax": {"pass": True}}},
        )
        verify_leads(leads, use_paid=False, workers=2, on_progress=lambda c, t, p: calls.append((c, t, p)))

    assert len(calls) == 2
    assert calls[-1] == (2, 2, 2)
