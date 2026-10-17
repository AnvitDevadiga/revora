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
    assert result["invoice_count_after"] == result["invoice_count_before"]
    assert result["invoice_total_cents_after"] == result["invoice_total_cents_before"]
    assert result["invariants"]["failed_transaction_rolled_back"]

def test_safe_migration_relinks_invoice_rows_and_preserves_value():
    result = execute("deduplicate_then_index", ["a", "b", "a", "a", "c"])
    assert result["success"] is True
    assert result["rows_before"] == 5 and result["rows_after"] == 3
    assert result["invoice_references_relinked"] > 0
    assert result["invoice_count_after"] == result["invoice_count_before"]
    assert result["invoice_total_cents_after"] == result["invoice_total_cents_before"]
    assert all(result["invariants"].values())

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

def test_one_click_learning_proof_changes_first_action_on_larger_unseen_workload():
    proof = Runtime().learning_proof()
    assert proof["proof"]["passed"] is True
    assert proof["proof"]["cold_first_action"] == "create_unique_index"
    assert proof["proof"]["cold_recovery_action"] == "deduplicate_then_index"
    assert proof["proof"]["learned_first_action"] == "deduplicate_then_index"
    assert proof["proof"]["prior_observations_retrieved"] >= 2
    assert proof["proof"]["learned_customer_rows"] > proof["proof"]["cold_customer_rows"]
    assert proof["proof"]["no_memory_baseline_succeeded"] is True
    assert proof["proof"]["same_final_outcome"] is True
    assert proof["proof"]["learned_attempts"] == 1
    assert proof["proof"]["no_memory_attempts"] == 2
    assert proof["proof"]["failed_attempts_avoided"] == 1
    assert proof["proof"]["learned_invoice_records_preserved"] is True
    assert proof["proof"]["learned_invoice_value_preserved"] is True

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

def test_memory_ablation_uses_paired_inputs_and_proves_retrieval():
    report = Runtime().memory_ablation()
    assert report["count"] == 3
    assert all(case["without_memory"]["retrieved_observations"] == 0 for case in report["cases"])
    assert all(case["with_memory"]["retrieved_observations"] >= 2 for case in report["cases"])
    assert all(case["without_memory"]["no_memory_baseline_success"] == case["with_memory"]["no_memory_baseline_success"]
               for case in report["cases"])
    assert report["memory_changed_action_cases"] >= 2

def test_no_memory_mode_does_not_retrieve_old_runs():
    agent = Runtime()
    agent.run({**request("train"), "values": ["a", "a", "b"]})
    result = agent.run({**request("no-memory"), "values": ["a", "a", "b"]}, use_memory=False)
    assert result["trace"][0]["evidence_count"] == 0
    assert result["memory_enabled"] is False

def test_no_memory_control_run_cannot_contaminate_later_memory_retrieval():
    agent = Runtime()
    task = {**request("control-seed"), "values": ["a", "a", "b"]}
    agent.run(task, use_memory=False)
    result = agent.run({**task, "request_id": "memory-after-control"}, use_memory=True)
    assert result["trace"][0]["evidence_count"] == 0
    assert result["initial"]["action"] == "create_unique_index"


def test_memory_mode_is_part_of_retry_identity():
    agent = Runtime()
    agent.run(request("mode-retry"), use_memory=False)
    with pytest.raises(ValueError, match="different input"):
        agent.run(request("mode-retry"), use_memory=True)


def test_ablation_experiments_do_not_reuse_training_contexts():
    agent = Runtime()
    first = agent.memory_ablation()
    second = agent.memory_ablation()
    assert first["experiment_id"] != second["experiment_id"]
    assert all(case["with_memory"]["retrieved_observations"] == 2 for case in second["cases"])


def test_schema_mismatch_rejects_old_procedure():
    agent = Runtime()
    agent.run(request("old-schema"))
    result = agent.run({**request("new-schema"), "schema_version": "customers-v2"})
    assert result["initial"]["action"] == "create_unique_index"
    assert result["trace"][0]["evidence_count"] == 0
    assert any(r["reason"] == "schema_version_mismatch" for r in result["rejected_memories"])


def test_memory_never_authorizes_an_unapproved_identity_merge():
    agent = Runtime()
    agent.run(request("training-permission"))
    result = agent.run({**request("no-permission"), "merge_authorized": False})
    assert result["initial"]["action"] == "request_review"
    assert result["review_required"] and not result["success"]
    assert result["events"][0]["outcome"]["executed"] is False
    assert any(r["reason"] == "merge_not_authorized" for r in result["rejected_memories"])

def test_identity_merge_is_denied_by_default():
    from app.main import SandboxRunIn
    assert SandboxRunIn.model_validate({"request_id": "safe-default", "values": ["x"]}).merge_authorized is False


def test_removing_procedure_support_withholds_learned_action():
    import json
    agent = Runtime()
    agent.run(request("procedure-source"))
    for key, value in list(agent.runs.items()):
        saved = json.loads(value)
        for event in saved["events"]:
            event.pop("verified_procedure_id", None)
        agent.runs[key] = json.dumps(saved)
    result = agent.run(request("missing-path"))
    assert result["initial"]["action"] == "request_review"
    assert any(r["reason"] == "missing_active_procedure_path" for r in result["rejected_memories"])


def test_adverse_suite_keeps_competent_baselines_and_permission_limits():
    from app.evaluation import evaluate_adversarial
    result = evaluate_adversarial()
    assert result["count"] == 6
    rows = {c["id"]: c for c in result["cases"]}
    assert rows["duplicates-small"]["memory"]["failed_tool_attempts"] == 0
    assert rows["duplicates-small"]["no_memory_recovery"]["failed_tool_attempts"] == 1
    assert rows["approval-required"]["memory"]["first_action"] == "request_review"
    assert rows["approval-required"]["static_precondition_aware"]["review_required"]
    assert rows["schema-changed"]["memory"]["retrieved_observations"] == 0
    assert rows["unrelated-service"]["memory"]["retrieved_observations"] == 0


def test_explicit_default_preconditions_preserve_request_retry_identity():
    agent = Runtime()
    original = agent.run(request("compatible-defaults"))
    assert agent.run({**request("compatible-defaults"), "schema_version": "customers-v1", "merge_authorized": True}) == original
