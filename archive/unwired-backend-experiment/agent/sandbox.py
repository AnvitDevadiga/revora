"""Deployment sandbox — the only world the agent is allowed to act on.

Four unfamiliar deployment conditions ("fixtures") are always on the menu.
Every canary run performs a *real* HTTP readiness probe against a disposable
localhost server; latency and error-rate values are clearly labeled synthetic
fixtures because this project deliberately has no production access. The
toolbox cannot touch Docker, shells, cloud accounts or real services.
"""

from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from time import perf_counter
from urllib.error import HTTPError
from urllib.request import urlopen

# ---------------------------------------------------------------------------
# Fixtures (the four "unfamiliar deployment conditions" of the demo)
# ---------------------------------------------------------------------------
SCENARIOS: dict[str, dict] = {
    "healthy": {
        "http_status": 200,
        "latency_ms": 18,
        "error_rate": 0.0,
        "summary": "Healthy release: readiness endpoint green, low latency, no error budget burn.",
        "logs": [
            "build 2.4.2 ready · digest sha256:4f1c…",
            "readiness probe /health → 200",
            "latency p95 18ms · error budget intact",
        ],
    },
    "readiness_failure": {
        "http_status": 503,
        "latency_ms": 20,
        "error_rate": 1.0,
        "summary": "Readiness endpoint failing: the new replica never becomes ready.",
        "logs": [
            "build 2.4.2 ready · digest sha256:9ab3…",
            "readiness probe /health → 503",
            "init container stuck waiting for dependency",
        ],
    },
    "canary_regression": {
        "http_status": 200,
        "latency_ms": 210,
        "error_rate": 0.18,
        "summary": "Latency and error regression: canary passes health but burns the error budget.",
        "logs": [
            "canary at 10% traffic · health 200",
            "latency p95 210ms (gate ≤120ms)",
            "error rate 18% (gate ≤5%)",
        ],
    },
    "slow_release": {
        "http_status": 200,
        "latency_ms": 145,
        "error_rate": 0.0,
        "summary": "Slow release: health is green but p95 latency exceeds the rollout gate.",
        "logs": [
            "canary at 10% traffic · health 200",
            "latency p95 145ms (gate ≤120ms)",
            "rollout progress stalled at 10%",
        ],
    },
}

THRESHOLDS = {"http_status": 200, "max_latency_ms": 120, "max_error_rate": 0.05}
STRATEGY = "10% canary → health gate → staged rollout → full promotion"

CONFIG_DEFAULTS = {"traffic_split": 10, "duration_s": 30, "rollback_on_failure": True}
CONFIG_LIMITS = {"traffic_split": (1, 25), "duration_s": (5, 120)}


def validate_canary_config(config: dict | None) -> dict:
    """Clamp and validate the model-supplied canary configuration."""
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ValueError("config must be an object")
    unknown = set(config) - set(CONFIG_DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown canary config keys: {', '.join(sorted(unknown))}")
    for key, (low, high) in CONFIG_LIMITS.items():
        if key in config:
            value = config[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{key} must be a number")
            if not low <= int(value) <= high:
                raise ValueError(f"{key} must be between {low} and {high}")
    rollback = config.get("rollback_on_failure", CONFIG_DEFAULTS["rollback_on_failure"])
    if not isinstance(rollback, bool):
        raise ValueError("rollback_on_failure must be a boolean")
    return {**CONFIG_DEFAULTS, **config}


# ---------------------------------------------------------------------------
# Disposable localhost target for the real readiness probe
# ---------------------------------------------------------------------------
@contextmanager
def _target(status: int):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status if self.path == "/health" else 404)
            self.end_headers()
            self.wfile.write(b'{"service":"revora-sandbox"}')

        def log_message(self, *_):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/health"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _probe(expected_status: int) -> tuple[int, float]:
    with _target(expected_status) as health_url:
        started = perf_counter()
        try:
            status = urlopen(health_url, timeout=2).status
        except HTTPError as error:
            status = error.code
        measured_ms = round((perf_counter() - started) * 1000, 2)
    return status, measured_ms


# ---------------------------------------------------------------------------
# Tools-facing functions
# ---------------------------------------------------------------------------
def inspect_deployment(target: str, scenario: str) -> dict:
    """Plan-level inspection: rollout strategy, gates, and the fixture preview."""
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown deployment scenario {scenario!r}")
    fixture = SCENARIOS[scenario]
    return {
        "target": target,
        "scenario": scenario,
        "status": "candidate",
        "strategy": STRATEGY,
        "gates": THRESHOLDS,
        "fixture_summary": fixture["summary"],
        "logs": list(fixture["logs"]),
        "note": "No production access: probe targets are disposable localhost servers.",
    }


def run_canary_check(target: str, scenario: str, config: dict | None = None) -> dict:
    """Execute the canary: real readiness probe + synthetic metric fixtures + gates."""
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown deployment scenario {scenario!r}")
    resolved = validate_canary_config(config)
    fixture = SCENARIOS[scenario]
    http_status, probe_ms = _probe(fixture["http_status"])

    gates = {
        "health": http_status == THRESHOLDS["http_status"],
        "latency": fixture["latency_ms"] <= THRESHOLDS["max_latency_ms"],
        "errors": fixture["error_rate"] <= THRESHOLDS["max_error_rate"],
    }
    passed = all(gates.values())
    return {
        "target": target,
        "scenario": scenario,
        "config": resolved,
        "http_status": http_status,
        "probe_ms": probe_ms,
        "synthetic_metrics": {"latency_ms": fixture["latency_ms"], "error_rate": fixture["error_rate"]},
        "gates": gates,
        "latency_budget_ms": THRESHOLDS["max_latency_ms"],
        "error_budget": THRESHOLDS["max_error_rate"],
        "decision": "promote" if passed else "rollback",
        "success": passed,
        "logs": list(fixture["logs"]),
        "execution_mode": (
            "real HTTP readiness probe against a disposable localhost target; "
            "latency/error-rate values are labeled synthetic fixtures"
        ),
    }


def canary_evidence(canary: dict) -> dict:
    """The *measured* subset of a canary result — the only numbers memory may store."""
    failed = [name for name, ok in canary["gates"].items() if not ok]
    description = (
        f"Canary for {canary['target']} ({canary['scenario']}): "
        + ("all gates passed" if not failed else f"failed gates: {', '.join(failed)}")
        + f"; http={canary['http_status']}, p95={canary['synthetic_metrics']['latency_ms']}ms, "
        + f"error_rate={canary['synthetic_metrics']['error_rate']}"
    )
    return {
        "description": description,
        "metrics": {
            "http_status": canary["http_status"],
            "probe_ms": canary["probe_ms"],
            "latency_ms": canary["synthetic_metrics"]["latency_ms"],
            "error_rate": canary["synthetic_metrics"]["error_rate"],
            "gates_passed": sum(bool(value) for value in canary["gates"].values()),
            "gates_total": len(canary["gates"]),
        },
    }
