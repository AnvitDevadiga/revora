"""Bounded deployment toolbox using real local HTTP probes against disposable targets."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from time import perf_counter
from urllib.request import urlopen
from urllib.error import HTTPError

SCENARIOS = {
    "healthy": {"status": 200, "latency_ms": 18, "error_rate": 0.0},
    "readiness_failure": {"status": 503, "latency_ms": 20, "error_rate": 1.0},
    "canary_regression": {"status": 200, "latency_ms": 210, "error_rate": 0.18},
    "slow_release": {"status": 200, "latency_ms": 145, "error_rate": 0.0},
}


@contextmanager
def target(status):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status if self.path == "/health" else 404)
            self.end_headers()
            self.wfile.write(b'{"service":"sandbox"}')
        def log_message(self, *_):
            return
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/health"
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def inspect_deployment(scenario):
    if scenario not in SCENARIOS:
        raise ValueError("Unknown deployment fixture")
    fixture = SCENARIOS[scenario]
    return {"scenario": scenario, "strategy": "10% canary → health gate → staged rollout",
            "thresholds": {"status": 200, "max_latency_ms": 120, "max_error_rate": 0.05},
            "synthetic_metrics": {"latency_ms": fixture["latency_ms"], "error_rate": fixture["error_rate"]}}


def probe_status(expected_status):
    with target(expected_status) as health_url:
        started = perf_counter()
        try:
            status = urlopen(health_url, timeout=2).status
        except HTTPError as error:
            status = error.code
        measured_probe_ms = round((perf_counter() - started) * 1000, 2)
    return status, measured_probe_ms


def run_canary(scenario):
    plan = inspect_deployment(scenario)
    fixture = SCENARIOS[scenario]
    status, measured_probe_ms = probe_status(fixture["status"])
    gates = {
        "health": status == plan["thresholds"]["status"],
        "latency": fixture["latency_ms"] <= plan["thresholds"]["max_latency_ms"],
        "errors": fixture["error_rate"] <= plan["thresholds"]["max_error_rate"],
    }
    promoted = all(gates.values())
    return {"scenario": scenario, "http_status": status, "probe_ms": measured_probe_ms,
            "synthetic_metrics": plan["synthetic_metrics"], "gates": gates,
            "decision": "promote" if promoted else "rollback", "success": promoted,
            "execution_mode": "real HTTP probe against disposable localhost target; synthetic latency/error fixtures"}


def promote_or_rollback(service, decision):
    """Describe a simulated release decision; no routing or deployment operation executes."""
    if not service or decision not in {"promote", "rollback"}:
        raise ValueError("A valid service and promote/rollback decision are required")
    return {"service": service, "action": decision, "executed": False, "simulated": True,
            "execution_mode": "bounded demo action; no production system or cloud account is connected",
            "detail": (f"{service} marked ready for staged promotion" if decision == "promote"
                       else f"{service} release held and rollback recorded")}


def evaluate_deployments():
    expected = {"healthy": "promote", "readiness_failure": "rollback",
                "canary_regression": "rollback", "slow_release": "rollback"}
    cases = []
    for scenario, wanted in expected.items():
        result = run_canary(scenario)
        cases.append({"scenario": scenario, "expected": wanted, "actual": result["decision"],
                      "correct": result["decision"] == wanted, "gates": result["gates"]})
    return {"cases": cases, "correct": sum(case["correct"] for case in cases),
            "count": len(cases), "method": "Four fixed held-out deployment fixtures; real local HTTP readiness probe plus synthetic latency/error metrics."}
