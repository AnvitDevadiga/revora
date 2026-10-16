"""Local SQLite agent: execute bounded tools, observe errors, retain experience.

No user databases, filesystem paths, shell commands, or arbitrary SQL accepted.
FalkorDB is the persistent context layer; SQLite is a disposable tool sandbox.
"""
import hashlib
import json
import sqlite3
from threading import RLock
from datetime import datetime, timezone

ACTIONS = ("create_unique_index", "deduplicate_then_index")


def execute(action, values):
    if action not in ACTIONS:
        raise ValueError("Tool is not allowlisted")
    database = sqlite3.connect(":memory:")
    try:
        database.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, email TEXT NOT NULL)")
        database.executemany("INSERT INTO customers(email) VALUES (?)", [(v,) for v in values])
        database.commit()
        before = database.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        try:
            database.execute("BEGIN")
            if action == "deduplicate_then_index":
                # Sandbox policy: retain the lowest ID of each duplicate email.
                database.execute("DELETE FROM customers WHERE id NOT IN (SELECT MIN(id) FROM customers GROUP BY email)")
            database.execute("CREATE UNIQUE INDEX unique_email ON customers(email)")
            database.commit()
            after = database.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
            return {"success": True, "rows_before": before, "rows_after": after,
                    "rows_removed": before - after, "index_created": True,
                    "detail": "Unique index verified in isolated SQLite database."}
        except sqlite3.IntegrityError as error:
            database.rollback()
            return {"success": False, "rows_before": before,
                    "rows_after": database.execute("SELECT COUNT(*) FROM customers").fetchone()[0],
                    "rows_removed": 0, "index_created": False, "detail": str(error)}
    finally:
        database.close()


def policy(events):
    beliefs = []
    for action in ACTIONS:
        evidence = [e for e in events if e["action"] == action]
        successes = sum(e["outcome"]["success"] for e in evidence)
        failures = len(evidence) - successes
        beliefs.append({"action": action, "score": (successes + 1) / (len(evidence) + 2),
                        "successes": successes, "failures": failures,
                        "evidence": [e["id"] for e in evidence]})
    # Prefer the less invasive tool when evidence is tied. Exploration is allowed
    # only because execute() always creates a fresh disposable SQLite database.
    best = max(beliefs, key=lambda b: b["score"])
    return {"action": best["action"], "beliefs": beliefs,
            "evidence": best["evidence"],
            "reason": "Highest success score in this exact context; ties favor no row deletion."}


class Runtime:
    def __init__(self, graph=None):
        self.graph = graph
        self.runs = {}
        self.lock = RLock()

    def history(self):
        if self.graph is not None:
            rows = self.graph.query("MATCH (r:AgentRun) RETURN r.payload ORDER BY r.created_at, r.id").result_set
            return [json.loads(row[0]) for row in rows]
        return [json.loads(value) for value in self.runs.values()]

    def run(self, request):
        """Request ID is scoped to the one local workspace, not an auth identity."""
        encoded = json.dumps(request, sort_keys=True)
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        run_id = request["request_id"]
        context = json.dumps({k: request[k] for k in ("service", "environment", "workload")}, sort_keys=True)
        with self.lock:
            previous = self.history()
            for run in previous:
                if run["id"] == run_id:
                    if run["fingerprint"] != fingerprint:
                        raise ValueError("Request ID already belongs to different input")
                    return run
            if len(previous) >= 1000:
                raise ValueError("Local demo run limit reached; use a fresh graph for a new experiment")
            if self.graph is not None:
                rows = self.graph.query('''MATCH (r:AgentRun {context:$context})-[:OBSERVED]->(o:AgentObservation)
                    RETURN o.payload ORDER BY r.created_at,o.id''', {"context": context}).result_set
                evidence = [json.loads(row[0]) for row in rows]
            else:
                evidence = [event for run in previous if run["context"] == context for event in run["events"]]
            initial = policy(evidence)
            events, trace = [], [{"role": "retriever", "evidence_count": len(evidence), "context": context}]
            for attempt in range(2):
                before = policy(evidence + events)
                failed_here = {e["action"] for e in events if not e["outcome"]["success"]}
                if before["action"] in failed_here:
                    alternative = next(action for action in ACTIONS if action not in failed_here)
                    before["action"] = alternative
                    before["evidence"] = next(b["evidence"] for b in before["beliefs"] if b["action"] == alternative)
                    before["reason"] = "Current tool failure overrides historical preference; never repeat a failed tool on unchanged input."
                trace.append({"role": "planner", "action": before["action"], "evidence": before["evidence"]})
                outcome = execute(before["action"], request["values"])
                event = {"id": f"{run_id}:{attempt}", "action": before["action"], "outcome": outcome}
                events.append(event)
                event["before"] = before
                event["after"] = policy(evidence + events)
                trace.append({"role": "executor", "tool": before["action"], "outcome": outcome})
                trace.append({"role": "memory", "observation": event["id"],
                              "relation": "SUPPORTS" if outcome["success"] else "CONTRADICTS"})
                if outcome["success"]:
                    break
            result = {"id": run_id, "fingerprint": fingerprint, "context": context,
                      "created_at": datetime.now(timezone.utc).isoformat(),
                      "goal": "Create a unique customer email index in a disposable database",
                      "initial": initial, "events": events, "trace": trace,
                      "final": policy(evidence + events), "success": events[-1]["outcome"]["success"],
                      "execution_mode": "real SQLite tools on disposable synthetic data",
                      "why_changed": [{"trigger": e["id"], "from": e["before"]["action"], "to": e["after"]["action"]}
                                      for e in events if e["before"]["action"] != e["after"]["action"]]}
            if self.graph is not None:
                self.graph.query('''CREATE (r:AgentRun {id:$id, context:$context, created_at:$created_at, payload:$payload})
                    WITH r UNWIND $events AS event
                    CREATE (o:AgentObservation {id:event.id, action:event.action, success:event.success, detail:event.detail, payload:event.payload})
                    CREATE (r)-[:OBSERVED]->(o)
                    CREATE (v:BeliefVersion {id:event.id, before:event.before, after:event.after})
                    CREATE (o)-[:REVISED]->(v)
                    CREATE (p:AgentDecision {id:event.id, action:event.action})
                    CREATE (r)-[:DECIDED]->(p)
                    CREATE (p)-[:PRODUCED]->(o)
                    WITH p,event UNWIND event.evidence AS evidence_id
                    MATCH (prior:AgentObservation {id:evidence_id})
                    CREATE (p)-[:BASED_ON]->(prior)''',
                    {"id": run_id, "context": context, "created_at": result["created_at"],
                     "payload": json.dumps(result), "events": [
                         {"id": e["id"], "action": e["action"], "success": e["outcome"]["success"],
                          "detail": e["outcome"]["detail"], "payload": json.dumps(e), "before": json.dumps(e["before"]),
                          "after": json.dumps(e["after"]), "evidence": e["before"]["evidence"]} for e in events]})
            else:
                self.runs[run_id] = json.dumps(result)
            return result


def evaluate():
    """Fixed out-of-sample synthetic values; no claims of generalization to production."""
    training = Runtime()
    base = {"service": "benchmark", "environment": "sandbox", "workload": "imported-contacts"}
    training.run({**base, "request_id": "training", "values": ["train-a", "train-a", "train-b"]})
    evidence = [e for run in training.history() for e in run["events"]]
    selected = policy(evidence)["action"]
    cases = []
    for size in (3, 7, 12, 25):
        for dirty in (False, True):
            values = [f"heldout-{size}-{i}" for i in range(size)]
            if dirty:
                values[-1] = values[0]
            baseline = execute(ACTIONS[0], values)
            memory = execute(selected, values)
            cases.append({"id": f"{'duplicates' if dirty else 'clean'}-{size}",
                          "baseline_success": baseline["success"], "memory_success": memory["success"],
                          "baseline_removed": baseline["rows_removed"], "memory_removed": memory["rows_removed"]})
    return {"cases": cases, "count": len(cases),
            "baseline_successes": sum(c["baseline_success"] for c in cases),
            "memory_successes": sum(c["memory_success"] for c in cases),
            "training_observations": len(evidence), "selected_action": selected,
            "method": "One training dataset; eight held-out synthetic datasets. Baseline always tries direct index creation. Frozen memory policy; first-attempt success only. Deduplication may remove duplicate rows under the explicit sandbox policy. Not a production safety benchmark."}
