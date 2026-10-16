# Verified local MVP status — 16 October 2026

The first three phases are complete for the bounded, single-workspace sandbox product described below. This supersedes earlier inflated completion counts. Public deployment is excluded.

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

## Evidence
18 Python tests passed. Real FalkorDB acceptance verified a fresh Runtime instance learns from the prior run, preserves failures, stores revisions, links the next decision to earlier observations and deduplicates retries. Browser acceptance on the containerized production build verified first-run failure/recovery, second-run improvement, both scenario replays, graph selection, evaluation results, reload history and no horizontal mobile overflow or page crashes. A regression test confirms a current tool failure overrides many historical successes, preventing repeated failure on unchanged input.

## Explicit boundaries
This is a bounded deterministic agent, not a general-purpose LLM agent. SQLite tools execute real SQL on disposable synthetic data. The deployment/migration story replays use synthetic outcomes. Evaluation results apply only to the supplied eight-case suite. Multi-worker coordination, authenticated multi-tenancy and production tool access are outside this local MVP. No public deployment was performed.

The pre-event prototype is disclosed in docs/PROVENANCE.md. Current commits cannot retroactively prove earlier implementation dates; eligibility remains the organizers' decision.
