# AquaSentinel

**Real-time water safety warning for the tidal Delaware at Penn's Landing, Philadelphia.**

AquaSentinel is a *virtual water-quality sensor*. Every hour it estimates whether E. coli in the Center City reach of the Delaware River is likely above the recreational safety limit (235 CFU/100 mL), flags the reach **Safe** or **Unsafe**, and publishes that estimate three ways: a public dashboard, an API and read-only MCP server that AI assistants can query, and standards-based FHIR sent to a (fictional) public-health agency.

Built for the OneAquaHealth IEEE Global Hackathon 2026.

**Live dashboard: https://aquasentinel-prc9.onrender.com**

## Try it

| What | Where |
|---|---|
| Dashboard (latest reading, history table, outfall map) | https://aquasentinel-prc9.onrender.com |
| Current status as JSON | `/api/status` |
| Recent readings | `/api/readings?limit=24` |
| Sewer outfalls the overflow rule considered | `/api/outfalls` |
| Notes for AI agents | `/llms.txt` |
| MCP server (read-only) | `/mcp` |

### Ask an AI assistant

The MCP server has three read-only tools (`list_monitored_locations`, `get_current_status`, `get_recent_readings`). It needs no login and cannot change anything. With Claude Code:

```
claude mcp add --transport http aquasentinel https://aquasentinel-prc9.onrender.com/mcp
```

Then ask, for example: *"Is the Delaware at Penn's Landing safe for kayaking right now?"*

## How the status is decided

The status is decided by deterministic code. No AI agent decides it.

1. **Rainfall rule.** Unsafe when rain over the two previous calendar days (midnight to midnight, US/Eastern) is at least **2.5 mm**, read from the NWS station at Philadelphia airport (KPHL). The NWS record has gaps; if the hours that *did* resolve already reach 2.5 mm, that total is used as a lower bound and shown as "≥". Otherwise the pull falls back to the Open-Meteo weather model.
2. **Sewer-overflow rule.** If a combined-sewer outfall within 5 km of Penn's Landing, with a report from the last 24 hours, shows an overflow now or in the past 72 hours (PWD's CSOcast), the reach is flagged Unsafe. This can only raise the status, never lower it.
3. **Model (confidence only).** A random forest trained on past near-shore E. coli samples reads the USGS gauge (temperature, conductance, dissolved oxygen, pH) and produces the **rule/model agreement** figure. It never decides Safe or Unsafe. When the gauge has no current reading, the agreement and the water-quality columns show "n/a" and the status is still given.

Pulls run at the top of every hour. Visitors and assistants only read stored data, so nobody can trigger requests to the data sources.

**Fail closed:** if rainfall data can't be obtained at all, nothing is recorded and the status goes "unavailable" once the last reading is more than 2 hours old. It never shows an all-clear it doesn't have.

## For the public-health agency

Changes of state are sent as FHIR R4 resources (an Observation and a Flag) through a FHIR Subscription to a **stubbed, fictional agency** ("RPHSA"), year-round. The all-clear comes only after 48 continuous hours of Safe. AquaSentinel does **not** alert the public directly: deciding to warn citizens belongs to the agency, so FHIR to the agency is the only notification channel.

## Data sources

- [USGS Penn's Landing gauge 01467200](https://waterdata.usgs.gov/monitoring-location/01467200/): water temperature, conductance, dissolved oxygen, pH, turbidity
- [NWS station KPHL](https://forecast.weather.gov/data/obhistory/KPHL.html): hourly rainfall; [Open-Meteo](https://open-meteo.com/) as a fallback
- [CSOcast](https://water.phila.gov/maps/csocast/) (Philadelphia Water Department): sewer-outfall status
- [DRBC](https://www.nj.gov/drbc/) near-shore E. coli samples: used offline to train the model

## Run it locally

Requires Python 3.11.

```
python3.11 -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/uvicorn app.server:app --reload        # dashboard at http://localhost:8000
./venv/bin/python -m pytest app/tests/            # run the tests
```

A new local database is empty until the first top-of-the-hour pull (the app does not pull at startup). To try the FHIR delivery, run the agency stub alongside it:

```
./venv/bin/uvicorn app.rphsa_stub:app --port 8001 --reload
```

Optional environment variables are listed in `.env.example`. None of them are secrets.

## Honest limits

- **An estimate, not a measurement.** Two levels only (Safe / Unsafe at 235 CFU/100 mL). We say "estimate" and "flag elevated risk", and we never claim to predict illness.
- **Small validation set.** The model is trained and checked on 69 sampled days (30 unsafe). On that set the rainfall rule (F1 0.64) beat the model (F1 0.56), which is why the rule decides and the model only reports agreement. The rule's score is optimistic because its 2.5 mm threshold was chosen from the same days it is scored on, and the agreement signal is weak.
- **Rainfall data is imperfect.** NWS hourly reports have gaps, and the Open-Meteo fallback can undercount light rain.
- **The gauge can go quiet.** The USGS water-quality sensors sometimes stop reporting while flow sensors continue. The dashboard then shows "n/a" and says when they last reported.
- **CSOcast is a report, not a confirmed discharge,** and many nearby outfalls report "no data".
- **Design choices, not agency facts:** the 48-hour all-clear window and the 2-hour freshness limit.
- **Prototype.** The agency is fictional and the FHIR endpoint is a stub.

## More detail

- [`docs/README.md`](docs/README.md) indexes the design documents.
- [`docs/product-brief.md`](docs/product-brief.md): the problem and the design
- [`docs/alert-rules-decisions.md`](docs/alert-rules-decisions.md): the exact rules and why
- [`docs/deploy-render.md`](docs/deploy-render.md): deploying to Render
- [`PRD.md`](PRD.md) and [`plan.md`](plan.md): requirements and milestones
- [`SECURITY.md`](SECURITY.md): security rules for this public repo

## License

[MIT](LICENSE). This covers the code and docs in this repository. The data sources listed above keep their own terms.
