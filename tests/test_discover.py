#!/usr/bin/env python3
"""
Unit tests for src/discover.py (Discovery strategy planning and query formulation).
"""
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import discover


def test_plan_with_empty_brief():
    brief = {"icp": {}}
    with pytest.raises(SystemExit):
        discover.plan(brief, cap=60)


def test_plan_generates_queries_for_all_paths():
    brief = {
        "icp": {
            "target_industries": ["B2B SaaS", "Fintech"],
            "target_titles": ["Head of Video", "Creative Director"],
            "target_markets": ["US", "Canada"],
            "buying_signals": ["hiring video editor"],
            "technologies": ["HubSpot"],
        }
    }
    queries = discover.plan(brief, cap=100)
    paths = {q[0] for q in queries}
    assert "direct" in paths
    assert "hiring" in paths
    assert "signal" in paths or "social" in paths or "semantic" in paths
    assert len(queries) <= 100


def test_plan_deduplicates_queries():
    brief = {
        "icp": {
            "target_industries": ["SaaS", "SaaS"],
            "target_titles": ["CMO", "CMO"],
            "target_markets": ["US"],
            "buying_signals": ["hiring"],
        }
    }
    queries = discover.plan(brief, cap=60)
    query_texts = [q[1] for q in queries]
    assert len(query_texts) == len(set(query_texts))
