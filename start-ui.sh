#!/usr/bin/env bash
# Start the LeadForge web wizard.
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -d .venv ]]; then
  echo "No .venv found. Run: ./setup.sh --install"
  exit 1
fi

if ! .venv/bin/python -c "import fastapi, uvicorn" 2>/dev/null; then
  echo "Installing UI dependencies…"
  .venv/bin/pip install -q fastapi uvicorn
fi

exec .venv/bin/python ui/server.py "$@"
