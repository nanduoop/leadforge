#!/usr/bin/env python3
"""
Unit tests for src/preflight.py (Preflight diagnostics and sanity checks).
"""
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import preflight


def test_run_checks_structure():
    res = preflight.run_checks()
    assert "checks" in res
    assert "ready" in res
    assert "blocking" in res
    assert "warnings" in res
    assert isinstance(res["checks"], list)
    assert len(res["checks"]) > 0

    for check_item in res["checks"]:
        assert "name" in check_item
        assert "status" in check_item
        assert check_item["status"] in (preflight.OK, preflight.WARN, preflight.FAIL)


def test_check_python_version():
    preflight.results.clear()
    preflight.c_python()
    assert len(preflight.results) == 1
    assert preflight.results[0][0] == "python"
    assert preflight.results[0][1] == preflight.OK


def test_check_writable():
    preflight.results.clear()
    preflight.c_writable()
    names = [r[0] for r in preflight.results]
    assert any("data/" in n for n in names)
    assert any("logs/" in n for n in names)
