# revora

**ai remembers. revora learns.**

Demo video: [`docs/artifacts/revora-demo.mp4`](docs/artifacts/revora-demo.mp4) — the team's ~3 minute walkthrough of the live incident loop (recording script and shot list: [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md)). Public replay: [revora-graph-memory.vercel.app](https://revora-graph-memory.vercel.app); fresh execution uses a separately connected backend.

A Graph Hacks project entered in **Track 2 — Agent Memory and Coordination** and **Track 1 — Agents That Act on Connected Data**: revora operates a live HTTP gateway, measures a candidate release, recovers from an introduced incident, and uses FalkorDB memory to prevent repeating the same failed version. A second workflow demonstrates evidence-driven customer migration with invoice-preservation checks.

## Project lead and AI assistance

**Project lead: Anvit Devadiga.** Anvit set revora's product direction and priorities, reported issues throughout development, and guided revisions through ongoing feedback and review. OpenAI Codex assisted with repository review, backend and frontend implementation, debugging, test support, and documentation. Anvit is responsible for the final design and submitted work.

## Start

```sh
docker compose up -d --build
```

Open http://localhost:3000, select **Live workspace**, and connect to http://localhost:8000. The default **Verified replay** view shows the sanitized acceptance record without a running API. Docker Desktop must be running for fresh execution. Compose starts FalkorDB, FastAPI and Next.js, bound to localhost only. On first launch the frontend image and dependencies may take several minutes to build.

For manual frontend development, start only the database/backend with `docker compose up -d --build falkordb backend`, then run `npm ci && npm run dev` inside frontend.

Stop with `docker compose stop`. The named database volume is retained. Do not use `down -v` unless you intend to delete all saved graph memory.

## Live incident response: the primary demo

The first panel runs a real local HTTP gateway with separate stable and candidate services. `GET /api/release/live` probes the route a caller actually receives; `GET /api/release/traffic` exposes the routed response with its actual HTTP status. The judge demo is a visible three-step sequence:

1. **Introduce failure** (`POST /api/release/inject`): route to a faulty project-owned candidate and leave it active. The default fault gives HTTP 503. The before state is HTTP 200; the during state is HTTP 503, including at `/api/release/traffic`.
2. **Diagnose & restore** (`POST /api/release/recover`): measure 12 HTTP requests through the broken gateway, restore the stable route when the error or p95 latency gate fails, and verify three healthy requests. The after state is HTTP 200.
3. **Retry same version**: retrieve the measured failure from FalkorDB and block the candidate before it serves another request. The new decision links to the earlier incident with `BASED_ON`.

The introduced fault and its recovery are separate graph records. The fault is intentional; HTTP responses, route changes, measurements and recovery are real operations within the running local service. Every candidate is measured directly while traffic stays on stable; promotion happens only after the candidate passes. **Force fresh check** reruns that off-route preflight for a repaired version previously blocked by memory. The one-call `/api/release/run` remains available for automated checks and configured-service preflight.

To reproduce the staged judge flow from a clean local stack, start Compose, then run the dashboard steps above. For an API-only check, use a new version each time and follow this order: `GET /api/release/live` should report 200; `POST /api/release/inject` with `{"version":"judge-v1","mode":"errors"}`; `GET /api/release/traffic` should return 503; `POST /api/release/recover`; `GET /api/release/traffic` should return 200; repeat the same inject request and confirm `decision` is `blocked_from_memory` with an empty `canary_samples` list. The API returns the actual traffic status, so the 503 probe can be captured with `curl -sS -o /dev/null -w '%{http_code}\n'` without treating the expected 503 as a shell failure. The recovery response should contain 12 failing measurements and three healthy verification requests.

Each incident is stored as a `ReleaseIncident` in FalkorDB with its measurements and outcome. Retrying the exact failed version retrieves that incident and prevents the candidate from serving any new requests. The new decision has a `BASED_ON` relationship to the earlier incident. A fresh version is measured again; **Force fresh check** overrides the memory block for a repaired candidate that keeps the same version. The history panel and `GET /api/release/history` expose the incident trail. Restarting the backend resets the gateway to the healthy stable route while retaining graph memory.

This is a project-owned service and controlled fault injection, not a connection to an external production deployment. Its value is that it demonstrates real detection, routing, verification, and memory-driven prevention in one inspectable loop. The older Groq deployment panel below it still uses disposable fixtures and synthetic latency/error inputs; do not present that panel as the live controller.

### Graph-backed analyst → reviewer → planner handoff

After an incident is recorded, click **Run agent handoff**. The Release Analyst summarizes measurements from the stored incident. A separate evidence reviewer recomputes the gate from raw HTTP statuses and latencies. It can accept the handoff, reject contradictory measurements, or request specific missing evidence. Recovery tools are allowlisted and never run for incomplete or rejected evidence. FalkorDB stores the reviewer as an `EvidenceReview` node with `AgentHandoff → REVIEWED_BY → EvidenceReview → FORWARDED_TO → CoordinationDecision` edges; the plan also links to its source incident through `BASED_ON`. This is an application-controlled deterministic workflow, not autonomous LLM agents.

### Connect actual HTTP services

An operator can configure two services reachable from the backend container in the root `.env`:

```text
REVORA_SERVICE_NAME=checkout
REVORA_STABLE_URL=http://stable-service:8080
REVORA_CANDIDATE_URL=http://candidate-service:8080
```

Both services must answer `GET /health`. revora uses the configured URLs only; the browser cannot supply arbitrary targets. Rebuild with `docker compose up -d --build backend frontend`. In this mode the fault selector disappears. Before updating the candidate endpoint, click **Park on stable**. After the new version is reachable, enter its version ID and click **Gate candidate release**. revora sends 12 direct HTTP health requests to the candidate while the gateway remains on stable. It routes to candidate only if error and p95 latency gates pass, then verifies the routed response. A failed candidate remains off the gateway and becomes graph evidence; repeat attempts at the same version are blocked unless explicitly rechecked. Callers can inspect the active routed health response at `http://localhost:8000/api/release/traffic`.

This is a health gate for one operator-configured service pair, not a general reverse proxy or cloud deployment controller. It does not deploy code, inspect production logs, or replace a platform load balancer. The supported API deployment is exactly one worker and one workspace: route state, rate limits and local gateway ownership remain process-local. The rate limit groups valid requests by the shared workspace API key, not by individual user. Behind a reverse proxy, set `FORWARDED_ALLOW_IPS` to the exact trusted proxy addresses so client IP fallback is accurate; do not use a wildcard unless the entire proxy chain is trusted. The model-backed agent is limited to one in-flight run because its tool state is process-scoped; concurrent JSON requests receive HTTP 429 with `Retry-After`, while the SSE endpoint emits a typed error event carrying the same status and retry hint. Production startup requires both `REVORA_API_KEY` and `FALKORDB_URL`; in-memory graph fallback is disabled unless explicitly enabled. The controlled demo mode remains the default when both service URLs are empty.

## Customer migration learning proof

Click **Run learning proof** in the dashboard. revora creates a fresh memory cohort and runs a two-round customer-identity migration in disposable SQLite. The cold round encounters a real unique-index constraint failure, rolls back, then recovers. Synthetic invoices reference the customer rows; recovery keeps one canonical identity, re-points every invoice, writes a merge-audit row, and creates the index only after reconciliation. It verifies invoice count/value, uniqueness, and foreign-key integrity.

The second round uses a larger dataset with different duplicate positions, but the same task context. It retrieves the first run's observations from FalkorDB and must select recovery on its first attempt. A no-memory agent runs the identical workload with the same two-attempt recovery budget. In the verified fixture, both agents succeed; memory saves one failed attempt. `LEARNING PROOF PASSED` requires cold failure and recovery, at least two retrieved observations, a changed first action, equal successful final outcomes, fewer attempts with memory, and all database invariants. This supports a narrow efficiency claim, not a general learning or success-rate claim. Each click creates a fresh cohort.

Click **Run memory ablation** to run three paired synthetic workloads in fresh contexts with graph retrieval enabled and disabled. It reports first action, success, attempts, failed tool calls, and retrieved observations on identical input values. In these fixed fixtures, graph retrieval changes the first action in 3/3 cases and avoids two failed calls; final success is unchanged because the no-memory agent can retry. A separate HTTP acceptance ablation repeats the same failed release after process restart: graph memory blocks the candidate with zero probes and serves HTTP 200, while a stateless control without persisted history reintroduces the fault and serves HTTP 503. This is a controlled local demonstration of persistent memory's consequence, not a production reliability benchmark.

Context includes service, environment and workload. Every request has a retry ID. Repeating a request returns its original result; reusing its ID for different input returns 409. Destructive identity reconciliation requires explicit `merge_authorized: true`; the API defaults permission to `false` and returns review-required without deleting rows.

The graph stores AgentRun, AgentDecision, AgentObservation and BeliefVersion nodes. Decisions link to prior observations through BASED_ON; observations link to revisions. The API traverses the current context's OBSERVED edges to retrieve evidence. Each run's graph records are persisted in one mutation. `POST /api/runtime/proof` creates a fresh cohort and returns the memory run, same-budget no-memory run, direct one-shot reference, invariant results, and a machine-checked proof verdict. `POST /api/runtime/ablation` executes the paired memory-on/memory-off comparison. `POST /api/coordination/run` accepts an incident ID from saved release history and records the analyst → reviewer → planner workflow; `GET /api/coordination/history` returns saved handoffs.

## Graph model and Cypher

The backend talks to FalkorDB with parameterised Cypher in graph `revora`. Node and relationship types are fixed in source; IDs, versions and scope values are query parameters. Representative queries from the running code:

```cypher
-- release memory: find the stored measured failure for this exact service + version
MATCH (i:ReleaseIncident {service:$service, version:$version})
WHERE i.decision IN ['rollback','promote']
RETURN i.id,i.timestamp,i.failed ORDER BY i.timestamp DESC LIMIT 1

-- reviewer handoff recorded as one mutation: incident → handoff → review → decision
MATCH (i:ReleaseIncident {id:$incident_id})-[:HANDED_OFF]->(h:AgentHandoff {id:$handoff_id})
CREATE (r:EvidenceReview {id:$review_id,agent:'evidence_reviewer',status:$review_status,payload:$review_payload,created_at:$created_at})
CREATE (h)-[:REVIEWED_BY]->(r)
CREATE (d:CoordinationDecision {id:$id,agent:'recovery_planner',action:$action,reason:$reason,payload:$payload,review_status:$review_status,execution_status:$execution_status})
CREATE (r)-[:FORWARDED_TO]->(d)
CREATE (h)-[:HANDED_OFF_TO]->(d) CREATE (d)-[:BASED_ON]->(i)
-- plus optional RETRIEVED (reused prior handoff) and EXECUTED (recovery outcome) edges

-- migration memory: retrieve observations that validate an active procedure
MATCH (r:AgentRun {context:$context})-[:OBSERVED]->(o:AgentObservation)
WHERE r.memory_enabled = true
OPTIONAL MATCH (o)-[:VALIDATES]->(p:VerifiedProcedure {status:'active'})
RETURN o.payload,r.schema_version,p.id ORDER BY r.created_at,o.id
```

Release-memory schema (incidents, handoffs, reviews, decisions, controller state):

```text
(ReleaseIncident)-[:HANDED_OFF]->(AgentHandoff)
(AgentHandoff)-[:REVIEWED_BY]->(EvidenceReview)
(EvidenceReview)-[:FORWARDED_TO]->(CoordinationDecision)
(AgentHandoff)-[:HANDED_OFF_TO]->(CoordinationDecision)
(CoordinationDecision)-[:BASED_ON]->(ReleaseIncident)     // decision links to its source incident
(CoordinationDecision)-[:EXECUTED]->(ReleaseIncident)      // recovery outcome
(CoordinationDecision)-[:RETRIEVED]->(AgentHandoff)        // reused handoff on retry
(ReleaseIncident)-[:BASED_ON]->(ReleaseIncident)           // retry links to the prior failure
(ReleaseControllerState {service})                         // versioned route + in-flight incident
```

Migration/learning schema:

```text
(AgentRun)-[:OBSERVED]->(AgentObservation)
(AgentObservation)-[:VALIDATES]->(VerifiedProcedure)
(AgentDecision)-[:BASED_ON]->(AgentObservation)
```

The deployment-memory schema used by the optional Groq fixture is listed under "Groq reasoning and deployment tools". Retry decisions repeatedly use `BASED_ON`, and the accepted replay record exposes the live graph (`/api/graph/state`) with its actual nodes and edges.

## Groq reasoning and deployment tools

The optional Groq agent uses Groq's OpenAI-compatible Chat Completions endpoint and local function calling. The configurable default is `openai/gpt-oss-20b`. Its allowlist requires five ordered tools: search exact-context FalkorDB memory, inspect a deployment plan, run a disposable HTTP canary, record the observed result, and apply the verified promote/rollback decision to the demo fixture. Every tool result is produced by application code, and mismatched or reordered calls are rejected. The release action is sandboxed and has no production or cloud credentials. This is the product's model-backed workflow; the release analyst, evidence reviewer, and recovery planner are bounded deterministic roles.

A belief is now an explicit gate-outcome expectation scoped to a service and scenario. An opposite observation in that same scope revises the expectation; a different scenario does not contradict it. Optional Groq reflection is retained as a non-authoritative proposal. Current measured gates always control release safety. Experience, evidence, belief revision, and links are written in one graph mutation.

The deployment memory schema is:

```text
(Experience)-[:LED_TO]->(Evidence)-[:SUPPORTS|CONTRADICTS]->(Belief)
(Belief)-[:UPDATES {reason}]->(Belief)
```

FalkorDB is central to the primary release-memory demo: the backend stores incidents, controller state, analyst handoffs, reviewer/planner decisions, and their provenance edges; a restarted process retrieves graph state to gate retries. The customer migration and Groq deployment panels are bounded synthetic workflows, not evidence that every panel is production-grade graph memory. With no `FALKORDB_URL`, the local API uses visibly labeled temporary in-process memory and cannot demonstrate restart persistence.

`Experience` stores the action, target/context, outcome, and timestamp. `Evidence` stores the source tool, description, timestamp, and observed/synthetic metrics. `Belief` stores a statement, confidence score, active/revised status, scope, and outcome. FalkorDB range indexes support exact service and belief-scope lookups. Query parameters are used for values; relationship and node types are fixed in source.

The API exposes `POST /api/agent/run` for a JSON result and `POST /api/agent/stream` for ordered Server-Sent Events over a POST request. The dashboard consumes the stream and shows tool starts/results as they arrive. If a client disconnects, cancellation stops event delivery only; the bounded worker continues the active run through its remaining model/tool steps, then releases the single agent slot. Agent capacity is one and excess JSON calls fail fast with HTTP 429; the SSE stream reports saturation in its first error event because HTTP headers are already sent when background work begins. `GET /api/graph/state` returns the connected evidence graph, `GET /api/graph/neighborhood?query=...` returns matching nodes and adjacent paths, and `GET /api/memories/history` returns the newest audit entries and belief links.

Groq references: [OpenAI compatibility](https://console.groq.com/docs/openai), [local tool calling](https://console.groq.com/docs/tool-use/local-tool-calling), and [supported models](https://console.groq.com/docs/models).

Create a root `.env` file locally:

```text
GROQ_API_KEY=your_private_key
GROQ_MODEL=openai/gpt-oss-20b
```

Then run `docker compose up -d --build backend frontend`. The key is injected only into the backend container, is ignored by Git, and is never sent to the browser. Free-tier limits can return rate-limit errors; the deterministic runtime remains usable without Groq.

The deployment toolbox makes a real HTTP request to a temporary localhost health endpoint. Readiness status is observed; latency and error-rate values are labeled synthetic fixtures. Any failed gate produces rollback. It cannot access Docker, shell commands, cloud accounts, or production targets.

SQLite is a real execution sandbox, but all data is disposable and synthetic. No production systems are operated. The migration planner is a transparent deterministic evidence policy, not an LLM: tool outcomes and integrity checks are measured by SQLite, while FalkorDB retrieval changes the next tool recommendation. Duplicate resolution retains the first customer ID, reassigns its synthetic invoice rows, and records the merge. Confidence scores are not calibrated safety guarantees.

## Evaluation and replay

The original eight-case migration comparison is retained as a bounded synthetic result, not evidence of superiority over a strong policy. The adverse suite uses isolated training and evaluation scopes across duplicate, clean, approval, schema-change, and unrelated-service cases. It reports retrieval rejection reasons, failed attempts, final success, and a static precondition-aware baseline. Memory changes choices in fixed cases, but this suite does not show it outperforming that competent baseline or demonstrate general learning.

The replay laboratory separately demonstrates deployment and migration stories. These are synthetic fixtures, clearly labeled in the UI, with revision timelines and evidence-removal analysis. Saved replay IDs avoid duplicate evidence.

## Verification

Use Python 3.12 for local backend setup. Python 3.14 is not supported by the pinned Pydantic native dependency. The active backend is `backend/app`, used by Docker, Compose, CI and the frontend API. The unshipped earlier backend experiment is archived under `archive/unwired-backend-experiment/`; it is not imported, deployed, or included in the active backend image.

```sh
cd backend
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest
```

Real database acceptance scripts are `backend/tests/integration_runtime.py` and `backend/tests/integration_lab.py`; supply FALKORDB_URL and PYTHONPATH=backend. They create uniquely named test graphs. Run only against a disposable test database.

Frontend production build: `cd frontend && npm ci && npm run build`.
Browser acceptance: `frontend/tests/browser.cjs` uses Playwright and installed Chrome, with the local API configured with test key `local-audit-key`. This is a test fixture, not a production secret. It records a walkthrough and screenshots under docs/artifacts.

## Scope and operating limits

- One local workspace, one API worker. Locks are process-local; multi-worker correctness, distributed coordination, and authenticated multi-tenant memory isolation are not implemented or claimed.
- Up to 1,000 runs per graph and 1,000 input rows per tool run.
- With no FALKORDB_URL the API uses temporary memory, visibly identified in the UI; Compose always configures FalkorDB.
- Optional REVORA_API_KEY protects API routes; health and CORS preflight remain accessible.
- The retired pre-event episode/procedure prototype and its routes are removed. Active memory uses the documented Experience/Evidence/Belief and AgentRun graph schemas.
- The public frontend is a clearly labeled replay of verified local acceptance evidence. A hosted live Python backend and FalkorDB are not included; live mode connects to an operator-provided backend.

See [PROJECT_STATUS.md](PROJECT_STATUS.md), [demo script](docs/DEMO_SCRIPT.md), and [submission audit](docs/SUBMISSION_AUDIT.md).

## Submission acceptance and public replay

Run `FALKORDB_URL=falkor://127.0.0.1:6379 PYTHONPATH=. python tests/integration_submission.py` from `backend/`. It creates a uniquely named graph, launches a receiving worker in a separate Python interpreter, restarts the FastAPI process with an incident active, and verifies restored-state recovery through the authenticated tool endpoint. It also checks graph links, isolated ablations, adverse-memory rejection, edge-removal behavior, scoped contradiction semantics, failed mutation safety, and interrupted-handoff retry, then exports evidence to `frontend/public/evidence.json` and `docs/artifacts/submission-evidence.json`. The test graph remains available for inspection. The public replay reads this acceptance record; it does not fabricate live API responses.

## Audit corrections and boundaries

The current backend uses scoped outcome expectations, a single mutation per belief revision, explicit memory rejection reasons, and active graph procedure support paths. The investigator and recovery worker can execute through a persisted handoff rather than merely annotate an already repaired incident. See the [fix ledger](docs/FIX_LEDGER.md) for the audit changes and evidence.

The action space remains predefined. `VerifiedProcedure` means a predefined tool sequence has supporting observed outcomes; it does not mean the system synthesized a new procedure. The fair warm-task baseline has the same two-attempt recovery budget and succeeds; graph memory reaches that same outcome while avoiding one failed tool call. The three-case ablation avoids two failed calls and changes no outcomes. The adverse evaluation includes competent static policies and does not establish general superiority over them. Baseline success and recovery costs are reported even when they weaken the project's performance claim.

Use `POST /api/coordination/diagnose` to measure and publish a handoff, then `POST /api/coordination/execute` with its `handoff_id` from a separate worker. `POST /api/coordination/recover` performs both steps for the UI. Expired or mismatched handoffs are rejected. The recovery tool reloads authoritative graph evidence and accepts no arbitrary command or target URL.

Run `POST /api/evaluation/adversarial` for the versioned adverse cases. Migration requests support `schema_version` and `merge_authorized`; denied merges require review. The separate Groq fixture reports simulated release decisions with `executed=false`; it does not operate the live gateway.
