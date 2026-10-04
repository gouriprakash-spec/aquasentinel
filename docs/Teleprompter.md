# AquaSentinel demo: teleprompter

How to read this: **SAY** lines are spoken aloud, word for word. **DO** lines are actions on screen; do not read them. **ON SCREEN** is the caption to show during that part (the captions also carry the message if the video ends up silent). Target length is about 4:35. Sections 4 and 5 are the longest on purpose.

Honest language rule: always say "estimate". Never say "predict" or "safe to swim".

---

## Before you hit record

- [ ] Muse trial run done: the connector works with `https://aquasentinel-prc9.onrender.com/mcp` and Muse calls `get_current_status`. If it fails, use Claude for Section 1.
- [ ] Record just after the top of the hour, so the reading is fresh (readings arrive hourly, there is no pull button).
- [ ] Local servers are up: main on port 8000, stub on port 8001 (stub started second).
- [ ] Browser tabs open: live dashboard, `/api/status`, `/api/readings?limit=24`, `/llms.txt`.
- [ ] Three terminal windows, large font, in the project folder.
- [ ] Notifications off; no private tabs or email visible.

---

## 0. Intro (about 0:20), on camera

**ON SCREEN:** "AquaSentinel: an hourly E. coli risk estimate for the Center City tidal Delaware"

**SAY:**
Hi, I'm Gouri. This is AquaSentinel.
After heavy rain, sewage can reach the Delaware River, and a lab test takes eighteen to twenty-four hours, so by the time you get a result, the water has already changed.
AquaSentinel is a virtual water-quality sensor. Every hour it estimates whether E. coli is above the recreational limit, and flags the river Safe or Unsafe.

---

## 1. Ask Muse (about 0:45)

**ON SCREEN:** "Asking an AI assistant. No login. Nothing pushed."

**DO:** Switch to the Muse window.

**SAY:**
First, the simplest way to use it: just ask.
I'm asking Muse, an AI assistant, a normal question.

**DO:** Type: `Is the Delaware at Penn's Landing safe for kayaking right now?` Send it. Wait for the tool call to `get_current_status` to appear, then for the answer. Pause on the word "estimate".

**SAY:**
Muse didn't scrape a web page. It called AquaSentinel's read-only server and got the same estimate we publish.
Notice it says "estimate", and keeps the caveats.
AquaSentinel pushes nothing to anyone. A person asks their own assistant.

(Fallback if Muse won't connect: say "Here it is in Claude" and do the same question there.)

---

## 2. The dashboard (about 0:50)

**ON SCREEN:** "The same reading, published for people"

**DO:** Switch to the live dashboard tab.

**SAY:**
Here is where that answer comes from. This is the public dashboard.
The banner at the top is the current status, Safe or Unsafe, against the 235 colony-forming-units limit.

**DO:** Point at the hourly table.

**SAY:**
This table is one row per hour.
If you see a "greater than or equal" sign on the rain total, some hourly rain reports were missing, so that number is a minimum, not an exact figure.
If you see "n/a" in the water-quality columns, the river gauge is silent right now. The status is still given, because the gauge only feeds the model's agreement figure. It never decides the status.

**DO:** Point at the outfall map.

**SAY:**
And this map shows the combined sewer outfalls near Penn's Landing.

---

## 3. How the status is decided (about 0:35)

**ON SCREEN:** "Code decides. Agents do not."

**SAY:**
Three pieces, and only the first two can decide.
One: the rainfall rule. If at least two and a half millimeters of rain fell over the two previous days, the river is flagged Unsafe.
Two: the sewer overflow rule. If a nearby outfall is overflowing or overflowed in the past three days, the river is flagged Unsafe. This rule can only raise the status. It can never lower it.
Three: a model trained on past water samples. It only reports how much it agrees with the rules. It never decides the status.
Deterministic code decides. Agents only read and report.

---

## 4. The agent-ready layer and how to connect (about 0:40)

**ON SCREEN:** "Same numbers. No scraping."

**DO:** Switch to the `/api/status` tab. Use pretty-print or zoom in so the fields are readable.

**SAY:**
Now, how Muse got that answer.
This is `/api/status`. The same estimate as plain JSON, no scraping.
You can see the risk tier, what decided it, and the label "model estimate".

**ON SCREEN:** "The day's history"

**DO:** Switch to the `/api/readings?limit=24` tab.

**SAY:**
And `/api/readings` gives the last twenty-four hours, one row per hour.

**ON SCREEN:** "A guide written for AI agents"

**DO:** Switch to the `/llms.txt` tab. Pause on the line that says an agent can read status, never change anything.

**SAY:**
This is `llms.txt`, a plain-text guide written for AI agents. It says what's available, and that an agent can read the status but never change anything.

**ON SCREEN:** "One line to connect. Read-only. No login."

**DO:** Switch to Terminal 1 and paste:
`claude mcp add --transport http aquasentinel https://aquasentinel-prc9.onrender.com/mcp`

**SAY:**
To connect an assistant, it's one line. There's no login, and there are three read-only tools: list locations, get the current status, and get recent readings.
For a custom connector like Muse or Claude.ai, you add a remote server with the same address, and set authentication to "none".

---

## 5. Fail closed, and the agency path (about 0:45)

**ON SCREEN:** "Missing data never gives an all-clear"

**DO:** Switch to the live `/api/status` tab and point at `"gauge_available": false` (only if the gauge is silent when you record). If it isn't silent, skip to the next block and say the line below as a statement, not a demo.

**SAY:**
The most important rule: fail closed. If data is missing, AquaSentinel says so. It never invents an all-clear. Right here the river gauge is silent, and the status is still given from rain and sewer data, with the water-quality columns showing "n/a".

**ON SCREEN:** "The only notification path: the agency"

**SAY:**
AquaSentinel sends no alerts to the public. That decision belongs to the public-health agency. Its one notification path is a standards-based FHIR message to an agency, in this demo a fictional one called RPHSA.

**DO:** Switch to Terminal 3 and paste:
`curl -s localhost:8001/rphsa/notifications | python3 -m json.tool`

**ON SCREEN:** "Step 1: the agency subscribes"

**SAY:**
Here's how it connects. The agency's system registered a FHIR Subscription: "tell me when the Flag for Penn's Landing changes."
AquaSentinel checked the request, and then called the agency's address back to confirm it was live. That's the entry you see here, marked "handshake".
That's a Subscription with a verification callback, which is our own convention on top of FHIR, not a standard FHIR payload.

**DO:** Point at `"kind": "handshake"`.

**ON SCREEN:** "Step 2: a status change sends a Flag"

**DO:** Switch to Terminal 2 and paste:
`./venv/bin/python -m pytest app/tests/test_fhir_end_to_end.py -v`

**SAY:**
Now the part that matters. When the status changes, from Safe to Unsafe, AquaSentinel sends the agency one FHIR bundle.
These tests show it end to end: an Unsafe onset arrives as an active Flag. The all-clear closes it as inactive. And if the agency's system is down, nothing breaks and nothing is lost silently.

**ON SCREEN:** "What the agency receives"

**SAY:**
That bundle holds the rainfall, the sewer overflow, and the river readings that were available. It includes the Safe or Unsafe estimate, marked preliminary, with links to exactly which readings decided it, so the agency can audit the decision. And it holds the Flag, active when an Unsafe period starts, inactive after the all-clear.
It only sends on a change of status, not on every reading, and it works all year round.

---

## 6. Close (about 0:20)

**ON SCREEN:** "An estimate, not a measurement. No public alerts. The agency decides."

**SAY:**
To be honest about the limits: this is an estimate, validated on a small set of samples.
The method is portable, so it can work for other rivers.
And there is deliberately no public alerting. That is the agency's call.
AquaSentinel: a virtual sensor that tells you the river's estimated state now, instead of tomorrow.

---

## If something goes wrong on the day

- **Muse won't connect:** use Claude for Section 1, and don't say Muse works.
- **Stub inbox is empty:** restart the stub (the main server must already be running).
- **Dashboard looks stale:** wait for the top of the hour and reload.
- **Local and live numbers differ:** the local server uses your own database. Say which one is on screen.
