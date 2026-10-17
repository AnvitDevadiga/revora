"""Local SQLite agent: execute bounded tools, observe errors, retain experience.

No user databases, filesystem paths, shell commands, or arbitrary SQL accepted.
FalkorDB is the persistent context layer; SQLite is a disposable tool sandbox.
"""
import hashlib
import json
import sqlite3
from threading import RLock
from datetime import datetime, timezone
from uuid import uuid4

ACTIONS = ("create_unique_index", "deduplicate_then_index")


def execute(action, values, merge_authorized=True):
    if action == "request_review" or (action == "deduplicate_then_index" and not merge_authorized):
        return {"success": False, "review_required": True, "executed": False, "rows_removed": 0,
                "detail": "Identity reconciliation requires explicit merge authorization; no database was changed."}
    if action not in ACTIONS:
        raise ValueError("Tool is not allowlisted")
    database = sqlite3.connect(":memory:")
    try:
        database.execute("PRAGMA foreign_keys = ON")
        database.executescript("""
            CREATE TABLE customers (id INTEGER PRIMARY KEY, email TEXT NOT NULL);
            CREATE TABLE invoices (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
                amount_cents INTEGER NOT NULL CHECK(amount_cents >= 0)
            );
            CREATE TABLE reconciliation_audit (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL,
                duplicate_customer_id INTEGER NOT NULL,
                canonical_customer_id INTEGER NOT NULL,
                invoices_relinked INTEGER NOT NULL,
                value_cents_preserved INTEGER NOT NULL
            );
        """)
        for index, email in enumerate(values, start=1):
            customer_id = database.execute("INSERT INTO customers(email) VALUES (?)", (email,)).lastrowid
            # Synthetic child records make referential integrity and financial
            # totals observable; no real customer or billing data is accepted.
            for invoice_index in range(1 + index % 3):
                cents = 500 + ((index * 97 + invoice_index * 31) % 50000)
                database.execute("INSERT INTO invoices(customer_id, amount_cents) VALUES (?, ?)",
                                 (customer_id, cents))
        database.commit()
        before = database.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        invoice_count_before, invoice_total_before = database.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount_cents), 0) FROM invoices").fetchone()
        try:
            database.execute("BEGIN")
            if action == "deduplicate_then_index":
                # Keep the first customer identity, re-point every dependent
                # invoice, audit the merge, then delete the duplicate identity.
                duplicates = database.execute(
                    "SELECT email, MIN(id) FROM customers GROUP BY email HAVING COUNT(*) > 1").fetchall()
                for email, canonical_id in duplicates:
                    duplicate_ids = [row[0] for row in database.execute(
                        "SELECT id FROM customers WHERE email = ? AND id <> ? ORDER BY id",
                        (email, canonical_id)).fetchall()]
                    for duplicate_id in duplicate_ids:
                        invoice_count, value_cents = database.execute(
                            "SELECT COUNT(*), COALESCE(SUM(amount_cents), 0) FROM invoices WHERE customer_id = ?",
                            (duplicate_id,)).fetchone()
                        database.execute("UPDATE invoices SET customer_id = ? WHERE customer_id = ?",
                                         (canonical_id, duplicate_id))
                        database.execute("INSERT INTO reconciliation_audit(email, duplicate_customer_id, canonical_customer_id, invoices_relinked, value_cents_preserved) VALUES (?, ?, ?, ?, ?)",
                                         (email, duplicate_id, canonical_id, invoice_count, value_cents))
                        database.execute("DELETE FROM customers WHERE id = ?", (duplicate_id,))
            database.execute("CREATE UNIQUE INDEX unique_email ON customers(email)")
            database.commit()
            after = database.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
            invoice_count_after, invoice_total_after = database.execute(
                "SELECT COUNT(*), COALESCE(SUM(amount_cents), 0) FROM invoices").fetchone()
            duplicate_count = database.execute(
                "SELECT COUNT(*) FROM (SELECT email FROM customers GROUP BY email HAVING COUNT(*) > 1)").fetchone()[0]
            orphan_count = len(database.execute("PRAGMA foreign_key_check").fetchall())
            invariants = {
                "unique_identity": duplicate_count == 0 and database.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name='unique_email'").fetchone()[0] == 1,
                "all_invoices_preserved": invoice_count_after == invoice_count_before,
                "invoice_value_preserved": invoice_total_after == invoice_total_before,
                "no_orphaned_invoice_references": orphan_count == 0,
            }
            success = all(invariants.values())
            relinked, audited_value = database.execute(
                "SELECT COALESCE(SUM(invoices_relinked), 0), COALESCE(SUM(value_cents_preserved), 0) FROM reconciliation_audit").fetchone()
            return {"success": success, "rows_before": before, "rows_after": after,
                    "rows_removed": before - after, "index_created": invariants["unique_identity"],
                    "invoice_count_before": invoice_count_before, "invoice_count_after": invoice_count_after,
                    "invoice_total_cents_before": invoice_total_before,
                    "invoice_total_cents_after": invoice_total_after,
                    "invoice_references_relinked": relinked,
                    "merge_audit_value_cents": audited_value,
                    "invariants": invariants,
                    "detail": ("Identity merge and unique index verified; invoice references and totals preserved."
                               if success else "Migration stopped: a data-integrity invariant failed.")}
        except sqlite3.IntegrityError as error:
            database.rollback()
            invoice_count_after, invoice_total_after = database.execute(
                "SELECT COUNT(*), COALESCE(SUM(amount_cents), 0) FROM invoices").fetchone()
            return {"success": False, "rows_before": before,
                    "rows_after": database.execute("SELECT COUNT(*) FROM customers").fetchone()[0],
                    "rows_removed": 0, "index_created": False,
                    "invoice_count_before": invoice_count_before, "invoice_count_after": invoice_count_after,
                    "invoice_total_cents_before": invoice_total_before,
                    "invoice_total_cents_after": invoice_total_after,
                    "invoice_references_relinked": 0,
                    "invariants": {"failed_transaction_rolled_back": True,
                                   "all_invoices_preserved": invoice_count_after == invoice_count_before,
                                   "invoice_value_preserved": invoice_total_after == invoice_total_before},
                    "detail": str(error)}
    finally:
        database.close()


def policy(events, merge_authorized=True):
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
    direct, guarded = beliefs
    guarded_supported = guarded["successes"] > 0
    if direct["failures"] and (not merge_authorized or not guarded_supported):
        return {"action": "request_review", "beliefs": beliefs, "evidence": direct["evidence"],
                "reason": "Known direct failure without an applicable, authorized procedure path; request review."}
    eligible = beliefs if merge_authorized and guarded_supported else [direct]
    best = max(eligible, key=lambda b: b["score"])
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

    def run(self, request, use_memory=True):
        """Request ID is scoped to the one local workspace, not an auth identity."""
        request = dict(request)
        schema_version = request.get("schema_version", "customers-v1")
        # Existing callers that omit the field retain the original demo flow;
        # the HTTP contract defaults to false, making destructive use explicit.
        merge_authorized = request.get("merge_authorized", True)
        fingerprint_input = dict(request)
        if schema_version == "customers-v1":
            fingerprint_input.pop("schema_version", None)
        if merge_authorized is True:
            fingerprint_input.pop("merge_authorized", None)
        encoded = json.dumps(fingerprint_input, sort_keys=True)
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        run_id = request["request_id"]
        context = json.dumps({k: request[k] for k in ("service", "environment", "workload")}, sort_keys=True)
        with self.lock:
            if self.graph is not None:
                rows = self.graph.query("MATCH (r:AgentRun {id:$id}) RETURN r.payload LIMIT 1", {"id": run_id}).result_set
                previous_run = json.loads(rows[0][0]) if rows else None
            else:
                previous_run = json.loads(self.runs[run_id]) if run_id in self.runs else None
            if previous_run is not None:
                if (previous_run["fingerprint"] != fingerprint or
                        previous_run.get("memory_enabled", True) != use_memory):
                    raise ValueError("Request ID already belongs to different input")
                return previous_run
            if self.graph is not None:
                count = self.graph.query("MATCH (r:AgentRun) RETURN count(r)").result_set[0][0]
                previous = []
                mismatches = self.graph.query(
                    "MATCH (r:AgentRun) WHERE r.context <> $context RETURN r.id ORDER BY r.created_at DESC LIMIT 20",
                    {"context": context}).result_set
                rejected = [{"run_id": row[0], "reason": "context_mismatch"} for row in mismatches]
            else:
                previous = self.history()
                count = len(previous)
                rejected = [{"run_id": r["id"], "reason": "context_mismatch"}
                            for r in previous if r["context"] != context][-20:]
            if count >= 1000:
                raise ValueError("Local demo run limit reached; use a fresh graph for a new experiment")
            candidates = []
            if use_memory and self.graph is not None:
                rows = self.graph.query(
                    "MATCH (r:AgentRun {context:$context})-[:OBSERVED]->(o:AgentObservation) "
                    "WHERE r.memory_enabled = true "
                    "OPTIONAL MATCH (o)-[:VALIDATES]->(p:VerifiedProcedure {status:'active'}) "
                    "RETURN o.payload,r.schema_version,p.id ORDER BY r.created_at,o.id",
                    {"context": context}).result_set
                candidates = [(json.loads(row[0]), row[1] or "customers-v1", row[2]) for row in rows]
            elif use_memory:
                candidates = [(event, r.get("schema_version", "customers-v1"), event.get("verified_procedure_id"))
                              for r in previous if r["context"] == context and r.get("memory_enabled", True)
                              for event in r["events"]]
            evidence = []
            for event, source_schema, procedure_id in candidates:
                rejection = ("schema_version_mismatch" if source_schema != schema_version else
                             "missing_active_procedure_path" if event["outcome"]["success"] and not procedure_id else
                             "merge_not_authorized" if event["action"] == ACTIONS[1] and not merge_authorized else None)
                if rejection:
                    rejected.append({"observation_id": event["id"], "reason": rejection})
                else:
                    evidence.append(event)
            initial = policy(evidence, merge_authorized)
            events, trace = [], [{"role": "retriever", "evidence_count": len(evidence), "context": context}]
            for attempt in range(2):
                before = policy(evidence + events, merge_authorized)
                failed_here = {e["action"] for e in events if not e["outcome"]["success"]}
                if failed_here and (before["action"] in failed_here or before["action"] == "request_review"):
                    alternative = (ACTIONS[1] if merge_authorized else "request_review")
                    before["action"] = alternative
                    before["evidence"] = next((b["evidence"] for b in before["beliefs"] if b["action"] == alternative), [])
                    before["reason"] = "Current tool failure overrides historical preference; never repeat a failed tool on unchanged input."
                trace.append({"role": "planner", "action": before["action"], "evidence": before["evidence"]})
                outcome = execute(before["action"], request["values"], merge_authorized)
                event = {"id": f"{run_id}:{attempt}", "action": before["action"], "outcome": outcome}
                if outcome["success"]:
                    event["verified_procedure_id"] = hashlib.sha256(f"{context}:{schema_version}:{event['action']}".encode()).hexdigest()
                events.append(event)
                event["before"] = before
                event["after"] = policy(evidence + events, merge_authorized)
                trace.append({"role": "executor", "tool": before["action"], "outcome": outcome})
                trace.append({"role": "memory", "observation": event["id"],
                              "relation": "SUPPORTS" if outcome["success"] else "CONTRADICTS"})
                if outcome["success"] or outcome.get("review_required"):
                    break
            result = {"id": run_id, "fingerprint": fingerprint, "context": context,
                      "created_at": datetime.now(timezone.utc).isoformat(),
                      "goal": "Create a unique customer email index in a disposable database",
                      "initial": initial, "events": events, "trace": trace,
                      "final": policy(evidence + events, merge_authorized), "success": events[-1]["outcome"]["success"],
                      "execution_mode": "real SQLite tools on disposable synthetic data",
                      "memory_enabled": use_memory, "schema_version": schema_version,
                      "merge_authorized": merge_authorized, "rejected_memories": rejected,
                      "review_required": events[-1]["outcome"].get("review_required", False),
                      "why_changed": [{"trigger": e["id"], "from": e["before"]["action"], "to": e["after"]["action"]}
                                      for e in events if e["before"]["action"] != e["after"]["action"]]}
            if self.graph is not None:
                self.graph.query('''MERGE (r:AgentRun {id:$id})
                    SET r.context=$context,r.created_at=$created_at,r.payload=$payload,
                        r.memory_enabled=$memory_enabled,r.schema_version=$schema_version
                    WITH r UNWIND $events AS event
                    CREATE (o:AgentObservation {id:event.id, action:event.action, success:event.success, detail:event.detail, payload:event.payload})
                    CREATE (r)-[:OBSERVED]->(o)
                    FOREACH (_ IN CASE WHEN event.success THEN [1] ELSE [] END |
                        MERGE (procedure:VerifiedProcedure {id:event.procedure_id})
                        SET procedure.action=event.action,procedure.schema_version=$schema_version,procedure.status='active'
                        MERGE (o)-[:VALIDATES]->(procedure))
                    CREATE (v:BeliefVersion {id:event.id, before:event.before, after:event.after})
                    CREATE (o)-[:REVISED]->(v)
                    CREATE (p:AgentDecision {id:event.id, action:event.action})
                    CREATE (r)-[:DECIDED]->(p)
                    CREATE (p)-[:PRODUCED]->(o)
                    WITH p,event UNWIND event.evidence AS evidence_id
                    MATCH (prior:AgentObservation {id:evidence_id})
                    CREATE (p)-[:BASED_ON]->(prior)''',
                    {"id": run_id, "context": context, "created_at": result["created_at"],
                     "payload": json.dumps(result), "memory_enabled": use_memory, "schema_version": schema_version, "events": [
                         {"id": e["id"], "action": e["action"], "success": e["outcome"]["success"],
                          "procedure_id": e.get("verified_procedure_id", ""), "detail": e["outcome"]["detail"], "payload": json.dumps(e), "before": json.dumps(e["before"]),
                          "after": json.dumps(e["after"]), "evidence": e["before"]["evidence"]} for e in events]})
            else:
                self.runs[run_id] = json.dumps(result)
            return result

    def learning_proof(self):
        """Run a cold migration and a different-sized warm migration in a fresh scope."""
        with self.lock:
            cohort = f"proof-{uuid4().hex[:12]}"
            service = f"customer-registry-{uuid4().hex[:8]}"
            workload = "invoice-preserving-identity-migration"
            cold_values = ["ava@demo.test", "noah@demo.test", "ava@demo.test",
                           "mia@demo.test", "noah@demo.test", "lee@demo.test"]
            warm_values = [f"member-{index:02d}@demo.test" for index in range(18)]
            warm_values.extend(["member-03@demo.test", "member-11@demo.test",
                                "member-03@demo.test", "member-00@demo.test"])
            cold = self.run({"request_id": f"{cohort}-cold", "service": service,
                             "environment": "sandbox", "workload": workload, "values": cold_values})
            warm = self.run({"request_id": f"{cohort}-warm", "service": service,
                             "environment": "sandbox", "workload": workload, "values": warm_values})
            # Compare the same workload and the same two-attempt recovery budget.
            # The separate one-shot baseline remains visible as a simpler reference.
            baseline = self.run({"request_id": f"{cohort}-no-memory", "service": service,
                                 "environment": "sandbox", "workload": workload,
                                 "values": warm_values}, use_memory=False)
            single_shot_baseline = execute(ACTIONS[0], warm_values)
            cold_first = cold["events"][0]["action"]
            warm_first = warm["events"][0]["action"]
            preserved = all(warm["events"][0]["outcome"].get("invariants", {}).values())
            proof_passed = (cold_first == ACTIONS[0]
                            and len(cold["events"]) == 2
                            and warm["trace"][0]["evidence_count"] >= 2
                            and warm_first == ACTIONS[1]
                            and len(warm["events"]) == 1
                            and baseline["success"] and warm["success"] and preserved
                            and len(baseline["events"]) > len(warm["events"]))
            return {
                "cohort": cohort,
                "task": "Migrate a customer identity table to unique emails without losing or orphaning invoice records.",
                "cold_run": cold,
                "learned_run": warm,
                "no_memory_agent_baseline": baseline,
                "single_shot_direct_baseline": single_shot_baseline,
                "proof": {
                    "passed": proof_passed,
                    "cold_first_action": cold_first,
                    "cold_recovery_action": cold["events"][-1]["action"],
                    "learned_first_action": warm_first,
                    "prior_observations_retrieved": warm["trace"][0]["evidence_count"],
                    "cold_customer_rows": len(cold_values),
                    "learned_customer_rows": len(warm_values),
                    "learned_invoice_records_preserved": warm["events"][0]["outcome"].get("invoice_count_after")
                        == warm["events"][0]["outcome"].get("invoice_count_before"),
                    "learned_invoice_value_preserved": warm["events"][0]["outcome"].get("invoice_total_cents_after")
                        == warm["events"][0]["outcome"].get("invoice_total_cents_before"),
                    "no_memory_baseline_succeeded": baseline["success"],
                    "same_final_outcome": baseline["success"] == warm["success"],
                    "failed_attempts_avoided": sum(not e["outcome"]["success"] for e in baseline["events"])
                        - sum(not e["outcome"]["success"] for e in warm["events"]),
                    "learned_attempts": len(warm["events"]),
                    "no_memory_attempts": len(baseline["events"]),
                    "single_shot_direct_baseline_failed": not single_shot_baseline["success"],
                },
                "limitations": "Synthetic customer and invoice data in disposable SQLite databases. This proves this bounded migration policy changed after recorded evidence; it is not a claim of general intelligence or production safety.",
            }

    def memory_ablation(self):
        """Replay paired fixed workloads with and without retrieval from prior runs."""
        cases = [
            {"id": "duplicates-small", "values": ["a", "b", "a", "c"]},
            {"id": "duplicates-large", "values": [f"member-{i:02d}" for i in range(16)] + ["member-03", "member-11", "member-03"]},
            {"id": "clean-small", "values": ["a", "b", "c", "d"]},
        ]
        report = []
        experiment_id = f"ablation-{uuid4().hex[:12]}"
        for case in cases:
            paired = {}
            for enabled in (False, True):
                context = {"service": f"{experiment_id}-{case['id']}-{int(enabled)}", "environment": "sandbox",
                           "workload": "paired-ablation-v1"}
                cohort = f"ablation-{uuid4().hex[:10]}"
                training_values = ["seen-a", "seen-a", "seen-b"]
                if enabled:
                    self.run({**context, "request_id": f"{cohort}-train", "values": training_values}, use_memory=True)
                result = self.run({**context, "request_id": f"{cohort}-eval-{case['id']}",
                                   "values": case["values"]}, use_memory=enabled)
                no_memory_baseline = execute(ACTIONS[0], case["values"])
                paired["with_memory" if enabled else "without_memory"] = {
                    "first_action": result["events"][0]["action"],
                    "success": result["success"],
                    "attempts": len(result["events"]),
                    "failed_tool_attempts": sum(not e["outcome"]["success"] and not e["outcome"].get("review_required") for e in result["events"]),
                    "retrieved_observations": result["trace"][0]["evidence_count"],
                    "rows_removed": result["events"][-1]["outcome"].get("rows_removed", 0),
                    "no_memory_baseline_success": no_memory_baseline["success"],
                }
            report.append({"id": case["id"], **paired,
                           "action_changed": paired["with_memory"]["first_action"] != paired["without_memory"]["first_action"],
                           "outcome_changed": paired["with_memory"]["success"] != paired["without_memory"]["success"]})
        return {"experiment_id": experiment_id, "cases": report, "count": len(report),
                "memory_changed_action_cases": sum(item["action_changed"] for item in report),
                "memory_changed_outcome_cases": sum(item["outcome_changed"] for item in report),
                "failed_tool_attempts_avoided": sum(item["without_memory"]["failed_tool_attempts"] - item["with_memory"]["failed_tool_attempts"] for item in report),
                "method": "Paired deterministic synthetic SQLite workloads. Each condition uses a fresh context; the memory condition first records a cold failure and guarded recovery, then retrieves those observations for the same held-out case. Both conditions include the same direct-index baseline on identical values. Results apply only to these fixtures."}


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
            guarded = execute(ACTIONS[1], values)
            cases.append({"id": f"{'duplicates' if dirty else 'clean'}-{size}",
                          "baseline_success": baseline["success"], "memory_success": memory["success"],
                          "baseline_removed": baseline["rows_removed"], "memory_removed": memory["rows_removed"],
                          "fixed_guarded_success": guarded["success"]})
    return {"cases": cases, "count": len(cases),
            "baseline_successes": sum(c["baseline_success"] for c in cases),
            "memory_successes": sum(c["memory_success"] for c in cases),
            "training_observations": len(evidence), "selected_action": selected,
            "fixed_guarded_successes": sum(c["fixed_guarded_success"] for c in cases),
            "method": "One training dataset; eight held-out synthetic datasets. Baseline always tries direct index creation. Frozen memory policy; first-attempt success only. Deduplication may remove duplicate rows under the explicit sandbox policy. Not a production safety benchmark."}
