#!/usr/bin/env python3
"""Pinned third-party repositories LeadForge actually uses.

Clients clone this repo and pull the same two vendors. Do not invent a third.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "vendors", "manifest.json")


def pins():
    data = json.load(open(MANIFEST))
    return [
        {
            "name": data["agent-reach"]["name"],
            "repo": data["agent-reach"]["repo"],
            "role": data["agent-reach"]["role"],
            "install": data["agent-reach"]["install"],
        },
        {
            "name": data["scrapling"]["name"],
            "repo": data["scrapling"]["repo"],
            "role": data["scrapling"]["role"],
            "install": data["scrapling"]["install"],
        },
    ]
