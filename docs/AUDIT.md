# Audit — 15 October 2026

## Fixed
- Belief insertion/reload keys differed, duplicating beliefs after restart. Keys now match; regression test checks evidence continuity.
- Procedures were saved but not loaded. Reload now restores steps.
- Recommendations always referred to Friday. Weekdays now follow evidence; unrelated topics require review.
- Tenant labels did not isolate data. Additional workspaces are explicitly rejected until real authentication/isolation is built.
- API authentication blocked health checks and CORS preflight. Both now bypass the key check.
- Rate-limit buckets accumulated indefinitely. Expired entries are removed and the host map is bounded.
- Reset was unrestricted. It now requires ENABLE_DEMO_RESET=true, locks concurrent writes and deletes only project node labels. UI asks for confirmation.
- Frontend could not call a protected API. Added an in-memory API-key field and request timeouts.
- Render Docker context did not match the Dockerfile. Corrected to backend.
- Added test discovery configuration and build-artifact ignore rules.

## Still required before claiming phases complete
### Phase 1
- Real FalkorDB integration, restart recovery and full browser tests for this revision.
- Atomic ingestion: writes currently span multiple queries and can partially succeed.
### Phase 2
- Context-sensitive claims distinguishing canary deployments from unrestricted deployments.
- Durable revision history and decision audit records.
- Goal-relevant retrieval and real tool execution/outcome collection. Current plans are canned previews.
- Calibrated confidence and held-out evaluation. Current score is heuristic.
- Authenticated tenant isolation; current deployment supports one workspace only.
### Phase 3
- Idempotent isolated scenario replay and before/after comparison.
- Interactive graph; current UI is an edge list.
- Browser/mobile/accessibility verification and recorded demo.

## Evidence and provenance
Six engine tests pass including four regression cases. The reload regression uses a query-result double, not live FalkorDB. Production frontend compilation is checked separately. No benchmark percentages or competition outcomes have been demonstrated. The pre-event engine remains the base of this code: disclose it and resolve the event's newly-built-core requirement before submission.

Recent changes were found in the Codex workspace, while the Downloads project still held the October 3 prototype. Do not assume the two copies are synchronized.
