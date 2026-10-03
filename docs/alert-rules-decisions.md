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
- **Amended 2026-10-03:** the overflow no longer lowers confidence. The confidence figure is the
  rule/model agreement and must display exactly that; an overflow changes only the tier and the
  stated reason. (It had been overwritten with a fixed placeholder, 0.3, so a sampling request
  would trigger; that feature was cut, and the placeholder misled readers: on 2026-10-03 the
  dashboard showed "30%" next to an overflow-decided Unsafe whose real agreement was 86-91%.)
  The placeholder constant was removed, and stored rows carrying it are corrected at startup.

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
- **Amended 2026-10-03 (Gouri):** a missing or older-than-2-hours USGS reading no longer makes the
  whole status "unavailable". The gauge only feeds the model, and the model only produces the
  rule/model agreement; it never decides Safe/Unsafe (the rainfall rule and the sewer-overflow rule
  do). So such a pull still produces a row: the tier comes from rainfall + sewer overflow, the
  water-quality columns and the rule/model agreement read "n/a", and the row's time is the pull
  time. These gauge-less rows count normally for agency alerts, INCLUDING the 48-hour all-clear.
  The FHIR Bundle omits the water-quality Observations and says no model confidence exists.
  Still fail closed: missing rainfall data (no row, status "unavailable").
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
- **Amended 2026-10-03 (Gouri): a rainfall lower bound can decide Unsafe.** The NWS station leaves
  holes in its hourly record (29% of the hours in a live 3-day check; old holes stay missing), and
  one unresolved hour used to fail the whole NWS reading, so the pull fell back to Open-Meteo - which
  measured 0.0 mm over a window where the resolved NWS hours already showed 4.6 mm. A missing hour can
  only ADD rain, so when the hours that DID resolve in the two-previous-days window already reach
  the rule's threshold (>= 2.5 mm), Unsafe is certain and is decided from that known total, stored
  with the number of missing hours (`rainfall_missing_hours`) and shown as "at least" (`>=`; FHIR
  uses the `>=` comparator). Below the threshold nothing can be certified, so it falls back to
  Open-Meteo exactly as before. This path can only produce Unsafe, never a Safe. The model's other
  rain features still come from Open-Meteo in that case.

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
