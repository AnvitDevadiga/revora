# Skeptical audit fix ledger — 17 October 2026

| Finding | Resolution | Verification |
|---|---|---|
| False contradictions | Scoped gate-outcome expectations replace service-wide safety-rule contradiction. Different scenarios coexist; same-scope outcome reversal revises the expectation. Model prose is non-authoritative. | Unit tests and real FalkorDB assertions. |
| Weak evaluation | Fixed guarded and static precondition-aware baselines are visible. Six adverse cases include schema change, unrelated service, and denied merge. Separate condition scopes prevent evaluation leakage. | Versioned fixtures, regression tests, raw public results. No claim of superiority over competent static policies. |
| Inconsequential coordination | Investigator measures without repairing; a separate receiving process reads a graph handoff, invokes a bounded HTTP recovery tool, and records the actual result. | Real HTTP 503 → 200 through separate-process execution; EXECUTED graph edge. |
| Irrelevant memories | Schema and authorization checks reject inapplicable procedure evidence with reason codes. Version and measurement-mode checks scope prior handoffs. | Adverse fixtures and negative tests. |
| Decorative graph relationships | A success observation must VALIDATE an active VerifiedProcedure for it to support learned selection. | Deleting support edges in a disposable graph changes reconciliation to review. |
| Partial belief updates | Experience, evidence, revision, and links are written as one graph mutation; the read/decision is inside the store lock. | Injected write failure leaves existing graph state unchanged. Supported deployment remains one API worker. |
| Simulated operation claims | Groq fixture action reports executed=false and simulated=true. Synthetic metrics remain nested and labeled in evidence. Actual recovery uses release_controller and recovery_tools. | API contracts and source inspection. |
| False synthetic provenance | Demo seeding requires ENABLE_DEMO_SEED=true and labels records synthetic_demo. Legacy reset excludes modern beliefs. | Regression test checks disabled default and synthetic origin. |
| Reproduction | Tested FalkorDB image digest pinned; source snapshot, clean-install checks, and acceptance script are supplied. | See current verification and publication record. |

The interrupted-handoff acceptance case completes recovery, injects failure before the planner decision commit, then resumes through a fresh workflow object. It must reuse the same recorded recovery ID rather than perform another recovery.

Remaining boundaries: deterministic action policies, fixed action space, controlled synthetic tasks, one API worker, no authenticated multi-tenancy, no high-concurrency guarantee, and no public live backend. No claim is made that these boundaries have been eliminated.
