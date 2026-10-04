# Milestone 4 — FHIR out, with real Subscription mechanics

Status: implemented (see `plan.md`); kept as the design record. Source of truth for what "expand Milestone 4"
means, per the conversation on 2026-09-26. Supersedes nothing — `plan.md`'s milestone list and
`docs/alert-rules-decisions.md` still govern where this fits and what triggers it.

## Why this exists

`plan.md`'s Milestone 4 says only: "OAH IG Observation + `Flag` delivered over a FHIR
`Subscription` to the RPHSA stub. Done when a tier change produces a valid Flag at the stub
endpoint, year-round." `docs/product-brief.md` establishes intent (an OAH IG R4 Observation with
`subject`/`status`/`method`/`derivedFrom`, a contributed `Flag` profile, delivery via "FHIR
`Subscription`" to a fictional stubbed RPHSA) but leaves every implementation detail open — no
canonical URLs, no Subscription channel mechanics, no code system for the Flag's tier code.
`docs/landing-page/BUILD-SPEC.md` even points to a "separate FHIR notes" document that was never
written. Gouri asked to expand this milestone specifically toward **real FHIR Subscription
mechanics** (criteria, channel, status, handshake, notify-on-change) rather than a bare
"POST a Flag whenever something changes." This document is that design.

## Scope decisions already made (do not re-litigate without going back to the user)

1. **Subscription criteria matching is scoped to our one real topic** — Flag changes for
   `Location/penns-landing` — not a general-purpose FHIR search-criteria engine. Any other
   criteria is rejected, not silently ignored.
2. **RPHSA is a separate FastAPI process** (`app/rphsa_stub.py`), with its own port and its own
   SQLite file — a genuinely separate system connected only over HTTP, not a second route bolted
   onto `app/server.py`.
3. **Channel type is `rest-hook`**, with a handshake step: AquaSentinel POSTs a confirmation ping
   to RPHSA's endpoint right after a Subscription is created, and the Subscription only becomes
   `active` once that succeeds.

## Honesty notes (things invented or adapted, flagged rather than silently assumed)

- **Code system URIs are placeholders.** The OAH IG publishes no real canonical URLs anywhere in
  this repo. We mint our own, clearly namespaced: `https://aquasentinel.example/fhir/CodeSystem/
  risk-tier` (codes `safe` / `unsafe`), used for both the Observation's value and the Flag's code.
  This is the FHIR-content equivalent of `config.py`'s `TODO(decide)` markers — invented, but
  labeled as ours, not dressed up as an official reference.
- **FHIR resource ids can't contain underscores** (FHIR id syntax: `[A-Za-z0-9\-\.]{1,64}`), so
  the Location resource uses id `penns-landing` (hyphen) even though the internal Python constant
  is `LOCATION_ID = "penns_landing"` (underscore, `app/scoring/pull_reading.py`). Deliberate
  mapping, not an inconsistency to "fix."
- **The handshake is our own convention, not a formally-specified R4 payload.** Base FHIR R4's
  `Subscription` resource doesn't mandate a handshake structure (that concept is more developed
  in the newer R5/backport Topic-based Subscription framework, which this project does not claim
  to implement). We implement a lightweight webhook-verification ping — normal real-world
  practice for confirming a callback URL is live — and say so in code comments rather than
  overclaiming R4 conformance.

## Architecture

Three pieces:

1. **AquaSentinel's FHIR server** — a new `app/fhir/routes.py` (FastAPI `APIRouter`), mounted into
   `app/server.py` via `app.include_router(...)` rather than added inline — this is a big enough
   concern to warrant its own file, the same way `app/alerts/gating.py` stayed separate from
   `app/server.py` in Milestone 3. Exposes `POST /fhir/Subscription`. Internally builds
   `Observation` + `Flag` resources from each gated reading and delivers them.
2. **RPHSA stub** (`app/rphsa_stub.py`, new standalone FastAPI app, own port) — on startup, POSTs
   a `Subscription` to AquaSentinel declaring its own `POST /rphsa/notifications` as the rest-hook
   channel. Stores whatever arrives there in its own separate SQLite file (not shared with
   AquaSentinel's `aquasentinel.db`). Exposes `GET /rphsa/notifications` so received data can
   actually be inspected/demoed.
3. **Delivery path** — `gating.evaluate_reading()` already decides `agency_event`
   (`unsafe_onset` / `all_clear` / `None`) on every dashboard-triggered pull (`app/server.py`'s
   `/api/pull-reading`, per Milestone 3). When `agency_event` is not `None`, a new
   `app/fhir/emit.py` builds the FHIR resources, finds the one active Subscription, and POSTs the
   Bundle to its `channel.endpoint`.

Data flow: dashboard pull → `pull_reading()` → `gating.evaluate_reading()` → (if `agency_event`
set) → `fhir.emit.emit_event()` → build Bundle → look up active Subscription → POST to RPHSA →
RPHSA stores it.

## Resource shapes

**Location** (included in every delivered Bundle so `subject` references resolve without RPHSA
fetching anything back): id `penns-landing`, name "Penn's Landing, Center City tidal Delaware",
`position` using the real coordinates already in `docs/landing-page/index.html`
(lat 39.946402, lon -75.139360).

**Observation** — one per live proxy (`water_temp_c`, `sp_conductance_uscm`,
`dissolved_oxygen_mgl`, `ph`, `turbidity_fnu`) plus one for the risk-tier estimate itself:
- All: `subject: Location/penns-landing`, `effectiveDateTime` from the reading's `time`.
- Proxy Observations: `valueQuantity` (value + unit), no `method`.
- Risk-tier Observation: `status: preliminary`, `method: estimated`, `valueCodeableConcept`
  (the risk-tier code system, `safe`/`unsafe`), `derivedFrom` referencing the proxy Observations
  by their Bundle-internal `fullUrl` — matching product-brief.md's explicit
  `subject`/`status`/`method`/`derivedFrom` spec, not an addition.

**Flag** — `status: active | inactive`, `code` using the risk-tier system, `subject:
Location/penns-landing`, `period.start` set on `unsafe_onset`, `period.end` set on `all_clear`.
**One Flag resource per unsafe episode**: `unsafe_onset` creates a new Flag; a later `all_clear`
updates that *same* resource (sets `status: inactive`, `period.end`) rather than creating a new
one. This requires tracking "the current open Flag id for this location" in SQLite so `all_clear`
knows which resource to update.

**Subscription** (created by RPHSA, sent to AquaSentinel): `status`, `reason`,
`criteria: "Flag?subject=Location/penns-landing"` (exact string, validated), `channel.type:
rest-hook`, `channel.endpoint`, `channel.payload: application/fhir+json`.

**Delivery payload**: a FHIR `Bundle` (type `collection`) containing the Location + proxy
Observations + risk-tier Observation + Flag together, so `derivedFrom` resolves within the
bundle — full-content delivery, not a bare ping requiring a GET-back.

## Endpoints

**AquaSentinel:**
- `POST /fhir/Subscription` — validates `channel.type == "rest-hook"` and `criteria ==
  "Flag?subject=Location/penns-landing"` exactly; anything else → `400` + `OperationOutcome`.
  On acceptance: store as `status: requested`, POST the handshake ping to `channel.endpoint`.
  Success → `status: active`. Failure → `status: error` (no retry loop — out of scope for a
  hackathon stub).

**RPHSA stub:**
- `POST /rphsa/notifications` — accepts both the handshake ping and real event Bundles
  (distinguished by shape: a Bundle has `resourceType: "Bundle"`; the handshake ping does not).
  Stores whatever arrives with a timestamp and a `kind` (`handshake` / `event`).
- `GET /rphsa/notifications` — lists what's been received, for inspection/demo.
- On its own startup: POSTs its Subscription to AquaSentinel's `/fhir/Subscription`, pointing at
  its own `/rphsa/notifications` as the channel endpoint. AquaSentinel's base URL is configurable
  (env var, defaulting to `http://localhost:8000` for local dev — no production deploy exists,
  per `plan.md`'s Rollout section, so hardcoded localhost defaults are acceptable here).

**Internal trigger** (`app/fhir/emit.py`, called from `app/server.py`'s `/api/pull-reading`
handler, right after the existing `gating.evaluate_reading()` call from Milestone 3):
1. If `agency_event` is `None`, do nothing.
2. Otherwise build/update the Flag (create on `unsafe_onset`, update the same one on `all_clear`),
   build the proxy + risk Observations, assemble the Bundle.
3. Look up the one active Subscription for our topic. If none exists (RPHSA hasn't subscribed
   yet), this is a no-op — not an error. Real Subscription semantics: you only get notified about
   things after you've subscribed.
4. POST the Bundle to the Subscription's `channel.endpoint`.
5. **Resilience**: a failed delivery (RPHSA unreachable, non-2xx response) is logged, not raised.
   It must never break `/api/pull-reading`'s response to the dashboard — FHIR delivery is a
   downstream concern, decoupled from serving the live reading, matching the existing
   architecture rule that the broadcaster/FHIR paths only ever *read* an already-decided signal.

## Storage (new tables)

**AquaSentinel** (new module `app/fhir/store.py`, mirroring the existing `app/alerts/` package
pattern; own tables in the existing `aquasentinel.db`):
- `fhir_subscriptions`: id, status, criteria, channel_endpoint, created_at, updated_at.
- `fhir_flags`: id, location, status, period_start, period_end (nullable). Looked up by
  `WHERE location = ? AND status = 'active'` to find the current open Flag, if any.

**RPHSA stub** (its own separate SQLite file — genuinely separate state from AquaSentinel's DB,
not a second connection into the same file):
- `received_notifications`: id, received_at, kind, raw payload (stored as JSON text, for
  inspection).

## Testing plan

- Pure-function tests for resource building: Observation/Flag/Bundle shape correctness, given a
  reading dict and a gating decision.
- Subscription validation: accepts our exact criteria + `rest-hook`; rejects any other criteria,
  any other channel type, with the right `400` + `OperationOutcome` shape.
- Handshake state transition: `requested → active` on a successful ping, `requested → error` on a
  failed one (mocked).
- Flag lifecycle: `unsafe_onset` creates one Flag; a later `all_clear` updates that *same* id
  (not a new one); a subsequent `unsafe_onset` after that creates a genuinely new Flag id.
- End-to-end wiring: a gating decision with `agency_event` set results in a Bundle POSTed to a
  mocked Subscription endpoint, with the right resources inside it.
- RPHSA stub: a received Bundle is stored and retrievable via `GET /rphsa/notifications`; a
  handshake ping is recognized and stored distinctly from an event.
- Resilience: a failed FHIR delivery (mocked network error) does not surface as an error on
  `/api/pull-reading` — the dashboard still gets its normal 200 response.

## Open items for the implementation plan to resolve

- RPHSA stub's own run command (`plan.md`'s Run Commands section will need a second `uvicorn`
  line once this exists).
- Whether `AQUASENTINEL_BASE_URL` (or similar) belongs in `.env` / `.env.example`, per
  `SECURITY.md`'s existing conventions for configurable-but-not-secret values.
