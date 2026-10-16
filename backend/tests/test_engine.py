from app.engine import MemoryEngine

def test_memory_evolves_and_changes_action():
    e = MemoryEngine()
    first = e.ingest("Friday deployment caused a production incident", "rollback", False)
    assert first["decision"]["action"].startswith("avoid")
    second = e.ingest("Friday deployment completed cleanly with a canary", "success", True)
    assert second["belief"]["status"] == "revised"
    assert len(second["belief"]["evidence"]) == 2
    assert second["decision"]["evidence"]
    e.ingest("Friday deployment completed cleanly with staged rollout", "success", True)
    assert e.decide()["action"].startswith("proceed")

def test_agent_run_creates_a_procedure_from_memory():
    e = MemoryEngine()
    e.ingest("Friday deployment caused a production incident", "rollback", False, source="production", impact=5)
    result = e.run_agent("Plan the safest next deployment")
    assert result["procedure"]["steps"]
    assert result["decision"]["action"].startswith("avoid")
