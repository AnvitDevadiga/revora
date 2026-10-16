"""Contextual memory experiment. All executions are explicitly simulated.

One graph write stores each run, its immutable observations, revisions and
decisions. Replay IDs make retrying a run idempotent. No production tools run.
"""
import hashlib
import json
from threading import RLock


SCENARIOS = {
    "deployment": {
        "title": "The Friday deployment myth",
        "context": "payments / Friday / release",
        "choices": ["direct", "canary"],
        "observations": [("direct", False), ("canary", True), ("canary", True),
                         ("direct", False), ("canary", True)],
        "goal": "Release payments safely",
    },
    "migration": {
        "title": "The migration that locks the database",
        "context": "orders / large table / schema migration",
        "choices": ["blocking", "online"],
        "observations": [("blocking", False), ("online", True), ("online", True),
                         ("blocking", False), ("online", True)],
        "goal": "Change the schema without an outage",
    },
}


def assess(events, choices):
    beliefs = []
    for action in choices:
        evidence = [e for e in events if e["action"] == action]
        wins = sum(e["success"] for e in evidence)
        failures = len(evidence) - wins
        beliefs.append({"action": action, "successes": wins, "failures": failures,
                        "score": round((1 + wins) / (2 + len(evidence)), 4),
                        "evidence": [e["id"] for e in evidence]})
    best = max(beliefs, key=lambda b: b["score"])
    # Unknown alternatives are investigated, never automatically executed.
    selected = best["action"] if best["successes"] >= 2 and best["score"] >= .7 else "inspect"
    return {"action": selected, "beliefs": beliefs,
            "reason": "At least two successful observations and score ≥ 0.70 required."
                      if selected == "inspect" else "Selected the eligible action with strongest contextual evidence.",
            "evidence": best["evidence"] if selected != "inspect" else []}


def experiment(scenario_id, run_id):
    scenario = SCENARIOS[scenario_id]
    events, timeline = [], []
    for index, (action, success) in enumerate(scenario["observations"]):
        before = assess(events, scenario["choices"])
        event = {"id": f"{run_id}:e{index}", "action": action, "success": success,
                 "context": scenario["context"], "source": "synthetic fixture",
                 "outcome": "healthy" if success else "incident; rollback required"}
        events.append(event)
        after = assess(events, scenario["choices"])
        timeline.append({"id": f"{run_id}:r{index}", "trigger": event["id"],
                         "before": before, "after": after,
                         "changed": before["action"] != after["action"]})
    final = assess(events, scenario["choices"])
    trace = [{"role": "retriever", "result": f"Retrieved {len(events)} observations for {scenario['context']}"},
             {"role": "planner", "result": final["action"]},
             {"role": "reviewer", "result": "Passed evidence threshold" if final["action"] != "inspect" else "Execution withheld"}]
    # Deterministic sandbox, not a real deployment or a learned outcome model.
    execution = {"tool": "sandbox.execute", "action": final["action"],
                 "success": final["action"] == scenario["choices"][1], "simulated": True}
    trace.append({"role": "executor", "result": execution})
    ablations = []
    for event in events:
        without = assess([e for e in events if e["id"] != event["id"]], scenario["choices"])
        ablations.append({"removed": event["id"], "action": without["action"],
                          "changed": without["action"] != final["action"]})
    return {"id": run_id, "scenario": scenario_id, "title": scenario["title"],
            "goal": scenario["goal"], "events": events, "timeline": timeline,
            "decision": final, "trace": trace, "execution": execution,
            "ablations": ablations, "limitations": "Synthetic sandbox; scores are Beta(1,1) posterior means, not calibrated real-world safety probabilities."}


class Laboratory:
    def __init__(self, graph=None):
        self.graph = graph
        self.cache = {}
        self.lock = RLock()

    def run(self, scenario, replay_id):
        if scenario not in SCENARIOS:
            raise ValueError("Unknown scenario")
        run_id = hashlib.sha256(f"v1:{scenario}:{replay_id}".encode()).hexdigest()[:24]
        with self.lock:
            if self.graph is not None:
                rows = self.graph.query("MATCH (r:LabRun {id:$id}) RETURN r.payload", {"id": run_id}).result_set
                if rows:
                    return json.loads(rows[0][0])
            elif run_id in self.cache:
                return json.loads(self.cache[run_id])
            result = experiment(scenario, run_id)
            payload = json.dumps(result)
            if self.graph is not None:
                # Single mutation: no half-written run if a later write fails.
                self.graph.query('''MERGE (r:LabRun {id:$id}) SET r.payload=$payload
                    MERGE (d:LabDecision {id:$id}) SET d.action=$action
                    MERGE (r)-[:DECIDED]->(d)
                    MERGE (x:LabExecution {id:$id}) SET x.simulated=true, x.success=$success
                    MERGE (d)-[:EXECUTED]->(x)
                    WITH r,d UNWIND $events AS e
                    MERGE (o:LabObservation {id:e.id})
                    SET o.action=e.action, o.success=e.success, o.context=e.context
                    MERGE (r)-[:OBSERVED]->(o)
                    MERGE (d)-[:CONSIDERED]->(o)
                    WITH DISTINCT r UNWIND $revisions AS revision
                    MERGE (v:LabRevision {id:revision.id})
                    SET v.before=revision.before, v.after=revision.after
                    MERGE (r)-[:REVISED]->(v)
                    WITH r,v,revision MATCH (o:LabObservation {id:revision.trigger})
                    MERGE (o)-[:TRIGGERED]->(v)''',
                    {"id": run_id, "payload": payload, "events": result["events"],
                     "action": result["decision"]["action"], "success": result["execution"]["success"],
                     "revisions": [{"id": t["id"], "trigger": t["trigger"],
                                    "before": json.dumps(t["before"]), "after": json.dumps(t["after"])}
                                   for t in result["timeline"]]})
            else:
                if len(self.cache) >= 100:
                    del self.cache[next(iter(self.cache))]
                self.cache[run_id] = payload
            return result
