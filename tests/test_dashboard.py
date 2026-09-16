#!/usr/bin/env python3
"""HTML dashboard is the human output. ChatGPT and client-facing agents skip
browser automation and render this instead.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from schema import Lead, Evidence
import dashboard
import output
from score import why_now


def _lead():
    lead = Lead(company="Wattpad", domain="wattpad.com")
    lead.contact = {
        "name": "Sam Lee",
        "title": "Head of Content",
        "email": "sam@wattpad.com",
        "origin": "team_page",
        "email_origin": "published",
    }
    lead.scores = {"priority": 84, "fit": 90, "intent": 80, "confidence": 85}
    lead.verification = {"confidence": 90}
    lead.signals = ["hiring video editors"]
    lead.add_evidence(Evidence(
        claim="Company is hiring video editors",
        url="https://wattpad.com/careers",
        source_type="company_careers",
        excerpt="We're hiring a Senior Video Editor",
    ))
    lead.company_data = {"location": "Toronto"}
    return lead


def test_dashboard_renders_company_and_contact():
    html = dashboard.render_leads([_lead()], title="Client leads")
    assert "Wattpad" in html
    assert "Sam Lee" in html
    assert "sam@wattpad.com" in html
    assert "Head of Content" in html


def test_dashboard_links_every_evidence_url():
    html = dashboard.render_leads([_lead()], title="Client leads")
    assert 'href="https://wattpad.com/careers"' in html
    assert "Company is hiring video editors" in html


def test_dashboard_shows_missing_email_as_looked_not_blank():
    lead = _lead()
    lead.contact["email"] = ""
    html = dashboard.render_leads([lead], title="Client leads")
    assert "none published" in html.lower() or "not found" in html.lower()
    assert "Wattpad" in html


def test_dashboard_is_self_contained():
    html = dashboard.render_leads([_lead()], title="Client leads")
    assert "<script" not in html.lower()
    assert "cdn." not in html.lower()
    assert "fonts.googleapis" not in html.lower()


def test_to_dashboard_writes_file(tmp_path):
    path = tmp_path / "dashboard.html"
    out = dashboard.to_html([_lead()], str(path), title="Client leads")
    assert Path(out).exists()
    text = Path(out).read_text()
    assert "Wattpad" in text
    assert text.strip().startswith("<")


def test_output_always_writes_dashboard(monkeypatch, tmp_path):
    """CSV plus dashboard. No browser-automation instructions in the file."""
    leads = [_lead()]
    monkeypatch.setattr(output.C, "is_linked", lambda app: False)
    csv_path, dash_path = output.write_local(leads, dest_dir=str(tmp_path), title="Client leads")
    assert Path(csv_path).exists()
    assert Path(dash_path).exists()
    html = Path(dash_path).read_text()
    assert "Wattpad" in html
    assert "composio" not in html.lower()
    assert "opencli" not in html.lower()
    assert "browserbase" not in html.lower()
