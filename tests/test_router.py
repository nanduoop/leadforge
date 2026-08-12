#!/usr/bin/env python3
"""
Unit tests for src/router.py (Deterministic source routing and fallback execution).
"""
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import router as R
from schema import SourceResult


def test_classify_url():
    assert R.classify_url("https://boards.greenhouse.io/acme/jobs/123") == "ats_board"
    assert R.classify_url("https://www.linkedin.com/company/acme") == "linkedin"
    assert R.classify_url("https://indeed.com/viewjob?id=1") == "aggregator"
    assert R.classify_url("https://twitter.com/acme") == "social"
    assert R.classify_url("https://techcrunch.com/2026/01/01/acme-raises") == "news"
    assert R.classify_url("https://acme.com/careers") == "company_careers"
    assert R.classify_url("https://acme.com/about") is None


def test_choose_web_search_firecrawl_available():
    task = R.Task("web_search", {"query": "test"})
    avail = {
        R.FIRECRAWL: True,
        R.BROWSER: False,
        R.AGENT_REACH: False,
        R.CLAY: False,
        R.COMPOSIO: False,
    }
    assert R.choose(task, avail) == R.FIRECRAWL


def test_choose_web_search_fallback_to_agent_reach():
    task = R.Task("web_search", {"query": "test"})
    avail = {
        R.FIRECRAWL: False,
        R.BROWSER: False,
        R.AGENT_REACH: True,
        R.CLAY: False,
        R.COMPOSIO: False,
    }
    assert R.choose(task, avail) == R.AGENT_REACH


def test_choose_interaction_forces_browser():
    task = R.Task("page_extract", {"url": "https://acme.com"}, requires_interaction=True)
    avail = {
        R.FIRECRAWL: True,
        R.BROWSER: True,
        R.AGENT_REACH: True,
        R.CLAY: False,
        R.COMPOSIO: False,
    }
    assert R.choose(task, avail) == R.BROWSER


def test_choose_none_available():
    task = R.Task("web_search", {"query": "test"})
    avail = {
        R.FIRECRAWL: False,
        R.BROWSER: False,
        R.AGENT_REACH: False,
        R.CLAY: False,
        R.COMPOSIO: False,
    }
    assert R.choose(task, avail) is None


def test_local_search_fallback_integration():
    import connectors as C
    res = C.local_search("video production agency Dubai", limit=5)
    assert isinstance(res, list)
    assert len(res) > 0
    assert "url" in res[0]
    assert res[0]["url"].startswith("http")


def test_browser_extract_does_not_crash_on_instantiation():
    import connectors as C
    if C.browser_available():
        res = C.browser_extract("https://example.com", "Extract heading")
        # Should return dict with ok boolean, not TypeError: Stagehand cannot be constructed directly
        assert "ok" in res
        assert "Stagehand cannot be constructed directly" not in str(res.get("error", ""))


def test_extract_fallback_when_firecrawl_uncredited():
    import connectors as C
    res = C.extract(["https://vimeo.com/about"], {"type": "object"}, "Extract company details")
    assert res is not None






def test_evidence_from_source_result():
    res = SourceResult(
        source=R.FIRECRAWL,
        status="success",
        items=[
            {"title": "Acme Hiring", "url": "https://greenhouse.io/acme", "description": "Hiring Video Editor"},
            {"title": "Acme News", "url": "https://techcrunch.com/acme", "description": "Acme raises series A"}
        ]
    )
    evidence_list = R.evidence_from(res, claim="Company is hiring")
    assert len(evidence_list) == 2
    assert evidence_list[0].source_type == "ats_board"
    assert evidence_list[1].source_type == "news"
    assert "Hiring Video Editor" in evidence_list[0].excerpt


def test_run_retries_and_fallback():
    # Test task where primary source fails but fallback or retry logic handles gracefully
    task = R.Task("web_search", {"query": "test"})
    avail = {R.FIRECRAWL: False, R.AGENT_REACH: False}
    result = R.run(task, avail=avail)
    assert not result.ok
    assert result.status == "error"
    assert "no source available" in result.error
