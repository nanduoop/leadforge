#!/usr/bin/env bash
# LeadForge setup. Checks first, changes nothing without --install.
#
#   ./setup.sh              report what is present and what is missing
#   ./setup.sh --install    install what is missing, no sudo
#
# Nothing here asks for a password or writes a credential to disk. Application auth
# happens through Composio in your own browser; the only key this project reads
# directly is BROWSERBASE_API_KEY, from your environment.
set -uo pipefail
cd "$(dirname "$0")"

INSTALL=0
[[ "${1:-}" == "--install" ]] && INSTALL=1

ok(){ printf "  ok    %s\n" "$1"; }
no(){ printf "  --    %s\n" "$1"; }
act(){ printf "        -> %s\n" "$1"; }

echo
echo "LeadForge setup"
echo "======================================================"

# ---------------------------------------------------------------- python + venv
echo
echo "Python"
PY=""
for c in /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12 python3.12 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    v=$("$c" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)
    if [[ -n "$v" ]] && [[ "${v%%.*}" -ge 3 ]] && [[ "${v#*.}" -ge 10 ]]; then PY="$c"; break; fi
  fi
done
if [[ -z "$PY" ]]; then
  no "python 3.10+ not found"
  act "install python 3.12, then rerun"
  exit 1
fi
ok "python $($PY -V 2>&1 | cut -d' ' -f2) at $PY"

if [[ -d .venv ]]; then
  ok "virtualenv .venv"
else
  no "virtualenv .venv"
  if [[ $INSTALL -eq 1 ]]; then
    "$PY" -m venv .venv && ok "created .venv"
  else
    act "./setup.sh --install"
  fi
fi

if [[ -d .venv ]]; then
  if .venv/bin/python -c "import stagehand, dns.resolver" 2>/dev/null; then
    ok "python deps (stagehand, dnspython)"
  else
    no "python deps"
    if [[ $INSTALL -eq 1 ]]; then
      .venv/bin/pip install -q --upgrade pip
      .venv/bin/pip install -q stagehand dnspython fastapi uvicorn pytest scrapling && ok "installed deps"
    else
      act "./setup.sh --install"
    fi
  fi
fi

# ------------------------------------------------------------------- composio
echo
echo "Composio  (one connection layer for every business app)"
COMPOSIO="$HOME/.composio/composio"
[[ -x "$COMPOSIO" ]] || COMPOSIO="$(command -v composio || true)"

# Headless path. No one can click through OAuth inside a container, so a cloud
# run inherits the connections a human already made: same Composio account,
# reached with an API key instead of a browser.
[[ -f .env ]] && . ./.env 2>/dev/null || true
if [[ -z "$COMPOSIO" ]] && [[ $INSTALL -eq 1 ]] && [[ -n "${COMPOSIO_API_KEY:-}" ]]; then
  no "composio CLI missing, installing for headless run"
  if command -v npm >/dev/null 2>&1; then
    npm install -g composio >/dev/null 2>&1 && COMPOSIO="$(command -v composio || true)"
  else
    curl -fsSL https://composio.dev/install.sh | bash >/dev/null 2>&1
    COMPOSIO="$HOME/.composio/composio"
    [[ -x "$COMPOSIO" ]] || COMPOSIO="$(command -v composio || true)"
  fi
fi
if [[ -n "$COMPOSIO" ]] && [[ -n "${COMPOSIO_API_KEY:-}" ]]; then
  if ! "$COMPOSIO" connections list >/dev/null 2>&1; then
    "$COMPOSIO" login --user-api-key "$COMPOSIO_API_KEY" --no-browser -y \
      >/dev/null 2>&1 && ok "composio authenticated from COMPOSIO_API_KEY"
  fi
fi

if [[ -n "$COMPOSIO" ]]; then
  ok "composio CLI"
  CONNS="$("$COMPOSIO" connections list 2>/dev/null || echo '{}')"
  check_app(){
    if echo "$CONNS" | grep -q "\"$1\"" && echo "$CONNS" | grep -A3 "\"$1\"" | grep -q ACTIVE; then
      ok "$1 connected"
    else
      no "$1 not connected"
      act "composio link $1"
    fi
  }
  echo
  echo "  required"
  check_app firecrawl
  echo
  echo "  recommended"
  check_app googlesheets
  echo
  echo "  optional"
  for a in neverbounce zerobounce hubspot; do check_app "$a"; done
else
  no "composio CLI not installed"
  act "npm install -g composio   (or: curl -fsSL https://composio.dev/install.sh | bash)"
  act "headless: set COMPOSIO_API_KEY in .env and rerun ./setup.sh --install"
fi

# ---------------------------------------------------------------- browserbase
echo
echo "Browserbase  (interactive browser, for JS-heavy and gated pages)"
if [[ -n "${BROWSERBASE_API_KEY:-}" ]]; then
  ok "BROWSERBASE_API_KEY in environment"
elif grep -q "BROWSERBASE_API_KEY" "$HOME/.zshrc" 2>/dev/null; then
  ok "BROWSERBASE_API_KEY in ~/.zshrc (read via the zshrc fallback)"
else
  no "BROWSERBASE_API_KEY not set"
  act "get a key at browserbase.com, then add to ~/.zshrc:"
  act 'export BROWSERBASE_API_KEY="bb_live_..."'
  act "optional. Firecrawl covers most pages without it."
fi

# ---------------------------------------------------------------- agent-reach
echo
echo "Agent Reach  (Instagram, Facebook, TikTok, LinkedIn social discovery)"
if command -v agent-reach >/dev/null 2>&1 || [[ -x "$HOME/.local/bin/agent-reach" ]]; then
  ok "agent-reach"
else
  no "agent-reach not installed (optional)"
  act "see vendors/manifest.json — https://github.com/Panniantong/Agent-Reach"
  act "pip install agent-reach   or   npm install -g agent-reach"
fi

# ---------------------------------------------------------------- scrapling
echo
echo "Scrapling  (bot-bypass page fetch, StealthyFetcher)"
if [[ -d .venv ]] && .venv/bin/python -c "from scrapling.fetchers import StealthyFetcher" 2>/dev/null; then
  ok "scrapling"
else
  no "scrapling not installed (optional, recommended)"
  act "see vendors/manifest.json — https://github.com/D4Vinci/Scrapling"
  act "pip install scrapling && scrapling install"
fi

# ---------------------------------------------------------------- config
echo
echo "Config"
[[ -f config/brief.json ]] && ok "config/brief.json" || {
  no "no brief yet"
  act "python3 src/intake.py --site <your-client-site>"
}
mkdir -p data logs

echo
echo "======================================================"
if [[ -d .venv ]] && [[ -n "$COMPOSIO" ]]; then
  echo "Ready. Start with:"
  echo "  ./run.sh --site acme.com"
else
  echo "Run ./setup.sh --install to fix what is missing."
fi
echo
