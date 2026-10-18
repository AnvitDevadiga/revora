"""Bounded analyst, reviewer, and planner roles exchange a durable graph handoff."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4
from .release_controller import measure_gates

HANDOFF_AUTH_WINDOW_SECONDS = 300


class CoordinationWorkflow:
    def __init__(self, graph=None):
        self.graph = graph
        self.lock = RLock()
        self.records: dict[str, dict] = {}

    def publish(self, incident: dict) -> dict:
        """Release Analyst publishes observed evidence; no recovery action executes."""
        if not isinstance(incident, dict) or not incident.get("id"):
            raise ValueError("A recorded incident with an ID is required")
        if (not incident.get("service") or not incident.get("version") or
                incident.get("decision") not in {"rollback", "blocked_from_memory", "promote", "diagnosed"}):
            raise ValueError("Incident must include service, version, and a measured release decision")
        canary_samples = incident.get("canary_samples") or []
        evidence = {"incident_id": incident["id"], "service": incident["service"],
                    "version": incident["version"], "decision": incident["decision"], "onset_id": incident.get("onset_id"),
                    "failed": bool(incident.get("failed")),
                    "status_before": (incident.get("before") or {}).get("status"),
                    "status_during": (incident.get("during") or {}).get("status"),
                    "status_after": (incident.get("after") or {}).get("status"),
                    "sample_count": len(canary_samples),
                    "reported_sample_count": (incident.get("metrics") or {}).get("sample_count", 0),
                    "canary_statuses": [sample.get("status") for sample in canary_samples],
                    "canary_latencies_ms": [sample.get("latency_ms") for sample in canary_samples],
                    "metrics": incident.get("metrics") or {},
                    "measurement_mode": incident.get("measurement_mode", "unknown")}
        handoff = {"id": str(uuid4()), "type": "AgentHandoff", "from_agent": "release_analyst",
                   "to_agent": "recovery_planner", "created_at": datetime.now(timezone.utc).isoformat(),
                   "evidence": evidence}
        with self.lock:
            if self.graph is not None:
                rows = self.graph.query(
                    "MATCH (i:ReleaseIncident {id:$incident_id,service:$service,version:$version}) "
                    "CREATE (h:AgentHandoff {id:$id,service:$service,version:$version,measurement_mode:$mode,created_at:$created_at,incident_id:$incident_id,payload:$payload}) "
                    "CREATE (i)-[:HANDED_OFF]->(h) RETURN h.id",
                    {"incident_id": incident["id"], "service": incident["service"],
                     "version": incident["version"], "id": handoff["id"],
                     "created_at": handoff["created_at"], "mode": evidence["measurement_mode"], "payload": json.dumps(handoff)}).result_set
                if not rows:
                    raise ValueError("Source incident was not found in the graph")
            else:
                self.records[handoff["id"]] = {"handoff": handoff, "plan": None}
        return handoff

    def consume(self, handoff_id: str, use_memory: bool = True, executor=None) -> dict:
        """Recovery Planner reads the persisted handoff, including in a fresh process."""
        with self.lock:
            handoff = self._load(handoff_id)
            evidence = handoff["evidence"]
            prior = self._prior_plan(evidence, handoff_id) if use_memory else None
            review = self._review(evidence)
            execution = None
            if executor is not None:
                completed = self._completed(handoff_id)
                if completed:
                    return completed
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(handoff["created_at"])).total_seconds()
                if age < 0 or age > HANDOFF_AUTH_WINDOW_SECONDS:
                    raise ValueError("Stale handoff: recovery authorization expired")
                if review["status"] == "accepted":
                    if evidence["decision"] != "diagnosed":
                        review = {"status": "rejected", "requested_evidence": [],
                                  "contradictions": ["Only a measured diagnosis can authorize the recovery tool."]}
                    else:
                        execution = executor.recover_handoff(evidence, handoff_id)
            failed = evidence["decision"] in {"rollback", "blocked_from_memory"} or evidence["failed"]
            if review["status"] == "needs_evidence":
                action = "request_evidence"
                reason = "Recovery is paused until the analyst supplies the requested measurements."
            elif review["status"] == "rejected":
                action = "reject_handoff"
                reason = "Recovery is blocked because the handoff contradicts its measured evidence."
            else:
                action = "rollback_and_verify" if execution and execution["decision"] == "rollback" else "keep_stable_and_verify" if failed else "continue_candidate_monitoring"
                reason = ("Measured failure or prior failed version requires stable routing and health verification."
                          if failed else "The recorded candidate passed its measured gate; continue bounded monitoring.")
            if prior and prior["action"] == "keep_stable_and_verify":
                reason += " Prior graph handoff retained the stable route after failure."
            review_id = str(uuid4())
            plan = {"id": str(uuid4()), "type": "CoordinationDecision", "agent": "recovery_planner",
                    "handoff_id": handoff_id, "action": action, "reason": reason,
                    "based_on": [evidence["incident_id"]],
                    "prior_handoff_id": prior["handoff_id"] if prior else None,
                    "memory_enabled": use_memory, "evidence_review_id": review_id,
                    "evidence_review": review, "execution": execution,
                    "execution_status": "verified" if execution and execution.get("recovered") else
                    "failed" if execution else "not_executed" if review["status"] != "accepted" else "planned"}
            result = {"handoff": handoff, "plan": plan, "trace": [
                {"agent": "release_analyst", "step": "publish_measured_incident", "output": evidence},
                {"agent": "evidence_reviewer", "step": "validate_measurements", "output": review},
                {"agent": "recovery_planner", "step": "read_persisted_handoff", "input_handoff_id": handoff_id,
                 "retrieved_prior_handoff": prior, "output": plan}],
                "provenance": {"source_incident_id": evidence["incident_id"],
                               "source": "persisted_graph_handoff" if self.graph is not None else "temporary_memory_handoff",
                               "measurement_mode": evidence["measurement_mode"]}}
            result["execution"] = execution
            result["recovery"] = execution
            if self.graph is not None:
                rows = self.graph.query(
                    "MATCH (i:ReleaseIncident {id:$incident_id})-[:HANDED_OFF]->(h:AgentHandoff {id:$handoff_id}) "
                    "CREATE (r:EvidenceReview {id:$review_id,agent:'evidence_reviewer',status:$review_status,payload:$review_payload,created_at:$created_at}) "
                    "CREATE (h)-[:REVIEWED_BY]->(r) "
                    "CREATE (d:CoordinationDecision {id:$id,agent:'recovery_planner',action:$action,reason:$reason,payload:$payload,review_status:$review_status,execution_status:$execution_status}) "
                    "CREATE (r)-[:FORWARDED_TO]->(d) "
                    "CREATE (h)-[:HANDED_OFF_TO]->(d) CREATE (d)-[:BASED_ON]->(i) "
                    "WITH d OPTIONAL MATCH (prior:AgentHandoff {id:$prior_id}) "
                    "FOREACH (_ IN CASE WHEN prior IS NULL THEN [] ELSE [1] END | CREATE (d)-[:RETRIEVED]->(prior)) "
                    "WITH d OPTIONAL MATCH (outcome:ReleaseIncident {id:$outcome_id}) "
                    "FOREACH (_ IN CASE WHEN outcome IS NULL THEN [] ELSE [1] END | CREATE (d)-[:EXECUTED]->(outcome)) "
                    "RETURN d.id",
                    {"incident_id": evidence["incident_id"], "handoff_id": handoff_id,
                     "id": plan["id"], "action": action, "reason": reason,
                     "review_id": review_id, "review_payload": json.dumps(review),
                     "created_at": datetime.now(timezone.utc).isoformat(),
                     "payload": json.dumps(plan), "prior_id": plan["prior_handoff_id"] or "",
                     "review_status": review["status"], "execution_status": plan["execution_status"],
                     "outcome_id": execution["id"] if execution else ""}).result_set
                if not rows:
                    raise ValueError("Handoff evidence path was not found in the graph")
            else:
                self.records[handoff_id] = result
            return result

    @staticmethod
    def _review(evidence: dict) -> dict:
        """Independent bounded reviewer: request missing proof or reject contradictions."""
        requested = []
        contradictions = []
        if not evidence.get("incident_id"):
            requested.append("source incident ID")
        if not evidence.get("service") or not evidence.get("version"):
            requested.append("service and candidate version")
        if evidence.get("decision") != "diagnosed":
            return {"status": "accepted" if not requested else "needs_evidence",
                    "requested_evidence": requested, "contradictions": contradictions}

        if evidence.get("measurement_mode") != "live_gateway":
            contradictions.append("diagnosis is not sourced from live gateway measurements")
        if evidence.get("status_before") is None:
            requested.append("healthy pre-incident HTTP status")
        elif evidence["status_before"] != 200:
            contradictions.append("pre-incident status was not healthy HTTP 200")
        statuses = evidence.get("canary_statuses")
        latencies = evidence.get("canary_latencies_ms")
        if not isinstance(statuses, list) or not isinstance(latencies, list):
            requested.append("per-request canary status and latency samples")
        elif len(statuses) < 12 or len(statuses) != len(latencies):
            requested.append("at least 12 paired canary status and latency samples")
        else:
            try:
                measured = measure_gates([{"status": status, "latency_ms": latency}
                                          for status, latency in zip(statuses, latencies)])
            except (TypeError, ValueError):
                contradictions.append("canary contains invalid HTTP status or latency measurements")
                measured = None
            if measured is None:
                expected_failure = None
                error_rate = p95 = None
            else:
                error_rate = measured["error_rate"]
                p95 = measured["p95_latency_ms"]
                expected_failure = measured["failed"]
            metrics = evidence.get("metrics") or {}
            try:
                reported_error_rate = float(metrics.get("error_rate", -1))
                reported_p95 = float(metrics.get("p95_latency_ms", -1))
            except (TypeError, ValueError):
                reported_error_rate, reported_p95 = -1, -1
            if measured is not None:
                if evidence.get("reported_sample_count") != len(statuses):
                    contradictions.append("reported sample count does not match the raw samples")
                if abs(reported_error_rate - error_rate) > .001:
                    contradictions.append("reported error rate does not match the raw samples")
                if abs(reported_p95 - p95) > 1:
                    contradictions.append("reported p95 latency does not match the raw samples")
                if bool(evidence.get("failed")) != expected_failure:
                    contradictions.append("release decision conflicts with the measured error and latency gates")
            if evidence.get("status_during") is None:
                requested.append("observed routed HTTP status during the incident")
            elif measured is not None and evidence["status_during"] == 200 and error_rate == 0 and p95 <= measured["max_p95_latency_ms"]:
                contradictions.append("incident is labeled diagnosed but samples show no failed gate")

        status = "rejected" if contradictions else "needs_evidence" if requested else "accepted"
        return {"status": status, "requested_evidence": requested,
                "contradictions": contradictions}

    def run(self, incident: dict, use_memory: bool = True) -> dict:
        return self.consume(self.publish(incident)["id"], use_memory)

    def _load(self, handoff_id):
        if self.graph is not None:
            rows = self.graph.query(
                "MATCH (:ReleaseIncident)-[:HANDED_OFF]->(h:AgentHandoff {id:$id}) RETURN h.payload",
                {"id": handoff_id}).result_set
            if rows:
                return json.loads(rows[0][0])
        elif handoff_id in self.records:
            return self.records[handoff_id]["handoff"]
        raise ValueError("Handoff not found")

    def execute_recovery_tool(self, handoff_id: str, executor):
        """Apply the shared expiry and evidence-review policy to direct tool calls."""
        with self.lock:
            handoff = self._load(handoff_id)
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(handoff["created_at"])).total_seconds()
            if age < 0 or age > HANDOFF_AUTH_WINDOW_SECONDS:
                raise ValueError("Recovery authorization expired")
            review = self._review(handoff["evidence"])
            if review["status"] != "accepted" or handoff["evidence"].get("decision") != "diagnosed":
                raise ValueError("Recovery blocked: persisted handoff is not an accepted measured diagnosis")
            return executor.recover_handoff(handoff["evidence"], handoff_id)

    def _prior_plan(self, evidence, exclude_id):
        service = evidence["service"]
        if self.graph is not None:
            rows = self.graph.query(
                "MATCH (h:AgentHandoff {service:$service})-[:HANDED_OFF_TO]->(d:CoordinationDecision) "
                "WHERE h.id <> $exclude_id AND h.version=$version AND h.measurement_mode=$mode RETURN h.id,d.action ORDER BY h.created_at DESC LIMIT 1",
                {"service": service, "exclude_id": exclude_id, "version": evidence["version"], "mode": evidence["measurement_mode"]}).result_set
            return {"handoff_id": rows[0][0], "action": rows[0][1]} if rows else None
        for item in reversed(list(self.records.values())):
            if (item.get("plan") and item["handoff"]["id"] != exclude_id and
                    item["handoff"]["evidence"]["service"] == service and
                    item["handoff"]["evidence"]["version"] == evidence["version"] and
                    item["handoff"]["evidence"]["measurement_mode"] == evidence["measurement_mode"]):
                return {"handoff_id": item["handoff"]["id"], "action": item["plan"]["action"]}
        return None

    def _completed(self, handoff_id):
        if self.graph is not None:
            rows = self.graph.query(
                "MATCH (h:AgentHandoff {id:$id})-[:HANDED_OFF_TO]->(d:CoordinationDecision {execution_status:'verified'}) "
                "RETURN h.payload,d.payload LIMIT 1", {"id": handoff_id}).result_set
            if rows:
                h, p = json.loads(rows[0][0]), json.loads(rows[0][1])
                return {"handoff": h, "plan": p, "execution": p["execution"], "recovery": p["execution"],
                        "retry_reused": True, "trace": [{"agent": "recovery_planner", "step": "reuse_verified_recovery"}],
                        "provenance": {"source_incident_id": h["evidence"]["incident_id"],
                                       "source": "persisted_graph_handoff", "measurement_mode": h["evidence"]["measurement_mode"]}}
        else:
            item = self.records.get(handoff_id)
            if item and (item.get("plan") or {}).get("execution_status") == "verified":
                return {**item, "retry_reused": True}
        return None

    def history(self, limit=20):
        if self.graph is not None:
            rows = self.graph.query(
                "MATCH (:ReleaseIncident)-[:HANDED_OFF]->(h:AgentHandoff) OPTIONAL MATCH (h)-[:HANDED_OFF_TO]->(d:CoordinationDecision) "
                "RETURN h.payload,d.payload ORDER BY h.created_at DESC LIMIT $limit", {"limit": limit}).result_set
            return [{"handoff": json.loads(row[0]), "plan": json.loads(row[1]) if row[1] else None} for row in rows]
        return list(reversed(list(self.records.values())))[:limit]

    def graph_view(self, service: str, limit: int = 20) -> dict:
        """Coordination subgraph for the newest incidents: incident -> handoff -> review -> decision.

        A variable-length traversal (up to 4 hops) collects every provenance edge,
        including retry BASED_ON links back to earlier failed incidents.
        """
        if self.graph is None:
            nodes, edges = {}, []
            items = [item for item in self.records.values() if item["handoff"]["evidence"]["service"] == service]
            for item in items[-limit:]:
                handoff, plan = item["handoff"], item.get("plan")
                incident_id = handoff["evidence"]["incident_id"]
                nodes[incident_id] = {"id": incident_id, "type": "ReleaseIncident",
                                      "version": handoff["evidence"]["version"],
                                      "status": handoff["evidence"]["decision"]}
                nodes[handoff["id"]] = {"id": handoff["id"], "type": "AgentHandoff", "agent": "release_analyst"}
                edges.append({"source": incident_id, "target": handoff["id"], "relation": "HANDED_OFF"})
                if plan and plan.get("id"):
                    nodes[plan["id"]] = {"id": plan["id"], "type": "CoordinationDecision",
                                         "agent": "recovery_planner", "status": plan.get("action")}
                    edges.append({"source": handoff["id"], "target": plan["id"], "relation": "HANDED_OFF_TO"})
            return {"nodes": list(nodes.values()), "edges": edges}
        rels = "HANDED_OFF|REVIEWED_BY|FORWARDED_TO|HANDED_OFF_TO|BASED_ON|EXECUTED|RETRIEVED"
        # Collect distinct reachable nodes (no path materialisation), then the edges among them.
        # Incidents with no edges yet still appear because they are collected on their own.
        rows = self.graph.query(
            "MATCH (i:ReleaseIncident {service:$service}) WITH i ORDER BY i.timestamp DESC LIMIT $limit "
            f"OPTIONAL MATCH (i)-[:{rels}*1..4]-(n) "
            "WITH collect(DISTINCT i) AS seeds, collect(DISTINCT n) AS reached "
            "WITH seeds + [n IN reached WHERE NOT n IN seeds] AS ns "
            f"UNWIND ns AS a OPTIONAL MATCH (a)-[r:{rels}]->(b) WHERE b IN ns "
            "WITH ns, collect(DISTINCT r) AS rs "
            "RETURN [n IN ns | [n.id, labels(n)[0], n.version, coalesce(n.decision, n.status, n.action), "
            "n.agent, coalesce(n.timestamp, n.created_at), n.error_rate, n.p95_latency_ms, n.sample_count]], "
            "[r IN rs | [startNode(r).id, type(r), endNode(r).id]]",
            {"service": service, "limit": limit}).result_set
        if not rows:
            return {"nodes": [], "edges": []}
        keys = ("id", "type", "version", "status", "agent", "timestamp", "error_rate", "p95_latency_ms", "sample_count")
        nodes = [dict(zip(keys, n)) for n in rows[0][0] or []]
        edges = [{"source": a, "relation": rel, "target": b} for a, rel, b in rows[0][1] or []]
        return {"nodes": nodes, "edges": edges}
