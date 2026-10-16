# Local demo script (about two minutes when narrated)

1. Open localhost:3000. Introduce the problem: agents repeat failures when they cannot connect outcomes to earlier decisions.
2. Explain the bounded task: create a unique email index in a disposable SQLite database. Duplicate rows are visible in the form.
3. Use a fresh service name. Click Run learning agent. Observe two attempts: constraint failure, then cleanup and successful index creation.
4. Select observation 1 in the evidence diagram. Show the actual SQLite error, unchanged row count after rollback and saved belief revision.
5. Run again with the same context. Show one attempt and two prior observations retrieved. The graph changed the first tool selected.
6. Load saved history to inspect both decisions. These persist in FalkorDB across API restarts.
7. Run held-out evaluation. Explain 4/8 versus 8/8 first-attempt success on this small synthetic suite. Show rows removed; do not claim production accuracy or general agent intelligence.
8. Replay the Friday scenario. Click observation 3 and explain why guarded successes must not erase direct-deployment failures. Expand the missing-evidence analysis.
9. Close: the product connects actions, outcomes and revised decisions so someone can inspect why the next plan changed.

The recorded artifact is an automated local browser walkthrough, without narration. The script above supplies the spoken explanation. Final naming remains open.
