# CLAUDE.md — AquaSentinel

## About This Project
- What it does: A virtual water-quality sensor that estimates E. coli risk (Safe / Unsafe) for the
  Center City tidal Delaware from live USGS gauge and NOAA rainfall data, publishes it on an
  agent-ready dashboard and as FHIR, and sends opt-in WhatsApp alerts to people who ask for them.
- Who it's for: Residents who use the river (kayakers, rowers, dog walkers) and a public-health
  agency (a fictional placeholder, "RPHSA", in this build). Judges of the OneAquaHealth IEEE
  Global Hackathon 2026 are the immediate audience.
- Status: Hackathon prototype. Build window ends **Sep 30, 2026**. Judging Oct 1-15.

## Read These First
All design documents live in `docs/`. Start with `docs/README.md`, which indexes them.
- `docs/product-brief.md` — what and why
- `docs/landing-page/BUILD-SPEC.md` — what to build and how (endpoints, rules, agents)
- `docs/landing-page/index.html` — the dashboard reference implementation (mocks marked `TODO(real)`)
- `docs/alert-rules-decisions.md` — exact alert rule values
- `AquaSentinel-dataset/DATA-DICTIONARY.md` — read before touching the CSVs

If documents disagree, the order of authority is: decisions log, then BUILD-SPEC, then brief.
If a conflict remains, stop and ask.

## Tech Stack
- Language: Python 3.11 (the dataset builder is already Python; the model needs scikit-learn)
- Framework: FastAPI for the API server; the official MCP Python SDK for the read-only MCP server
- Front end: the plain HTML/CSS/JS in `docs/landing-page/index.html`, served by FastAPI
- Database: SQLite (readings, subscriptions, alert state)
- Key dependencies: scikit-learn, pandas, httpx, the MCP Python SDK, a FHIR R4 resource library
  or hand-built JSON validated against the OAH IG profiles
- Stack approved by Gouri on Sep 23, 2026. Still ask before installing each package.
- Front end stays framework-free (no React, no build step): FastAPI serves the page as static
  files and the page polls `/api/status`. Extend `docs/landing-page/index.html`; do not rewrite it.

## Run Commands
- Install: `python3.11 -m venv venv && ./venv/bin/pip install -r requirements.txt`
- Train/evaluate the model: `./venv/bin/python -m app.model.train`
- Test: `./venv/bin/python -m pytest app/tests/`
- Dev server: `./venv/bin/uvicorn app.server:app --reload` (serves the dashboard at `/` and the
  API at `/api/pull-reading`, `/api/readings`)
- RPHSA stub (Milestone 4 demo, fictional agency receiver): `./venv/bin/uvicorn
  app.rphsa_stub:app --port 8001 --reload` (run alongside the main dev server so it can
  register its Subscription and receive FHIR notifications)
- Lint/typecheck: `[pending]` — not set up yet

## Build Order and Cut Line
Build top to bottom. If behind schedule on Sep 27, cut from the bottom, never the middle.
1. **Must work:** model (DONE Sep 25 - post-2021/turbidity model only, 8 features; the pre-2021
   model was evaluated and dropped, see `plan.md` milestone 1) -> real `pullReading()` (DONE Sep 25
   - USGS 01467200 + NWS KPHL, not NOAA NCEI, which lags too much for live use; wired into the
   dashboard via `app/server.py`, see `plan.md` milestone 2) -> alert rules -> FHIR Observation +
   Flag to the RPHSA stub -> WhatsApp alerts.
2. **The differentiator:** Subscription Agent (plain-language request -> read-back -> "YES" ->
   deterministic validation).
3. **Cheap and worth it:** read-only MCP server, `/api/status`, `/llms.txt`, JSON-LD.
4. **Show, don't fully build:** one scripted turn of the Sampling Coordinator loop.
5. **Stretch, cut first:** CSO overflow rule (CSOcast access unverified), forecast rain heads-up.
Sep 29-30 are reserved for the demo video, the public repo, and the submission text.

## Architecture Rules (non-negotiable)
- **Code decides, agents don't.** Tier, validity, change of state, recipients, and training
  labels are decided by deterministic code. Agents only parse, read, draft, and match.
- **Fail closed.** Missing, stale (gauge reading older than 2 hours), or malformed input ->
  "status unavailable", no message, never an all-clear.
- **Two levels only:** Safe / Unsafe at 235 CFU/100 mL. No Caution level.
- **The broadcaster (OpenClaw) only sends** an already-decided signal. It holds no health data,
  no FHIR path, and no agency credentials.
- **MCP server is read-only.** No tool may subscribe, alert, or change anything.
- **Honest language.** Say "estimate" and "flag elevated risk"; never "predict illness". Never
  show a bacteria value the system does not have.
- **USGS data gotcha:** each parameter's `values` array can hold several blocks. For temp,
  conductance, DO and pH the data were in the "ISM Test Bed (barge)" block, not `values[0]`.
  Select the non-empty block by method.
- **Values not yet decided** (low-confidence cutoff, forecast threshold T, CSO outfall set) live
  in config as clearly marked placeholders. Do not invent numbers for them.

## Core Operating Rules
### Never without explicit approval:
- Install packages, dependencies, or CLIs
- Delete files or folders
- Push, publish, or deploy to any remote
- Add features not in the request
- Send a real WhatsApp message to any number

### Always:
- State the next action in one sentence before executing
- Make the smallest change that solves the problem
- Ask when intent is ambiguous — do not guess
- Verify the result after each change and report back

### When things fail:
- Report the exact error and the likely cause
- Do not mask, swallow, or reroute around errors
- Propose one specific fix — not a menu

## Security
See `SECURITY.md` in this repo — those rules are non-negotiable and apply on top of anything below.
This repo will be **public**: no tokens, phone numbers, or personal contact details in any
committed file. WhatsApp credentials live only in `.env` on the server side.

## Coding Style
- Indentation: 4 spaces (Python), 2 spaces (HTML/CSS/JS)
- Naming: descriptive; snake_case in Python, camelCase in JS
- Comments: explain the WHY, not the WHAT
- Keep all rule thresholds in one config module, never inline

## Definition of Done
A task is done when:
1. Behavior matches the request and the docs in `docs/`
2. No console or terminal errors
3. Tests pass, including: the alert rules (freshness, change of state, 48h all-clear, season
   May 1 - Oct 31, agency year-round) and one test that the MCP output, `/api/status`, and the
   dashboard banner agree for the same reading

## Open Questions / Known Gaps
- WhatsApp Cloud API: confirm how long the access token lasts, and whether alert messages
  (sent outside the 24h reply window) need an approved template and how long approval takes.
- `.env.example` still has template placeholders; update it with the real variable names
  (WhatsApp token, phone number ID, webhook verify token, and any model API key) before use.
- `PRD.md` (requirements) and `plan.md` (milestones, testing, rollout) are populated from `docs/`
  as of Sep 25, 2026. `docs/` stays the source of truth; if they drift, update them from `docs/`.
- CSOcast: measured or modeled, update rate, machine-readable feed, and reuse terms all unverified.
