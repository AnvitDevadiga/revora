# revora demo video script

Target: about 3 minutes. Record `http://localhost:3000` with the full browser visible. A fresh candidate version appears on each page load. Keep the same version for the first failure and later retry. The fault is deliberate and local; the HTTP status, traffic route, measurements, recovery, and FalkorDB writes are real. Start a fresh stack with `docker compose up -d --build`, wait for the dashboard to show persistent FalkorDB memory, and probe the live service before recording.

Recording deliverables:

- `docs/artifacts/revora-demo.mp4` — the ~3 minute continuous walkthrough, recorded by the team from this script and saved at this path. Add an `.srt` captions sidecar if captions are burned in.
- `docs/artifacts/current-demo.mp4` and `frontend/public/demo.mp4` — a 14-second two-checkpoint clip (active HTTP 503; graph handoff recovery with three HTTP 200 checks) with `demo.vtt` captions, served in the replay page's walkthrough section. It is explicitly not a continuous screen recording.
- `docs/artifacts/demo-walkthrough.webm` — the silent automated acceptance recording produced by `frontend/tests/browser.cjs`.

Captions or a voice-over can be added on top of the recorded walkthrough if the submission panel requires them.

## 0:00–0:18 — Problem

**Screen:** revora heading and live incident panel.

**Say:** “Software agents remember instructions, but often forget what their actions caused. When a release fails, revora records the evidence and changes the next release decision.”

## 0:18–0:38 — Healthy baseline

**Screen:** Click **probe again**. Point to **public endpoint: HTTP 200**, stable route, and the generated version field.

**Say:** “This is a running HTTP gateway. The service is healthy right now. I’m going to introduce a faulty candidate and leave it active so you can verify the failure yourself.”

## 0:38–1:00 — Make the failure observable

**Screen:** Keep **Every request fails · HTTP 503** selected. Click **1. introduce failure**. Pause on the red live status. Click **probe again**. Show **during · live: HTTP 503**.

**Say:** “The service is now returning HTTP 503. It stays broken when I probe it again. This is a controlled fault in revora’s local service, and that status comes from an actual request to the running endpoint.”

## 1:00–1:30 — Diagnose and recover

**Screen:** Click **2. investigate, hand off & restore**. Point to **BEFORE 200 → DURING 503 → AFTER 200**, twelve red measured requests, and three healthy recovery checks.

**Say:** “revora measures twelve requests while the bad version is live. The error gate fails, so it changes the gateway route back to stable. It then verifies three successful requests. We can see the outage and the repair as separate states.”

## 1:30–1:53 — Learning from the incident

**Screen:** Click **3. retry same version**. Show **known regression blocked**, zero candidate requests, and the prior incident ID in the trail.

**Say:** “Now I try the same version again. revora retrieves the earlier measured rollback from FalkorDB and blocks the candidate before it serves traffic. The new decision has a BASED_ON link to the recorded incident.”

## 1:53–2:18 — Evidence-reviewed graph handoff

**Screen:** On the recovered incident, click **run agent handoff**. Expand the handoff evidence. Show Release Analyst → Evidence Reviewer → Recovery Planner and the source incident ID.

**Say:** “The analyst passes measured evidence. An independent bounded reviewer checks the raw measurements and can reject contradictions or request specific missing evidence before the planner can call a recovery tool. FalkorDB keeps the handoff connected to its source incident.”

## 2:18–2:43 — Memory ablation

**Screen:** Show the recorded persistent-memory HTTP retry comparison: FalkorDB restart blocks the same failed version with zero candidate probes and HTTP 200; stateless restart reintroduces the injected failure and returns HTTP 503. Then, if time permits, show the paired migration rows: graph retrieval changes first action in three fixtures while final outcomes stay the same.

**Say:** “The same release is retried after recovery. The graph-backed process restores the prior incident and blocks it before probing; a stateless restart has no history and sees the injected HTTP 503 again. This is controlled local evidence, not a production failure-rate estimate. In the separate migration comparison, both approaches also succeed, but memory avoids retries on the duplicate fixtures.”

## 2:43–3:00 — A second task

**Screen:** Scroll to **run learning proof**. Click it and point to the cold failure, learned first action, no-memory baseline, and invoice integrity checks.

**Say:** “The same memory mechanism is also tested on a database migration. The first attempt hits a real uniqueness constraint. revora recovers while preserving linked invoices. On the larger workload, both memory and no-memory agents succeed under the same retry budget; memory avoids one failed attempt. This proves a bounded efficiency gain, not a higher success rate.”

## 3:00–3:12 — Close

**Screen:** Return to the incident trail and 200 → 503 → 200 result.

**Say:** “The useful result is an inspectable loop: detect a real failure, restore service, retain the evidence, and prevent the same failed release. This demo runs in a controlled local environment; operator-configured HTTP services can use the same gate.”

## Judge questions

- **Is the failure real?** “The fault is intentionally introduced in a project-owned service. The gateway really returns HTTP 503, then HTTP 200 after routing changes. The endpoint can be probed repeatedly while broken.”
- **What does FalkorDB do?** “It persists the incident and the BASED_ON link. After a backend restart, the same failed version remains blocked.”
- **Does an LLM decide the safety gate?** “No. The live controller applies measured HTTP thresholds. The separate Groq agent handles tool-based deployment reasoning; neither path can turn a failed health check into a pass.”
- **Is this connected to production?** “No external deployment was connected for this video. revora supports operator-configured stable/candidate HTTP endpoints, but the recorded example uses controlled local services.”

## Current backend proof

Show the receiving worker executing recovery from the graph handoff, then show EXECUTED linking its decision to the recovery outcome. Run the adverse evaluation and show changed-schema and permission refusals. State plainly that the competent static baseline can match these fixed tasks. Show the edge-removal acceptance result changing reconciliation to review.
