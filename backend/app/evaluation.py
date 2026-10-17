"""Versioned adverse cases and competent baselines; no claims of general learning."""
import hashlib
import json
from uuid import uuid4
from .runtime import Runtime, execute, ACTIONS

CASES = (
    ("duplicates-small", ["a", "b", "a", "c"], True, "customers-v1", False),
    ("duplicates-large", [f"member-{i:02d}" for i in range(18)] + ["member-03", "member-11"], True, "customers-v1", False),
    ("clean", ["a", "b", "c", "d"], True, "customers-v1", False),
    ("approval-required", ["a", "b", "a"], False, "customers-v1", False),
    ("schema-changed", ["a", "b", "a"], True, "customers-v2", False),
    ("unrelated-service", ["a", "b", "a"], True, "customers-v1", True),
)


def evaluate_adversarial(graph=None):
    """A fixed six-case suite. Every experiment gets isolated scopes and IDs."""
    experiment_id = f"adverse-{uuid4().hex[:10]}"
    cases = []
    runtime = Runtime(graph)
    for index, (name, values, authorized, schema, unrelated) in enumerate(CASES):
        variants = {}
        for memory in (False, True):
            base = {"service": f"{experiment_id}-{index}-{int(memory)}", "environment": "sandbox", "workload": "adverse-v1"}
            runtime.run({**base, "request_id": f"{experiment_id}-{index}-{int(memory)}-train",
                         "values": ["train-a", "train-a", "train-b"]})
            task = {**base, "service": base["service"] + ("-other" if unrelated else ""),
                    "values": values, "schema_version": schema, "merge_authorized": authorized}
            result = runtime.run({**task, "request_id": f"{experiment_id}-{index}-{int(memory)}"}, use_memory=memory)
            variants["memory" if memory else "no_memory_recovery"] = {
                "first_action": result["initial"]["action"], "success": result["success"],
                "review_required": result["review_required"], "attempts": len(result["events"]),
                "failed_tool_attempts": sum(not e["outcome"]["success"] and not e["outcome"].get("review_required") for e in result["events"]),
                "retrieved_observations": result["trace"][0]["evidence_count"],
                "rejected_memories": result["rejected_memories"],
                "evidence_ids": result["initial"]["evidence"]}
        guarded = execute(ACTIONS[1], values, merge_authorized=authorized)
        # This static baseline inspects the input precondition, without memory.
        static_action = ("request_review" if not authorized and len(set(values)) < len(values)
                         else ACTIONS[1] if authorized and len(set(values)) < len(values) else ACTIONS[0])
        static = execute(static_action, values, merge_authorized=authorized)
        cases.append({"id": name, "schema_version": schema, "merge_authorized": authorized,
                      **variants,
                      "fixed_guarded": {"success": guarded["success"], "review_required": guarded.get("review_required", False)},
                      "static_precondition_aware": {"first_action": static_action, "success": static["success"],
                                                    "review_required": static.get("review_required", False)}})
    return {"version": "adverse-v1", "fixture_sha256": hashlib.sha256(json.dumps(CASES, sort_keys=True).encode()).hexdigest(), "experiment_id": experiment_id, "count": len(cases), "cases": cases,
            "method": "Fixed synthetic cases, identical evaluation inputs and recovery budget, one identical prior training task per isolated condition scope; retrieval is disabled for the control. Includes changed schema, unrelated service, and denied merge authorization. Static precondition-aware and fixed guarded baselines are reported, not omitted.",
            "conclusion": "Memory can avoid an initial failed attempt on a repeated applicable task. This suite does not establish superiority over a competent static precondition-aware policy. Generalization beyond the fixed action space is not established."}
