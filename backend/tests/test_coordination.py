from app.coordination import CoordinationWorkflow


def test_two_agents_handoff_measured_incident_with_provenance_and_memory():
    workflow = CoordinationWorkflow()
    incident = {"id": "incident-1", "service": "checkout", "version": "v9", "decision": "rollback",
                "failed": True, "measurement_mode": "live_gateway",
                "during": {"status": 503}, "after": {"status": 200},
                "metrics": {"sample_count": 12}}
    first = workflow.run(incident)
    assert [step["agent"] for step in first["trace"]] == ["release_analyst", "evidence_reviewer", "recovery_planner"]
    assert first["plan"]["action"] == "keep_stable_and_verify"
    assert first["plan"]["based_on"] == ["incident-1"]
    assert first["provenance"]["source_incident_id"] == "incident-1"

    next_incident = {**incident, "id": "incident-2", "decision": "blocked_from_memory"}
    second = workflow.run(next_incident)
    assert second["plan"]["prior_handoff_id"] == first["handoff"]["id"]
    assert "Prior graph handoff" in second["plan"]["reason"]
    assert len(workflow.history()) == 2


def test_handoff_rejects_unmeasured_or_malformed_incident():
    workflow = CoordinationWorkflow()
    for bad in ({}, {"id": "x", "service": "svc", "version": "v1"}):
        try:
            workflow.run(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid handoff input must be rejected")


def test_prior_memory_from_different_version_is_not_retrieved():
    workflow = CoordinationWorkflow()
    old = {"id": "old", "service": "checkout", "version": "v1", "decision": "rollback", "failed": True}
    workflow.run(old)
    result = workflow.run({**old, "id": "new", "version": "v2", "decision": "promote", "failed": False})
    assert result["plan"]["prior_handoff_id"] is None
    assert result["plan"]["action"] == "continue_candidate_monitoring"


def test_diagnosed_handoff_executes_recovery_and_reuses_completed_result():
    from app.release_controller import ReleaseController
    controller = ReleaseController()
    workflow = CoordinationWorkflow()
    try:
        controller.inject("coord-v1", "errors")
        diagnosis = controller.diagnose()
        assert controller.status()["gateway_probe"]["status"] == 503
        handoff = workflow.publish(diagnosis)
        result = workflow.consume(handoff["id"], executor=controller)
        assert result["plan"]["action"] == "rollback_and_verify"
        assert result["execution"]["after"]["status"] == 200
        assert result["execution"]["diagnosis_id"] == diagnosis["id"]
        assert workflow.consume(handoff["id"], executor=controller)["retry_reused"]
    finally:
        controller.close()


def test_old_incident_handoff_cannot_recover_new_incident():
    import pytest
    from app.release_controller import ReleaseController
    controller = ReleaseController()
    workflow = CoordinationWorkflow()
    try:
        controller.inject("old-v", "errors")
        handoff = workflow.publish(controller.diagnose())
        controller.park()
        controller.inject("new-v", "errors")
        with pytest.raises(ValueError, match="Stale handoff"):
            workflow.consume(handoff["id"], executor=controller)
        assert controller.status()["gateway_probe"]["status"] == 503
        controller.recover()
    finally:
        controller.close()


def test_completed_tool_replay_cannot_clear_or_reroute_a_later_incident():
    from app.release_controller import ReleaseController
    controller = ReleaseController()
    workflow = CoordinationWorkflow()
    try:
        controller.inject("first-v", "errors")
        handoff = workflow.publish(controller.diagnose())
        completed = workflow.consume(handoff["id"], executor=controller)["execution"]
        assert completed["decision"] == "rollback"
        controller.inject("second-v", "errors")
        before = controller.status()
        replay = workflow.execute_recovery_tool(handoff["id"], controller)
        after = controller.status()
        assert replay["id"] == completed["id"]
        assert after["incident_active"] is True
        assert after["active_route"] == before["active_route"] == "candidate"
        assert after["gateway_probe"]["status"] == 503
        controller.recover()
    finally:
        controller.close()


def test_expired_handoff_cannot_execute():
    import pytest
    workflow = CoordinationWorkflow()
    handoff = workflow.publish({"id": "diagnosis", "service": "checkout", "version": "v1", "decision": "diagnosed", "failed": True, "metrics": {"sample_count": 12}})
    handoff["created_at"] = "2020-01-01T00:00:00+00:00"
    class ForbiddenExecutor:
        def recover_handoff(self, *_):
            raise AssertionError("Expired evidence must not reach the tool")
    with pytest.raises(ValueError, match="expired"):
        workflow.consume(handoff["id"], executor=ForbiddenExecutor())


def test_unconsumed_handoff_remains_visible_for_resume():
    workflow = CoordinationWorkflow()
    handoff = workflow.publish({"id": "pending", "service": "checkout", "version": "v1", "decision": "diagnosed", "failed": True, "metrics": {"sample_count": 12}})
    assert workflow.history()[0]["handoff"]["id"] == handoff["id"]
    assert workflow.history()[0]["plan"] is None


def test_reviewer_requests_exact_missing_diagnosis_evidence_without_execution():
    import pytest
    workflow = CoordinationWorkflow()
    handoff = workflow.publish({"id": "incomplete", "service": "checkout", "version": "v1",
                                "decision": "diagnosed", "failed": True,
                                "measurement_mode": "live_gateway", "before": {"status": 200},
                                "during": {"status": 503}, "metrics": {"sample_count": 12}})

    class ForbiddenExecutor:
        def recover_handoff(self, *_):
            pytest.fail("Incomplete evidence must never execute the recovery tool")

    result = workflow.consume(handoff["id"], executor=ForbiddenExecutor())
    assert result["plan"]["action"] == "request_evidence"
    assert result["plan"]["execution_status"] == "not_executed"
    assert "at least 12 paired canary status and latency samples" in result["plan"]["evidence_review"]["requested_evidence"]


def test_reviewer_rejects_contradictory_diagnosis_samples_without_execution():
    import pytest
    workflow = CoordinationWorkflow()
    samples = [{"status": 200, "latency_ms": 10} for _ in range(12)]
    handoff = workflow.publish({"id": "contradictory", "service": "checkout", "version": "v1",
                                "decision": "diagnosed", "failed": True,
                                "measurement_mode": "live_gateway", "before": {"status": 200},
                                "during": {"status": 503}, "canary_samples": samples,
                                "metrics": {"sample_count": 12, "error_rate": 1, "p95_latency_ms": 10}})

    class ForbiddenExecutor:
        def recover_handoff(self, *_):
            pytest.fail("Contradictory evidence must never execute the recovery tool")

    result = workflow.consume(handoff["id"], executor=ForbiddenExecutor())
    assert result["plan"]["action"] == "reject_handoff"
    assert result["plan"]["execution_status"] == "not_executed"
    assert any("error rate" in issue for issue in result["plan"]["evidence_review"]["contradictions"])


def test_same_onset_replay_repairs_interrupted_recovery_state():
    from app.release_controller import ReleaseController

    controller = ReleaseController()
    workflow = CoordinationWorkflow()
    try:
        onset = controller.inject("crash-window-v1", "errors")
        handoff = workflow.publish(controller.diagnose())
        completed = workflow.consume(handoff["id"], executor=controller)["execution"]
        assert completed["decision"] == "rollback"
        # Simulate restart restoring the persisted pre-recovery snapshot after
        # the outcome node was written but before controller state was cleared.
        controller.pending = onset
        controller.route = "candidate"
        replay = controller.recover_handoff(handoff["evidence"], handoff["id"])
        assert replay["id"] == completed["id"]
        assert controller.pending is None
        assert controller.route == "stable"
    finally:
        controller.close()


def test_graph_view_exposes_incident_handoff_and_decision():
    workflow = CoordinationWorkflow()
    workflow.run({"id": "incident-1", "service": "checkout", "version": "v9", "decision": "rollback",
                  "failed": True, "during": {"status": 503}, "after": {"status": 200}})
    view = workflow.graph_view("checkout")
    assert {"ReleaseIncident", "AgentHandoff", "CoordinationDecision"} <= {n["type"] for n in view["nodes"]}
    assert {"HANDED_OFF", "HANDED_OFF_TO"} <= {e["relation"] for e in view["edges"]}


class _FakeTransport:
    def __init__(self, content):
        self.content, self.calls = content, []

    def complete(self, messages, tools):
        self.calls.append(messages)
        if isinstance(self.content, Exception):
            raise self.content
        return {"content": self.content}


def _planner(content):
    from app.llm_planner import LLMPlanner
    return LLMPlanner(_FakeTransport(content), "test-model")


FAILED = {"id": "incident-9", "service": "checkout", "version": "v9", "decision": "rollback",
          "failed": True, "during": {"status": 503}, "after": {"status": 200}}


def test_llm_planner_proposal_accepted_when_it_matches_evidence():
    planner = _planner('{"action":"keep_stable_and_verify","rationale":"v9 failed; stay stable.","cited":["incident"]}')
    result = CoordinationWorkflow(planner=planner).run(FAILED)
    assert result["plan"]["planner"]["source"] == "llm"
    assert result["plan"]["planner"]["guardrail"] == "accepted"
    assert result["plan"]["action"] == "keep_stable_and_verify"
    assert result["plan"]["reason"].startswith("v9 failed; stay stable.")
    assert "graph_memory" in planner.transport.calls[0][1]["content"]


def test_llm_planner_unsafe_proposal_is_vetoed():
    planner = _planner('{"action":"continue_candidate_monitoring","rationale":"looks fine","cited":[]}')
    result = CoordinationWorkflow(planner=planner).run(FAILED)
    assert result["plan"]["planner"]["guardrail"] == "vetoed"
    assert result["plan"]["action"] == "keep_stable_and_verify"
    assert "looks fine" not in result["plan"]["reason"]


def test_llm_planner_failure_falls_back_to_deterministic_plan():
    for content in (RuntimeError("Groq API unavailable"), "not json", '{"action":"rm -rf"}'):
        result = CoordinationWorkflow(planner=_planner(content)).run(FAILED)
        assert result["plan"]["planner"]["guardrail"] == "planner_error"
        assert result["plan"]["action"] == "keep_stable_and_verify"
