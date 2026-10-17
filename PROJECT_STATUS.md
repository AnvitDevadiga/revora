# Verified local MVP status — 17 October 2026

The first three phases describe the bounded, single-workspace sandbox product below. The public frontend provides a clearly labeled replay of verified local evidence; live backend hosting remains separate. Coordination is a deterministic application workflow with analyst, evidence reviewer, and planner roles; it does not claim autonomous LLM agents.

## Phase 1 — local foundation
- [x] Next.js, FastAPI and FalkorDB configuration
- [x] Localhost-only ports and persistent graph volume
- [x] Database health check and optional API-key protection
- [x] Input validation, CORS and request limits
- [x] Real database reload and idempotency acceptance tests

## Phase 2 — memory-driven action
- [x] Context-specific graph evidence retrieval
- [x] Real SQLite tool execution and observed constraint failures
- [x] Bounded recovery loop and outcome ingestion
- [x] Next-run tool choice changes from persisted experience
- [x] Immutable observation, decision and belief-version records
- [x] Failed experience retained and linked to revisions
- [x] Fixed held-out synthetic comparison with transparent baseline
- [x] Request retries and input-conflict handling

## Phase 3 — inspectable local demo
- [x] Interactive decision/evidence diagram
- [x] Explanation of recommendation changes
- [x] Saved run history and complete decision records
- [x] Two idempotent scenario replays and evidence-removal analysis
- [x] Comparison dashboard with per-case results and data-removal costs
- [x] Chrome desktop and 390px mobile acceptance
- [x] Recorded automated walkthrough and narration script
- [x] Dedicated Experience → Evidence → Belief graph for deployment outcomes
- [x] Contradiction links, revised belief versions, and UPDATES lineage
- [x] Groq reflection proposal with deterministic evidence validation and fallback
- [x] Five-step ordered deployment agent with streamed tool events
- [x] Graph state, neighborhood, and memory-history API routes
- [x] Live graph and belief-history dashboard
- [x] Real local HTTP gateway with stable/candidate routing and measured release checks
- [x] Actual rollback to stable after observed errors or latency regression
- [x] Recovery verified with three post-rollback HTTP requests
- [x] FalkorDB incident trail and BASED_ON links for blocked repeat releases
- [x] Healthy new version promotion and persistent memory after backend restart
- [x] Optional operator-configured stable/candidate HTTP endpoints with candidate preflight
- [x] Stable-route parking before candidate updates and version-specific graph blocking
- [x] Staged incident demo with persistent public HTTP 503, measured recovery, and visible 200 → 503 → 200 states
- [x] Candidate preflight stays off-route until measured gates pass, including explicit fresh checks of previously failed versions
- [x] Release Analyst → Evidence Reviewer → Recovery Planner handoff with persisted incident provenance and graph links
- [x] Receiver-side EvidenceReview graph node; incomplete evidence requests specific measurements and contradictory evidence blocks tool execution
- [x] Paired memory retrieval on/off ablation for fixed synthetic SQLite workloads
- [x] Persistent release retry ablation: graph-restored memory blocks a repeated fault after restart; stateless control observes HTTP 503

## Phase 4 — memory observatory frontend

- [x] Full visual revamp: dark instrument design ("the memory observatory"), Geist Sans/Mono with tabular numerals, hairline structure, CSS-only motion — 12 signature animations (boot reveal with wordmark draw, status morph, stage filmstrip, route lanes with real probe ticks, handoff chain with reviewer stamp, graph draw-in with delta-only updates, stream lines, ablation growth, view switch, connect flow, scroll spine, microinteractions)
- [x] All protected strings and roles byte-identical; fetch/SSE logic, endpoints and conditional rendering unchanged
- [x] Lowercase brand voice across UI copy, metadata, aria-labels and empty states; graph relation names surfaced honestly in the UI
- [x] Live end-to-end re-verification in Chrome against the real backend (cold/learned runs, 12-row evaluation tables, injected failure → restore → memory-blocked retry, reviewer handoff, replays, graph, Groq stream, 390 px with zero overflow, no page crashes)

## Evidence
63 backend tests pass. The Next.js production build and pinned Docker images build. The paired migration ablation changes first actions in 3/3 fixtures, avoids two failed calls, and changes no final outcomes; a same-workload learning comparison likewise shows both agents succeed while memory saves one attempt. A separate real HTTP retry ablation shows the graph-restored controller blocks a previously failed version after restart (zero retry probes and HTTP 200) while a stateless control reintroduces the fault (HTTP 503). This is one controlled local fixture, not a reliability estimate. A competent static precondition-aware baseline is not beaten in the adverse migration suite. The `.env` file is ignored and untracked. The checkout contains pre-existing uncommitted work; existing commits do not prove the timing of all event work. Real FalkorDB acceptance passes: a separate process reads the persisted diagnosis and recovers through the authenticated bounded tool, the receiving reviewer rejects incomplete or contradictory evidence without executing recovery, and an interrupted handoff retry reuses the completed recovery. The retired episode/procedure prototype and its routes have been removed.

## Explicit boundaries
The default incident controller has real local HTTP measurements and route changes with deliberately injected faults in project-owned services. Operator-configured HTTP services can also be gated, but no external customer deployment was connected in this verification. The older Groq deployment panel is separate: it still uses synthetic latency/error fixtures and a demo-only promote/rollback label. The customer migration workflow operates disposable synthetic SQLite records. Multi-worker coordination, authenticated multi-tenancy, cloud deployment control, and a hosted live backend are outside this local build.

The acceptance script restarts FastAPI during an active incident, restores the controller route and pending incident from a versioned FalkorDB state record, then recovers through the actual authenticated tool endpoint. These workflows use deterministic bounded roles. The supported setup is one workspace and one API worker; multi-worker correctness and tenant isolation are not implemented.

## Public frontend

https://revora-anvitdevadiga.vercel.app is deployed in the existing Vercel Hobby account. Anonymous HTTP and Chrome verification pass for the replay, source-record inspection, evidence JSON, setup guide, and 390px mobile layout. The hosted replay does not run a live Python backend.

## Clean submission check

The sanitized archive was extracted into a fresh directory with Python 3.12. The 63-test suite and real FalkorDB acceptance script pass against the current checkout. The public walkthrough contains two captioned live UI checkpoints and is labeled as non-continuous.
