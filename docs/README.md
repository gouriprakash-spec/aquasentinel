# AquaSentinel docs

Everything a builder needs, in one place. Paths below are relative to the repo root.

| File | What it is | Use it for |
|---|---|---|
| `docs/product-brief.md` | What AquaSentinel is and why: problem, architecture, both pillars, scope, demo, risks | Understanding intent and scope |
| `docs/landing-page/BUILD-SPEC.md` | Build instructions for the dashboard: data sources and endpoints, alert rules, agent-ready layer (MCP, `llms.txt`, JSON-LD) | What to build and how |
| `docs/landing-page/index.html` | Reference implementation of the dashboard (plain HTML/CSS/JS); two behaviors mocked and marked `TODO(real)` | Look, layout, and where real code plugs in |
| `docs/alert-rules-decisions.md` | The five alerting decisions and their reasons | The exact rule values |
| `docs/images/` | Maps: RiverCast coverage, the uncovered corridor, the Penn's Landing gauge setup | Context |
| `AquaSentinel-dataset/` | Training data, builder script, and `DATA-DICTIONARY.md` | Model training; read the dictionary before the CSVs |

## If documents disagree
1. `docs/alert-rules-decisions.md` (most specific, most recent)
2. `docs/landing-page/BUILD-SPEC.md`
3. `docs/product-brief.md`

If a conflict remains, stop and ask. Do not guess.

## Values not decided yet
Keep these as clearly marked placeholders in config until they are derived: the
low-confidence cutoff (from model validation), the forecast rain threshold T (from the rainfall
data), and the CSO outfall set near Penn's Landing (research).

## This repo will be public
Do not add secrets, tokens, phone numbers, or personal contact details to any file here.
