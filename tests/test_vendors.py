#!/usr/bin/env python3
"""Vendor pins: Agent Reach (social) and Scrapling (bot-bypass fetch).

These two repositories are pulled into LeadForge and used as the social and
anti-bot backends. Tests call the production functions, not a reimplementation.
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import connectors as C
import router as R
import vendors


def test_vendor_pins_name_the_two_required_repos():
    pins = vendors.pins()
    names = {p["name"] for p in pins}
    assert "agent-reach" in names
    assert "scrapling" in names
    reach = next(p for p in pins if p["name"] == "agent-reach")
    scrap = next(p for p in pins if p["name"] == "scrapling")
    assert "Panniantong" in reach["repo"] or "agent-reach" in reach["repo"].lower()
    assert "d4vinci" in scrap["repo"].lower() or "D4Vinci" in scrap["repo"]
    assert reach["role"] == "social"
    assert scrap["role"] == "bot_bypass"


def test_vendor_manifest_is_committed():
    path = ROOT / "vendors" / "manifest.json"
    assert path.exists(), "vendors/manifest.json must be in the repo so clients pull the same pins"
    data = json.loads(path.read_text())
    assert data["agent-reach"]["repo"].startswith("https://github.com/")
    assert data["scrapling"]["repo"].startswith("https://github.com/")


def test_available_reports_scrapling():
    caps = C.available()
    assert "scrapling" in caps
    assert isinstance(caps["scrapling"], bool)


def test_available_reports_agent_reach():
    caps = C.available()
    assert "agent_reach" in caps


def test_scrapling_is_preferred_page_fetch_when_installed():
    task = R.Task("page_extract", {"url": "https://example.com"})
    avail = {
        R.FIRECRAWL: True,
        R.BROWSER: False,
        R.AGENT_REACH: False,
        R.CLAY: False,
        R.COMPOSIO: False,
        R.SCRAPLING: True,
    }
    assert R.choose(task, avail) == R.SCRAPLING


def test_scrapling_fetch_uses_stealthy_fetcher(monkeypatch):
    fake_page = MagicMock()
    fake_page.body = "<html><body>Acme Corp team page Jane Doe CMO jane@acme.com</body></html>"
    fake_page.status = 200
    fake_page.url = "https://acme.com/team"

    fetch = MagicMock(return_value=fake_page)
    fake_cls = MagicMock()
    fake_cls.fetch = fetch
    fake_cls.adaptive = False

    import types
    fake_mod = types.SimpleNamespace(StealthyFetcher=fake_cls)
    monkeypatch.setitem(sys.modules, "scrapling.fetchers", fake_mod)

    result = C.scrapling_fetch("https://acme.com/team")
    assert result["ok"] is True
    assert result["source"] == "scrapling"
    assert "Acme Corp" in result["text"]
    fetch.assert_called()
    kwargs = fetch.call_args.kwargs
    assert kwargs.get("headless") is True


def test_scrapling_fetch_reports_missing_install():
    with patch.object(C, "_scrapling_available", return_value=False):
        result = C.scrapling_fetch("https://acme.com")
    assert result["ok"] is False
    assert "scrapling" in (result.get("error") or "").lower()


def test_agent_reach_social_dispatches_platform(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        class R:
            returncode = 0
            stdout = json.dumps({
                "results": [
                    {
                        "title": "Acme on Instagram",
                        "url": "https://instagram.com/acme",
                        "text": "hiring a CMO",
                    }
                ]
            })
            stderr = ""
        return R()

    monkeypatch.setattr(C.shutil, "which", lambda b: "/usr/local/bin/agent-reach")
    real_exists = C.os.path.exists

    def exists(p):
        if "agent-reach" in str(p):
            return True
        return real_exists(p)

    monkeypatch.setattr(C.os.path, "exists", exists)
    monkeypatch.setattr(C.subprocess, "run", fake_run)

    items = C.agent_reach_social("instagram", "video production agency", limit=5)
    assert items
    assert items[0]["url"].startswith("https://instagram.com/")
    assert items[0]["source"] == "agent_reach"
    assert items[0]["platform"] == "instagram"
    cmd = captured["cmd"]
    assert "instagram" in " ".join(cmd).lower() or any("instagram" in str(c).lower() for c in cmd)


def test_social_task_routes_to_agent_reach():
    task = R.Task("social", {"query": "roofing DFW", "platform": "tiktok"})
    avail = {
        R.FIRECRAWL: True,
        R.BROWSER: True,
        R.AGENT_REACH: True,
        R.CLAY: False,
        R.COMPOSIO: False,
        R.SCRAPLING: True,
    }
    assert R.choose(task, avail) == R.AGENT_REACH


def test_social_platforms_cover_instagram_facebook_tiktok():
    platforms = C.SOCIAL_PLATFORMS
    for p in ("instagram", "facebook", "tiktok"):
        assert p in platforms
