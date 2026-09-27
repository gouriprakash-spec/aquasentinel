# Plan — AquaSentinel

*Source of truth: `docs/`. See `PRD.md` for what we are building and why; this file is how and in
what order. Build window closes Oct 4, 2026 (extended from the original Sep 30); judging Oct 5–15.*

## Overview
AquaSentinel is a virtual (soft) water-quality sensor for the Center City tidal Delaware — the
reach Philadelphia's existing RiverCast advisory cannot see. Because an E. coli lab culture takes
18–24 hours, we estimate present-day risk from real-time proxies (rainfall and antecedent rain,
turbidity, specific conductance, temperature, dissolved oxygen) and classify it Safe or Unsafe
against EPA's 235 CFU/100 mL single-sample limit. One scoring output feeds three surfaces: the
dashboard banner, `/api/status` plus a read-only MCP server, and FHIR resources sent to a stubbed
health agency. A Sampling Coordinator Agent requests confirmatory samples where the model is
unsure. The governing rule throughout: **agents read and draft, deterministic code decides.**

Now, because the data exist and are joined (330 labeled samples, built and reproducible in
`AquaSentinel-dataset/`), and because the hackathon build window closes Oct 4, 2026.

**Scope decision (2026-09-26): no direct-to-public alerting.** The original plan included a
Subscription Agent and WhatsApp alerts (public push notifications). That's cut, deliberately, not
for time: deciding to alert citizens about a public-health risk, and actually doing it, is the
public health agency's jurisdiction, not a hackathon prototype's to claim unilaterally without the
agency's buy-in. This is the same principle Milestone 4 already follows — FHIR delivery goes to
RPHSA first and always, year-round; the agency has the authority over what happens next, AquaSentinel
doesn't. The public dashboard stays exactly as it is (pull-based, anyone can already look it up);
what's gone is AquaSentinel proactively pushing interpreted health-risk messages to individuals.

## Milestones
Build top to bottom. If behind on Oct 1, **cut from the bottom, never the middle.**

1. **Model trained and honestly validated. DONE 2026-09-25.** Tree ensemble (random forest) plus
   an MLR baseline plus a rules fallback. The dataset's two regimes (pre-2021 without turbidity,
   post-2021 with it) were evaluated *separately*, but only the post-2021 model ships: live
   scoring always runs on "today," turbidity has streamed continuously since 2021-10-28, and the
   pre-2021 model (precision 0.346, recall 0.30) was outperformed by the rainfall-only rules
   fallback (precision 0.372, recall 0.653) — no case for shipping a second, weaker ML model.
   Shipped model: precision 0.615, recall 0.533 (5-fold CV, 60 rows, 15 unsafe). Rules fallback
   threshold: 2.5mm prior-48h rain (`app/config.py:RAIN_FALLBACK_THRESHOLD_MM`). Low-confidence
   cutoff still to be derived — not yet needed until the scoring job (milestone 2) calls the model.
2. **Real `pullReading()`. DONE 2026-09-25.** Live USGS `01467200` proxies + NWS `KPHL` station
   observations (switched from NOAA NCEI, which lags ~3 days - see `docs/landing-page/BUILD-SPEC.md`),
   scored by the shipped model, tier decided in deterministic code. Verified against real live
   APIs, not just tests: `app/scoring/pull_reading.py` returns a real reading with a real
   timestamp; the USGS "block 0 is empty" gotcha turned out to be "block 0 is stale, not empty" -
   fixed to pick the freshest block by timestamp, not position or method text. A single NWS
   request only reliably covers ~38-40h, which is why the model's rainfall features were cut
   further to `precip_mm` + `precip_prev_24h_mm` only (see milestone 1's model, now 8 features).
   Wired into the dashboard: `app/server.py` (FastAPI) serves `docs/landing-page/index.html` and
   `POST /api/pull-reading`/`GET /api/readings`; the page's `pullReading()` is real, not mocked,
   and readings persist in SQLite (`app/db.py`) so they survive a reload. Verified in a real
   browser (Playwright): live pull renders correctly, and a real USGS 503 (rate-limited during
   testing) correctly failed closed - no fake reading shown, error surfaced in the UI. 31 passing
   tests across `app/tests/test_usgs_ingestion.py`, `test_nws_ingestion.py`, `test_pull_reading.py`,
   `test_db.py`, `test_server.py`.
3. **Alert rules and gating.** Freshness (>2h → "unavailable"), change of state only, 48-hour
   all-clear, season gate (public May 1 – Oct 31; agency year-round), fail-closed throughout.
   Done when tests cover each rule and a deliberately stale input produces no message.
4. **FHIR out.** OAH IG Observation + `Flag` delivered over a FHIR `Subscription` to the RPHSA
   stub, with real Subscription mechanics (criteria, channel, handshake) - see
   `docs/superpowers/specs/2026-09-26-fhir-milestone4-design.md` for the full design. Done when
   a tier change produces a valid Flag at the stub endpoint, year-round.
5. **Agent-ready layer. DONE 2026-09-27** (first of three co-equal contributions — see
   `docs/product-brief.md`). `/api/status`, the read-only MCP server, `/llms.txt`, JSON-LD. Done
   when the consistency test passes — MCP, `/api/status`, and the banner agree on tier and
   timestamp for one reading.
6. **Advisory Reader Agent, native mode** (second of three co-equal contributions). Reads a
   source's agent-ready interface (AquaSentinel's own MCP server, as the reference native
   source) and normalizes the result with provenance, without ever remapping a source's rating
   onto AquaSentinel's own scale. In scope, not stretch — cut from the bottom (milestone 8) first
   if time runs short. Design spec:
   `docs/superpowers/specs/2026-09-27-advisory-reader-agent-design.md`; not yet built.
7. **Sampling Coordinator, one scripted turn** (third of three co-equal contributions). Low-confidence
   signal → drafted agency request → held-out DRBC result → label gate → retrain. Done when the
   turn runs end to end in the demo, with no claim of a measured accuracy gain. In scope, not
   stretch.
8. **Stretch, cut first.** CSO overflow rule (CSOcast access unverified); the forecast rain
   heads-up (threshold T not yet derived); and, only if time allows after milestones 6 and 7 are
   done, a WhatsApp notification to one fixed internal group (project team/stakeholders, e.g. for
   demo purposes) on a tier change. **This is not a reopening of the "no direct-to-public
   alerting" scope decision above** — the audience is internal only, never river users or the
   general public, and RPHSA's FHIR delivery (milestone 4) stays the only public-health
   notification channel. Confirmed with Gouri 2026-09-27.
9. **Oct 3–4 — reserved.** Demo video, public repo, submission text. Not build time.

## Technical Approach
- **Architecture:** four stages — SOURCES → READ → DECIDE → ACT. Agents sit only in READ (and in
  drafting within the sampling loop); a deterministic validator/gating layer owns DECIDE. See the
  diagrams in `docs/product-brief.md`. The agents never call each other; each consumes the same
  validated signal independently, so one failing cannot take the other down.
- **The contract** every component speaks:
  `{ location, time, risk tier, confidence, source, source_url, retrieved_at, evidence }`.
- **Key components:**
  - *Ingestion + scoring job* (scheduled): pulls USGS + NOAA, scores with the trained model,
    emits one scoring output.
  - *Validator + gating* (deterministic): schema, tier values, freshness, evidence check; then
    change of state, all-clear window, season, and recipient matching. Fails closed.
  - *Thin API server* (FastAPI): serves the static dashboard, `/api/status`, `/llms.txt`, and
    hosts the read-only MCP server.
  - *FHIR emitter*: Observation + Flag from the same scoring output, to the RPHSA stub.
  - *Sampling Coordinator Agent* + *label gate*: drafts and tracks; code decides what becomes a
    training label.
- **Data flow:** USGS + NOAA → scoring job → **one** scoring output → (a) dashboard banner and
  table, (b) `/api/status` + MCP + JSON-LD, (c) validator/gating → FHIR Flag to RPHSA, (d) on high
  risk *or* low confidence → sampling request. Nothing computes status twice.
- **Stack:** Python 3.11, FastAPI, SQLite, scikit-learn, pandas, httpx, the official MCP Python
  SDK. Front end stays framework-free — extend `docs/landing-page/index.html`, do not rewrite it.
  Ask before installing each package.
- **Config:** all thresholds in one config module, never inline. Undecided values (low-confidence
  cutoff, forecast threshold T, CSO outfall set) stay clearly marked placeholders.

## Testing Plan
- **Alert rules** — unit tests per rule: freshness (>2h → unavailable), change of state only,
  48-hour all-clear with an Unsafe reading restarting the clock, season gate (public May 1 –
  Oct 31, agency year-round).
- **Fail-closed path** — a stale, malformed, or schema-invalid input produces "status
  unavailable", no message, and never an all-clear.
- **Consistency test** (required by the Definition of Done) — MCP output, `/api/status`, and the
  dashboard banner report the same tier and timestamp for one reading.
- **Model validation** — precision/recall on the "unsafe" class against 235 CFU/100 mL, reported
  honestly for the shipped model rather than blended across the 2021 instrument change. Done, in
  `app/tests/test_model.py`.
- **MCP read-only** — assert no write tool is exposed.
- **USGS parsing** — a fixture with multiple `values` blocks (including an empty `values[0]`)
  parses to the barge-block reading.
- **Manual** — pull a live reading in the browser and confirm the banner, table, and
  `/api/status` agree; run one simulated event replay end to end.

## Risks
- Too few unsafe labels (15 in the shipped post-2021 model) → ensemble + regression baseline +
  rules fallback; report classification metrics; frame as a transferable proof-of-concept, not a
  production model. The pre-2021 model (30 unsafe rows) was evaluated and dropped as not good
  enough to ship (see milestone 1) rather than kept to look more thorough than it was.
- CSOcast unusable (access, cadence, or terms) → it is milestone 8 and cut first; rainfall-only
  fallback stands.
- Scope overrun near Oct 1 → cut from the bottom of the milestone list only. Oct 3–4 stay
  reserved for submission.
- Bad lab result entering training → deterministic label gate; the agent cannot write labels.
- Overclaiming → claim the design and one simulated loop turn, never a proven accuracy gain.

## Rollout
- No production deploy. This is a hackathon prototype: the app runs locally (or on one small
  host) with a stubbed RPHSA FHIR endpoint and a stubbed agency sampling inbox.
- No direct-to-public alerting exists or is planned (see the Overview's scope decision) — nothing
  to roll out on that front.
- The repo is made **public** at submission: no tokens, phone numbers, or personal contact
  details in any committed file. Credentials live only in `.env` on the server side.
- Delivery is the 3–5 minute demo video plus the public repo and submission text, Oct 3–4.

## Open Questions
- No server-side scheduled job exists yet. Milestone 3's gating logic is triggered only by
  the dashboard (on open, on click, and hourly while the tab stays open) - if nobody has the
  page open, no reading is pulled and no alert state updates, so a real Unsafe change or a
  pending all-clear could go unnoticed. This is fine for development/demo but must be replaced
  by a real scheduled job (plan.md's "Ingestion + scoring job (scheduled)") before milestone 4
  sends anything to a real agency.
- `app/alerts/gating.py`'s `evaluate_reading()` still computes a `public_event` field (season-gated
  separately from `agency_event`) alongside every decision, left over from before the no-public-
  alerting scope decision. It's inert - nothing reads it - but it's not deleted, since the M3
  gating tests already cover it correctly and ripping it out isn't needed for anything currently
  planned. Flagged here so it's a known, deliberate leftover, not silent dead code.
- Run commands are not yet set up — fill in install / dev / test / lint in `CLAUDE.md` once the
  project is scaffolded.
- `.env.example` still holds template placeholders; update it with the real variable names
  (model API key, any others) before use.
- CSOcast: measured or modeled, update rate, machine-readable feed, reuse terms.
- Low-confidence cutoff and forecast threshold T — both to be *derived*, not chosen.
- Which FHIR approach: a resource library or hand-built JSON validated against the OAH IG
  profiles.
- `app/mcp_server.py`'s `TransportSecuritySettings` allowlist only covers `testserver`/
  `localhost:8000`/`127.0.0.1:8000` (Milestone 5) — a deliberate gap, not an oversight, since
  no real deploy host is chosen yet (see the `deployment_target` decision). Once one is, add
  its real hostname to both `allowed_hosts` and `allowed_origins` and update this line to say
  it's done.
- `docs/landing-page/llms.txt`'s `TODO_GITHUB_REPO_URL` placeholder needs the real public repo
  URL before submission (the repo isn't public yet, so the real URL doesn't exist).
