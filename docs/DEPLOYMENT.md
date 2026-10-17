# Deployment

Production replay: https://revora-anvitdevadiga.vercel.app

Project: `revora-graph-memory`, scope `anvitdevadiga`, Vercel Hobby.
Verified production deployment: `dpl_FXQWNopG2G4jMCSZtpdtWhPhcgUS`.

The deployment contains the Next.js frontend, sanitized synthetic evidence, and a captioned two-checkpoint walkthrough. Its replay works without a Python host or database. The **Live workspace** mode connects to an operator-provided backend; it does not imply that the Python service is hosted by this Vercel project. The MP4 shows captured failure and recovery states, not a continuous recording.

## Redeploy the frontend

With Vercel CLI authenticated to the existing account:

```sh
cd frontend
vercel deploy --prod --yes --project revora-graph-memory --scope anvitdevadiga
```

Vercel uploads application files; never include the root `.env` or backend credentials. `NEXT_PUBLIC_API_URL` can hold a public backend origin, but no key or database URL. The workspace key is entered in memory by the operator and is not stored in browser storage.

## Regenerate the recorded evidence

From `backend/` with the local FalkorDB instance running:

```sh
FALKORDB_URL=falkor://127.0.0.1:6379 PYTHONPATH=. python tests/integration_submission.py
```

The script launches a separate receiving agent process, checks the graph, and writes the public evidence JSON. Redeploy after reviewing the new record. The raw evidence file is intentionally public and contains only synthetic task data and measurements.

## Fully hosted live execution

The existing Python release controller owns background local HTTP servers and routing state. It needs a persistent process and a reachable FalkorDB service. Vercel's frontend deployment does not provide that process. Configure a backend host and graph separately, use one API worker, configure the exact frontend origin in `CORS_ORIGINS`, and protect the API before making it reachable publicly. Set `FORWARDED_ALLOW_IPS` to the hosting provider's exact trusted proxy addresses; this controls whether Uvicorn accepts forwarded client IP headers. Rate limiting is per shared workspace API key, not per user. Keep `GROQ_MAX_CONCURRENT_RUNS=1` until agent tool state is made request-scoped. The local startup path remains supported.

Production verification covered anonymous HTTP 200 for `/`, `/evidence.json`, and `/setup.md`, Chrome replay stage switching, node inspection, no console errors, and a 390px mobile layout with no horizontal overflow. No paid plan was selected.
