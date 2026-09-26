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
  not a regulatory season. This also keeps a live demo during judging (Oct 1-15) in season.

## Still open (to be derived, not decided)
- Low-confidence cutoff: set from the trained model's validation results.
- Forecast rain threshold T: derive from our rainfall data.
- CSO outfall set near Penn's Landing: research PWD outfall locations and the tidal excursion.
