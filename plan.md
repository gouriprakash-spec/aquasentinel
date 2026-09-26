# Plan — AquaSentinel

*Source of truth: `docs/`. See `PRD.md` for what we are building and why; this file is how and in
what order. Build window closes Sep 30, 2026; judging Oct 1–15.*

## Overview
AquaSentinel is a virtual (soft) water-quality sensor for the Center City tidal Delaware — the
reach Philadelphia's existing RiverCast advisory cannot see. Because an E. coli lab culture takes
18–24 hours, we estimate present-day risk from real-time proxies (rainfall and antecedent rain,
turbidity, specific conductance, temperature, dissolved oxygen) and classify it Safe or Unsafe
against EPA's 235 CFU/100 mL single-sample limit. One scoring output feeds four surfaces: the
dashboard banner, `/api/status`, a read-only MCP server, and FHIR resources sent to a stubbed
health agency. A Subscription Agent turns plain-language requests ("warn me if it's unsafe to
kayak near Penn's Landing this weekend") into confirmed, validated subscriptions, and a Sampling
Coordinator Agent requests confirmatory samples where the model is unsure. The governing rule
throughout: **agents read and draft, deterministic code decides.**

Now, because the data exist and are joined (330 labeled samples, built and reproducible in
`AquaSentinel-dataset/`), and because the hackathon build window closes Sep 30, 2026.

## Milestones
Build top to bottom. If behind on Sep 27, **cut from the bottom, never the middle.**

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
   stub. Done when a tier change produces a valid Flag at the stub endpoint, year-round.
5. **WhatsApp alerts.** OpenClaw broadcasts an already-decided signal to matching subscriptions
   on Meta's Cloud API test number, with source attribution. Done when a change of state reaches
   a pre-registered test phone and no other.
6. **Subscription Agent** (the differentiator). Plain-language request → place resolved via MCP →
   read-back → "YES" → deterministic validation → stored. Done when an unmonitored place is
   declined with a link and "STOP" deletes immediately via code.
7. **Agent-ready layer** (cheap and worth it). `/api/status`, the read-only MCP server,
   `/llms.txt`, JSON-LD. Done when the consistency test passes — MCP, `/api/status`, and the
   banner agree on tier and timestamp for one reading.
8. **Sampling Coordinator, one scripted turn** (show, don't fully build). Low-confidence signal →
   drafted agency request → held-out DRBC result → label gate → retrain. Done when the turn runs
   end to end in the demo, with no claim of a measured accuracy gain.
9. **Stretch, cut first.** CSO overflow rule (CSOcast access unverified) and the forecast rain
   heads-up (threshold T not yet derived).
10. **Sep 29–30 — reserved.** Demo video, public repo, submission text. Not build time.

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
  - *Thin API server* (FastAPI): serves the static dashboard, `/api/status`, `/api/subscribe`,
    `/llms.txt`, and hosts the read-only MCP server.
  - *Subscription store* (SQLite): phone, location, window, alert-on. Structured only — never the
    free-text request.
  - *Subscription Agent*: parse, resolve via MCP, read back. Decides nothing.
  - *Broadcaster (OpenClaw)*: sandboxed sender. No health data, no FHIR path, no agency creds.
  - *FHIR emitter*: Observation + Flag from the same scoring output, to the RPHSA stub.
  - *Sampling Coordinator Agent* + *label gate*: drafts and tracks; code decides what becomes a
    training label.
- **Data flow:** USGS + NOAA → scoring job → **one** scoring output → (a) dashboard banner and
  table, (b) `/api/status` + MCP + JSON-LD, (c) validator/gating → FHIR Flag to RPHSA and
  WhatsApp to matching subscribers, (d) on high risk *or* low confidence → sampling request.
  Nothing computes status twice.
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
- **Subscription validator** — rejects non-E.164 numbers, unknown locations, invalid or
  over-length windows; "STOP" deletes; nothing is stored without an explicit "YES".
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
- WhatsApp token expiry or template approval delay → verify both in milestone 5, early; prefer a
  system-user token; if templates block alerts, demo the send path with the reply-window message.
- CSOcast unusable (access, cadence, or terms) → it is milestone 9 and cut first; rainfall-only
  fallback stands.
- Agent misparses a subscription → read-back + "YES" + deterministic validator; decline
  unmonitored places rather than approximate.
- Scope overrun near Sep 27 → cut from the bottom of the milestone list only. Sep 29–30 stay
  reserved for submission.
- Bad lab result entering training → deterministic label gate; the agent cannot write labels.
- Overclaiming → claim the design and one simulated loop turn, never a proven accuracy gain.

## Rollout
- No production deploy. This is a hackathon prototype: the app runs locally (or on one small
  host) with a stubbed RPHSA FHIR endpoint and a stubbed agency sampling inbox.
- WhatsApp runs on Meta's Cloud API **test number**, inbound limited to up to 5 pre-registered
  phones — which fits the demo. No real member of the public is messaged. Requires an HTTPS
  webhook with a valid (not self-signed) certificate.
- The repo is made **public** at submission: no tokens, phone numbers, or personal contact
  details in any committed file. Credentials live only in `.env` on the server side.
- Delivery is the 3–5 minute demo video plus the public repo and submission text, Sep 29–30.

## Open Questions
- No server-side scheduled job exists yet. Milestone 3's gating logic is triggered only by
  the dashboard (on open, on click, and hourly while the tab stays open) - if nobody has the
  page open, no reading is pulled and no alert state updates, so a real Unsafe change or a
  pending all-clear could go unnoticed. This is fine for development/demo but must be replaced
  by a real scheduled job (plan.md's "Ingestion + scoring job (scheduled)") before milestones
  4-5 send anything to a real agency or a real subscriber.
- Run commands are not yet set up — fill in install / dev / test / lint in `CLAUDE.md` once the
  project is scaffolded.
- `.env.example` still holds template placeholders; update it with the real variable names
  (WhatsApp token, phone number ID, webhook verify token, model API key) before use.
- WhatsApp token lifetime and whether out-of-window alerts need an approved template.
- CSOcast: measured or modeled, update rate, machine-readable feed, reuse terms.
- Low-confidence cutoff and forecast threshold T — both to be *derived*, not chosen.
- Which FHIR approach: a resource library or hand-built JSON validated against the OAH IG
  profiles.
