from app.memory import MemoryStore


def canary(decision, scenario="canary_regression"):
    return {"scenario": scenario, "decision": decision, "success": decision == "promote",
            "http_status": 200 if decision == "promote" else 503, "probe_ms": 8,
            "synthetic_metrics": {"latency_ms": 18 if decision == "promote" else 210,
                                  "error_rate": 0 if decision == "promote" else 0.2},
            "gates": {"health": decision == "promote", "latency": decision == "promote",
                      "errors": decision == "promote"}}


def test_records_evidence_path_and_revises_conflicting_belief():
    memory = MemoryStore()
    first = memory.record_deployment("payments", "canary_regression", "promote", True,
                                     "All gates passed", canary("promote"))
    second = memory.record_deployment("payments", "canary_regression", "rollback", False,
                                      "Latency gate failed", canary("rollback"),
                                      {"revised_statement": "Require current gates before promotion.",
                                       "reason": "The new failure changes confidence in prior success."})

    assert first["contradiction_detected"] is False
    assert second["contradiction_detected"] is True
    assert second["belief"]["proposition_type"] == "gate_outcome_expectation"
    assert second["model_proposal_authoritative"] is False
    state = memory.graph_state()
    relations = [edge["relation"] for edge in state["edges"]]
    assert relations.count("LED_TO") == 2
    assert "CONTRADICTS" in relations and "UPDATES" in relations and "SUPPORTS" in relations
    old_belief = next(node for node in state["nodes"] if node["id"] == first["belief"]["id"])
    assert old_belief["status"] == "revised"
    assert memory.history()[0]["outcome"] == "failure"


def test_only_matching_canary_decision_can_be_recorded():
    memory = MemoryStore()
    observed = canary("rollback")
    try:
        memory.record_deployment("payments", observed["scenario"], "promote", True,
                                 "forged", observed)
    except ValueError as error:
        assert "match the observed" in str(error)
    else:
        raise AssertionError("Mismatched observed and recorded action must be rejected")


def test_groq_reflection_can_propose_revision_but_cannot_change_the_observed_result():
    from app.groq_agent import GroqAgent

    memory = MemoryStore()
    memory.record_deployment("payments", "canary_regression", "promote", True, "All gates passed",
                             canary("promote"))

    class ReflectionTransport:
        def complete(self, messages, tools):
            assert tools == []
            return {"content": '{"revised_statement":"Require current gates before every promotion.",'
                    '"reason":"The observed rollback contradicts the earlier success."}'}

    agent = GroqAgent(transport=ReflectionTransport(), memory=memory)
    agent.last_canary = canary("rollback")
    result = agent.record_outcome("payments", "canary_regression", "rollback", False,
                                  "Latency and error gates failed")
    assert result["contradiction_detected"] is True
    assert result["model_proposal"]["revised_statement"] == "Require current gates before every promotion."
    assert "never authorizes promotion" in result["belief"]["statement"]
    assert result["belief"]["outcome"] == "failure"
    assert result["evidence"]["metrics"]["gates"]["latency"] is False


def test_different_scenario_outcomes_do_not_contradict_each_other():
    memory = MemoryStore()
    first = memory.record_deployment("payments", "healthy", "promote", True, "pass", canary("promote", "healthy"))
    second = memory.record_deployment("payments", "regression", "rollback", False, "fail", canary("rollback", "regression"))
    assert not second["contradiction_detected"]
    assert memory.active_belief("payments", "healthy")["id"] == first["belief"]["id"]
    assert not any(e["relation"] == "CONTRADICTS" for e in memory.graph_state()["edges"])


def test_failed_graph_mutation_does_not_publish_local_partial_state():
    class FailureGraph:
        def query(self, statement, params=None):
            if statement.startswith("CREATE INDEX"):
                return None
            if "CREATE (x:Experience" in statement:
                raise RuntimeError("injected write failure")
            return type("Result", (), {"result_set": []})()
    memory = MemoryStore(FailureGraph())
    import pytest
    with pytest.raises(RuntimeError, match="injected"):
        memory.record_deployment("payments", "healthy", "promote", True, "pass", canary("promote", "healthy"))
    assert memory._nodes == {} and memory._edges == {}
