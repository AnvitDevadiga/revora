"""Real FalkorDB acceptance and sanitized evidence export. Leaves a uniquely named test graph."""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import socket
import time
from urllib.request import urlopen, Request
from app.evaluation import evaluate_adversarial

from falkordb import FalkorDB
from app.coordination import CoordinationWorkflow
from app.release_controller import ReleaseController
from app.runtime import Runtime
from app.memory import MemoryStore


def evaluate_release_memory_ablation(graph):
    """Compare a graph-backed retry with an identically restarted stateless control."""
    service = "retry-ablation-" + uuid4().hex[:10]
    version = "same-fault-v1"

    remembered = ReleaseController(graph, service=service)
    try:
        onset = remembered.inject(version, "errors")
        diagnosis = remembered.diagnose()
        handoff = CoordinationWorkflow(graph).publish(diagnosis)
        recovered = CoordinationWorkflow(graph).consume(handoff["id"], executor=remembered)
        assert recovered["plan"]["evidence_review"]["status"] == "accepted"
        assert recovered["execution"]["recovered"] is True
    finally:
        remembered.close()

    remembered_after_restart = ReleaseController(graph, service=service)
    try:
        memory_retry = remembered_after_restart.inject(version, "errors")
        memory_status = remembered_after_restart.status()["gateway_probe"]["status"]
    finally:
        remembered_after_restart.close()

    stateless = ReleaseController(service=service)
    try:
        stateless.inject(version, "errors")
        stateless.diagnose()
        stateless.recover()
    finally:
        stateless.close()

    stateless_after_restart = ReleaseController(service=service)
    try:
        control_retry = stateless_after_restart.inject(version, "errors")
        control_status = stateless_after_restart.status()["gateway_probe"]["status"]
    finally:
        stateless_after_restart.close()

    result = {"service": service, "version": version,
              "memory": {"decision": memory_retry["decision"],
                         "candidate_samples": len(memory_retry["canary_samples"]),
                         "routed_status": memory_status},
              "no_persistent_memory": {"decision": control_retry["decision"],
                                       "candidate_probes": 1,
                                       "candidate_status": control_retry["during"]["status"],
                                       "routed_status": control_status},
              "memory_prevented_repeated_503": memory_status == 200 and control_status == 503,
              "scope": "controlled local fault; stateless control is restarted without graph history"}
    assert result["memory"]["decision"] == "blocked_from_memory"
    assert result["memory"]["candidate_samples"] == 0 and memory_status == 200
    assert control_retry["decision"] == "introduced" and control_retry["during"]["status"] == 503
    assert control_status == 503 and result["memory_prevented_repeated_503"]
    return result


def verify():
    graph_name = "submission_acceptance_" + uuid4().hex
    graph = FalkorDB.from_url(os.environ["FALKORDB_URL"]).select_graph(graph_name)
    controller = ReleaseController(graph)
    api_process = None
    try:
        baseline = controller.status()
        onset = controller.inject("acceptance-v1", "errors")
        assert baseline["gateway_probe"]["status"] == 200
        assert controller.status()["gateway_probe"]["status"] == 503
        diagnosis = controller.diagnose()
        assert controller.status()["gateway_probe"]["status"] == 503
        analyst = CoordinationWorkflow(graph)
        handoff = analyst.publish(diagnosis)
        # Stop the original controller and its gateway. The replacement API
        # process must restore the route, fault and pending incident from graph.
        controller.close()
        controller = None
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            api_port = listener.getsockname()[1]
        api_key = "acceptance-local-key"
        api_env = {**os.environ, "FALKORDB_GRAPH": graph_name,
                   "REVORA_API_KEY": api_key, "PORT": str(api_port)}
        api_process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(api_port)],
            cwd=Path(__file__).resolve().parents[1], env=api_env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        origin = f"http://127.0.0.1:{api_port}"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if api_process.poll() is not None:
                raise RuntimeError("Restarted FastAPI process exited before becoming healthy")
            try:
                with urlopen(origin + "/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(.1)
        else:
            raise RuntimeError("Restarted FastAPI process did not become healthy")
        # Execute the receiver in a separate interpreter. It reads the handoff
        # from FalkorDB, then calls the real authenticated FastAPI tool route.
        receiver_code = """
import json, os
from falkordb import FalkorDB
from app.coordination import CoordinationWorkflow
from app.recovery_tools import HTTPRecoveryTool
graph = FalkorDB.from_url(os.environ['FALKORDB_URL']).select_graph(os.environ['REVORA_TEST_GRAPH'])
print(json.dumps(CoordinationWorkflow(graph).consume(os.environ['REVORA_TEST_HANDOFF'], executor=HTTPRecoveryTool(os.environ['REVORA_TEST_TOOL_ORIGIN'], os.environ['REVORA_TEST_API_KEY']))))
"""
        receiver = subprocess.run([sys.executable, "-c", receiver_code],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "REVORA_TEST_GRAPH": graph_name, "REVORA_TEST_HANDOFF": handoff["id"],
                 "REVORA_TEST_TOOL_ORIGIN": origin, "REVORA_TEST_API_KEY": api_key},
            capture_output=True, text=True, check=True, timeout=30)
        coordinated = json.loads(receiver.stdout)
        recovered = coordinated["execution"]
        assert recovered["after"]["status"] == 200 and recovered["recovered"]
        assert len(recovered["canary_samples"]) == 12
        assert all(sample["status"] == 503 for sample in recovered["canary_samples"])
        assert len(recovered["recovery_samples"]) == 3
        assert coordinated["plan"]["action"] == "rollback_and_verify"
        # Exercise the public direct tool route as an ordinary retry, then
        # replay it while a distinct incident is live. The old replay must be
        # a read-only result and must not clear or reroute the newer incident.
        request_headers = {"x-api-key": api_key, "content-type": "application/json"}
        def post_json(path, value):
            request = Request(origin + path, data=json.dumps(value).encode(), headers=request_headers)
            with urlopen(request, timeout=5) as response:
                return json.loads(response.read())
        replayed = post_json("/api/coordination/tool/recover", {"handoff_id": handoff["id"]})
        assert replayed["id"] == recovered["id"]
        new_incident = post_json("/api/release/inject", {"version": "replay-race-v1", "mode": "errors"})
        assert new_incident["decision"] == "introduced"
        replayed_again = post_json("/api/coordination/tool/recover", {"handoff_id": handoff["id"]})
        assert replayed_again["id"] == recovered["id"]
        with urlopen(Request(origin + "/api/release/live", headers={"x-api-key": api_key}), timeout=5) as response:
            active_after_replay = json.loads(response.read())
        assert active_after_replay["incident_active"] is True
        assert active_after_replay["gateway_probe"]["status"] == 503
        post_json("/api/release/recover", {})
        assert controller is None
        api_process.terminate()
        api_process.wait(timeout=10)
        api_process = None
        controller = ReleaseController(graph)
        assert controller.status()["incident_active"] is False
        assert CoordinationWorkflow(graph).consume(handoff["id"], executor=controller)["retry_reused"]
        blocked = controller.inject("acceptance-v1", "errors")
        assert blocked["decision"] == "blocked_from_memory" and blocked["canary_samples"] == []
        assert coordinated["provenance"]["source"] == "persisted_graph_handoff"
        assert coordinated["plan"]["based_on"] == [diagnosis["id"]]
        assert coordinated["plan"]["evidence_review"]["status"] == "accepted"
        assert graph.query("MATCH (:AgentHandoff)-[:REVIEWED_BY]->(r:EvidenceReview)-[:FORWARDED_TO]->(:CoordinationDecision) RETURN count(r)").result_set[0][0] == 1
        second = CoordinationWorkflow(graph).run(blocked)
        assert second["plan"]["prior_handoff_id"] == handoff["id"]
        path = graph.query("MATCH (i:ReleaseIncident)-[:HANDED_OFF]->(h:AgentHandoff)-[:HANDED_OFF_TO]->(d:CoordinationDecision)-[:BASED_ON]->(i) RETURN count(d)").result_set[0][0]
        assert path == 2
        assert graph.query("MATCH (:CoordinationDecision)-[:EXECUTED]->(:ReleaseIncident) RETURN count(*)").result_set[0][0] == 1
        try:
            analyst.publish({**recovered, "id": "missing-source"})
        except ValueError:
            pass
        else:
            raise AssertionError("Unknown source incident must not produce a handoff")
        class ForbiddenRecovery:
            def recover_handoff(self, *_):
                raise AssertionError("An unreviewed handoff must not execute recovery")
        missing_handoff = analyst.publish({**diagnosis, "canary_samples": []})
        missing_review = analyst.consume(missing_handoff["id"], executor=ForbiddenRecovery())
        assert missing_review["plan"]["action"] == "request_evidence"
        contradictory_samples = [{"status": 200, "latency_ms": 10} for _ in range(12)]
        contradictory = analyst.publish({**diagnosis, "canary_samples": contradictory_samples})
        contradictory_review = analyst.consume(contradictory["id"], executor=ForbiddenRecovery())
        assert contradictory_review["plan"]["action"] == "reject_handoff"
        reviewer_evidence = {"missing": missing_review["plan"]["evidence_review"],
                             "contradictory": contradictory_review["plan"]["evidence_review"]}
        controller.inject("interrupted-v1", "errors")
        interrupted_handoff = CoordinationWorkflow(graph).publish(controller.diagnose())
        class InterruptedDecisionGraph:
            def query(self, text, params=None):
                if "CREATE (d:CoordinationDecision" in text:
                    raise RuntimeError("interrupted after recovery before decision commit")
                return graph.query(text, params or {})
        try:
            CoordinationWorkflow(InterruptedDecisionGraph()).consume(interrupted_handoff["id"], executor=controller)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Expected decision commit interruption")
        committed_recovery = next(i for i in controller.history(1000) if i.get("handoff_id") == interrupted_handoff["id"])
        resumed = CoordinationWorkflow(graph).consume(interrupted_handoff["id"], executor=controller)
        assert resumed["execution"]["id"] == committed_recovery["id"]
        assert len([i for i in controller.history(1000) if i.get("handoff_id") == interrupted_handoff["id"]]) == 1
        proof = Runtime(graph).learning_proof()
        assert proof["proof"]["passed"]
        ablations = [Runtime(graph).memory_ablation(), Runtime(graph).memory_ablation()]
        for report in ablations:
            assert all(c["with_memory"]["retrieved_observations"] == 2 for c in report["cases"])
            assert all(c["without_memory"]["retrieved_observations"] == 0 for c in report["cases"])
            assert report["memory_changed_action_cases"] == 3
            assert report["memory_changed_outcome_cases"] == 0
        adverse = evaluate_adversarial(graph)
        schema_case = next(c for c in adverse["cases"] if c["id"] == "schema-changed")
        assert schema_case["memory"]["retrieved_observations"] == 0
        edge_runtime = Runtime(graph)
        edge_task = {"service": "edge-ablation", "environment": "sandbox", "workload": "identity", "values": ["a", "a", "b"]}
        training = edge_runtime.run({**edge_task, "request_id": "edge-training"})
        before_edges = edge_runtime.run({**edge_task, "request_id": "edge-before"})
        graph.query("MATCH (r:AgentRun {context:$context})-[:OBSERVED]->(:AgentObservation)-[v:VALIDATES]->(:VerifiedProcedure) DELETE v", {"context": training["context"]})
        after_edges = edge_runtime.run({**edge_task, "request_id": "edge-after"})
        assert before_edges["initial"]["action"] == "deduplicate_then_index"
        assert after_edges["initial"]["action"] == "request_review"
        edge_ablation = {"before": before_edges["initial"]["action"], "after": after_edges["initial"]["action"],
                         "rejected": after_edges["rejected_memories"]}
        MemoryStore(graph).record_deployment("acceptance", "healthy", "promote", True, "Observed fixture gates", {
            "decision": "promote", "success": True, "gates": {"health": True}})
        belief_memory = MemoryStore(graph)
        def fixture(decision, scenario):
            return {"decision": decision, "success": decision == "promote", "scenario": scenario,
                    "synthetic_metrics": {"pass": decision == "promote"}, "execution_mode": "synthetic_expectation_test"}
        first_expectation = belief_memory.record_deployment("scoped-test", "same", "promote", True, "synthetic pass", fixture("promote", "same"))
        unrelated_expectation = belief_memory.record_deployment("scoped-test", "other", "rollback", False, "synthetic fail", fixture("rollback", "other"))
        reversed_expectation = belief_memory.record_deployment("scoped-test", "same", "rollback", False, "synthetic reversal", fixture("rollback", "same"))
        assert not unrelated_expectation["contradiction_detected"]
        assert reversed_expectation["contradiction_detected"]
        assert reversed_expectation["contradicted_belief_id"] == first_expectation["belief"]["id"]
        count_before = graph.query("MATCH (n) RETURN count(n)").result_set[0][0]
        class FailMutation:
            def query(self, text, params=None):
                if "CREATE (x:Experience" in text:
                    raise RuntimeError("injected mutation failure")
                return graph.query(text, params or {})
        try:
            MemoryStore(FailMutation()).record_deployment("scoped-test", "same", "promote", True, "must fail", fixture("promote", "same"))
        except RuntimeError:
            pass
        else:
            raise AssertionError("Expected injected graph write failure")
        assert graph.query("MATCH (n) RETURN count(n)").result_set[0][0] == count_before
        assert belief_memory.active_belief("scoped-test", "same")["id"] == reversed_expectation["belief"]["id"]
        release_ablation = evaluate_release_memory_ablation(graph)
        edges = graph.query("MATCH (a)-[r]->(b) WHERE a:ReleaseIncident OR a:AgentHandoff OR a:CoordinationDecision OR a:EvidenceReview RETURN a.id,labels(a)[0],type(r),b.id,labels(b)[0]").result_set
        nodes = {}
        links = []
        for source, source_type, relation, target, target_type in edges:
            nodes[source] = {"id": source, "type": source_type}
            nodes[target] = {"id": target, "type": target_type}
            links.append({"source": source, "target": target, "relation": relation})
        return {"schema_version": 1, "recorded_at": datetime.now(timezone.utc).isoformat(),
                "verification": "Passed against a real local FalkorDB instance, a restarted FastAPI backend, and project-owned HTTP services",
                "limitations": "Recorded local acceptance evidence. HTTP faults are intentionally injected; all migration data is synthetic. This public replay does not operate a hosted backend.",
        "checks": {"live_200_503_200": True, "memory_blocks_retry": True,
                           "fresh_process_handoff": True, "backend_restart_restores_active_incident": True,
                           "actual_fastapi_recovery_route": True, "graph_evidence_cycle": True,
                           "receiver_requests_missing_or_rejects_contradictory_evidence": True,
                           "review_decision_is_graph_node": True,
                           "persistent_memory_prevents_repeat_503": True,
                           "repeated_ablation_isolated": True,
                           "consequential_separate_process_recovery": True, "graph_edge_ablation": True, "adversarial_memory_rejection": True, "scoped_contradiction_semantics": True, "failed_mutation_preserves_state": True, "interrupted_handoff_resumes_without_repeating_recovery": True},
                "release": {"baseline": baseline, "onset": onset, "diagnosis": diagnosis, "recovered": recovered, "blocked": blocked},
                "coordination": coordinated, "next_agent": second, "evidence_review_tests": reviewer_evidence,
                "learning_proof": proof, "ablation": ablations[0], "release_memory_ablation": release_ablation,
                "adversarial_evaluation": adverse, "graph_edge_ablation": edge_ablation,
                "graph": {"nodes": list(nodes.values()), "edges": links}}
    finally:
        if api_process is not None:
            api_process.terminate()
            try:
                api_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                api_process.kill()
                api_process.wait(timeout=10)
        if controller is not None:
            controller.close()


if __name__ == "__main__":
    evidence = verify()
    root = Path(__file__).resolve().parents[2]
    for path in (root / "docs/artifacts/submission-evidence.json", root / "frontend/public/evidence.json"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evidence, indent=2) + "\n")
    print("PASS: restart-safe incident recovery through FastAPI; graph provenance; isolated memory ablations")
