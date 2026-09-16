# LeadForge

Universal lead generation. You describe your business. The system finds who to
target, proves each lead is real, and delivers a scored dashboard with the
evidence attached.

It is not tied to one industry. Plug it into Claude, ChatGPT, Codex, OpenCode,
or any other agent. The brief drives the search.

```bash
./setup.sh --install
./start-ui.sh                  # guided wizard — asks, then confirms, then runs
./run.sh --site yourcompany.com
```

Public repo: https://github.com/nanduoop/leadforge

## For ChatGPT and chat-only agents

Skip browser automation. Do not invent Playwright, OpenCLI, or "click this"
steps. Ask the eight ICP questions, wait for confirmation, run the pipeline,
and hand over `data/dashboard.html`. That file is the product.

See `docs/for-agents.md`.

## Connections — asked, never assumed

The wizard and `src/preflight.py` list every integration and the exact command
to link it. Only Firecrawl is required.

This repo pulls two vendors (pinned in `vendors/manifest.json`):

| Vendor | Repo | Role |
|---|---|---|
| Agent Reach | https://github.com/Panniantong/Agent-Reach | Instagram, Facebook, TikTok, LinkedIn social discovery |
| Scrapling | https://github.com/D4Vinci/Scrapling | StealthyFetcher bot-bypass page fetch |

```bash
pip install scrapling && scrapling install
# Agent Reach: pip install agent-reach  or  npm install -g agent-reach
composio link firecrawl
```

## Web UI

LeadForge ships with a local step-by-step wizard for setup, intake, pipeline progress, and results.

```bash
./start-ui.sh
# opens http://127.0.0.1:7842
```

The wizard walks through:

1. **Connections** — Firecrawl, Sheets, NeverBounce, HubSpot, Agent Reach, Scrapling
2. **Your business** — reads your website and pre-fills a draft brief
3. **Target audience** — ICP questions. Empty until you answer. Nothing is assumed.
4. **Review** — read-back of the brief. You confirm before any credits are spent.
5. **Pipeline** — live progress across all eight stages
6. **Results** — scored leads table, CSV, and `data/dashboard.html`

Firecrawl is required. Google Sheets is optional (CSV and the HTML dashboard are
always written locally). Scrapling and Agent Reach unlock bot-walled pages and
social discovery when installed.

## What makes this different from a scraper

A commodity lead list gives you a company, an email and a phone number, and tells you
nothing about why you are calling. Every row here carries **why now**, the **evidence
behind it**, and **separate scores** for fit, intent and confidence, so you can sort
by buying intent and open a URL to check any claim yourself.

Two rules hold everywhere in the codebase:

**Nothing is trusted because a model said it.** Every claim carries evidence: the URL,
the source type, the timestamp and an excerpt. Confidence is computed from how many
*independent* sources agree, weighted by how hard each kind of source is to fake. Five
copies of one syndicated story count once, not five times.

**Discovery never verifies its own claims.** The component that finds a lead does not
get to rule on whether it is real. Verification reads the record fresh, because
anything asked to check its own work confirms it.

## Pipeline

```
intake -> discover -> dedup -> qualify -> contacts -> verify -> score -> export
```

Each stage writes to disk and emits an event. If verification fails at minute forty,
verification retries; discovery does not run again, because its results are already
saved and nothing about them changed.

```bash
./run.sh --resume              # continue where it stopped
./run.sh --from verify         # rerun one stage onward
./run.sh --status              # what happened last time
```

| Stage | Does |
|---|---|
| `intake.py` | Turns a website, a document or a sentence into a structured brief. Asks only about gaps that change the search |
| `discover.py` | Five parallel paths: hiring, signal, direct, social, semantic |
| `dedup.py` | Identity resolution before anything expensive. Unions evidence rather than discarding it |
| `qualify.py` | Cheap ICP gate, so only plausible companies reach the paid stages |
| `contacts.py` | Finds the decision maker on the company's own pages. Guessed addresses are labelled guesses |
| `verify.py` | Six independent checks. MX failure is disqualifying |
| `score.py` | Six dimensions, kept separate. One number hides why a lead is good |
| `output.py` | Google Sheets or CSV. A CSV is always written |

## Architecture

Composio is the connection layer for **business applications**. Everything else is
research infrastructure and is called directly, because forcing a browser or a crawler
to pretend it is an OAuth app makes the system worse, not simpler.

| Layer | Tool | Role |
|---|---|---|
| App auth | **Composio** | One link per app: Sheets, CRM, verification APIs. No key touches disk |
| Web extraction | **Firecrawl** | High-throughput search, scrape and structured extract |
| Interactive browser | **Browserbase** | JS-heavy pages, real logins, anything needing a click |
| Bot-bypass fetch | **Scrapling** | StealthyFetcher. Bot-walled pages Firecrawl cannot read |
| Specialist access | **Agent Reach** | Instagram, Facebook, TikTok, LinkedIn, Reddit |
| B2B records | **Clay** | Structured firmographics, when connected |

Tool choice is **deterministic**. `router.py` maps a task's properties to a source, so
the same task always routes the same way and a failure is reproducible. The model
decides *what information is needed*; the router decides *which infrastructure fetches
it*. Each capability has a fallback chain, so an unavailable source degrades instead
of ending the run.

Every source returns an explicit status: `success`, `partial`, `blocked`, `not_found`,
`rate_limited`, `invalid`, `error`. A failure never silently becomes an empty list,
because "blocked" and "no results" mean opposite things for what to do next.

## Setup

`./setup.sh` reports what is present and changes nothing. `./setup.sh --install` fixes
what is missing, without sudo.

Only Firecrawl is required:

```bash
composio link firecrawl
composio link googlesheets     # recommended, else CSV
composio link neverbounce      # optional, adds a provider verdict
```

Browserbase is optional and read from `BROWSERBASE_API_KEY` in your environment.
Firecrawl covers most pages without it.

### Credentials

No credential is ever written into this repo. `config/secrets.json` and `data/` are
gitignored, and application auth happens through Composio in your own browser.

`connectors.py` reads keys from the environment, then `config/secrets.json`, then
`~/.zshrc`. That last fallback is deliberate: a key exported in `.zshrc` is invisible
to cron and other non-interactive shells, which is exactly when unattended runs
happen.

## Cost

Firecrawl bills about 2 credits per search and 5 per structured extract. A default
60-query sweep is roughly 120 credits plus extraction on whatever qualifies. Preview
the plan and spend nothing:

```bash
python3 src/discover.py --dry-run
```

`qualify` runs before `contacts` on purpose. Filtering first means extraction only
ever touches companies worth paying for.

## Verification

Six checks, weighted by how hard each is to fake:

| Check | Weight | Notes |
|---|---:|---|
| syntax | 20 | Catches placeholders and role accounts |
| domain live | 20 | The company actually exists |
| MX | 25 | No MX means undeliverable. Disqualifying, not a deduction |
| provider | 25 | NeverBounce or ZeroBounce, when linked |
| pattern | 10 | Matches the company's own address convention |
| corroboration | 10 | Found independently more than once |

The placeholder list is explicit because every entry has been seen in real vendor
output. One provider returned `mp@xxx.com` as a verified address, and another resolved
a real domain to a test record named "FDC Fake Company". Both pass a syntax check and
a single API call, which is why a single API call is not enough.

Scoring is against what could actually be checked, so an unlinked provider lowers
certainty rather than silently capping every lead.

## Output

| Company | Domain | Contact | Title | Email | Priority | Fit | Intent | Confidence | Why Now | Evidence | Sources | Verified |
|---|---|---|---|---|---|---|---|---|---|---|---|---|

A local CSV is written on every run even when Sheets is the target, because the run is
expensive and the local copy survives an API failure.

## Known limits

- **Clay** is not wired in. Its MCP connection needs authorization, and its title
  filter silently returns unfiltered results, which once produced a contact list
  containing four VCs, a professor and a celebrity. Use it only after confirming the
  filter works.
- **Apollo** is paywalled at the bulk-export step and its connection is expired.
- **LinkedIn through Composio** is ad-targeting only. There is no people search, so
  LinkedIn data has to come through the browser layer.
- Pattern-derived emails are guesses by construction. They are labelled `inferred`,
  weighted 0.20, and depend on verification to survive.
