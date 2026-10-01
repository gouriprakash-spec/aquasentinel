# AquaSentinel alert rules — decisions log

Decisions made one at a time on Sep 23, 2026, and applied to `docs/product-brief.md` and
`docs/landing-page/BUILD-SPEC.md`.

## 1. Sewer overflow with a Safe model reading → Unsafe (two levels only)
- Decision: keep two levels, Safe and Unsafe. An active CSO overflow near the reach forces Unsafe
  and lowers confidence (which also triggers a sampling request). No "Caution" level.
- Guardrail: an overflow-driven Unsafe says why ("sewer overflow under way near Penn's Landing"),
  and never shows or implies a measured or modeled bacteria value it does not have.
- Consequence: the "improved to Caution" case in the flowchart goes away; any Unsafe → Safe
  change goes through the all-clear waiting window.

## 2. Off-season → agency always, public paused
- Decision: RPHSA receives every FHIR Flag change (active and inactive) year-round. Only public
  WhatsApp messages are paused outside recreation season. The dashboard updates year-round.
- **Superseded 2026-09-26:** public WhatsApp alerts were cut entirely (see `plan.md`'s Overview) —
  deciding to alert citizens about a public-health risk is the agency's jurisdiction, not this
  prototype's to claim without its buy-in. What survives from this decision: RPHSA still receives
  every FHIR Flag change year-round (that part was never seasonal to begin with), and the
  dashboard still updates year-round, unaffected either way.

## 3. All-clear waiting window → 48 hours
- Decision: after an Unsafe period, the all-clear (FHIR Flag inactive, public all-clear message)
  goes out only after 48 continuous hours of Safe. Any Unsafe reading restarts the clock.
- Reason: a false all-clear is the worst failure; matches the fail-closed design.

## 4. Gauge freshness limit → 2 hours
- Decision: if the newest USGS 01467200 reading is more than 2 hours old, the status is
  "unavailable" (no message, never an all-clear); the rainfall-only fallback may still run.
- Basis (checked Sep 23, 2026): the gauge reports every 5 minutes; the newest reading was about
  30 minutes old when fetched. Data are provisional.

## 5. Recreation season (public messages) → May 1 to October 31
- Decision: public WhatsApp alerts and all-clears go out May 1 through October 31. Outside it,
  only the agency is notified (decision 2).
- Basis: our own choice, to cover fall kayaking and rowing. Pennsylvania's rule (25 Pa. Code 93.7)
  defines the swimming season as May 1 to September 30; state October as an AquaSentinel choice,
  not a regulatory season. This also keeps a live demo during judging (Oct 5-15) in season.
- **Superseded 2026-09-26:** moot — there is no public alert channel left to season-gate (see
  decision 2's supersession). `app/alerts/gating.py` still computes a season-gated `public_event`
  field internally (harmless, unused — see `plan.md`'s Open Questions), so this decision's May 1 -
  Oct 31 dates remain accurate to what the code does, just not to anything that acts on it.

## 6. Model decides confidence, a rainfall rule decides the tier
- Decision: `app.model.rules_fallback.classify_by_rainfall()` (prior-48h rain >=
  `RAIN_FALLBACK_THRESHOLD_MM`) decides `risk_tier` in live scoring. A random forest retrained
  on Penn's Landing near-shore labels only sets `confidence` (high when it agrees with the
  rule, low when it doesn't) - it never decides the tier itself.
- Reason: date-grouped (honest) cross-validation showed the originally-shipped channel-station
  model carried no real out-of-sample skill (F1 dropped from 0.49 to 0.11 once dates weren't
  split across train and test) - the rainfall rule consistently outperformed it. A model
  retrained specifically on near-shore labels does show real skill, but on the full 69-row
  near-shore history (not a smaller turbidity-restricted subset) the rule still edges it out.
  Full analysis: `docs/superpowers/specs/2026-09-27-model-honesty-fix-milestone1b-design.md`.
- Basis: independently re-derived twice (once during the original leak review, once fresh
  during spec approval) with matching results both times.

## Still open (to be derived, not decided)
- ~~Low-confidence cutoff: set from the trained model's validation results.~~ Resolved
  2026-09-27 - see decision 6 and `app/config.py:LOW_CONFIDENCE_CUTOFF`.
- Forecast rain threshold T: derive from our rainfall data.
- ~~CSO outfall set near Penn's Landing: research PWD outfall locations and the tidal
  excursion.~~ Resolved 2026-10-01 - a 5km radius from Penn's Landing (not a hand-picked
  outfall list), chosen from real outfall density and a real observed live overflow event,
  not a published tidal-excursion figure (none was found). See
  `docs/superpowers/specs/2026-10-01-cso-overflow-rule-milestone6-design.md`'s Scope
  decisions and Honesty notes.
