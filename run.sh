#!/usr/bin/env bash
# LeadForge: one command for a full run.
#   ./run.sh --site acme.com
#   ./run.sh --text "we sell X to Y"
#   ./run.sh --resume
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs data
LOG="logs/$(date +%Y-%m-%d-%H%M).log"
PY=".venv/bin/python"; [[ -x "$PY" ]] || PY="python3"
exec "$PY" src/run.py "$@" 2>&1 | tee "$LOG"
