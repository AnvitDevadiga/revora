from concurrent.futures import ThreadPoolExecutor
import pytest
from app.runtime import Runtime, execute, evaluate

def request(identifier, service="contacts"):
    return {"request_id": identifier, "service": service, "environment": "sandbox",
            "workload": "import", "values": ["a", "a", "b"]}

def test_real_database_failure_rolls_back():
    result = execute("create_unique_index", ["a", "a", "b"])
    assert result["success"] is False
    assert result["rows_after"] == 3
    assert result["index_created"] is False

def test_agent_learns_and_second_run_avoids_known_failure():
    agent = Runtime()
    first = agent.run(request("first"))
    assert len(first["events"]) == 2
    assert not first["events"][0]["outcome"]["success"]
    assert first["success"]
    assert first["why_changed"]
    second = agent.run(request("second"))
    assert len(second["events"]) == 1
    assert second["events"][0]["action"] == "deduplicate_then_index"
    assert second["initial"]["evidence"] == ["first:1"]

def test_context_prevents_unrelated_evidence_leakage():
    agent = Runtime()
    agent.run(request("first"))
    second = agent.run(request("second", service="other"))
    assert len(second["events"]) == 2
    assert second["trace"][0]["evidence_count"] == 0

def test_retries_and_conflicting_ids():
    agent = Runtime()
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: agent.run(request("same")), range(6)))
    assert all(result == results[0] for result in results)
    assert len(agent.history()) == 1
    with pytest.raises(ValueError):
        agent.run(request("same", service="different"))

def test_evaluation_reports_case_results_not_marketing_numbers():
    report = evaluate()
    assert report["count"] == 8
    assert report["baseline_successes"] == 4
    assert report["memory_successes"] == 8
    assert report["training_observations"] == 2

def test_unregistered_tool_rejected():
    with pytest.raises(ValueError):
        execute("shell", ["a"])

def test_current_failure_overrides_many_old_successes():
    agent = Runtime()
    for index in range(6):
        agent.run({**request(f"clean-{index}"), "values": ["a", "b"]})
    changed = agent.run(request("now-dirty"))
    assert changed["success"]
    assert [e["action"] for e in changed["events"]] == ["create_unique_index", "deduplicate_then_index"]
    assert "overrides" in changed["events"][1]["before"]["reason"]
