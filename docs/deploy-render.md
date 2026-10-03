# Deploying AquaSentinel to Render — checklist

Status: written 2026-10-02, **not yet run**. Render facts below come from Render's own docs
(render.com/docs/free, /python-version, /web-services, /deploy-fastapi), checked that day;
re-check anything that costs money. Nothing here is a secret — the only environment variable is
your public hostname.

## 0. Decide the instance type first (read this before step 3)
Render's docs say **free web services spin down after 15 minutes without a request**, take about a
minute to start again, and have an **ephemeral filesystem: local files are lost on redeploy,
restart or spin-down. Persistent disks are not available on the free tier. Paid instances do not
spin down.**

What that means for AquaSentinel:
- The hourly scheduler runs inside the app, so it sleeps when the service sleeps. After 2 hours
  without a pull, the status becomes "unavailable" (fail closed, by design).
- The SQLite database lives on that filesystem, so every spin-down wipes the stored readings,
  alert state and FHIR Subscription. The startup pull restores one reading within seconds of boot.
- Right after a wake-up, the first request to `/mcp` or `/api/status` can be slow, or say
  "no readings yet" until the startup pull finishes.

Recommendation: a free instance is fine for a first test of the deploy. For recording the demo and
for judging (Oct 5-15), use a **paid always-on instance**. Price not verified — check Render's
pricing page. (A cheaper trick is a periodic ping to keep a free instance awake, but it does not
fix the wiped database after a restart or redeploy.)

## 1. Put the code on GitHub (publishes it — needs your explicit OK each time)
- Create an empty **public** repo on github.com (no README, no .gitignore, no license; we have them).
- Secrets check done 2026-10-02: no key-like strings in tracked files, `.env` is not tracked,
  `.gitignore` covers `.env` and `*.db`. Re-run a check right before the first push.
- Then: `git remote add origin <repo URL>` and `git push -u origin main`.

## 2. Create the Render service
Sign in to render.com with GitHub, then New > Web Service > pick the repo.

| Setting | Value |
|---|---|
| Language | Python 3 (version comes from `.python-version`, which pins 3.11) |
| Branch | `main` |
| Build command | `pip install -r requirements.txt` |
| Start command | `uvicorn app.server:app --host 0.0.0.0 --port $PORT` |
| Instance type | see step 0 |
| Health check path | `/api/status` (answers 200 even when the status is "unavailable") |

Render requires the server to bind `0.0.0.0` and the `PORT` variable (default 10000); the start
command above does both.

## 3. Environment variable (required, or every MCP request is rejected)
| Key | Value |
|---|---|
| `AQUASENTINEL_ALLOWED_HOSTS` | your bare hostname, e.g. `aquasentinel.onrender.com` — **no** `https://`, no path |

The hostname is `<service name>.onrender.com`, so pick the service name first and set this at
creation. If it is wrong or missing the MCP endpoint answers `421 Invalid Host header`. A pasted
full URL is skipped and a warning is logged. Not needed: `AQUASENTINEL_BASE_URL` and
`RPHSA_BASE_URL` (see "What is not deployed").

## 3b. Persistent disk (recommended for an unattended demo)
Without a disk the SQLite file is wiped on every restart or redeploy, so the readings table drops
back to one row. A disk keeps the readings, alert state and FHIR records.
1. In the service: **Disks > Add Disk**. Name it anything, **Mount Path** `/var/data`, size **1 GB**
   (about $0.25/GB per month; you can grow a disk later but never shrink it). Needs a paid instance.
2. **Environment**: add `AQUASENTINEL_DB_PATH` = `/var/data/aquasentinel.db`.
3. Save. Render redeploys. From then on the data survives restarts and redeploys.

Trade-off (from Render's docs): a service with a disk cannot do zero-downtime deploys, so each
redeploy has a brief outage while the old instance stops and the new one starts. That only happens
when you push code or change a setting. Turn **Auto-Deploy off** before judging so nothing redeploys
by accident. The app creates the database folder if it is missing, and uses the repo-root file when
the variable is unset (local dev).

## 4. Verify the live service (replace HOST)
```
curl -s https://HOST/api/status                      # a reading, or "unavailable" for ~10s after boot
curl -s "https://HOST/api/readings?limit=3"          # stored rows
curl -s https://HOST/llms.txt | head
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://HOST/api/pull-reading   # expect 404
curl -s -X POST https://HOST/mcp -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"check","version":"0.1"}}}'
```
The last call should return an `initialize` result, not `421`.

## 5. Connect an assistant (the demo)
- **Claude Code (primary plan):** `claude mcp add --transport http aquasentinel https://HOST/mcp`,
  then ask "Is the Delaware at Penn's Landing safe for kayaking right now?" It should call
  `get_current_status`. Check the answer keeps the word "estimate".
- **Claude.ai / ChatGPT custom connectors:** same URL, no authentication. Availability depends on
  plan; not verified here.
- **Meta Muse (only if you get access):** Custom connector, MCP, auth "none". Third-party sources
  say this is supported; **not verified against Meta's docs and not tested.** Do not claim it works
  until you have seen it work.

Frame every demo as polling: a person asks their own assistant; AquaSentinel sends nothing to
anyone (see the no-public-alerting decision in `plan.md`).

## What is not deployed
The RPHSA stub (`app/rphsa_stub.py`) is a separate process. Without it, no FHIR Subscription
exists on the deployed app, so FHIR delivery is a quiet no-op there. Show the FHIR handshake and
Flag delivery locally in the video, with both processes running.

## Known limits to keep in mind
- Pulls run at startup and on the hour; a tier change is detected at most hourly.
- SQLite without a disk (step 3b skipped): history resets on restart or redeploy.
- Muse is untested; Claude is the primary demo assistant.
