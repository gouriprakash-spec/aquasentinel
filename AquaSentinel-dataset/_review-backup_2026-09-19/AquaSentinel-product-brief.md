# AquaSentinel — Product Brief

*Working name. OneAquaHealth IEEE Global Hackathon 2026.*
*Tracks: Digital Health Standards + AI-Supported Assessment.*
*Status: registered on IEEE Devpost (Aug 27, 2026). Build window Sep 16–30.*

## One-line pitch

AquaSentinel is a virtual water-quality sensor whose "this water is unsafe today" signal drives two things at once: a standards-based public-health alert that reaches people and health systems, and an adaptive sampling request that tells an environmental agency where to collect the next confirmatory sample — so the same prediction both warns the public and makes itself smarter over time.

## The problem

More than half of Pennsylvania's waterways hit unsafe fecal-bacteria (E. coli) levels for part of the year. In older cities like Philadelphia, aging combined sewer systems overflow after heavy rain and discharge raw sewage directly into the Delaware and Schuylkill. Exposure causes gastrointestinal, respiratory, ear, eye, and skin illness.

The core failure is not measurement — it is timing and reach. An E. coli lab culture takes roughly 18–24 hours, so by the time a result exists the water has already changed. There is no real-time public warning system, so people kayak, swim, and let pets wade the day after rain, unaware. That "day after rain" gap is what we attack.

There is a second, quieter failure underneath the first: the samples that would train a good early-warning model are scarce, scattered across agencies, and clustered in a few study years. Any honest system here has to treat labeled data as the binding constraint — not an afterthought. AquaSentinel is designed around that reality rather than pretending it away.

## Why now — the gap we are closing

Predicting bacteria from real-time proxies is already proven, and we say so plainly. Philadelphia Water Department's RiverCast has forecast Schuylkill bacteria hourly (green/yellow/red) since 2005, using rainfall, flow, and turbidity calibrated against historical grab samples. We treat RiverCast as evidence the method works — not as something to reinvent. Its documented limits are precisely our opening:

- **Stale.** RiverCast's live relationships were fit to PWD bacteria and turbidity data from 1998–2000 and calibrated to EPA's 2002 bacteria guidance. Its program lead has said the method has not changed since 2005. The operating statistics are roughly 26 years old.
- **One short reach.** It covers only the non-tidal Schuylkill from Flat Rock Dam down to Fairmount Dam (the head of tide). It says nothing about the tidal Delaware — the contaminated corridor the problem is actually about.
- **No notification.** It is a webpage (phillyrivercast.org), not an alert. Nobody is warned; they have to go look.
- **Siloed and non-interoperable.** It publishes a color rating, not machine-readable data a health system, dashboard, or app can consume.
- **Indicator mismatch.** The published model estimates fecal coliform (a historical indicator), not E. coli directly, against today's EPA E. coli standard.
- **Passive recalibration only.** RiverCast is "periodically compared" to routine grab samples in the same reach — but that sampling is not directed by the model and stays in one stretch.

Two maps in the folder make the gap concrete: `RiverCast-coverage-map.png` (the one segment it covers) and `Contaminated-corridor-whitespace-map.png` (the region-wide whitespace it ignores — Delaware from Trenton to Chester, tidal Schuylkill, Pennypack Creek, Brandywine Creek).

RiverCast's model is published (JWRPM/ASCE 2007), so reproducing it as a citable baseline is legitimate — but "we reproduced RiverCast" is not novelty. Our contribution is what RiverCast is not: portable, interoperable, actionable, and self-improving.

## The architecture — one signal, two consumers

The whole system hinges on a deliberately simple contract. The virtual sensor emits a single, standardized signal:

> `{ location, time, estimated risk tier, confidence }`

Everything downstream consumes only that. This is what lets the two pillars be genuinely co-equal instead of tangled together: the notification layer and the agency-sampling loop are independent consumers of the same signal, and the signal is source-agnostic — it works whether it comes from the trained tidal-Delaware model, a reproduced RiverCast-style regression, or a plain rainfall/CSO rule as a fallback. It also de-risks the build: both pillars can be developed against the contract before the model is final.

```
                         ┌─────────────────────────────┐
   proxies ─────────────▶│      VIRTUAL SENSOR          │
   (rain+antecedent,     │  tree ensemble + MLR + rules │
    turbidity, temp,     │  → {location, time,          │
    conductance, DO)     │     risk tier, confidence}   │
                         └──────────────┬──────────────┘
                                        │  (the contract)
                     ┌──────────────────┴──────────────────┐
                     ▼                                      ▼
        ┌────────────────────────┐            ┌────────────────────────────┐
        │  PILLAR 1               │            │  PILLAR 2                   │
        │  PUBLIC NOTIFICATION    │            │  AGENCY-SAMPLING LOOP       │
        │  OAH Observation + Flag │            │  model-directed sampling    │
        │  → agency → WhatsApp     │            │  on high-risk OR low-conf   │
        │  (OpenClaw broadcaster) │            │  → lab result → new label   │
        └────────────────────────┘            └────────────────────────────┘
```

## The engine — a virtual (soft) sensor

Because a lab culture takes 18–24 hours, we estimate present-day risk from real-time proxies: rainfall and antecedent 24–48h rainfall, turbidity, specific conductance, temperature, and dissolved oxygen, plus event signals such as CSO discharge or an ingested forecast. (The tidal Penn's Landing gauge has no continuous streamflow before 2023, so rainfall carries the storm/CSO signal rather than flow.) The model classifies a location and time against EPA thresholds (126 CFU/100 mL geometric mean, 410 statistical threshold value, 235 single-sample) into a green/yellow/red tier.

**Where it runs — the tidal Delaware, RiverCast's blind spot.** We deliberately build the proof-of-concept where RiverCast cannot go. Near-shore E. coli labels exist at DRBC's Ben Franklin Bridge and Navy Yard stations (349 results across 2005–2025), and the USGS Penn's Landing gauge (01467200) streams continuous temperature, specific conductance, and dissolved oxygen back to 2007 and continuous turbidity since October 2021. That co-location of labels and proxy stream is what makes a trainable sensor possible here — and it is novel, because RiverCast is Schuylkill-and-non-tidal only. `PennsLanding-proxy-gauge-map.png` shows the setup.

**Model choice, honestly scoped.** Small sample size is the binding constraint — after joining, the modern turbidity-paired regime holds ~60 dates — so the headline model is a tree ensemble (random forest / gradient boosting) with a multiple-linear-regression baseline and a rules fallback. Deep learning is out — it needs hundreds to thousands of samples we do not have. This is backed by the literature: a systematic review of 53 freshwater-beach models found ~81% average accuracy, with plain regression making up 70% of them and advanced methods winning only marginally. We report classification metrics — precision and recall on the "unsafe" class against the EPA single-sample threshold — not R², and we validate the two data regimes (pre-2021 without turbidity, post-2021 with turbidity) separately, because the 2021 instrument change is a silent-bias trap.

The pitch framing is deliberate: the prediction step is conventional and literature-validated on purpose. The contribution is not a modeling breakthrough on a few dozen samples — it is everything the two pillars do with the prediction.

## Pillar 1 — Public notification (standards-based, delivered where people are)

Pillar 1 has two jobs: publish the prediction as standards-based data other systems can act on, and get a plain-language warning to actual people. These are different problems, and we solve them with different tools rather than forcing one standard to do both.

**The standards layer — conform to OneAquaHealth's own FHIR, then fill its gap.** OneAquaHealth publishes an official HL7-EU FHIR Implementation Guide (R4), and it is Observation-centric — it already represents water-quality indicators as FHIR `Observation`s. So we conform rather than invent: the sensor's predicted indicator is emitted as an **OAH Indicators Observation** (`subject` = the Location, `status` = preliminary, `method` = estimated, `derivedFrom` = the proxy Observations it was computed from). Modeling the prediction this way — a first-class, provenance-carrying *estimate* distinct from a lab measurement — is itself a data-quality contribution the IG has not yet addressed.

When predicted risk crosses the threshold, we raise a **FHIR `Flag`** (`subject` = the Location, `status` active→inactive to carry the in-effect→all-clear lifecycle, the green/yellow/red tier in its code). The IG defines *no* alert or notification resource — that gap is exactly our whitespace — so the Flag-based water-safety alert is a concrete artifact we can contribute upstream. This is the honest "why FHIR" answer: FHIR is the consortium's own standard, so emitting it is how AquaSentinel plugs into the OAH Decision Support System — not FHIR for its own sake.

**The delivery layer — two hops, and FHIR only where a FHIR system is listening.** The alert travels in two hops. Hop 1 is machine-to-machine: the Observation and Flag are delivered over a FHIR `Subscription` to a public-health surveillance agency's system — the real FHIR-speaking receiver. Hop 2 is the human last mile: the agency broadcasts a plain-language warning to citizens who have opted in, over the WhatsApp Business API. We deliberately do **not** wrap that citizen message in FHIR — a phone is not a FHIR endpoint, and forcing the standard where nothing consumes it would be interoperability theater. The same discipline governs Pillar 2's sampling task, which also stays off FHIR.

For the hackathon, the agency is a clearly-labeled fictional placeholder — the "Regional Public Health Surveillance Agency (RPHSA)" — standing in for the real body that would adopt this; its surveillance system is stubbed as the hop-1 receiver, and OpenClaw posts to a demo WhatsApp channel framed as "the last mile the agency would run." The demo runs on Meta's official Cloud API test number, so it uses the *same* API a real agency would, honestly.

**The health annotation.** The alert carries a literature-based risk note drawn from Wade et al. (2003): in freshwater, a one-log increase in E. coli was associated with a relative risk of about 2.12 for gastrointestinal illness. We use this to upgrade a "red" tier from a bare color into a health-meaningful statement — but honestly. That estimate's confidence interval crosses 1.0 (0.925–4.85), so it is not statistically significant on its own; the language is "flag elevated risk," never "predict illness," and we cross-reference the newer NEEAR studies and EPA's 2012 criteria. Wade is an annotation on the alert, not a training label.

**Gating and the trust boundary.** The gating logic — deciding whether to actually send — matters as much as the prediction: alerts fire on change-of-state only, decay over a 24–48h all-clear window, and respect location relevance and season, so people are warned when it counts and not desensitized by noise. Delivery uses **OpenClaw 2.0** strictly as a sandboxed outbound broadcaster: it reads an already-decided public "unsafe" signal and pushes it, holding no health data, no FHIR path, and only the demo's own messaging credential — never the agency's. Everything that decides or determines — the threshold/tier, the FHIR emission, the gating — stays deterministic and outside the agent. That boundary is a deliberate architectural feature, and it maps directly to the security criterion in the Digital Health Standards track.

## Pillar 2 — The agency-sampling loop (adaptive, model-directed)

The same signal drives a second, equal consumer aimed at the label-scarcity problem underneath everything. When the sensor predicts high risk **or** reports low confidence, it emits a task to an environmental agency: collect a physical confirmatory sample here, now. Roughly 18–24 hours later the lab result returns and becomes a new labeled training point — fed exactly where the model is weakest. Over time the system asks for samples where it is most uncertain, closing an active-learning loop that directly attacks the constraint that limits every model in this space.

This is also the more defensible public-health posture: a request for a confirmatory sample is decision support to an agency, not a public-health determination handed to citizens — a lower-liability claim than the notification pillar alone.

The differentiation from RiverCast is sharp and must be stated as such. RiverCast is passively "compared" against routine grab samples in a single reach; its sampling is not model-directed. Ours is model-directed and adaptive — sample on high-risk or low-confidence days, route the request as an actionable interoperable agency trigger, and feed the result back as a label. And a note on standards discipline: the "go collect water" task does not ride on FHIR, because it is not clinical — it uses a plain task/webhook or an environmental-data standard. The `confidence` field in the contract is what makes this pillar load-bearing — it is what tells the loop where to look.

**Hackathon honesty.** In a two-week build we can show the loop's architecture and simulate one full turn (uncertain prediction → sampling request → returned label → retrain). We cannot demonstrate the model measurably improving — that needs weeks of accumulated samples. We claim the design and the mechanism, not a proven accuracy gain. Judges should hear "we built and demonstrated the self-improving loop," not "we proved it made the model better."

## Why it is novel and defensible

The novelty is explicitly not "can we predict bacteria from rain" — that is settled, and RiverCast is our evidence. The contribution is making that prediction generalizable, interoperable, actionable for public health, and self-improving. RiverCast's own paper presents a site-specific fitted model; the method transfers but the coefficients are locked to one river and one calibration window. Each new location needs its own calibration labels — which is exactly what Pillar 2 generates. The two pillars reinforce each other: notification makes the prediction matter, and the sampling loop makes it portable to any city by manufacturing the local labels a new site would otherwise lack.

One-sentence version for judges: *RiverCast tells one stretch of one river it might be dirty. We make that warning interoperable, portable to any city, deliverable as a health alert — and self-improving, because the system samples where it is unsure.*

## Data — built, with honest caveats

The training dataset is built and reproducible (`AquaSentinel-dataset/`, with a documented builder script and a data dictionary). All values are real, pulled from public APIs — nothing is synthesized.

Confirmed and used:

- **Proxy stream:** USGS Penn's Landing gauge 01467200 — continuous temperature, specific conductance, dissolved oxygen, and pH since 2007; continuous turbidity since Oct 28, 2021. Via the USGS Water Data API.
- **E. coli labels (tidal Delaware):** DRBC stations at Ben Franklin Bridge and Navy Yard — 349 results, 2005–2025, ~16/year (a 2020 gap), via the EPA Water Quality Portal. This is the RiverCast blind spot and the reason the tidal-Delaware POC is trainable and novel.
- **Rainfall:** NOAA NCEI daily precipitation at Philadelphia International Airport, used to build antecedent-rain features.
- **Prior-art baseline (optional):** the published RiverCast model (JWRPM/ASCE 2007) as a citable recipe; nearby Schuylkill proxies (Norristown turbidity from 2012) are available if we add that baseline.

The joined result: **330 paired samples**, split into two regimes kept separate — pre-2021 (250 rows, 30 unsafe) with temperature/conductance/DO/pH/rainfall, and post-2021 (64 rows, 60 with turbidity, 16 unsafe) which adds turbidity. The combined-sewer mechanism is visible in the data: unsafe samples averaged 13.9 mm of prior-48h rain versus 3.9 mm for safe ones.

Caveats we state up front:

- **Small N is the constraint.** The binding limit is the positive (unsafe) count — 30 in the earlier regime, 16 in the modern one — not the row count. This is why the model is a tree ensemble plus a regression baseline, why we report classification metrics, and why the honest framing is "transferable proof-of-concept," not "data-hungry production model."
- **Two regimes, validated separately.** The 2021 turbidity instrument change means combining them naively would introduce silent bias.
- **Cross-agency join.** Proxies (USGS) and labels (DRBC) are matched by date, not co-located at a single instrument; the two stations bracket the gauge. A deliberate, disclosed choice.
- **Rainfall is a metro proxy.** The airport is ~7 miles from the stations and the upstream CSO outfalls, so it captures storm timing well and basin-specific intensity less well.
- **Minor label handling.** Non-detects are encoded as clearly-safe and flagged; same-day duplicates take the maximum (conservative for a safety label).

## Track and judging alignment

- **AI-Supported Assessment** — the virtual sensor: assessing recreation/ecosystem safety from monitoring data.
- **Digital Health Standards** — we conform to the official HL7-EU OneAquaHealth FHIR IG (R4) for the prediction Observation, and contribute the water-safety `Flag` alert profile the IG does not yet define; delivery is a FHIR `Subscription` to the agency system, with the sandboxed-broadcaster boundary speaking to the security criterion.
- **Impact and mission alignment** — built on real Philadelphia data, designed to plug into OneAquaHealth's City Dashboards / Decision Support System via its own FHIR standard, and portable to any city. The OAH Catalogue of Measures (D2.4) and its health-informatics reviewership suggest genuine consortium appetite for the FHIR angle.
- **Innovation, architecture, UX, scalability** — novelty is portability + interoperability + notification + the adaptive loop (not the prediction); the trust boundary is architecture quality; the WhatsApp last mile is UX; standards-based output is scalability.

## Scope for the two-week build (Sep 16–30)

Build for real:

- USGS + DRBC + NCEI ingestion and the paired tidal-Delaware training set (two regimes) — done.
- A first prediction model — tree ensemble with an MLR baseline and a rules fallback.
- The OAH-conformant Observation emitter, the `Flag` alert, and a FHIR `Subscription` to the stubbed agency (RPHSA) system.
- The WhatsApp last mile via OpenClaw on Meta's Cloud API test number, clearly labeled as the simulated agency channel.
- The agency-sampling trigger and one simulated full turn of the loop.
- A minimal citizen alert plus a map/dashboard view for the demo.

Acceptable to mock or stub:

- The agency's surveillance system (a stub FHIR endpoint stands in for RPHSA).
- Live production sensor integration beyond the confirmed gauges.
- Full multi-city deployment (show the portability design; demo on Philadelphia).
- A measured accuracy improvement from the loop (demonstrate the mechanism, not the gain).

## Demo script (3–5 min video)

Open on the WHYY problem and the "day after rain" gap. Show a real rainfall/CSO event in the historical tidal-Delaware data. Run AquaSentinel: proxies in, risk tier out, with a confidence value. Show the prediction published as an OAH Indicators Observation and, on threshold breach, a `Flag` delivered by FHIR Subscription to the (stubbed) RPHSA surveillance system — then RPHSA's plain-language warning arriving on a phone via WhatsApp, clearly labeled as the simulated agency last mile. Then show the same signal driving Pillar 2: a model-directed sampling request, and one simulated loop turn where the returned lab result becomes a new label. Close on portability to other cities and the one-sentence pitch.

## Risks and mitigations

- **Too few labeled samples** → tree ensemble + regression baseline + rules fallback; frame as a transferable method validated on available data; Pillar 2 is the long-term answer.
- **Prior-art skepticism** → lead with "prediction is proven (RiverCast is our evidence); interoperability, notification, and the adaptive loop are the contribution."
- **Two co-equal pillars split judges' attention** → the shared contract keeps them one coherent system, not two products; the demo shows a single signal driving both in one flow.
- **FHIR fit challenged** → we conform to the OAH IG's own Observation profile and address a Location (legal in R4), reserve FHIR for the machine-to-machine hop where a FHIR system actually consumes it, and keep the citizen message and the sampling task off FHIR by design.
- **"Just an AI agent" concern** → OpenClaw is a sandboxed broadcaster only; every decision and determination is deterministic and outside the agent.
- **Overclaiming the loop** → claim the design and one simulated turn, never a proven accuracy gain.
- **Impersonating an agency** → the agency is a clearly-labeled fictional placeholder; the last mile is explicitly a simulation of what the agency would run.

## Key dates

- **Registered:** Aug 27, 2026 (IEEE Devpost) — done, no action outstanding.
- **Build window:** Sep 16–30, 2026.
- **Judging:** Oct 1–15, 2026.
- **Winners announced:** Oct 24, 2026 (IEEE iGET Conference).
- **Prize:** $3,000+ total; top 3 receive a $1,000 IEEE Merit Award each.

## Submission checklist

- [ ] Track alignment statement (both tracks)
- [ ] Project description (problem + impact)
- [ ] Demo video (3–5 min)
- [ ] Public GitHub repository with code
- [ ] Working prototype / proof-of-concept (sensor + both pillars, one simulated loop turn)
