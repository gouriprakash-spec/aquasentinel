# Sampling-request trigger protocol — design (Milestone 8, first decision set)

Status: designed, not built (Milestone 8 was cut 2026-10-02; see `docs/product-brief.md`'s
Future directions).

Date: 2026-10-02. Decisions made with Gouri one at a time. Scope: **when** the Sampling
Coordinator asks an agency for a confirmatory water sample. Not in scope here: the drafting
agent, reading lab results back, the label gate, and retraining (each is its own piece of
Milestone 8; see `docs/product-brief.md` Pillar 2).

## Purpose
A sampling request exists to get a real lab result where it is most useful: when the system
is unsure, and when it is flagging risk. Per the brief, the trigger is deterministic code, never
an agent's decision, and a request never changes a tier or reaches the public.

## Decisions

| # | Rule | Decided |
|---|---|---|
| R1 | **Only fresh readings can trigger (fail closed).** A reading older than `FRESHNESS_LIMIT_HOURS` (2h), malformed, or unavailable never triggers. A CSO field of "Reading unavailable" does not trigger on its own. | Follows the existing freshness rule |
| R2 | **Trigger = Unsafe OR low confidence.** Due when `risk_tier == "Unsafe"` or `confidence < LOW_CONFIDENCE_CUTOFF`. The request records which condition fired (one or both) and what decided the tier (`decision_basis`). | Gouri chose "both", as the brief says. Declined narrowing to low-confidence only. |
| R3 | **Rolling 24h cooldown, per location, no open-request tracking.** No new request while the last one is under `SAMPLING_COOLDOWN_HOURS` old. A trigger absorbed by the cooldown is logged, not silently dropped. | Gouri chose cooldown-only over "one open request at a time". 24h chosen over calendar-day. |
| R4 | **Window = request creation to +24h.** `window_start` is when the request is created (when the agency learns of it), not the gauge time. The future label gate accepts a result only if its sample time is inside the window. | Gouri chose +24h over +72h. |
| R5 | **Content.** Location, reason (tier, confidence, 48h rain, CSO value), window, and a reference to the OAH field-sampling protocol. A deterministic template for now; an agent may replace the drafting later without changing this protocol. | Proposed, approved |
| R6 | **Posture.** A request only asks an agency for a sample. It never changes a tier, never goes to the public, and stays off FHIR (brief, Pillar 2). In the demo it lands in a stub agency inbox. | Brief + approved |
| R7 | **Config.** `SAMPLING_COOLDOWN_HOURS = 24`, `SAMPLING_WINDOW_HOURS = 24`, in the config module. | Approved |

## Where it lives
One pure function, `should_request_sample(reading, last_request_time, now)`, plus a small
`sampling_requests` table (id, location, created_at, window_start, window_end, reasons,
decision_basis, tier, confidence, rain_48h_mm, cso, status). `status` is always `"requested"` in
this piece; later pieces (result matching, label gate) add their own values. Called from the shared
`_pull_and_publish` path in `app/server.py`, right after gating, so nothing computes status a
second time. Rejected alternative: a separate scheduled job rescanning readings (would compute
the same signals twice).

## Honesty notes
- **24h is a design choice, not an agency fact.** Both the cooldown and the window are anchored to
  the brief's "lab result 18–24 hours later"; no agency confirmed them. The config comments must
  say so.
- **Cooldown-only has two known limits, accepted:** (1) a rain event longer than a day produces a
  second request after 24h, which an agency would see as a repeat for one event; (2) a request that
  never gets a result just ages out, since no "open" state is tracked and nothing prompts follow-up.
- **Frequency vs. reality:** DRBC's near-shore stations produce ~16 results a year. Even with the
  cooldown, a persistently Unsafe or low-confidence stretch could ask more often than any agency
  samples. For the hackathon this is one scripted turn to a stub inbox, so it is a disclosed
  limit and not something this build solves.
- **Boundary:** a request is allowed once `now - last_request_time >= cooldown` (exactly 24h
  passes). Pinned by a test.

## Testing (what "done" means for this piece)
Unit tests on `should_request_sample`: Unsafe alone fires; low confidence alone fires; both
fire and record both reasons; neither does not fire; a stale reading never fires; a CSO value of
"Reading unavailable" alone does not fire; within-24h is suppressed and logged; exactly 24h is
allowed; the window equals creation to +24h. Pull-path tests: several pulls inside one hour
create exactly one request; a re-pull of the same gauge reading (the row-update path) does not
create a second; a failed pull creates none; a created request never changes the stored tier.

## Out of scope for this document
The label gate, result matching, retraining, any LLM drafting, and any agency-capacity cap beyond
the cooldown. Each needs its own decisions.
