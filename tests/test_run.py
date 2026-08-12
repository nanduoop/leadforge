#!/usr/bin/env python3
"""
Unit tests for src/run.py (Job Controller state management and stage execution).
"""
import sys
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import run


def test_job_initialization_and_events(tmp_path):
    job_file = tmp_path / "job.json"
    job = run.Job(str(job_file))

    assert job.state["events"] == []
    job.emit("TEST_EVENT", detail="sample")

    assert job_file.exists()
    saved_state = json.load(open(job_file))
    assert len(saved_state["events"]) == 1
    assert saved_state["events"][0]["event"] == "TEST_EVENT"
    assert saved_state["events"][0]["detail"] == "sample"


def test_job_stage_lifecycle(tmp_path):
    job_file = tmp_path / "job.json"
    job = run.Job(str(job_file))

    job.start("intake")
    assert job.status_of("intake") == "running"
    assert job.status_from_events("intake") == "running"

    job.done("intake", gaps=0)
    assert job.status_of("intake") == "completed"
    assert job.status_from_events("intake") == "completed"

    job.start("discover")
    job.fail("discover", "API limit reached")
    assert job.status_of("discover") == "failed"
    assert job.status_from_events("discover") == "failed"
    assert "API limit reached" in job.state["stages"]["discover"]["error"]


def test_job_reset_stages(tmp_path):
    job_file = tmp_path / "job.json"
    job = run.Job(str(job_file))

    job.done("intake")
    job.done("discover")
    job.reset_stages(["discover", "dedup"])

    assert job.status_of("intake") == "completed"
    assert job.status_of("discover") == "pending"
    assert job.status_of("dedup") == "pending"


def test_job_start_clears_downstream(tmp_path):
    job_file = tmp_path / "job.json"
    job = run.Job(str(job_file))

    job.done("discover")
    job.done("dedup")
    job.start("discover")

    assert job.status_of("discover") == "running"
    assert job.status_of("dedup") == "pending"
    assert job.status_of("qualify") == "pending"


def test_status_from_events_after_failure(tmp_path):
    job_file = tmp_path / "job.json"
    job = run.Job(str(job_file))

    job.emit("LEAD_GEN_STARTED")
    job.start("discover")
    job.fail("discover", "rate limited")

    assert job.status_from_events("discover") == "failed"
