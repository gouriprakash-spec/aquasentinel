# AquaSentinel — Product Brief

*Working name. OneAquaHealth IEEE Global Hackathon 2026.*
*Tracks: Digital Health Standards + AI-Supported Assessment.*
*Status: registered on IEEE Devpost (Aug 27, 2026). Build window Sep 16–30.*
*Revised Sep 23, 2026: notifications now run through an agent-ready dashboard (MCP server, `llms.txt`, JSON-LD) and a Subscription Agent that turns plain-language alert requests into confirmed subscriptions; the sampling loop is a separate Sampling Coordinator Agent on the same signal; third-party sources such as RiverCast are deferred to Future directions. Earlier versions are kept outside this repo.*
*Revised Sep 26, 2026: the Subscription Agent and WhatsApp public alerts are cut, deliberately —
deciding to alert citizens about a public-health risk, and doing it, is the agency's jurisdiction,
not a hackathon prototype's to claim without its buy-in. FHIR-to-RPHSA stays the only notification
channel, agency-first and year-round; the public dashboard is unaffected. The three co-equal
contributions are now the agent-ready publishing layer, the Advisory Reader Agent's extensibility,
and the Sampling Coordinator's self-improving loop.*
*Revised Oct 2, 2026: the Advisory Reader Agent and the Sampling Coordinator Agent are cut from
the build and moved to Future directions (Gouri's call, with one day of build time left: a
deployed, demonstrable product over two more half-built pieces). This supersedes the "three
co-equal contributions" framing above. What is built: the agent-ready publishing layer (MCP
server, `/api/status`, `llms.txt`, JSON-LD), the live sensor and CSO rule, the alert rules, and
FHIR delivery to the agency. What is demonstrated: an outside personal assistant polling
AquaSentinel for readings (a person asks their assistant; AquaSentinel pushes nothing). What is
designed but not built: both agents. Pillar 1's reader agent and all of Pillar 2 below describe
the design as intended, not what shipped.*

## One-line pitch

AquaSentinel is a virtual water-quality sensor built to work with the agencies that have
authority over public health, not around them. The sensor estimates today's bacteria risk for the
tidal Delaware and publishes it on a live, agent-ready dashboard that anyone — a resident or their
own AI agent — can check directly, while the same validated signal reaches the responsible health
agency as standards-based FHIR, year-round. A reader agent is designed to extend that same
agent-ready pattern to other water-advisory sites later. A second agent uses the same signal to
request confirmatory samples where the model is unsure, so the system gets smarter over time.

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
 │ Legacy advisory site│─HTML─┘  │ extract and    │  │ (deterministic,  │  │ → RPHSA stub, year-round│
 │ (future direction)  │         │ normalize      │  │  fails closed)   │  │ (the only notification  │
 └─────────────────────┘         └────────────────┘  └────────┬─────────┘  │  channel - agency-first) │
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

There is no subscriber-side path: AquaSentinel does not decide who gets alerted or how — the
agency does. The dashboard and the agent-ready surfaces (MCP, `/api/status`, `llms.txt`, JSON-LD)
are pull-based: anyone, human or agent, can check current status themselves, on their own terms.

## The engine — a virtual (soft) sensor

Because a lab culture takes 18–24 hours, we estimate present-day risk from real-time proxies: rainfall and antecedent 24–48h rainfall, turbidity, specific conductance, temperature, and dissolved oxygen. Two event signals, CSO discharge and a rainfall forecast, sit alongside the model rather than inside it (see below). (The tidal Penn's Landing gauge has no continuous streamflow before 2023, so rainfall carries the storm/CSO signal rather than flow.) The model classifies a location and time into two levels, Safe or Unsafe, against EPA's 235 CFU/100 mL single-sample value. The 126 CFU/100 mL geometric mean and 410 statistical threshold value are reference criteria for context, not alert levels.

**Event signals — CSO discharge and an ingested forecast.** Rainfall is the strongest signal in our data (unsafe samples averaged 13.9 mm of prior-48h rain versus 3.9 mm for safe ones) because rain is what triggers combined sewer overflows. Two event signals get closer to that mechanism than rain alone, but neither is in the training data, so neither becomes a model feature in this build. A feature the model has never seen in its history cannot be validated, and adding one quietly would undercut the honest-metrics claim. Instead, each plays a narrower, deterministic role.

- **CSO discharge (what is happening now).** Philadelphia has 164 CSO outfalls discharging to the Schuylkill, the Delaware, and city creeks. PWD's FY24 NPDES annual report puts combined overflow at nearly 14 billion gallons from July 2023 to June 2024. PWD publishes overflow activity on its CSOcast map (water.phila.gov/maps/csocast). Planned use: when outfalls near the Center City reach show active overflow, a rule forces the status to Unsafe (amended 2026-10-03: the reported confidence is the rule/model agreement and is not altered by the overflow). The status says why ("sewer overflow under way near Penn's Landing") and never shows or implies a bacteria value it does not have. That also makes the sampling loop request a confirmatory sample exactly when a real overflow is under way. Because the reach is tidal, "near" means outfalls on both sides of Penn's Landing within the tidal excursion, not only upstream; the exact outfall set is a design choice to document, not a fact we have yet. Not yet verified: whether CSOcast status is measured or modeled, how often it updates, whether it offers a machine-readable feed (the page is a JavaScript map), and whether City terms like RiverCast's apply. Using overflow status as an internal input is not the same as republishing it, but that is our reading, not legal advice, and alerts would never restate CSOcast content as ours. If access or terms do not work out, this stays a Future direction and the rules fallback runs on rainfall alone.
- **Rainfall forecast (what is about to happen).** The virtual sensor estimates present conditions from observed proxies; a forecast adds a look-ahead. The National Weather Service API (api.weather.gov) publishes hourly gridpoint forecasts that include `quantitativePrecipitation`, the forecast rain amount, for the Philadelphia grid cell. Planned use: when forecast rain over the next 24 hours crosses a threshold drawn from our own rainfall data (not an invented number), the dashboard shows a separate "rain expected, conditions may worsen" heads-up. The forecast never changes the current estimate or tier, and heads-ups are labeled as forecast-based, distinct from the estimate. Federal weather data is generally public domain, so it carries none of the reuse constraints City sites do (confirm on the NWS site before the demo).

**Where it runs — the tidal Delaware, RiverCast's blind spot.** We deliberately build the proof-of-concept where RiverCast cannot go. Near-shore E. coli labels exist at DRBC's Ben Franklin Bridge and Navy Yard stations (349 results across 2005–2025), and the USGS Penn's Landing gauge (01467200) streams continuous temperature, specific conductance, and dissolved oxygen back to 2007 and continuous turbidity since October 2021. That co-location of labels and proxy stream is what makes a trainable sensor possible here — and it is novel, because RiverCast is Schuylkill-and-non-tidal only. `docs/images/PennsLanding-proxy-gauge-map.png` shows the setup.

**Model choice, honestly scoped.** Small sample size is the binding constraint — after joining, the modern turbidity-paired regime holds 60 rows — so the headline model is a tree ensemble (random forest / gradient boosting) with a multiple-linear-regression baseline and a rules fallback. Deep learning is out — it needs hundreds to thousands of samples we do not have. This is backed by the literature: a systematic review of 53 freshwater-beach models found ~81% average accuracy, with plain regression making up 70% of them and advanced methods winning only marginally. We report classification metrics — precision and recall on the "unsafe" class against the EPA single-sample threshold — not R², and we validate the two data regimes (pre-2021 without turbidity, post-2021 with turbidity) separately, because the 2021 instrument change is a silent-bias trap.

The pitch framing is deliberate: the prediction step is conventional and literature-validated on purpose. The contribution is not a modeling breakthrough on a few dozen samples — it is everything the two pillars do with the prediction.

**Amended 2026-09-27.** A post-hoc review found that the shipped random-forest's reported
precision/recall did not survive honest, date-grouped cross-validation - two label stations
sharing one upstream gauge meant ordinary folds leaked identical feature rows across train and
test. Finding and fixing that leak is part of this project's data-quality story, not a footnote:
live scoring now uses a disclosed, one-line rainfall rule to decide Safe/Unsafe, and a model
retrained on Penn's Landing near-shore labels only informs how much to trust that decision.

## Pillar 1 — The dashboard and the Advisory Reader Agent

**The dashboard.** The public face of AquaSentinel is the landing page in `docs/landing-page/` (hero, live readings, status banner, readings table). Its readings are real per `docs/landing-page/BUILD-SPEC.md` (a scheduled job pulls them on the hour, not at startup; the page only reads what is stored, and the public "Pull latest reading" button was removed 2026-10-02 so no visitor can trigger live requests to the data sources): proxies from USGS Penn's Landing gauge 01467200, rainfall from NOAA/Open-Meteo, scored by the trained model, with the Safe/Unsafe tier decided deterministically against the EPA 235 CFU/100 mL single-sample limit. Alongside the human-facing page, the dashboard publishes a machine-readable status endpoint in the contract format. There is no reason to scrape a page we control, and no reason for us to decide who else gets to see it — it's public, pull-based, on the same terms for everyone.

**An agent-ready dashboard (the built contribution).** AquaSentinel is built to be the kind of site agents can read reliably, not only a site that reads others. It exposes the same scored output through three agent-facing surfaces: a read-only **MCP server** (tools such as `list_monitored_locations` and `get_current_status(location)`, returning the contract); an **`llms.txt`** file that describes the site and points agents to the MCP server and the status endpoint; and **schema.org JSON-LD** on the page describing the dataset, the monitored reach, and when it was last updated. All three, and the human-facing banner, come from one scoring output, so they cannot disagree. The MCP server has no write tools: an agent can read status but cannot change anything. We use MCP because it is the most mature of the emerging agent standards; we deliberately skip WebMCP, a W3C community-group draft published Sep 17, 2026, because it runs inside an open browser page and does not suit a background reading agent. These standards help agents find and call our data; FHIR remains what a health system receives. The two sit side by side.

**The Advisory Reader Agent (designed, not built: cut 2026-10-02, see Future directions).** This agent is what makes the agent-ready pattern generalize beyond our own sensor. It works in two modes. In **native mode** it calls a source's published agent interface (an MCP server, or a structured feed advertised in `llms.txt`) and receives structured data; AquaSentinel is the reference native source. In **legacy mode** it would read the page of a site that publishes nothing for agents, such as RiverCast, and extract the rating, with heavier validation than native signals. Legacy mode is designed but deferred for this build (see Future directions); the hackathon build runs native mode only. In native mode the read itself is a deterministic MCP client call — the agentic work this build demonstrates is the reading and normalizing itself, plus the Sampling Coordinator below. Its output is the normalized contract plus provenance: the source URL, the retrieval time, and the verbatim evidence snippet it read the rating from. It maps each source's rating without reinterpreting it. Any third-party rating would keep its own scale and thresholds, never remapped onto AquaSentinel's 235 threshold. The agent does not decide whether to alert, change a tier, or send anything.

The long-term point of the agent is that advisory sites like RiverCast, which offer no machine-readable output today, could be turned into standards-based, agent-readable feeds — the same pattern AquaSentinel demonstrates on itself. For this build we demonstrate the pattern on AquaSentinel's own agent-ready interface and defer third-party sources; see Future directions for what we found on RiverCast and why.

**Validation and gating (deterministic).** Every normalized signal passes checks before anything acts on it: schema and allowed tier values, freshness (a stale page yields no signal), and an evidence check that the quoted snippet actually contains the claimed rating. Gating then applies the decided alert rules (full log in `docs/alert-rules-decisions.md`): alerts fire on change of state only; a gauge reading older than 2 hours makes the status "unavailable"; an all-clear goes out only after 48 continuous hours of Safe, and any Unsafe reading restarts that clock; the agency (RPHSA) receives every FHIR Flag change year-round. The system fails closed. An extraction that fails any check never produces an "all clear"; the dashboard shows "status unavailable" and no message goes out.

**No direct-to-public alerting, by design.** An earlier version of this design included a Subscription Agent that parsed plain-language requests ("warn me if it's unsafe to kayak near Penn's Landing this weekend") into confirmed WhatsApp alert subscriptions. That's cut, deliberately, not for lack of time to build it. Deciding to alert citizens about a public-health risk, and actually doing it, is the responsible agency's jurisdiction — not something a hackathon prototype should claim unilaterally, even with an opt-in mechanism, without that agency's buy-in. The public dashboard is unaffected: anyone can still check current status themselves, on their own initiative, exactly as before. What's gone is AquaSentinel proactively deciding who gets told what, and when.

**The standards layer stays, and is now the only notification path.** From the validated signal we emit OneAquaHealth's own FHIR (HL7-EU OAH IG, R4). AquaSentinel predictions are published as an OAH Indicators Observation (`subject` = Location, `status` = preliminary, `method` = estimated, `derivedFrom` = the proxy Observations). Threshold crossings raise a FHIR `Flag` (active→inactive for in-effect→all-clear, tier in its code), delivered over a real FHIR `Subscription` (criteria, channel, a handshake step) to the stubbed agency system, the fictional "Regional Public Health Surveillance Agency (RPHSA)" — year-round, regardless of season. Signals read from third-party sites produce a Flag only, with the source recorded, because we have no underlying measurements to put in an Observation. The IG defines no alert resource, so the Flag-based water-safety alert remains a contribution we can offer upstream.

**The health annotation.** Alerts carry a literature-based note from Wade et al. (2003): in freshwater, a one-log increase in E. coli was associated with a relative risk of about 2.12 for gastrointestinal illness. The confidence interval crosses 1.0 (0.925–4.85), so the language is "flag elevated risk," never "predict illness," cross-referenced with the NEEAR studies and EPA's 2012 criteria. Wade is an annotation, not a training label.

## Pillar 2 — The Sampling Coordinator Agent (adaptive, model-directed)

*Designed, not built: cut from the build 2026-10-02, see Future directions. The trigger protocol
is written down in `docs/superpowers/specs/2026-10-02-sampling-trigger-protocol-design.md`.*

The same validated signal drives a second, independent consumer aimed at the label-scarcity problem underneath everything. The trigger is a deterministic rule, not an agent's decision: an AquaSentinel signal with high risk **or** confidence below threshold. Signals from third-party sources, once added, would not trigger sampling; there is no AquaSentinel model at those sites to improve.

**What the agent does.** Once triggered, the Sampling Coordinator Agent handles the parts of the workflow that are messy and external. It drafts the confirmatory-sampling request to the environmental agency, stating the location, the reason (for example, "low confidence after 18 mm of prior-48h rain"), the collection window, and a reference to the OAH field-sampling protocol. It tracks the open request, watches for the lab result 18–24 hours later (from the Water Quality Portal, an email, or a PDF), and matches that result to the request that asked for it.

**The label gate (deterministic).** A returned result becomes training data only after code checks it: units (CFU or MPN per 100 mL), detection limits and non-detects, sample time inside the requested window, location match, and same-day duplicates resolved to the maximum. Only then is it added to the training set and the model retrained, with the two data regimes still validated separately. The agent never writes a label directly. One misread lab report written straight into training data would quietly corrupt the model, so this gate is a data-quality feature, not a formality.

**Posture and standards.** A sampling request is decision support to an agency, not a public-health determination, which is a lower-liability claim than public alerting. The request is not clinical, so it stays off FHIR and uses a plain task/webhook or an environmental-data standard. RiverCast is only passively "compared" against routine grab samples in one reach; ours is model-directed, adaptive, and closes the loop into retraining.

**Hackathon honesty.** We will show the architecture and simulate one full turn: uncertain prediction → sampling request to a stub agency inbox → returned lab result (a held-out historical DRBC result stands in) → label gate → retrain. We cannot show the model measurably improving; that needs weeks of accumulated samples. We claim the design and the mechanism, not an accuracy gain.

## Why it is novel and defensible

The novelty is explicitly not "can we predict bacteria from rain." That is settled, and RiverCast is our evidence. The contribution has four parts. First, a sensor where RiverCast cannot go: the tidal Delaware. Second, AquaSentinel models the other side of the interoperability bargain: a public-health data site built to be agent-ready from the start (MCP, `llms.txt`, JSON-LD), not only a site that reads others — so an AI agent, or a future system built with agency buy-in, can query current status reliably instead of scraping a page. Third, a reader agent designed to extend that same agent-ready reading pattern to other advisory sites, with every decision kept deterministic and agents confined to understanding and reading — never deciding a tier or who gets told what. Fourth, a self-improving sampling loop that manufactures the local labels a new site would otherwise lack.

RiverCast's own paper presents a site-specific fitted model; the method transfers but the coefficients are locked to one river and one calibration window. Pillar 2 generates the calibration labels each new location needs, and the reader agent lets the alert layer cover sites before we ever train a model there. The loop supplies labels, not instrumentation, so transfer still presumes a continuous proxy gauge, which the tidal corridor already has at several stations.

One-sentence version for judges: *RiverCast tells one stretch of one river it might be dirty, on a webpage nobody is warned by. AquaSentinel adds a sensor for the reach RiverCast cannot see, a dashboard and MCP server built so any agent can read it reliably instead of scraping a page, a reader agent designed to extend that same pattern to other advisory sites, standards-based FHIR delivered to the responsible health agency year-round, and a second agent that samples where the model is unsure, so the system keeps getting better — all without AquaSentinel ever deciding, on its own, who gets warned.*

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

- **AI-Supported Assessment** — the virtual sensor assessing recreation/ecosystem safety from monitoring data, plus a reader agent designed to extend that same assessment pattern to other advisory sites.
- **Digital Health Standards** — conformance to the official HL7-EU OneAquaHealth FHIR IG (R4) for the prediction Observation, a contributed water-safety `Flag` profile the IG lacks, and real FHIR `Subscription` delivery mechanics (criteria, channel, handshake) to the agency system. On security: agents never decide, validation fails closed, and no agency credentials leave the agency.
- **Impact and mission alignment** — real Philadelphia data, designed to plug into OneAquaHealth's City Dashboards / Decision Support System via its own FHIR standard, portable to any gauged site, and able to extend to existing advisory sites immediately — respecting agency jurisdiction over public alerting rather than working around it.
- **Innovation, architecture, UX, scalability** — novelty is the agent-ready publishing pattern, shown working with an outside personal assistant (the reader agent's extensibility and the adaptive sampling loop are designed future work, not built); the agents-read, code-decides boundary is the architecture story; the dashboard and the agent-ready surfaces (MCP, `/api/status`, `llms.txt`, JSON-LD) are the UX, usable by a person or their own agent on their own terms; adding a source is an adapter, not a new model, and an agent-ready source needs no adapter at all, which is the scalability story.

## Scope for the build (Sep 16–Oct 4)

Build for real:

- USGS + DRBC + NCEI ingestion and the paired tidal-Delaware training set (two regimes) — done.
- The status decision: a disclosed rainfall rule (at least 2.5 mm over the two previous calendar days) decides Safe/Unsafe; a random forest trained on 69 near-shore sampled days only reports the rule/model agreement; and a combined-sewer overflow rule (CSOcast) can only escalate to Unsafe. Done.
- The dashboard: a real scheduled `pull_reading()` (on the hour, not at startup) from live USGS, NWS (Open-Meteo as a fallback) and CSOcast data, the hourly readings table, the outfall map, and the machine-readable status endpoint (per `docs/landing-page/BUILD-SPEC.md`). Deployed on Render with a persistent disk.
- The agent-ready layer: a read-only MCP server, `llms.txt`, and JSON-LD, all served from the same scoring output.
- Deterministic validation and gating, including the fail-closed path.
- The OAH-conformant Observation, the `Flag` alert, and real FHIR `Subscription` mechanics to the stubbed RPHSA system.

Demonstrated:

- Outside AI assistants polling AquaSentinel's read-only MCP server for the current reading: Claude
  (connected over MCP) and Meta's Muse (tested 2026-10-04). A person asks
  their assistant; AquaSentinel pushes nothing.

Cut from the build, designed only (see Future directions):

- The Advisory Reader Agent (native and legacy modes).
- The Sampling Coordinator Agent and its simulated loop turn through the label gate.

Acceptable to mock or stub:

- The agency's surveillance system (stub FHIR endpoint for RPHSA).
- Third-party sources: legacy mode is designed, not built (see Future directions).
- Out of scope: WebMCP (draft standard, browser-bound).
- Full multi-city deployment and a measured accuracy gain from the loop.

## Demo script (3–5 min video)

Revised 2026-10-04 (Gouri): the video opens with Gouri on camera, then goes straight to Muse answering from the MCP server; the rest explains where that answer came from. The planned historical replay with a demo clock was dropped (it was never built). Record when a recent reading exists: readings come only from the hourly scheduler (there is no pull button and no startup pull), and the page's table shows only the newest 9 rows. The video is silent, so the intro and the Muse question appear as on-screen text.

0. **Intro, on camera (about 0:20).** Gouri introduces themself and the project in one or two sentences.
1. **Ask Muse (about 0:45).** With the connector already installed, ask Muse "Is the Delaware at Penn's Landing safe for kayaking right now?" Show it calling `get_current_status` and answering with the word "estimate" kept. AquaSentinel pushes nothing; a person asks their own assistant. (Muse was tested 2026-10-04. If it does not connect on the day, show Claude instead.)
2. **Where the answer comes from: the dashboard (about 0:50).** The same reading and its banner, the hourly table (point out the "≥" on the rain total when some hourly reports were missing, and the "n/a" in the water-quality columns when the USGS sensors are silent), and the outfall map. The status is still given when the gauge is silent: the gauge only feeds the model's agreement figure.
3. **How the status is decided (about 0:35).** The rainfall rule, the sewer-overflow rule that can only raise the status, and the model that only reports agreement. Code decides, agents do not.
4. **The agent-ready layer and how to connect (about 0:40).** `/api/status`, `/api/readings?limit=24` for the day's history, and `/llms.txt`: the same numbers, no scraping. Then show how an assistant connects to the read-only MCP server (no login, three read-only tools: `list_monitored_locations`, `get_current_status`, `get_recent_readings`). Install in Claude Code with one command: `claude mcp add --transport http aquasentinel https://aquasentinel-prc9.onrender.com/mcp`. For a custom connector (Muse, or Claude.ai), add a remote MCP server with that same URL and authentication set to "none".
5. **Fail closed, and the agency path (about 0:45).** Show the real gauge outage on the live site as the honest-handling example, and the tests for the closed path (missing rainfall data records nothing and never produces an all-clear). Then show the FHIR `Subscription` handshake and the resulting `Flag` delivered to the RPHSA stub, year-round, through the end-to-end tests or a local run with the stub: the one and only notification path, agency-first. Exact steps (run both servers locally; the Render site cannot reach a laptop's stub): (a) Terminal 1, start the main server first: `./venv/bin/uvicorn app.server:app --reload`. (b) Terminal 2, start the stub: `./venv/bin/uvicorn app.rphsa_stub:app --port 8001 --reload`; it registers its `Subscription` on startup (up to 5 tries, 1 second apart), and its log shows `Registered with AquaSentinel on attempt 1: status=active`. If the stub started first and gave up, restart it. (c) Terminal 3, show the handshake the stub received: `curl -s localhost:8001/rphsa/notifications | python3 -m json.tool` (one entry with `"kind": "handshake"`). Optionally POST the `Subscription` by hand to `localhost:8000/fhir/Subscription` (criteria `Flag?subject=Location/penns-landing`, channel `rest-hook`, endpoint `http://localhost:8001/rphsa/notifications`) and show the response `"status": "active"`; posting it again returns the same id. (d) Show the `Flag` through the tests, because a Flag is only sent when the status changes and readings arrive only at the top of the hour: `./venv/bin/python -m pytest app/tests/test_fhir_end_to_end.py -v` (Unsafe onset arrives as an active Flag, the all-clear closes it as inactive, and an unreachable stub raises nothing and stores nothing). What the agency receives: a FHIR Bundle of the rainfall, sewer-overflow and water-quality Observations, a risk Observation marked `preliminary` whose `derivedFrom` points at the entries that decided the tier (so the agency can audit it), and the `Flag` (`active` at an Unsafe onset, `inactive` after the all-clear). Say "a Subscription with a verification callback", not "the FHIR handshake": the callback is our own convention, not a standard R4 payload.
6. **Close (about 0:20).** Honest limits (an estimate on a small validation set), portability, the deliberate absence of any public alerting, and the one-sentence pitch.

## Risks and mitigations

- **Too few labeled samples** → tree ensemble + regression baseline + rules fallback; frame as a transferable method; Pillar 2 is the long-term answer.
- **Prior-art skepticism** → lead with "prediction is proven (RiverCast is our evidence); the reader agent, interoperability, and the adaptive loop are the contribution."
- **Agent misreads a site** → evidence-snippet check, freshness check, fail closed; an uncertain extraction never produces an "all clear."
- **Agent paraphrases health risk** → any reader agent (e.g. the Advisory Reader Agent) is confined to a fixed vocabulary around the published tier, enforced at the code layer; agents never decide or reword a tier.
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

**The Advisory Reader Agent (cut from the build 2026-10-02).** Designed in Pillar 1 and in
`docs/superpowers/specs/2026-09-27-advisory-reader-agent-design.md`, with an implementation plan
already written (`docs/superpowers/plans/`). Native mode reads a source's published agent
interface (AquaSentinel's own MCP server is the reference source) and normalizes the result with
provenance, never remapping a source's rating onto AquaSentinel's scale. Legacy mode, for sites
that publish nothing for agents, is the RiverCast entry below. Cut because the build window
could not hold it alongside the deploy; reading AquaSentinel's own server was also circular as
a proof, and an outside assistant polling it (the Muse demo) shows the interface working more
convincingly.

**The Sampling Coordinator Agent (cut from the build 2026-10-02).** Designed in Pillar 2. The
first decision set, the trigger protocol, is written and committed:
`docs/superpowers/specs/2026-10-02-sampling-trigger-protocol-design.md` (a request fires on an
Unsafe tier or low confidence, with a rolling 24h cooldown and a 24h collection window, both
anchored to the brief's 18-24h lab turnaround and not confirmed by any agency). Still undecided:
the stub agency inbox, result matching, the label gate's rules, and retraining. Known limit
carried forward: DRBC samples about 16 times a year, so even with the cooldown a persistently
Unsafe stretch could ask for more samples than an agency would take.

**RiverCast as the first legacy-mode source.** Checked Sep 23, 2026. Technically it is easy: the rating is plain page text with a timestamp ("RiverCast is RED for Wednesday, 9/23/2026 at 4:31 AM"), and the site publishes nothing for agents (no API, feed, data download, `robots.txt`, or `llms.txt`). The blocker is reuse: its Terms of Use allow sharing pages only "exactly as presented on the website, without any addition or modification," and prohibit "distribution or republication in any other form ... and any modification whatsoever" without the City's prior written permission. A reworded alert reads as republication in another form (our reading, not legal advice). To pursue it: send a permission request (drafted separately, not in this repo, not sent) to RiverCastInfo@phila.gov; if granted, alerts credit RiverCast, link back, and carry its own E. coli bands (green below 410, yellow 410–1,783, red above 1,783 CFU/100 mL for lightly used areas) without remapping. Also reconcile the 2007 paper's indicator with the site's current E. coli description before citing either.

**More legacy sources.** Other advisory sites in the corridor, checked the same way: agent-readiness first, then reuse terms, before any extraction is built.

**Agent-ready advocacy.** Offer the AquaSentinel pattern (MCP server, `llms.txt`, explicit reuse terms) to advisory publishers, so they become native-mode sources and legacy extraction is no longer needed.

**Agency-authorized automated sampling.** Raised 2026-09-26. The Sampling Coordinator Agent (Pillar 2) already drafts a confirmatory-sampling request to the agency when the model is uncertain; today that request assumes a human samples the water. Two real, distinct technologies could fulfill that request faster and more often than a human crew can, if operated *by the agency* (or under its permit):

- A stationary **automated water sampler** (e.g. the ISCO/Teledyne-style units already common in environmental monitoring) at a fixed point, drawing a bottled sample on a schedule or event trigger (e.g. "sample when the model flags Unsafe"). This is the more realistic near-term option — the same class of device agencies already deploy, just triggered by AquaSentinel's request instead of a human's.
- A moored **smart-buoy sensor array** for continuous in-situ sensing (temperature, conductivity, DO, pH, turbidity, and more) at multiple points across the reach, or an **autonomous surface vehicle** that can navigate to a sample site — both more capable but more experimental and further out.

Whichever device collects the water, the sample still needs an accredited lab culture — collection doesn't skip the 18-24 hour bottleneck this whole project is built around, and handling an official water-safety sample (cooling, chain of custody, time-to-analysis limits) is a regulated process. This is not something AquaSentinel would deploy or operate itself — the same jurisdictional reasoning that keeps alerting agency-first applies to physically entering a public tidal waterway to collect a sample that could inform an official health assessment. Designed, not built, and not budgeted for this hackathon's build window — a future direction for the request-fulfillment side of Pillar 2, not a new pillar.

## Key dates

- **Registered:** Aug 27, 2026 (IEEE Devpost) — done, no action outstanding.
- **Build window:** Sep 16–Oct 4, 2026 (extended from the original Sep 30 close).
- **Judging:** Oct 5–15, 2026.
- **Winners announced:** Oct 24, 2026 (IEEE iGET Conference).
- **Prize:** $3,000+ total; top 3 receive a $1,000 IEEE Merit Award each.

## Submission checklist

- [ ] Track alignment statement (both tracks)
- [ ] Project description (problem + impact)
- [ ] Demo video (3–5 min)
- [ ] Public GitHub repository with code
- [ ] Working prototype / proof-of-concept (virtual sensor, agent-ready dashboard live on Render, read-only MCP server, FHIR delivery to the RPHSA stub). The reader agent and the sampling agent are designed, not built (see Future directions).
