# CLAUDE.md — AquaSentinel

## About This Project
- What it does: A virtual water-quality sensor that estimates E. coli risk (Safe / Unsafe) for the
  Center City tidal Delaware from live USGS gauge and NOAA rainfall data, publishes it on an
  agent-ready dashboard, and delivers it as standards-based FHIR to a stubbed public-health agency.
- Who it's for: Residents who use the river (kayakers, rowers, dog walkers), who can look up the
  public dashboard themselves, and a public-health agency (a fictional placeholder, "RPHSA", in
  this build), which receives every FHIR change year-round. Judges of the OneAquaHealth IEEE
  Global Hackathon 2026 are the immediate audience.
- **Scope decision (2026-09-26): no direct-to-public alerting.** A Subscription Agent + WhatsApp
  push notifications were cut — not for time, but because deciding to alert citizens about a
  public-health risk, and actually doing it, is the agency's jurisdiction, not a hackathon
  prototype's to claim without the agency's buy-in. FHIR-to-RPHSA stays the only notification
  channel, agency-first and year-round; the public dashboard is unaffected (still pull-based,
  still there for anyone to check themselves). See `plan.md`'s Overview for the full rationale.
- Status: Hackathon prototype. Build window ends **Oct 4, 2026** (extended from the original
  Sep 30). Judging Oct 5-15.

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
- Database: SQLite (readings, alert state, FHIR Subscriptions/Flags for the RPHSA delivery)
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
Build top to bottom. If behind schedule on Oct 1, cut from the bottom, never the middle.
1. **Must work:** model (DONE Sep 25 - post-2021/turbidity model only, 8 features; the pre-2021
   model was evaluated and dropped, see `plan.md` milestone 1) -> real `pullReading()` (DONE Sep 25
   - USGS 01467200 + NWS KPHL, not NOAA NCEI, which lags too much for live use; wired into the
   dashboard via `app/server.py`, see `plan.md` milestone 2) -> alert rules -> FHIR Observation +
   Flag to the RPHSA stub.
2. **Two co-equal contributions:** the read-only MCP server / `/api/status` / `/llms.txt` /
   JSON-LD agent-ready layer, and one scripted turn of the Sampling Coordinator loop.
3. **Stretch, cut first:** CSO overflow rule (CSOcast access unverified), forecast rain heads-up.
Oct 3-4 are reserved for the demo video, the public repo, and the submission text.

## Architecture Rules (non-negotiable)
- **Code decides, agents don't.** Tier, validity, change of state, recipients, and training
  labels are decided by deterministic code. Agents only parse, read, draft, and match.
- **Fail closed.** Missing, stale (gauge reading older than 2 hours), or malformed input ->
  "status unavailable", no message, never an all-clear.
- **Two levels only:** Safe / Unsafe at 235 CFU/100 mL. No Caution level.
- **No direct-to-public alerting.** AquaSentinel never pushes a health-risk message to an
  individual — that decision belongs to the agency. FHIR delivery to RPHSA is the only
  notification channel, agency-first and year-round.
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
committed file.

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
- `.env.example` still has template placeholders; update it with the real variable names
  (any model API key, plus `AQUASENTINEL_BASE_URL`/`RPHSA_BASE_URL` already added for Milestone 4)
  before use.
- `PRD.md` (requirements) and `plan.md` (milestones, testing, rollout) are populated from `docs/`
  as of Sep 25, 2026. `docs/` stays the source of truth; if they drift, update them from `docs/`.
- CSOcast: measured or modeled, update rate, machine-readable feed, and reuse terms all unverified.
