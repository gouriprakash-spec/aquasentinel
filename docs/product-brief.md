# AquaSentinel — Product Brief

*Working name. OneAquaHealth IEEE Global Hackathon 2026.*
*Tracks: Digital Health Standards + AI-Supported Assessment.*
*Status: registered on IEEE Devpost (Aug 27, 2026). Build window Sep 16–30.*
*Revised Sep 23, 2026: notifications now run through an agent-ready dashboard (MCP server, `llms.txt`, JSON-LD) and a Subscription Agent that turns plain-language alert requests into confirmed subscriptions; the sampling loop is a separate Sampling Coordinator Agent on the same signal; third-party sources such as RiverCast are deferred to Future directions. Earlier versions are kept outside this repo.*

## One-line pitch

AquaSentinel is a virtual water-quality sensor with an agentic alert layer. The sensor estimates today's bacteria risk for the tidal Delaware and publishes it on a live dashboard. A subscription agent lets people ask for the alerts they need in plain language ("warn me if it's unsafe to kayak near Penn's Landing this weekend") and turns that into a confirmed subscription, while the same signal reaches health systems as standards-based FHIR. A reader agent is designed to extend the alert layer to other water-advisory sites later. A second agent uses the same signal to request confirmatory samples where the model is unsure, so the system both warns the public and gets smarter over time.

## The problem

More than half of Pennsylvania's waterways hit unsafe fecal-bacteria (E. coli) levels for part of the year. In older cities like Philadelphia, aging combined sewer systems overflow after heavy rain and discharge raw sewage directly into the Delaware and Schuylkill. Exposure causes gastrointestinal, respiratory, ear, eye, and skin illness.

The core failure is not measurement — it is timing and reach. An E. coli lab culture takes roughly 18–24 hours, so by the time a result exists the water has already changed. There is no real-time public warning system, so people kayak, swim, and let pets wade the day after rain, unaware. That "day after rain" gap is what we attack.

There is a second, quieter failure underneath the first: the *positive* samples that would train a good early-warning model — confirmed unsafe days, and any labels at a site without its own monitoring history — are scarce, and a continuous proxy gauge co-located with a label station is rarer still. Any honest system here has to treat those positive and new-site labels as the binding constraint, not an afterthought. AquaSentinel is designed around that reality rather than pretending it away.

## Why now — the gap we are closing

Predicting bacteria from real-time proxies is already proven, and we say so plainly. Philadelphia Water Department's RiverCast has forecast Schuylkill bacteria hourly (green/yellow/red) since 2005, using rainfall, flow, and turbidity calibrated against historical grab samples. We treat RiverCast as evidence the method works — not as something to reinvent. Its documented limits are precisely our opening:

- **Stale.** RiverCast's live relationships were fit to PWD bacteria and turbidity data from 1998–2000 and calibrated to EPA's 2002 bacteria guidance. Its program lead has said the method has not changed since 2005. The operating statistics are roughly 26 years old.
- **One short reach.** It covers only the non-tidal Schuylkill from Flat Rock Dam down to Fairmount Dam (the head of tide). It says nothing about the tidal Delaware — the contaminated corridor the problem is actually about.
- **No notification.** It is a webpage (phillyrivercast.org), not an alert. Nobody is warned; they have to go look.
- **Siloed and non-interoperable.** It publishes a color rating as page text, not machine-readable data a health system, dashboard, or app can consume. As of Sep 23, 2026 the site lists no API, feed, or data download, and has no `robots.txt` or `llms.txt`.
- **Different thresholds.** The current site reports E. coli bands for lightly used areas: green below 410, yellow 410–1,783, red above 1,783 CFU/100 mL. Its green therefore covers values above the 235 CFU/100 mL single-sample limit AquaSentinel uses. (Our earlier research described the 2007 published model as fecal-coliform-based; the site now states E. coli. Reconcile against the paper before citing either.)
- **Passive recalibration only.** RiverCast is "periodically compared" to routine grab samples in the same reach — but that sampling is not directed by the model and stays in one stretch.

Two maps in the folder make the gap concrete: `docs/images/RiverCast-coverage-map.png` (the one segment it covers) and `docs/images/Contaminated-corridor-whitespace-map.png` (the region-wide whitespace it ignores — Delaware from Trenton to Chester, tidal Schuylkill, Pennypack Creek, Brandywine Creek).

RiverCast's model is published (JWRPM/ASCE 2007), so reproducing it as a citable baseline is legitimate — but "we reproduced RiverCast" is not novelty. Our contribution is what RiverCast is not: portable, interoperable, actionable, and self-improving.

## The architecture — agents read and act, code decides

The system still hinges on one deliberately simple contract, now carrying provenance so it can describe signals from any source:

> `{ location, time, risk tier, confidence, source, source_url, retrieved_at, evidence }`

The design rule is a clean split of responsibilities. Agents do the messy external work that a rule cannot: understanding people's plain-language alert requests, reading websites and normalizing what they say, drafting requests to an agency, and picking up lab results that come back as emails or PDFs. Deterministic code makes every decision: whether a signal is valid, whether an alert fires, whether a sampling request is triggered, and whether a returned lab result may become a training label. No agent decides a tier, triggers another agent, or writes training data directly.

The agents do not hand work to each other. Both consume the same validated signal independently, so a failure in one never takes the other down, and the trigger for each is a plain rule rather than an agent's judgment.

```
 SOURCES                      READ                    DECIDE                    ACT
 ┌─────────────────────┐
 │ AquaSentinel sensor │─MCP──┐
 │ → live dashboard    │      │  ┌────────────────┐  ┌──────────────────┐  ┌─────────────────────────┐
 └─────────────────────┘      ├─▶│ ADVISORY READER│─▶│ VALIDATOR +      │─▶│ NOTIFICATION            │
 ┌─────────────────────┐      │  │ AGENT          │  │ GATING           │  │ FHIR Observation + Flag │
 │ Legacy advisory site│─HTML─┘  │ extract and    │  │ (deterministic,  │  │ → RPHSA stub            │
 │ (future direction)  │         │ normalize      │  │  fails closed)   │  │ WhatsApp to subscribers │
 └─────────────────────┘         └────────────────┘  └────────┬─────────┘  │ (OpenClaw broadcaster)  │
                                                              │            └─────────────────────────┘
                                          high risk OR low    │
                                          confidence (rule;   ▼
                                          AquaSentinel only) ┌──────────────────────────────┐
                                                             │ SAMPLING COORDINATOR AGENT    │
                                                             │ request → lab result →        │
                                                             │ label gate (deterministic) →  │
                                                             │ retrain                       │
                                                             └──────────────────────────────┘
```

The subscriber side runs alongside the signal path:

```
 resident on WhatsApp       SUBSCRIPTION AGENT           confirm            VALIDATOR              SUBSCRIPTION STORE
 or the page text box  ──▶  parse request; resolve  ──▶  read-back,   ──▶   (deterministic):  ──▶  phone, location,
 "warn me if it's           place via MCP                 saved only        E.164, monitored       window, alert-on
  unsafe to kayak near      list_monitored_locations      on "YES"          location, valid              │
  Penn's Landing this                                                       window, max length           ▼
  weekend"                                                                               gating matches each validated
                                                                                         change of state to subscriptions
```

## The engine — a virtual (soft) sensor

Because a lab culture takes 18–24 hours, we estimate present-day risk from real-time proxies: rainfall and antecedent 24–48h rainfall, turbidity, specific conductance, temperature, and dissolved oxygen. Two event signals, CSO discharge and a rainfall forecast, sit alongside the model rather than inside it (see below). (The tidal Penn's Landing gauge has no continuous streamflow before 2023, so rainfall carries the storm/CSO signal rather than flow.) The model classifies a location and time into two levels, Safe or Unsafe, against EPA's 235 CFU/100 mL single-sample value. The 126 CFU/100 mL geometric mean and 410 statistical threshold value are reference criteria for context, not alert levels.

**Event signals — CSO discharge and an ingested forecast.** Rainfall is the strongest signal in our data (unsafe samples averaged 13.9 mm of prior-48h rain versus 3.9 mm for safe ones) because rain is what triggers combined sewer overflows. Two event signals get closer to that mechanism than rain alone, but neither is in the training data, so neither becomes a model feature in this build. A feature the model has never seen in its history cannot be validated, and adding one quietly would undercut the honest-metrics claim. Instead, each plays a narrower, deterministic role.

- **CSO discharge (what is happening now).** Philadelphia has 164 CSO outfalls discharging to the Schuylkill, the Delaware, and city creeks. PWD's FY24 NPDES annual report puts combined overflow at nearly 14 billion gallons from July 2023 to June 2024. PWD publishes overflow activity on its CSOcast map (water.phila.gov/maps/csocast). Planned use: when outfalls near the Center City reach show active overflow, a rule forces the status to Unsafe and lowers the reported confidence. The status says why ("sewer overflow under way near Penn's Landing") and never shows or implies a bacteria value it does not have. That also makes the sampling loop request a confirmatory sample exactly when a real overflow is under way. Because the reach is tidal, "near" means outfalls on both sides of Penn's Landing within the tidal excursion, not only upstream; the exact outfall set is a design choice to document, not a fact we have yet. Not yet verified: whether CSOcast status is measured or modeled, how often it updates, whether it offers a machine-readable feed (the page is a JavaScript map), and whether City terms like RiverCast's apply. Using overflow status as an internal input is not the same as republishing it, but that is our reading, not legal advice, and alerts would never restate CSOcast content as ours. If access or terms do not work out, this stays a Future direction and the rules fallback runs on rainfall alone.
- **Rainfall forecast (what is about to happen).** The virtual sensor estimates present conditions from observed proxies; a forecast adds a look-ahead. The National Weather Service API (api.weather.gov) publishes hourly gridpoint forecasts that include `quantitativePrecipitation`, the forecast rain amount, for the Philadelphia grid cell. Planned use: when forecast rain over the next 24 hours crosses a threshold drawn from our own rainfall data (not an invented number), the dashboard shows a separate "rain expected, conditions may worsen" heads-up, and subscribers who opted into heads-ups receive it. The forecast never changes the current estimate or tier, and heads-ups are labeled as forecast-based, distinct from the estimate. Federal weather data is generally public domain, so it carries none of the reuse constraints City sites do (confirm on the NWS site before the demo).

**Where it runs — the tidal Delaware, RiverCast's blind spot.** We deliberately build the proof-of-concept where RiverCast cannot go. Near-shore E. coli labels exist at DRBC's Ben Franklin Bridge and Navy Yard stations (349 results across 2005–2025), and the USGS Penn's Landing gauge (01467200) streams continuous temperature, specific conductance, and dissolved oxygen back to 2007 and continuous turbidity since October 2021. That co-location of labels and proxy stream is what makes a trainable sensor possible here — and it is novel, because RiverCast is Schuylkill-and-non-tidal only. `docs/images/PennsLanding-proxy-gauge-map.png` shows the setup.

**Model choice, honestly scoped.** Small sample size is the binding constraint — after joining, the modern turbidity-paired regime holds 60 rows — so the headline model is a tree ensemble (random forest / gradient boosting) with a multiple-linear-regression baseline and a rules fallback. Deep learning is out — it needs hundreds to thousands of samples we do not have. This is backed by the literature: a systematic review of 53 freshwater-beach models found ~81% average accuracy, with plain regression making up 70% of them and advanced methods winning only marginally. We report classification metrics — precision and recall on the "unsafe" class against the EPA single-sample threshold — not R², and we validate the two data regimes (pre-2021 without turbidity, post-2021 with turbidity) separately, because the 2021 instrument change is a silent-bias trap.

The pitch framing is deliberate: the prediction step is conventional and literature-validated on purpose. The contribution is not a modeling breakthrough on a few dozen samples — it is everything the two pillars do with the prediction.

## Pillar 1 — The dashboard and the Advisory Reader Agent

**The dashboard.** The public face of AquaSentinel is the landing page in `docs/landing-page/` (hero, subscribe strip, live readings, status banner, readings table). Its "Pull latest reading" becomes real per `docs/landing-page/BUILD-SPEC.md`: proxies from USGS Penn's Landing gauge 01467200, rainfall from NOAA PHL, scored by the trained model, with the Safe/Unsafe tier decided deterministically against the EPA 235 CFU/100 mL single-sample limit. Alongside the human-facing page, the dashboard publishes a machine-readable status endpoint in the contract format. There is no reason to scrape a page we control.

**An agent-ready dashboard.** AquaSentinel is built to be the kind of site agents can read reliably, not only a site that reads others. It exposes the same scored output through three agent-facing surfaces: a read-only **MCP server** (tools such as `list_monitored_locations` and `get_current_status(location)`, returning the contract); an **`llms.txt`** file that describes the site and points agents to the MCP server and the status endpoint; and **schema.org JSON-LD** on the page describing the dataset, the monitored reach, and when it was last updated. All three, and the human-facing banner, come from one scoring output, so they cannot disagree. The MCP server has no write tools: an agent can read status but cannot subscribe a phone number or change anything. We use MCP because it is the most mature of the emerging agent standards; we deliberately skip WebMCP, a W3C community-group draft published Sep 17, 2026, because it runs inside an open browser page and does not suit a background alerting agent. These standards help agents find and call our data; FHIR remains what a health system receives. The two sit side by side.

**The Advisory Reader Agent.** This agent is what makes the notification layer generalize beyond our own sensor. It works in two modes. In **native mode** it calls a source's published agent interface (an MCP server, or a structured feed advertised in `llms.txt`) and receives structured data; AquaSentinel is the reference native source. In **legacy mode** it would read the page of a site that publishes nothing for agents, such as RiverCast, and extract the rating, with heavier validation than native signals. Legacy mode is designed but deferred for this build (see Future directions); the hackathon build runs native mode only. In native mode the read itself is a deterministic MCP client call, so the agentic work this build demonstrates is on the subscriber side, below. Its output is the normalized contract plus provenance: the source URL, the retrieval time, and the verbatim evidence snippet it read the rating from. It maps each source's rating without reinterpreting it. Any third-party rating would keep its own scale and thresholds, never remapped onto AquaSentinel's 235 threshold. The agent does not decide whether to alert, change a tier, or send anything.

The long-term point of the agent is that advisory sites like RiverCast, which offer no notification and no machine-readable output, could be turned into subscribable, standards-based feeds. For this build we demonstrate the pattern on AquaSentinel's own agent-ready interface and defer third-party sources; see Future directions for what we found on RiverCast and why.

**Validation and gating (deterministic).** Every normalized signal passes checks before anything acts on it: schema and allowed tier values, freshness (a stale page yields no signal), and an evidence check that the quoted snippet actually contains the claimed rating. Gating then applies the decided alert rules (full log in `docs/alert-rules-decisions.md`): alerts fire on change of state only; a gauge reading older than 2 hours makes the status "unavailable"; an all-clear goes out only after 48 continuous hours of Safe, and any Unsafe reading restarts that clock; the agency (RPHSA) receives every FHIR Flag change year-round, while public messages go out only from May 1 to October 31 (our choice, extending Pennsylvania's May 1 to September 30 swimming season to cover fall paddling) and only to subscribers whose location and time window match. The system fails closed. An extraction that fails any check never produces an "all clear"; the dashboard shows "status unavailable" and no message goes out.

**The Subscription Agent — alerts on the terms people actually ask for.** People do not think in location IDs and time windows; they say "warn me if it's unsafe to kayak near Penn's Landing this weekend." The Subscription Agent takes that request, over WhatsApp or a text box on the dashboard, and turns it into a structured subscription: phone, monitored location, time window, and what to alert on (unsafe, all-clear, and optionally forecast-based rain heads-ups). It resolves places by calling the MCP server's `list_monitored_locations`, so it can only subscribe people to places AquaSentinel actually covers. A request for an unmonitored place, such as Boathouse Row on the Schuylkill, is declined plainly rather than approximated, with a link to the source that does cover it (linking is not republication). If a request is ambiguous, the agent asks one clarifying question.

Nothing is saved until the agent reads the subscription back in plain words and the person replies "YES." A deterministic validator then checks it (E.164 number, a monitored location, a valid window, a maximum duration) before it enters the store. "STOP" is handled by code, not the agent, and removes the subscription immediately. The agent may also answer "is it safe today?" by calling `get_current_status`, but only through a fixed template around the published tier and its estimate language; it never characterizes risk in its own words. Activity ("kayak," "swim," "walk the dog") is recorded to word the message but never changes the threshold, because the model has one validated threshold. We keep the phone number and the structured subscription, not the free-text request.

**Notifications to the people who ask for them.** Subscribers opt in through the Subscription Agent or the subscribe strip (E.164 phone number, confirmation via an approved WhatsApp Cloud API template, as in the build spec). On a validated change of state, gating selects only the subscriptions whose location and time window match, and OpenClaw 2.0 acts strictly as a sandboxed broadcaster: it reads an already-decided signal and sends a plain-language message with source attribution (for example, "Source: AquaSentinel estimate, 08:40"). It holds no health data, no FHIR path, and only the demo's own messaging credential. The demo runs on Meta's official Cloud API test number; in production the agency would operate this last mile.

**The standards layer stays.** From the validated signal we still emit OneAquaHealth's own FHIR (HL7-EU OAH IG, R4). AquaSentinel predictions are published as an OAH Indicators Observation (`subject` = Location, `status` = preliminary, `method` = estimated, `derivedFrom` = the proxy Observations). Threshold crossings raise a FHIR `Flag` (active→inactive for in-effect→all-clear, tier in its code), delivered over a FHIR `Subscription` to the stubbed agency system, the fictional "Regional Public Health Surveillance Agency (RPHSA)". Signals read from third-party sites produce a Flag only, with the source recorded, because we have no underlying measurements to put in an Observation. The IG defines no alert resource, so the Flag-based water-safety alert remains a contribution we can offer upstream. The citizen WhatsApp message stays off FHIR by design: a phone is not a FHIR endpoint.

**The health annotation.** Alerts carry a literature-based note from Wade et al. (2003): in freshwater, a one-log increase in E. coli was associated with a relative risk of about 2.12 for gastrointestinal illness. The confidence interval crosses 1.0 (0.925–4.85), so the language is "flag elevated risk," never "predict illness," cross-referenced with the NEEAR studies and EPA's 2012 criteria. Wade is an annotation, not a training label.

## Pillar 2 — The Sampling Coordinator Agent (adaptive, model-directed)

The same validated signal drives a second, independent consumer aimed at the label-scarcity problem underneath everything. The trigger is a deterministic rule, not an agent's decision: an AquaSentinel signal with high risk **or** confidence below threshold. Signals from third-party sources, once added, would not trigger sampling; there is no AquaSentinel model at those sites to improve.

**What the agent does.** Once triggered, the Sampling Coordinator Agent handles the parts of the workflow that are messy and external. It drafts the confirmatory-sampling request to the environmental agency, stating the location, the reason (for example, "low confidence after 18 mm of prior-48h rain"), the collection window, and a reference to the OAH field-sampling protocol. It tracks the open request, watches for the lab result 18–24 hours later (from the Water Quality Portal, an email, or a PDF), and matches that result to the request that asked for it.

**The label gate (deterministic).** A returned result becomes training data only after code checks it: units (CFU or MPN per 100 mL), detection limits and non-detects, sample time inside the requested window, location match, and same-day duplicates resolved to the maximum. Only then is it added to the training set and the model retrained, with the two data regimes still validated separately. The agent never writes a label directly. One misread lab report written straight into training data would quietly corrupt the model, so this gate is a data-quality feature, not a formality.

**Posture and standards.** A sampling request is decision support to an agency, not a public-health determination, which is a lower-liability claim than public alerting. The request is not clinical, so it stays off FHIR and uses a plain task/webhook or an environmental-data standard. RiverCast is only passively "compared" against routine grab samples in one reach; ours is model-directed, adaptive, and closes the loop into retraining.

**Hackathon honesty.** We will show the architecture and simulate one full turn: uncertain prediction → sampling request to a stub agency inbox → returned lab result (a held-out historical DRBC result stands in) → label gate → retrain. We cannot show the model measurably improving; that needs weeks of accumulated samples. We claim the design and the mechanism, not an accuracy gain.

## Why it is novel and defensible

The novelty is explicitly not "can we predict bacteria from rain." That is settled, and RiverCast is our evidence. The contribution has four parts. First, a sensor where RiverCast cannot go: the tidal Delaware. Second, an agent layer that lets anyone ask for water-safety alerts in plain language and turns them into confirmed, validated subscriptions, with agents confined to understanding and reading and every decision kept deterministic; the reader side is designed to extend to other advisory sites. Third, AquaSentinel models the other side of that bargain: a public-health data site built to be agent-ready from the start, so the reader's native path shows what every advisory site could offer. Fourth, a self-improving sampling loop that manufactures the local labels a new site would otherwise lack.

RiverCast's own paper presents a site-specific fitted model; the method transfers but the coefficients are locked to one river and one calibration window. Pillar 2 generates the calibration labels each new location needs, and the reader agent lets the alert layer cover sites before we ever train a model there. The loop supplies labels, not instrumentation, so transfer still presumes a continuous proxy gauge, which the tidal corridor already has at several stations.

One-sentence version for judges: *RiverCast tells one stretch of one river it might be dirty, on a webpage nobody is warned by. AquaSentinel adds a sensor for the reach RiverCast cannot see, an agent that lets anyone ask, in plain language, for the water-safety alerts they need, while health systems receive the same signal as standards-based FHIR, and a second agent that samples where the model is unsure, so the system keeps getting better.*

## Data — built, with honest caveats

The training dataset is built and reproducible (`AquaSentinel-dataset/`, with a documented builder script and a data dictionary). All values are real, pulled from public APIs — nothing is synthesized.

Confirmed and used:

- **Proxy stream:** USGS Penn's Landing gauge 01467200 — continuous temperature, specific conductance, dissolved oxygen, and pH since 2007; continuous turbidity since Oct 28, 2021. Via the USGS Water Data API.
- **E. coli labels (tidal Delaware):** DRBC stations at Ben Franklin Bridge and Navy Yard — 349 results, 2005–2025, ~16/year (a 2020 gap), via the EPA Water Quality Portal. This is the RiverCast blind spot and the reason the tidal-Delaware POC is trainable and novel.
- **Rainfall:** NOAA NCEI daily precipitation at Philadelphia International Airport, used to build antecedent-rain features.
- **Prior-art baseline (optional):** the published RiverCast model (JWRPM/ASCE 2007) as a citable recipe; nearby Schuylkill proxies (Norristown turbidity from 2012) are available if we add that baseline.

The joined result: **330 labeled samples**, of which 310 carry USGS proxy features and split into two regimes kept separate — pre-2021 (250 rows, 30 unsafe) with temperature/conductance/DO/pH/rainfall, and post-2021 (60 rows, all with turbidity, 15 unsafe) which adds turbidity. The remaining 20 samples have a lab label and rainfall but no gauge reading that day (4 unsafe); they stay in the master for the rainfall-only fallback and are held out of proxy-model training. The combined-sewer mechanism is visible in the data: unsafe samples averaged 13.9 mm of prior-48h rain versus 3.9 mm for safe ones.

Caveats we state up front:

- **Small N is the constraint.** The binding limit is the positive (unsafe) count — 30 in the earlier regime, 15 in the modern one — not the row count. This is why the model is a tree ensemble plus a regression baseline, why we report classification metrics, and why the honest framing is "transferable proof-of-concept," not "data-hungry production model."
- **Two regimes, validated separately.** The 2021 turbidity instrument change means combining them naively would introduce silent bias.
- **Cross-agency join.** Proxies (USGS) and labels (DRBC) are matched by date, not co-located at a single instrument; the two stations bracket the gauge. A deliberate, disclosed choice.
- **Rainfall is a metro proxy.** The airport is ~7 miles from the stations and the upstream CSO outfalls, so it captures storm timing well and basin-specific intensity less well.
- **Minor label handling.** Non-detects are encoded as clearly-safe and flagged; same-day duplicates take the maximum (conservative for a safety label).

## Track and judging alignment

- **AI-Supported Assessment** — the virtual sensor assessing recreation/ecosystem safety from monitoring data, plus a subscription agent that turns plain-language requests into confirmed, validated alert subscriptions.
- **Digital Health Standards** — conformance to the official HL7-EU OneAquaHealth FHIR IG (R4) for the prediction Observation, a contributed water-safety `Flag` profile the IG lacks, and FHIR `Subscription` delivery to the agency system. On security: agents never decide, validation fails closed, the broadcaster is sandboxed, and no agency credentials leave the agency.
- **Impact and mission alignment** — real Philadelphia data, designed to plug into OneAquaHealth's City Dashboards / Decision Support System via its own FHIR standard, portable to any gauged site, and able to extend alerts to existing advisory sites immediately.
- **Innovation, architecture, UX, scalability** — novelty is the plain-language subscription agent, the agent-ready publishing pattern, and the adaptive loop; the agents-read, code-decides boundary is the architecture story; plain-language subscriptions, the dashboard, and the WhatsApp last mile are the UX; adding a source is an adapter, not a new model, and an agent-ready source needs no adapter at all, which is the scalability story.

## Scope for the two-week build (Sep 16–30)

Build for real:

- USGS + DRBC + NCEI ingestion and the paired tidal-Delaware training set (two regimes) — done.
- A first prediction model — tree ensemble with an MLR baseline and a rules fallback.
- The dashboard: real `pullReading()`, the machine-readable status endpoint, and the subscribe backend (per `docs/landing-page/BUILD-SPEC.md`).
- The agent-ready layer: a read-only MCP server, `llms.txt`, and JSON-LD, all served from the same scoring output.
- The Advisory Reader Agent in native mode, reading AquaSentinel through its MCP server.
- The Subscription Agent: plain-language requests over WhatsApp and a dashboard text box, read-back confirmation, deterministic validation, deterministic STOP, and templated status replies.
- Deterministic validation and gating, including the fail-closed path.
- The OAH-conformant Observation, the `Flag` alert, and a FHIR `Subscription` to the stubbed RPHSA system.
- The WhatsApp last mile via OpenClaw on Meta's Cloud API test number, with source attribution.
- The Sampling Coordinator Agent and one simulated loop turn through the label gate.

Acceptable to mock or stub:

- The agency's surveillance system (stub FHIR endpoint for RPHSA) and the agency's sampling inbox.
- The returned lab result (a held-out historical DRBC result stands in).
- Third-party sources: legacy mode is designed, not built (see Future directions).
- Out of scope: WebMCP (draft standard, browser-bound).
- Full multi-city deployment and a measured accuracy gain from the loop.

## Demo script (3–5 min video)

Open on the WHYY problem and the "day after rain" gap. Show the dashboard pulling a real reading from the Penn's Landing gauge. On a phone, a resident texts "warn me if it's unsafe to kayak near Penn's Landing this weekend"; the Subscription Agent resolves the place through the MCP server, reads the subscription back, and saves it on "YES." A second request, for Boathouse Row on the Schuylkill, is declined as outside AquaSentinel's coverage, with a link to RiverCast. Then replay a historical rainfall/CSO event (demo clock set to the replayed weekend) so the tier flips to Unsafe. Show the Advisory Reader Agent reading AquaSentinel natively through its MCP server and producing the normalized signal with its provenance. Show validation passing, then a deliberately stale or malformed response failing closed ("status unavailable," no message). On the valid change of state, show the `Flag` delivered by FHIR Subscription to the RPHSA stub and the plain-language WhatsApp warning arriving on the kayaker's phone, and only on phones whose subscription covers that place and time, labeled as the simulated agency last mile. Then show the same signal, at low confidence, triggering the Sampling Coordinator Agent: the drafted agency request, the returned lab result passing the label gate, and the retrain. Close on portability and the one-sentence pitch.

## Risks and mitigations

- **Too few labeled samples** → tree ensemble + regression baseline + rules fallback; frame as a transferable method; Pillar 2 is the long-term answer.
- **Prior-art skepticism** → lead with "prediction is proven (RiverCast is our evidence); the reader agent, interoperability, and the adaptive loop are the contribution."
- **Agent misreads a site** → evidence-snippet check, freshness check, fail closed; an uncertain extraction never produces an "all clear."
- **Subscription Agent misreads a request** → read-back and explicit "YES" before saving; a deterministic validator checks number, location, and window; unmonitored places are declined, never approximated.
- **WhatsApp test-number limits** → inbound works only from up to 5 pre-registered phones, which fits the demo; confirm early how long the access token lasts and whether the alert message needs an approved template, since alerts arrive outside the 24h reply window.
- **Agent paraphrases health risk** → status replies use a fixed template around the published tier; activity words never change the threshold; STOP is handled by code.
- **Agent standards are young and may shift** → build on MCP, the most established; `llms.txt` and JSON-LD are cheap to add and harmless if ignored; skip WebMCP for now.
- **Third-party sites restrict reuse** → deferred for this build; RiverCast's terms prohibit republication without the City's written permission (see Future directions).
- **Threshold mismatch across sources (once added)** → each source keeps its own rating and thresholds (for example RiverCast 410/1,783 vs AquaSentinel 235); nothing is remapped onto another source's scale.
- **"Agents for show" concern** → each agent handles external, unstructured work a rule cannot; every decision is deterministic and visible.
- **Cascading agent failure** → the agents do not call each other; both consume the validated signal independently.
- **Bad labels entering training** → the deterministic label gate; the agent cannot write training data.
- **FHIR fit challenged** → conform to the OAH IG's own Observation profile, address a Location (legal in R4), use FHIR only for the machine-to-machine hop, keep the citizen message and sampling task off FHIR.
- **Overclaiming the loop** → claim the design and one simulated turn, never a proven accuracy gain.
- **Impersonating an agency or a source** → the agency is a clearly labeled fictional placeholder; third-party ratings are always attributed to their publisher, never presented as ours.
- **Scope in the remaining days** → one native source, one loop turn; the dashboard and notification path come before the sampling agent.

## Future directions

**RiverCast as the first legacy-mode source.** Checked Sep 23, 2026. Technically it is easy: the rating is plain page text with a timestamp ("RiverCast is RED for Wednesday, 9/23/2026 at 4:31 AM"), and the site publishes nothing for agents (no API, feed, data download, `robots.txt`, or `llms.txt`). The blocker is reuse: its Terms of Use allow sharing pages only "exactly as presented on the website, without any addition or modification," and prohibit "distribution or republication in any other form ... and any modification whatsoever" without the City's prior written permission. A reworded alert reads as republication in another form (our reading, not legal advice). To pursue it: send a permission request (drafted separately, not in this repo, not sent) to RiverCastInfo@phila.gov; if granted, alerts credit RiverCast, link back, and carry its own E. coli bands (green below 410, yellow 410–1,783, red above 1,783 CFU/100 mL for lightly used areas) without remapping. Also reconcile the 2007 paper's indicator with the site's current E. coli description before citing either.

**More legacy sources.** Other advisory sites in the corridor, checked the same way: agent-readiness first, then reuse terms, before any extraction is built.

**Agent-ready advocacy.** Offer the AquaSentinel pattern (MCP server, `llms.txt`, explicit reuse terms) to advisory publishers, so they become native-mode sources and legacy extraction is no longer needed.

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
- [ ] Working prototype / proof-of-concept (sensor, agent-ready dashboard, reader agent in native mode, subscription agent, notifications, sampling agent with one simulated loop turn)
