# PRD — AquaSentinel

*Source of truth: `docs/`. This PRD summarizes `docs/product-brief.md`,
`docs/landing-page/BUILD-SPEC.md`, and `docs/alert-rules-decisions.md`. If they disagree, the
decisions log wins, then the build spec, then the brief.*

## Problem
More than half of Pennsylvania's waterways hit unsafe fecal-bacteria (E. coli) levels for part of
the year. In older cities like Philadelphia, aging combined sewer systems overflow after heavy
rain and discharge raw sewage directly into the Delaware and Schuylkill, causing gastrointestinal,
respiratory, ear, eye, and skin illness. The core failure is not measurement — it is timing and
reach. An E. coli lab culture takes 18–24 hours, so by the time a result exists the water has
already changed, and there is no real-time public warning system. People kayak, swim, and let pets
wade the day after rain, unaware. That "day after rain" gap is what we attack.

There is a second failure underneath it: the confirmed-unsafe samples that would train a good
early-warning model are scarce, and a continuous proxy gauge co-located with a label station is
rarer still. Label scarcity is the binding constraint, not an afterthought.

## Goal
Ship a working prototype that estimates today's E. coli risk (Safe / Unsafe) for the Center City
tidal Delaware from live USGS gauge and NOAA rainfall data, publishes it on an agent-ready
dashboard and as standards-based FHIR to a stubbed public-health agency — demonstrated end to end
for OneAquaHealth IEEE Global Hackathon 2026 judges by Oct 4, 2026 (extended from the original
Sep 30). No direct-to-public alerting: see the scope decision below.

**Scope decision (2026-09-26):** a Subscription Agent and WhatsApp push notifications to the
public were cut, deliberately, not for time. Deciding to alert citizens about a public-health
risk, and actually doing it, is the public health agency's jurisdiction — not a hackathon
prototype's to claim unilaterally without the agency's buy-in. FHIR-to-RPHSA (agency-first,
year-round) stays the only notification channel; the public dashboard is unaffected.

Scope update 2026-10-02: the Advisory Reader Agent and the Sampling Coordinator Agent were cut
from the build (one day of build time left) and moved to Future directions in
`docs/product-brief.md`. The built contribution is the agent-ready publishing layer
(MCP/`/llms.txt`/JSON-LD); the demo shows an outside personal assistant (Meta's Muse) polling it
for readings.

## Non-Goals
- Predicting illness, or reporting a bacteria value the system does not have. Language is
  "estimate" and "flag elevated risk" only.
- A three-level rating. Two levels only: Safe / Unsafe at 235 CFU/100 mL. No "Caution".
- Legacy-mode extraction from third-party advisory sites (e.g. RiverCast). Designed, not built —
  its Terms of Use prohibit republication in modified form without written permission.
- WebMCP. A W3C community-group draft, browser-bound, unsuited to a background reading agent.
- Direct-to-public alerting of any kind (see the Goal section's scope decision), or handling real
  agency credentials. RPHSA is a fictional placeholder agency for this demo.
- A measured accuracy gain from the sampling loop. We claim the design and one simulated turn.
- Multi-city deployment, or a React/build-step front end.

## Users
- Primary user: Philadelphia-area residents who use the tidal Delaware — kayakers, rowers, dog
  walkers — who want a warning before they get in or near the water.
- Secondary users: a public-health agency (fictional placeholder "RPHSA" in this build) receiving
  the same signal as FHIR; OneAquaHealth hackathon judges; AI agents reading the published data.

## User Stories
- As a resident, I want to ask "is it safe today?" — by checking the dashboard myself, or by
  asking my own AI agent to call AquaSentinel's MCP tool or `/api/status` — and get a
  plain-language answer, so that I can decide whether to go on the water right now.
- As RPHSA, I want every change of water-safety status delivered as a FHIR Flag year-round, so
  that my surveillance system has a standards-based feed regardless of recreation season.
- As an AI agent, I want to call an MCP tool or `/api/status`, so that I can read the current risk
  tier reliably without scraping a page.
- As the project team, I want the model to direct confirmatory sampling where it is unsure, so
  that the label scarcity that limits the model today shrinks over time.

## Requirements
### Must-have (v1)
Build order is top to bottom; see `plan.md` for the cut line.
- **Model.** Tree ensemble (random forest) with an MLR baseline and a rules fallback, reporting
  precision/recall on the "unsafe" class against the 235 CFU/100 mL threshold, never R². The
  dataset's two regimes (pre-2021 without turbidity, post-2021 with it) were evaluated
  **separately** — but only the post-2021 model ships. Live scoring always runs on "today", and
  continuous turbidity has streamed since 2021-10-28, so the pre-2021 regime never applies to a
  live reading; it was also outperformed by the rainfall-only rules fallback (see below), so
  there was no case for shipping a second, weaker ML model. Decided 2026-09-25.
- **Real `pullReading()`.** Live proxies from USGS Penn's Landing gauge `01467200` (temp,
  specific conductance, DO, pH, turbidity) plus NOAA NCEI antecedent rainfall at `USW00013739`,
  scored by the model. The Safe/Unsafe tier is decided by deterministic code, not the model and
  not an agent. Run by a scheduled job at the top of every hour (not at startup); the dashboard only reads the
  stored readings (the public pull button and route were removed 2026-10-02 so no visitor can
  trigger live requests to the data sources).
- **Alert rules and gating (deterministic, fail-closed).** Per `docs/alert-rules-decisions.md`:
  newest gauge reading older than 2 hours → status "unavailable" (no message, never an
  all-clear); alert on change of state only; all-clear only after 48 continuous hours of Safe,
  with any Unsafe reading restarting the clock; RPHSA receives every Flag change year-round.
- **Standards layer.** An OAH IG (HL7-EU, R4) Indicators Observation (`subject` = Location,
  `status` = preliminary, `method` = estimated, `derivedFrom` = the proxy Observations) and a
  `Flag` on threshold crossings (active → inactive for in-effect → all-clear), delivered over a
  FHIR `Subscription` to the stubbed RPHSA system. This is the only notification channel that
  exists — agency-first, by design (see the Goal section's scope decision).
- **Agent-ready layer (the built contribution).** `GET /api/status` returning the
  contract; a **read-only** MCP server (`list_monitored_locations`, `get_current_status`,
  `get_recent_readings` — no write tools); `/llms.txt`; and schema.org `Dataset` JSON-LD on the
  page. All generated from one scoring output so they cannot disagree.
- **Consistency test.** One test asserts the MCP tool output, `/api/status`, and the rendered
  dashboard banner report the same tier and timestamp for the same reading.

### Nice-to-have (later)
- **CUT 2026-10-02, now Future directions — Sampling Coordinator Agent, one scripted turn:**
  low-confidence or high-risk signal → drafted agency request → returned lab result (a held-out
  historical DRBC result stands in) → deterministic label gate → retrain. Only the trigger
  protocol was designed (`docs/superpowers/specs/2026-10-02-sampling-trigger-protocol-design.md`).
- **CUT 2026-10-02, now Future directions — Advisory Reader Agent, native mode:** reading
  AquaSentinel through its own MCP server and emitting the normalized contract with provenance —
  demonstrates the reader pattern is designed to extend to other advisory sites, not just
  AquaSentinel's own. Spec and plan are kept in `docs/superpowers/`.
- CSO overflow rule: DONE 2026-10-01. Active overflow near the reach forces Unsafe and lowers
  confidence, naming the reason.
- Rainfall-forecast heads-up from the NWS gridpoint `quantitativePrecipitation`, shown separately
  and labeled as forecast-based. It never changes the current tier. Blocked on deriving
  threshold T.
- Legacy mode for third-party advisory sites, pending written reuse permission.

## Success Metrics
- The dashboard banner, `/api/status`, and the MCP server report an identical tier and timestamp
  for the same reading — the consistency test passes.
- A validated change of state to Unsafe produces a FHIR Flag at the RPHSA stub, year-round.
- A stale (>2h) or malformed input yields "status unavailable", no message and no all-clear —
  demonstrated live, not just asserted.
- Precision and recall on the "unsafe" class are reported for the shipped (post-2021) model
  honestly, with the dropped pre-2021 model's numbers kept on record as the reason it was cut.
- A 3–5 minute demo video runs the full script end to end (see `docs/product-brief.md`).

## Risks & Assumptions
- Risk: too few labeled samples — 30 unsafe rows pre-2021, 15 post-2021, is the binding
  constraint → Mitigation: tree ensemble + regression baseline + rules fallback; classification
  metrics not R²; framed as a transferable proof-of-concept; Pillar 2 is the long-term answer.
- Risk: CSOcast access or reuse terms do not work out → Mitigation: the CSO rule is the first
  thing cut; the rules fallback runs on rainfall alone.
- Risk: a reader agent (e.g. the Advisory Reader Agent) paraphrases health risk in its own words →
  Mitigation: fixed vocabulary around the published tier enforced at the code layer; agents never
  decide a tier.
- Risk: "agents for show" skepticism → Mitigation: each agent handles external, unstructured work
  a rule cannot; every decision is deterministic and visible; the agents never call each other.
- Risk: a bad lab result corrupts training data → Mitigation: the deterministic label gate (units,
  detection limits, sample time in window, location match, duplicates resolved to the maximum);
  the agent cannot write a label.
- Assumption: USGS `01467200` and NOAA `USW00013739` stay available at documented cadence (the
  gauge reports every 5 minutes, provisional) through the demo window.
- Assumption: values not yet decided — the low-confidence cutoff, forecast threshold T, and the
  CSO outfall set — stay clearly marked placeholders in config. We do not invent numbers.
- Assumption: proxies (USGS) and labels (DRBC) joined by date, not co-located — a deliberate,
  disclosed choice; the two label stations bracket the gauge.

## Open Questions
- CSOcast: is overflow status measured or modeled, how often does it update, is there a
  machine-readable feed, and do the City's reuse terms permit internal use?
- Low-confidence cutoff — to be derived from the trained model's validation results, not chosen.
- Forecast rain threshold T — to be derived from our own rainfall data.
- The CSO outfall set near Penn's Landing — needs research into PWD outfall locations and the
  tidal excursion on both sides of the reach.
- RiverCast: reconcile the 2007 paper's indicator (described as fecal coliform) against the site's
  current E. coli description before citing either.
