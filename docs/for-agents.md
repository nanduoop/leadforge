# For agents plugging this repo in

Clone LeadForge into Claude Code, ChatGPT, OpenCode, Codex, Antigravity, or any
other agent. `CLAUDE.md` points at `AGENTS.md`. This file is the extra contract
that those agents must follow after this release.

## What to deliver

The product is `data/dashboard.html` plus the CSV. That is what a client opens.

ChatGPT and any chat-only agent skip automation. Do not tell the person how to
drive a browser, OpenCLI, or a Composio OAuth dance beyond one `composio link`
command. Do not invent Playwright / Selenium / "open Titan" steps. Hand them
the dashboard.

## Never assume. Always ask, then confirm.

Required ICP fields stay empty until the person answers them:

1. What do you sell, in one sentence?
2. What result does a client get? A number beats an adjective.
3. Which industries are you targeting?
4. Which job titles actually sign off on buying this?
5. Which countries or regions?
6. What tells you a company needs you right now?

Site-derived copy is a draft, not user evidence. After they answer, read the
brief back and wait for an explicit yes. `src/confirm.py` enforces this:
`plan_run(brief, confirmed=False)` always refuses. Inventing a company, a
title, or a market to keep going is a bug.

## Connections — ask, do not guess

Run `python3 src/preflight.py --json`. Every failing check has a `fix` string.
Ask the person to connect what they have. Firecrawl is the only required paid
connection. The two vendors this repo pulls:

| Vendor | Repo | Role |
|---|---|---|
| Agent Reach | https://github.com/Panniantong/Agent-Reach | Instagram, Facebook, TikTok, LinkedIn, Reddit |
| Scrapling | https://github.com/D4Vinci/Scrapling | StealthyFetcher bot-bypass page fetch |

Pins live in `vendors/manifest.json`. Install them. Do not reimplement them.

```bash
./setup.sh --install
pip install scrapling && scrapling install
# Agent Reach: pip install agent-reach   or   npm install -g agent-reach
composio link firecrawl          # required
composio link googlesheets       # optional, CSV is always written
```

## Evidence

Nothing is trusted because a model said it. Every claim carries a URL, a source
type, a timestamp and an excerpt. Discovery never verifies its own claims.
