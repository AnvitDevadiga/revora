# Memory laboratory — first three phases

This describes the October 15 replay module. The October 16 SQLite runtime now supplies real sandbox execution, graph-based retrieval, durable revisions and held-out comparisons. See PROJECT_STATUS.md for the current verified checklist.

The laboratory adds an independently testable vertical slice to Revora.

## What judges can verify
1. Select the deployment or migration scenario and replay it.
2. Select observation 1: failure is retained and execution is withheld.
3. Select observation 3: two successful guarded actions make that strategy eligible.
4. Inspect both strategy scores. Direct-action failures remain attached to direct actions; guarded success does not erase them.
5. Inspect the retriever/planner/reviewer/executor trace and final simulated outcome.
6. Expand the evidence-removal analysis to inspect decision sensitivity.
7. Replay again: the existing experiment is returned, without duplicate evidence.

The observations and simulator are synthetic fixtures. The role trace is a deterministic pipeline, not independent LLM agents. Scores use a Beta(1,1) prior and binary outcomes; they are not calibrated safety probabilities. Evidence removal tests sensitivity, not causality. No production deployment executes.

## Persistence
LabRun → OBSERVED → LabObservation → TRIGGERED → LabRevision.
LabRun → DECIDED → LabDecision → EXECUTED → LabExecution.
LabDecision → CONSIDERED → LabObservation.

A single mutation stores the experiment graph. A versioned deterministic replay ID prevents sequential retries from inflating evidence. Run one API worker: process-local locking does not guarantee cross-worker uniqueness. Legacy memories use different labels and remain untouched.

## Verification
- 10 engine/laboratory tests pass.
- Next.js production build passes.
- Real FalkorDB integration validates fresh-engine reload, retry idempotency, five observation/revision links and retained failures.
- Full browser interaction and deployment remain separate acceptance checks.

## Remaining phase gaps
This is stronger than the legacy prototype but not the completed three-phase product. General goal understanding, learned procedure retrieval, independent outcome collection, held-out benchmarks, browser QA and a recorded demo remain outstanding. Legacy routes still have the limitations listed in AUDIT.md. The event-window provenance requirement still needs resolution for retained pre-event code.
