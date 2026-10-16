from concurrent.futures import ThreadPoolExecutor
from app.laboratory import Laboratory, assess, experiment


def test_unknown_actions_require_inspection():
    assert assess([], ["direct", "canary"])["action"] == "inspect"


def test_contexts_do_not_cancel_each_others_failures():
    result = experiment("deployment", "test")
    direct, canary = result["decision"]["beliefs"]
    assert direct["failures"] == 2 and direct["successes"] == 0
    assert canary["successes"] == 3 and canary["failures"] == 0
    assert result["decision"]["action"] == "canary"
    assert result["timeline"][0]["after"]["action"] == "inspect"
    assert result["execution"]["simulated"] is True


def test_evidence_removal_can_withhold_execution():
    events = [{"id": str(i), "action": "canary", "success": True} for i in range(2)]
    assert assess(events, ["direct", "canary"])["action"] == "canary"
    assert assess(events[:1], ["direct", "canary"])["action"] == "inspect"


def test_retries_and_concurrency_do_not_duplicate_runs():
    lab = Laboratory()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: lab.run("migration", "same"), range(12)))
    assert len(lab.cache) == 1
    assert all(result == results[0] for result in results)
    results[0]["events"].clear()
    assert len(lab.run("migration", "same")["events"]) == 5
