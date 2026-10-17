# revora — reproduce the evidence

The public Vercel site replays a captured, verified local acceptance run. A live backend is not hosted by that deployment.

## Requirements

Docker Desktop with Compose; Python 3.12 for acceptance checks. FalkorDB is the memory database. Local HTTP services and SQLite records are controlled synthetic fixtures.

## Start the live stack

From the project root:

```sh
docker compose up -d --build
```

Open http://localhost:3000, select **Live workspace**, enter http://localhost:8000, and connect. The status should identify persistent FalkorDB memory. Compose binds all exposed ports to localhost. Graph data remains in the named volume when services stop.

## Reproduce the recorded proof

1. Introduce a failure with a fresh candidate version.
2. Probe again: the gateway remains at HTTP 503.
3. Investigate, hand off, and restore: twelve measurements precede rollback; three healthy requests verify recovery.
4. Retry the same version: the graph blocks it without new candidate samples.
5. Run agent handoff and inspect the source incident and planner decision.
6. Run the migration learning proof and paired memory ablation.

## Run independent acceptance

```sh
cd backend
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q
FALKORDB_URL=falkor://127.0.0.1:6379 PYTHONPATH=. python tests/integration_submission.py
```

The acceptance script uses a uniquely named test graph and leaves it available for inspection. It verifies HTTP 200 → 503 → 200, a blocked retry, a separate worker process reading a persisted handoff, a restarted FastAPI process restoring the active incident from FalkorDB, and recovery through its actual authenticated bounded HTTP tool. It also checks graph provenance and isolated ablations. The retired pre-event episode/procedure prototype is no longer part of the API or active graph. The script exports sanitized synthetic evidence to `frontend/public/evidence.json` and `docs/artifacts/submission-evidence.json`.

## Interpret the results

The analyst, reviewer, and planner use constrained deterministic policies. They are not autonomous LLM agents. The reviewer checks raw sample counts, statuses, latencies, and gate consistency. It can request missing evidence or reject contradictions without calling the recovery tool; its result is persisted as an `EvidenceReview` graph node between the handoff and plan. The same-workload, same-budget migration comparison shows the no-memory agent also succeeds, taking two attempts where memory takes one. Across the three migration ablation fixtures, memory avoids two failed calls and changes no final outcomes. A separate controlled HTTP retry ablation shows graph memory blocks the same previously failed version after restart (zero retry probes; routed HTTP 200), while a stateless restart reintroduces the injected fault (one observed HTTP 503). These fixtures do not establish general learning or production reliability.

## Hosting

The Next.js dashboard can run on Vercel Hobby. The live Python gateway needs a persistent process, and the graph needs a reachable FalkorDB instance. Those services must be deployed separately. Keep keys in backend environment settings; do not put database or Groq credentials into the frontend. The public replay requires no credentials and makes no API calls to a local backend.

## Evaluation limits

The adverse suite exposes fixed guarded and static precondition-aware baselines. Removing graph procedure support withholds the learned action. The reviewer refusal tests verify that incomplete or contradictory evidence cannot invoke recovery. The interrupted-worker test verifies recovery reuse after a decision-commit failure. The supported deployment is one workspace and one API worker; process-local locks do not provide distributed concurrency or tenant isolation. See `docs/FIX_LEDGER.md` for implementation scope and verified results.

FalkorDB is required to reproduce persistent incident memory and restart behavior. If the API starts without `FALKORDB_URL`, it uses temporary process memory; that mode is useful for UI exploration but does not satisfy the persistence demonstration. Coordination roles are deterministic application components, not independent LLM agents. The migration and Groq panels remain bounded demonstrations, not production evaluation.
