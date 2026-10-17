# revora submission audit — 17 October 2026

Production frontend: https://revora-graph-memory.vercel.app

This is a verified replay deployment in the user’s Vercel Hobby account. The latest deployment (`dpl_HCW3fpThjHZ4uja3s3mrxoW5NGdu`) is READY and aliased to production. Chrome confirms the four-node incident → analyst handoff → persisted evidence review → planner path and the memory-on/stateless HTTP retry comparison. The hosted frontend contains no backend or database credentials.

## Verified in this session

- 63 backend tests pass, covering local HTTP recovery, off-route rechecks, evidence-review refusals, and memory-disabled training isolation.
- The frontend was rebuilt as a "memory observatory" (dark instrument aesthetic, Geist Sans/Mono, CSS-only motion, 12 signature animations, lowercase voice). All protected strings and element roles were kept byte-identical, and the full live flow was re-verified end-to-end on a real backend in Chrome: cold run → learned run (`2 → 1` tool attempts), held-out tables with exactly 12 body rows and a unique `4/4`, live fault injection → measured restore → memory-blocked retry, accepted reviewer handoff, both laboratory replays, graph render, Groq agent stream, and a 390 px layout with zero horizontal overflow and no page crashes.
- The handoff now has a bounded receiving-side reviewer persisted as an `EvidenceReview` graph node with `REVIEWED_BY` and `FORWARDED_TO` edges. It recomputes error rate and p95 from per-request statuses and latencies, requests specific missing samples, and rejects measurements that conflict with the stated gate. Rejected or incomplete handoffs never invoke recovery.
- A new controlled HTTP retry ablation uses the same service/version/fault after restart. The graph-backed controller restores history and blocks the retry with zero candidate probes (routed HTTP 200); a stateless restart reintroduces the fault and observes HTTP 503. This is one local fixture, not a production failure-rate estimate.
- The public replay includes the controlled release retry comparison, reviewer outcome, and updated same-budget migration baseline.
- The acceptance run now restarts the FastAPI process while a measured incident is still active. The fresh process restores the controller state from FalkorDB and recovers through the authenticated `/api/coordination/tool/recover` route; the test verifies the post-restart state and idempotent handoff retry.
- The learning proof compares the same warm workload under the same two-attempt budget. The no-memory agent succeeds in two attempts; graph-backed memory succeeds in one. Both reach the same final outcome. Across the three fixed ablation cases, memory avoids two failed tool calls; it changes no final outcomes.
- Next.js production build and the updated Docker images build successfully. A fresh `npm ci` and production build also pass from the extracted submission bundle.
- Real FalkorDB acceptance passes using a uniquely named graph. A separate Python process consumes the saved diagnosis and performs recovery through the allowlisted tool. The run also checks isolated ablations, adverse-memory rejection, VALIDATES-edge removal, scoped contradiction semantics, atomic-failure handling, and recovery reuse after a simulated interruption.
- HTTP 200 → sustained 503 → rollback → HTTP 200 is measured; the same version is blocked without candidate samples.
- The analyst publishes a graph handoff. A **separate Python interpreter** receives only the graph/handoff IDs through its environment, reads the handoff from FalkorDB, and writes a decision linked to the source incident.
- Two consecutive ablation experiments retrieve exactly two observations in each memory condition and zero in each control, with no cross-experiment evidence reuse.
- Migration integrity checks preserve invoice count, value, and references.
- The retired prototype API and its graph labels are absent; the active graph contains no remaining prototype records.
- Chrome verifies the live workspace, actual failure/recovery, handoff, retry blocking, and ablation. Replay stage selection and source inspection work; the 390px layout has no horizontal overflow.
- The public replay requires no database or model credentials. The local `.env` is ignored and untracked; the deployment upload contains only frontend application files, public synthetic evidence, and build configuration.

## What changed

1. The candidate preflight runs while traffic stays on stable. Explicit rechecks of failed versions use the same protection.
2. The handoff reader and writer use the same node label. A receiver-side evidence reviewer checks raw measurements before an allowlisted tool can run; the planner reads the persisted handoff instead of relying on the analyst's in-process result.
3. Missing source incidents and missing handoffs are rejected instead of reporting a successful graph write.
4. Each ablation gets a unique memory scope. Retried requests cannot silently change memory mode.
5. A public evidence view presents the measured acceptance record, graph IDs, paired comparison, and integrity checks. The separate live workspace connects to an actual backend.

## Track 2 fit and remaining limits

The implementation demonstrates persistent episodic memory, bounded procedural tool choice, semantic belief revisions, and a durable handoff. FalkorDB is used by the receiving planner and later decisions, and graph edges expose the source of each handoff.

The analyst, evidence reviewer, and planner use deterministic policies. They do not establish independent LLM reasoning, authenticated multi-tenancy, high-concurrency correctness, arbitrary goal understanding, or production operations. The supported setup is one workspace and one API worker. A process-local lock is not a distributed coordination mechanism. Those capabilities should not be claimed.

The migration ablation changes first action in 3/3 supplied fixtures. Duplicate workloads need one attempt with memory and two without; both conditions eventually succeed. The same-budget comparison reports both succeed, while memory avoids one failed call. The separate controlled HTTP retry fixture shows a genuine observed outcome difference after restart: graph memory blocks the known failed version before probing; stateless memory loss permits an injected 503. The adverse migration suite still does not show superiority over its competent static baseline. These are bounded results, not general learning or a production reliability estimate.

The recovery controller persists its route and in-flight incident in a versioned FalkorDB state record. On restart it validates and restores the record; malformed state fails closed instead of guessing. This is still a single-service, single-worker demo. It is not a distributed lock or multi-tenant controller.

## Submission package

The sanitized source archive excludes environment secrets, Git history, backup directories, and the project-history PDF. The public deployment contains only the frontend and verified replay assets; it has no live backend credentials.

## Delivery still required

- Attach the repository (https://github.com/AnvitDevadiga/revora) through the hackathon submission panel.
- Record the continuous ~3 minute walkthrough (script in `docs/DEMO_SCRIPT.md`) and save it at `docs/artifacts/revora-demo.mp4`; add narration or captions if the panel requires them. The included two-checkpoint clip is not a continuous recording and should not be presented as one.
- Submit the repository, video, overview, and public replay/local instructions through the hackathon panel before 18 October 2026, 11:59 PM IST.
- For a fully hosted live demo, supply a persistent Python host and FalkorDB service. The Vercel frontend alone replays recorded evidence.

## Reproduce

```sh
docker compose up -d --build
cd backend
python -m pip install -r requirements.txt
python -m pytest -q
FALKORDB_URL=falkor://127.0.0.1:6379 PYTHONPATH=. python tests/integration_submission.py
```

The integration script intentionally leaves its uniquely named acceptance graph for inspection. Stop the stack with `docker compose stop` to retain the graph volume.
