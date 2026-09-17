#!/usr/bin/env bash
#
# LeadForge One-Command Setup + Pipeline
# Run this from any directory:
#   curl -fsSL https://raw.githubusercontent.com/nanduoop/leadforge/main/onboarding.sh | bash
#
# It installs prerequisites, pulls the two pinned vendors, builds a brief from
# your answers, and runs the pipeline. Nothing is assumed.
set -uo pipefail

REPO_ROOT="/Users/madiwal/Documents/leadforge"
if [ ! -d "$REPO_ROOT" ]; then
  echo "Cloning LeadForge..."
  git clone https://github.com/nanduoop/leadforge "$REPO_ROOT"
fi
cd "$REPO_ROOT"

# --- prerequisites -----------------------------------------------------------
echo "Installing vendors..."
if command -v agent-reach >/dev/null 2>&1; then
  echo "  Agent Reach: already installed"
else
  echo "  Installing Agent Reach..."
  if command -v npm >/dev/null 2>&1; then
    npm install -g agent-reach 2>/dev/null || pip install -q agent-reach
  else
    pip install -q agent-reach
  fi
fi

if python3 -c "from scrapling.fetchers import StealthyFetcher" >/dev/null 2>&1; then
  echo "  Scrapling: already installed"
else
  echo "  Installing Scrapling..."
  pip install -q 'scrapling[fetchers]>=0.4.0' curl_cffi 2>/dev/null || pip install -q scrapling
fi

echo "Checking system dependencies..."
if [ ! -d ".venv" ] || ! .venv/bin/python -c "import stagehand, dns.resolver, fastapi" 2>/dev/null; then
  echo "  Running setup.sh --install..."
  ./setup.sh --install
else
  echo "  Dependencies: already installed"
fi

# --- brief -------------------------------------------------------------------
if [ ! -f "config/brief.json" ]; then
  echo ""
  echo "No brief found. Let me walk you through it..."
  echo ""
  echo "1. What does your business do? (one sentence)"
  read -r BUSINESS_DESCRIPTION
  echo ""
  echo "2. Who are your ideal customers?"
  echo "   Examples: roofing contractors, SaaS founders, medical practices"
  read -r TARGET_INDUSTRY
  echo ""
  echo "3. What job titles actually sign off on buying this?"
  echo "   Examples: Owner, CEO, Head of Marketing"
  read -r TARGET_TITLES
  echo ""
  echo "4. Which countries or regions?"
  echo "   Examples: US, Canada, UK, Dallas Fort Worth"
  read -r TARGET_MARKETS
  echo ""
  echo "5. What tells you a company needs you right now?"
  echo "   Examples: hiring, expanding, raised funding"
  read -r BUYING_SIGNALS
  echo ""
  echo "6. Who should we never contact?"
  echo "   Examples: recruitment agency, staffing agency"
  read -r EXCLUSIONS

  mkdir -p config
  cat > config/brief.json << EOF
{
  "client": {
    "name": "Your Client",
    "offer": "$BUSINESS_DESCRIPTION",
    "outcome": "We need to find ideal prospects quickly",
    "value_claim": "We deliver verified, scored leads with evidence attached"
  },
  "icp": {
    "target_industries": ["$(echo "$TARGET_INDUSTRY" | sed 's/,/", "/g')"],
    "target_titles": ["$(echo "$TARGET_TITLES" | sed 's/,/", "/g')"],
    "target_markets": ["$(echo "$TARGET_MARKETS" | sed 's/,/", "/g')"],
    "buying_signals": ["$(echo "$BUYING_SIGNALS" | sed 's/,/", "/g')"],
    "exclusions": ["$(echo "$EXCLUSIONS" | sed 's/,/", "/g')"]
  },
  "meta": {
    "source": ["agent_onboarding"],
    "gaps": [],
    "answered_by": {
      "offer": "user",
      "target_industries": "user",
      "target_titles": "user",
      "target_markets": "user",
      "buying_signals": "user"
    }
  }
}
EOF
  echo "Brief created."
else
  echo "Brief found. Checking for gaps..."
  GAPS=$(python3 -c "
import json
with open('config/brief.json') as f:
    brief = json.load(f)
icp = brief.get('icp', {})
gaps = []
for field in ['offer', 'target_industries', 'target_titles', 'target_markets', 'buying_signals']:
    val = icp.get(field, []) if isinstance(icp.get(field), list) else brief.get('client', {}).get(field, '')
    if not val or (isinstance(val, list) and not [x for x in val if str(x).strip()]):
        gaps.append(field)
print(','.join(gaps))
")
  if [ -n "$GAPS" ]; then
    echo "Gaps: $GAPS"
    echo ""
    echo "Fill in the missing fields:"
    for gap in $(echo "$GAPS" | tr ',' ' '); do
      echo ""
      echo "  What is your $gap?"
      read -r VALUE
      python3 -c "
import json
with open('config/brief.json') as f:
    brief = json.load(f)
if '$gap' in brief.get('icp', {}):
    brief['icp']['$gap'] = ['$(echo "$VALUE" | sed 's/,/", "/g')']
elif '$gap' in brief.get('client', {}):
    brief['client']['$gap'] = '$VALUE'
brief.setdefault('meta', {}).setdefault('answered_by', {})['$gap'] = 'user'
if '$gap' in brief.get('meta', {}).get('gaps', []):
    brief['meta']['gaps'].remove('$gap')
with open('config/brief.json', 'w') as f:
    json.dump(brief, f, indent=2)
"
      echo "  -> $gap set"
    done
  else
    echo "Brief complete."
  fi
fi

# --- run ---------------------------------------------------------------------
echo ""
echo "Running LeadForge pipeline..."
echo "  Available: $(python3 -c "
import sys; sys.path.insert(0, 'src')
import connectors as C
caps = C.available()
print(', '.join(k for k, v in caps.items() if v and not k.startswith('_')))
" 2>/dev/null || echo 'checking...')"

./run.sh --site yourcompany.com --limit 12

echo ""
echo "Pipeline complete!"
echo ""
echo "Results:"
CSV=$(find . -maxdepth 1 -name 'leads-*.csv' 2>/dev/null | head -1)
DASH=$(find . -name 'dashboard.html' 2>/dev/null | head -1)
[ -n "$CSV" ] && echo "  CSV: $CSV"
[ -n "$DASH" ] && echo "  Dashboard: $DASH"
echo ""
echo "Next steps:"
echo "  ./run.sh --resume         # Continue from where it left off"
echo "  ./start-ui.sh            # Interactive wizard with live progress"
echo ""