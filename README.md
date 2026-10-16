# Living Memory (working name: ENGRAM)

A local Graph Hacks Track 2 demo: an agent executes a bounded task, records failure and recovery in FalkorDB, and changes its next tool choice using those observations.

## Start

```sh
docker compose up -d --build
```

Open http://localhost:3000. Docker Desktop must be running. Compose starts FalkorDB, FastAPI and Next.js, bound to localhost only. On first launch the frontend image and dependencies may take several minutes to build.

For manual frontend development, start only the database/backend with `docker compose up -d --build falkordb backend`, then run `npm ci && npm run dev` inside frontend.

Stop with `docker compose stop`. The named database volume is retained. Do not use `down -v` unless you intend to delete all saved graph memory.

## The demonstrated agent

Enter a new service name, keep the duplicate synthetic email rows, and click **Run learning agent**. The agent tries creating a unique index in a disposable SQLite database. The real constraint error is recorded; a second allowlisted tool deduplicates rows and creates the index. Run again in the same context: the agent retrieves previous observations from FalkorDB before choosing its first tool.

Context includes service, environment and workload. Every request has a retry ID. Repeating a request returns its original result; reusing its ID for different input returns 409.

The graph stores AgentRun, AgentDecision, AgentObservation and BeliefVersion nodes. Decisions link to prior observations through BASED_ON; observations link to revisions. The API traverses the current context's OBSERVED edges to retrieve evidence. Each run's graph records are persisted in one mutation.

## Groq reasoning and deployment tools

The optional Groq agent uses Groq's OpenAI-compatible Chat Completions endpoint and local function calling. The configurable default is `openai/gpt-oss-20b`. Its allowlist contains four tools: search exact-context FalkorDB memory, inspect a deployment plan, run a disposable HTTP canary, and record the observed result. The loop requires all four tools before accepting a final answer and stops after eight model turns.

Groq references: [OpenAI compatibility](https://console.groq.com/docs/openai), [local tool calling](https://console.groq.com/docs/tool-use/local-tool-calling), and [supported models](https://console.groq.com/docs/models).

Create a root `.env` file locally:

```text
GROQ_API_KEY=your_private_key
GROQ_MODEL=openai/gpt-oss-20b
```

Then run `docker compose up -d --build backend frontend`. The key is injected only into the backend container, is ignored by Git, and is never sent to the browser. Free-tier limits can return rate-limit errors; the deterministic runtime remains usable without Groq.

The deployment toolbox makes a real HTTP request to a temporary localhost health endpoint. Readiness status is observed; latency and error-rate values are labeled synthetic fixtures. Any failed gate produces rollback. It cannot access Docker, shell commands, cloud accounts, or production targets.

SQLite is a real execution sandbox, but all data is disposable and synthetic. No production systems are operated. The policy is deterministic, not an LLM; tool choice uses a Beta(1,1) success score and a tie-break favoring no row deletion. Deduplication explicitly keeps the first row for each email. Confidence scores are not calibrated safety guarantees.

## Evaluation and replay

The evaluation freezes a policy after one training dataset, then compares it with direct index creation on eight different synthetic datasets. It reports first-attempt success and rows removed. The verified result is 4/8 baseline successes and 8/8 memory-policy successes on this suite only. A second suite covers four unfamiliar deployment conditions: healthy release, readiness failure, latency/error regression, and slow release; the fixed gate policy produces the expected promote/rollback decision in all four fixtures.

The replay laboratory separately demonstrates deployment and migration stories. These are synthetic fixtures, clearly labeled in the UI, with revision timelines and evidence-removal analysis. Saved replay IDs avoid duplicate evidence.

## Verification

```sh
cd backend
python -m pip install -r requirements.txt
python -m pytest
```

Real database acceptance scripts are `backend/tests/integration_runtime.py` and `backend/tests/integration_lab.py`; supply FALKORDB_URL and PYTHONPATH=backend. They create uniquely named test graphs. Run only against a disposable test database.

Frontend production build: `cd frontend && npm ci && npm run build`.
Browser acceptance: `frontend/tests/browser.cjs` uses Playwright and installed Chrome, with the local API configured with test key `local-audit-key`. This is a test fixture, not a production secret. It records a walkthrough and screenshots under docs/artifacts.

## Scope and operating limits

- One local workspace, one API worker. Multi-worker concurrency and authenticated multi-tenant access are not implemented.
- Up to 1,000 runs per graph and 1,000 input rows per tool run.
- With no FALKORDB_URL the API uses temporary memory, visibly identified in the UI; Compose always configures FalkorDB.
- Optional ENGRAM_API_KEY protects API routes; health and CORS preflight remain accessible.
- Legacy prototype routes remain for compatibility. Their graph labels are separate from the event-built runtime and laboratory.
- Public deployment is excluded from this delivery. Existing hosting templates are not a verified deployment.

See [PROJECT_STATUS.md](PROJECT_STATUS.md), [demo script](docs/DEMO_SCRIPT.md), [provenance](docs/PROVENANCE.md) and [audit history](docs/AUDIT.md).
