#!/usr/bin/env python3
"""
Integration tests for ui/server.py (FastAPI web endpoints).
"""
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ui"))
sys.path.insert(0, str(ROOT / "src"))

from server import app

client = TestClient(app)


def test_index_route():
    response = client.get("/")
    assert response.status_code == 200
    assert "LeadForge" in response.text


def test_api_setup():
    response = client.get("/api/setup")
    assert response.status_code == 200
    data = response.json()
    assert "checks" in data
    assert "ready" in data


def test_api_integrations():
    response = client.get("/api/integrations")
    assert response.status_code == 200
    data = response.json()
    assert "integrations" in data
    assert len(data["integrations"]) > 0


def test_api_intake_questions():
    response = client.get("/api/intake/questions")
    assert response.status_code == 200
    data = response.json()
    assert "questions" in data


def test_api_intake_brief():
    response = client.get("/api/intake/brief")
    assert response.status_code == 200
    data = response.json()
    assert "brief" in data
    assert "summary" in data


def test_api_pipeline_status():
    response = client.get("/api/pipeline/status")
    assert response.status_code == 200
    data = response.json()
    assert "overall" in data
    assert "stages" in data
    assert "summary" in data
    assert len(data["stages"]) == 8
    for stage in data["stages"]:
        assert "percent" in stage
        assert "hint" in stage


def test_no_model_picker_surface():
    """LeadForge never calls an LLM API directly, so it must not offer a model choice.

    Extraction happens inside Firecrawl and Browserbase. A picker here could only
    ever be decorative — the previous one wrote to a module global that no other
    code read, and listed a "Gemini 3.6 Flash" that does not exist.
    """
    assert client.get("/api/models").status_code == 404
    assert client.post("/api/model/select", json={"model": "auto"}).status_code == 404

    markup = (ROOT / "ui" / "static" / "index.html").read_text()
    assert "select-model" not in markup
    assert "flash" not in markup.lower()


def test_api_intake_save(monkeypatch, tmp_path):
    # Point the endpoint at a throwaway config dir. This test used to POST a real
    # `site`, which both fetched example.com over the network and overwrote the
    # operator's own config/brief.json every time the suite ran.
    (tmp_path / "config").mkdir()
    monkeypatch.setattr("server.ROOT", tmp_path)

    response = client.post("/api/intake/save", json={
        "text": "We sell video editing to SaaS",
        "answers": {
            "target_industries": "SaaS",
            "target_titles": "CMO",
            "target_markets": "US",
            "buying_signals": "hiring video editor",
        },
    })
    assert response.status_code == 200
    data = response.json()
    assert "summary" in data
    assert data["summary"]["offer"]
    assert (tmp_path / "config" / "brief.json").exists()


def test_api_intake_site_error(monkeypatch):
    def fake_from_site(site, brief):
        brief["meta"]["last_site_read"] = {
            "ok": False,
            "source": None,
            "error": "all fetch methods failed",
        }
        return brief

    monkeypatch.setattr("server.intake.from_site", fake_from_site)
    response = client.post("/api/intake/site", json={"site": "https://bad.example"})
    assert response.status_code == 502


def test_api_pipeline_reset():
    response = client.post("/api/pipeline/reset")
    assert response.status_code == 200
    assert response.json()["reset"] is True


def test_static_assets():
    for path in ("/static/app.js", "/static/styles.css"):
        response = client.get(path)
        assert response.status_code == 200


def test_stage_percent_helper():
    from server import stage_percent
    assert stage_percent({"progress": {"percent": 30}}, "running") == 30
    assert stage_percent({}, "completed") == 100


def test_api_leads():
    response = client.get("/api/leads")
    assert response.status_code == 200
    data = response.json()
    assert "stage" in data
    assert "count" in data
    assert "leads" in data


def test_api_leads_dashboard_missing():
    response = client.get("/api/leads/dashboard")
    assert response.status_code in (200, 404)
    if response.status_code == 200:
        assert "text/html" in response.headers.get("content-type", "")
