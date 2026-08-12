# AGENTS.md

You are the interface to this repository. There is no app to hand someone — a person
clones LeadForge into whatever agent they already use (Claude Code, OpenCode,
Antigravity, Codex, a cloud runner) and talks to you. This file is the workflow you run.

Every agent tool reads this file by convention. `CLAUDE.md` points here. Do not
duplicate this into a second instructions file; edit this one.

---

## What LeadForge does

Someone describes their business. LeadForge derives a search strategy from that,
finds companies across five independent discovery paths, proves each lead is real with
evidence attached, scores it, and exports to CSV and Google Sheets.

It is industry-agnostic. The brief drives the search — no hardcoded verticals.

```
intake → discover → dedup → qualify → contacts → verify → score → export
```

Each stage writes an artifact to disk, so a run resumes across processes, machines and
days. State and the event log live in `data/job.json`; `src/run.py` owns both.

| Stage | Module | Artifact | Costs money |
|---|---|---|---|
| intake | `src/intake.py` | `config/brief.json` | no |
| discover | `src/discover.py` | `data/discovered.json` | **yes** — Firecrawl search |
| dedup | `src/dedup.py` | `data/deduped.json` | no |
| qualify | `src/qualify.py` | `data/qualified.json` | no |
| contacts | `src/contacts.py` | `data/contacts.json` | **yes** — Firecrawl scrape per company |
| verify | `src/verify.py` | `data/verified.json` | only if NeverBounce is linked |
| score | `src/score.py` | `data/scored.json` | no |
| export | `src/output.py` | `data/export.json` + CSV | no |

---

## Two invariants — do not break these

1. **Nothing is trusted because a model said it.** Every claim carries a URL, a source
   type, a timestamp and an excerpt. Confidence comes from how many *independent*
   sources agree; five syndicated copies of one press release count once.
2. **Discovery never verifies its own claims.** The stage that finds a lead does not
   rule on whether it is real. `verify.py` re-reads the record from scratch.

If someone asks you to "just get leads faster", do not satisfy it by generating company
names or contact details yourself. A plausible list with nothing behind it is the exact
failure this pipeline exists to prevent. Run fewer queries instead — `--limit 12`.

---

## The workflow

### 1. Find out where you are running

```bash
.venv/bin/python src/preflight.py --json
```

`ready: false` means the pipeline cannot run. Every failing check carries a `fix`
string — read it and act, do not guess. Two environments behave differently:

**A human's own machine.** They have already run `composio link firecrawl` in a
browser. Preflight passes. Go to step 2.

**Headless — container, CI, Codex, cloud runner.** Nobody can click through OAuth
here. You need `COMPOSIO_API_KEY` in the environment or `.env`; `./setup.sh --install`
then installs the Composio CLI and authenticates non-interactively, inheriting every
app the human already linked from their own browser. If the key is missing, stop and
ask for it — see `.env.example`. Do not attempt an OAuth flow.

### 2. Get a brief

A fresh clone has none: `config/brief.json` and `data/` are gitignored, deliberately,
because they hold a client's data. Build one without prompting:

```bash
.venv/bin/python src/run.py --only intake --site acme.com --yes
.venv/bin/python src/run.py --only intake --text "we sell X to Y" --yes
```

`--yes` never prompts, which is what you want — **you** hold the conversation with the
person, then pass the result in. Ask them, in their own words, in this order:

1. What do you sell, in one sentence?
2. What result does a client get? A number beats an adjective.
3. Which industries are you targeting?
4. Which job titles actually sign off on buying this?
5. Which countries or regions?
6. What tells you a company needs you *right now* — hiring, just raised, posting a lot?
7. Who should you never contact — competitors, agencies, recruiters?
8. Which client names can you point to publicly?

Items 1, 3, 4, 5 and 6 are load-bearing; the search is weak without them. If someone
gives you a website, read it first and come back with the gaps filled in as a draft
rather than asking all eight cold.

### 3. Show the cost, then ask

Discovery and contacts spend real Firecrawl credits. Before any live run:

```bash
.venv/bin/python src/run.py --plan --limit 12          # human-readable
.venv/bin/python src/run.py --plan --json --limit 12   # for you to parse
```

`--plan` spends nothing. It returns `total_queries`, `estimated_credits` and the
breakdown by discovery path.

Tell the person the query count and estimated credits, and **wait for a yes**. Never
start a first run at the default `--limit 60` somewhere new; start at 12 and widen once
the results look right.

### 4. Run

```bash
./run.sh --resume                            # full pipeline, skips finished stages
.venv/bin/python src/run.py --only contacts  # one stage against existing input
.venv/bin/python src/run.py --from score     # re-run a tail
```

Poll progress from another process — `run.sh` writes to `logs/` and streams:

```bash
.venv/bin/python src/run.py --status --json
```

That returns `next_stage`, `complete`, `failed`, and per-stage `artifact_exists`.
Report progress from it in plain language. Do not paste raw JSON at someone.

### 5. When a stage fails

The pipeline stops and records the error in `data/job.json`. Read it, fix the cause,
then re-run **that stage only** — never restart from `intake`, which would re-spend
everything already paid for.

```bash
.venv/bin/python src/run.py --status --json    # which stage, what error
.venv/bin/python src/run.py --only contacts    # after fixing
```

### 6. Deliver

`data/export.json` and a timestamped CSV in `data/`. Google Sheets too if
`googlesheets` is linked. Summarise what was found, how many leads carry verified
contacts, and what the evidence rests on — not just a row count.

---

## Working on the code

```bash
.venv/bin/python -m pytest tests/ -q     # 85 tests, all offline, no credits
```

Two rules when adding tests:

- Call the production function. Several tests here once re-implemented inline the very
  logic they claimed to cover, so they passed no matter what the source did.
- Before trusting a new test, break the code it covers and watch it fail.

`src/router.py` decides *which* tool fetches a given piece of information. It is
deterministic — same task plus same availability gives the same choice. Keep it that way.

Comments explain *why*, not *what*. Read `src/schema.py` or `src/verify.py` and match them.

---

## LeadForge does not call an LLM API

There is no `ANTHROPIC_API_KEY`, no `OPENAI_API_KEY`, no model parameter anywhere.
Structured extraction happens *inside* Firecrawl (`jsonOptions`) and
Browserbase/Stagehand; those services own the model choice.
`tests/test_server_ui.py::test_no_model_picker_surface` enforces this — an earlier UI
offered a "Gemini 3.6 Flash", which is not a real model, wired to a variable nothing read.

The only model that matters to this repo is the one running you.

---

## The local web UI is optional

`./start-ui.sh` serves a wizard at `http://127.0.0.1:7842`. It is a convenience for
someone sitting at their own machine, not the product, and it cannot run in a headless
environment. Everything it does is available through the CLI above. If you are working
in a cloud runner, ignore `ui/` entirely.
