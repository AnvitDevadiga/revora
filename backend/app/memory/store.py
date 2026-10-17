from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from threading import RLock
from typing import Any
from redis.exceptions import ResponseError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    """Small graph repository with an in-memory fallback for local development."""

    def __init__(self, graph: Any = None):
        self.graph = graph
        self.lock = RLock()
        self._nodes: dict[str, dict] = {}
        self._edges: dict[tuple[str, str, str], dict] = {}
        if graph is not None:
            self.ensure_schema()

    def ensure_schema(self) -> None:
        """Create the small set of exact-match indexes used by graph retrieval."""
        if self.graph is None:
            return
        statements = (
            "CREATE INDEX FOR (b:Belief) ON (b.scope)",
            "CREATE INDEX FOR (b:Belief) ON (b.service)",
            "CREATE INDEX FOR (b:Belief) ON (b.scenario)",
            "CREATE INDEX FOR (x:Experience) ON (x.service)",
            "CREATE INDEX FOR (x:Experience) ON (x.scenario)",
            "CREATE INDEX FOR (r:AgentRun) ON (r.id)",
            "CREATE INDEX FOR (r:AgentRun) ON (r.context)",
            "CREATE INDEX FOR (r:AgentRun) ON (r.created_at)",
            "CREATE INDEX FOR (i:ReleaseIncident) ON (i.service)",
            "CREATE INDEX FOR (i:ReleaseIncident) ON (i.version)",
            "CREATE INDEX FOR (i:ReleaseIncident) ON (i.handoff_id)",
            "CREATE INDEX FOR (h:AgentHandoff) ON (h.incident_id)",
        )
        for statement in statements:
            try:
                self.graph.query(statement)
            except ResponseError as error:
                if "is already indexed" not in str(error).casefold():
                    raise

    def _query(self, cypher: str, params: dict | None = None) -> list[list[Any]]:
        if self.graph is None:
            return []
        result = self.graph.query(cypher, params or {})
        return list(getattr(result, "result_set", []) or [])

    def active_belief(self, service: str, scenario: str | None = None) -> dict | None:
        rows = self._query(
            "MATCH (b:Belief {service:$service,status:'active'}) "
            "WHERE $scenario='' OR b.scenario=$scenario "
            "RETURN b.id,b.statement,b.confidence_score,b.status,b.outcome,b.scope "
            "ORDER BY b.timestamp DESC LIMIT 1",
            {"service": service, "scenario": scenario or ""})
        if rows:
            return dict(zip(("id", "statement", "confidence_score", "status", "outcome", "scope"), rows[0]))
        return next((n.copy() for n in reversed(list(self._nodes.values()))
                     if n["type"] == "Belief" and n.get("service") == service
                     and (scenario is None or n.get("scenario") == scenario)
                     and n.get("status") == "active"), None)

    def search(self, query: str, service: str, scenario: str, limit: int = 8) -> list[dict]:
        rows = self._query(
            "MATCH (x:Experience {service:$service, scenario:$scenario})-[:LED_TO]->(e:Evidence) "
            "OPTIONAL MATCH (e)-[r:SUPPORTS|CONTRADICTS]->(b:Belief) "
            "RETURN x.id,x.action_taken,x.context,x.outcome,x.timestamp,e.id,e.description,e.metrics, "
            "type(r),b.id,b.statement,b.confidence_score,b.status "
            "ORDER BY x.timestamp DESC LIMIT $limit",
            {"service": service, "scenario": scenario, "limit": limit},
        )
        if rows:
            return [dict(zip(("experience_id", "action_taken", "context", "outcome", "timestamp",
                              "evidence_id", "description", "metrics", "relation", "belief_id",
                              "belief", "confidence_score", "belief_status"), row)) for row in rows]
        items = []
        for node in reversed(list(self._nodes.values())):
            if node.get("type") != "Experience" or node.get("service") != service or node.get("scenario") != scenario:
                continue
            evidence = next((n for n in self._nodes.values() if n.get("type") == "Evidence" and
                             (node["id"], "LED_TO", n["id"]) in self._edges), None)
            if evidence is None:
                continue
            linked = next(((relation, self._nodes.get(target)) for (source, relation, target) in self._edges
                           if source == evidence["id"] and relation == "SUPPORTS"), (None, None))
            relation, belief = linked
            items.append({"experience_id": node["id"], "action_taken": node["action_taken"],
                          "context": node["context"], "outcome": node["outcome"],
                          "timestamp": node["timestamp"], "evidence_id": evidence["id"],
                          "description": evidence["description"], "metrics": evidence["metrics"],
                          "relation": relation, "belief_id": belief.get("id") if belief else None,
                          "belief": belief.get("statement") if belief else None,
                          "confidence_score": belief.get("confidence_score") if belief else None,
                          "belief_status": belief.get("status") if belief else None})
            if len(items) >= limit:
                break
        return items

    def record_deployment(self, service: str, scenario: str, decision: str, success: bool,
                          reason: str, canary: dict, reflection: dict | None = None) -> dict:
        """Update a scoped gate-outcome expectation in one graph mutation.

        An expectation about the same service/scenario can be superseded by an
        opposite observation. Outcomes in different scenarios do not contradict
        one another. Neither expectation can override current safety gates.
        """
        if (decision not in {"promote", "rollback"} or success != (decision == "promote")
                or canary.get("decision") != decision or canary.get("success") is not success
                or canary.get("scenario", scenario) != scenario):
            raise ValueError("The recorded outcome must match the observed canary decision")
        with self.lock:
            timestamp = _now()
            experience_id, evidence_id = str(uuid.uuid4()), str(uuid.uuid4())
            scope = f"deployment:{service}:{scenario}"
            outcome = "success" if success else "failure"
            current = self.active_belief(service, scenario)
            contradicted = bool(current and current.get("scope") == scope and current.get("outcome") != outcome)
            belief_id = current["id"] if current and not contradicted else str(uuid.uuid4())
            statement = (f"Gate-outcome expectation for {service} / {scenario}: "
                         f"{'pass' if success else 'fail'}, based on the latest observation. "
                         "Always remeasure current gates; this expectation never authorizes promotion.")
            confidence = min(0.95, round(float(current.get("confidence_score") or 0.6) + 0.04, 2)) if current and not contradicted else 0.6
            reflection_reason = ("Opposite measured gate outcome supersedes the expectation in the same scope."
                                 if contradicted else None)
            mode = canary.get("execution_mode", "fixture_with_synthetic_metrics" if canary.get("synthetic_metrics") else "unspecified")
            metrics = {"http_status": canary.get("http_status"), "probe_ms": canary.get("probe_ms"),
                       "gates": canary.get("gates", {}), "synthetic_metrics": canary.get("synthetic_metrics", {}),
                       "measurement_mode": mode}
            belief = {"id": belief_id, "type": "Belief", "scope": scope, "service": service,
                      "scenario": scenario, "proposition_type": "gate_outcome_expectation",
                      "statement": statement, "confidence_score": confidence, "status": "active",
                      "outcome": outcome, "expected_gate_pass": success, "timestamp": timestamp,
                      "reason": reflection_reason}
            context = {"service": service, "scenario": scenario, "gates": canary.get("gates", {})}
            experience = {"id": experience_id, "type": "Experience", "action_taken": f"Evaluate {scenario} release for {service}: {decision}",
                          "context": context, "outcome": outcome, "timestamp": timestamp,
                          "service": service, "scenario": scenario}
            evidence = {"id": evidence_id, "type": "Evidence", "description": reason,
                        "source_tool": "run_canary", "metrics": metrics, "timestamp": timestamp,
                        "measurement_mode": mode}
            if self.graph is not None:
                self._query(
                    "OPTIONAL MATCH (old:Belief {id:$old_id}) "
                    "CREATE (x:Experience {id:$xid,action_taken:$action,context:$context,outcome:$outcome,timestamp:$timestamp,service:$service,scenario:$scenario}) "
                    "CREATE (e:Evidence {id:$eid,description:$description,source_tool:'run_canary',metrics:$metrics,timestamp:$timestamp,measurement_mode:$mode}) "
                    "MERGE (b:Belief {id:$bid}) "
                    "SET b.scope=$scope,b.service=$service,b.scenario=$scenario,b.statement=$statement,b.proposition_type='gate_outcome_expectation',"
                    "b.confidence_score=$confidence,b.status='active',b.outcome=$outcome,b.expected_gate_pass=$success,b.timestamp=$timestamp "
                    "CREATE (x)-[:LED_TO]->(e) CREATE (e)-[:SUPPORTS]->(b) "
                    "FOREACH (_ IN CASE WHEN $contradicted AND old IS NOT NULL THEN [1] ELSE [] END | "
                    "SET old.status='revised' CREATE (e)-[:CONTRADICTS]->(old) CREATE (old)-[:UPDATES {reason:$revision_reason}]->(b))",
                    {"old_id": current["id"] if contradicted else "", "xid": experience_id,
                     "action": experience["action_taken"], "context": json.dumps(context), "outcome": outcome,
                     "timestamp": timestamp, "service": service, "scenario": scenario, "eid": evidence_id,
                     "description": reason, "metrics": json.dumps(metrics), "mode": mode, "bid": belief_id,
                     "scope": scope, "statement": statement, "confidence": confidence, "success": success,
                     "contradicted": contradicted, "revision_reason": reflection_reason or ""})
            else:
                if contradicted:
                    self._nodes[current["id"]]["status"] = "revised"
                    self._edges[(evidence_id, "CONTRADICTS", current["id"])] = {}
                    self._edges[(current["id"], "UPDATES", belief_id)] = {"reason": reflection_reason}
                self._nodes.update({experience_id: experience, evidence_id: evidence, belief_id: belief})
                self._edges[(experience_id, "LED_TO", evidence_id)] = {}
                self._edges[(evidence_id, "SUPPORTS", belief_id)] = {}
            return {"experience": experience, "evidence": evidence, "belief": belief,
                    "contradiction_detected": contradicted,
                    "contradicted_belief_id": current["id"] if contradicted else None,
                    "reflection": reflection_reason,
                    "model_proposal": reflection, "model_proposal_authoritative": False}

    def graph_state(self) -> dict:
        if self.graph is not None:
            rows = self._query(
                "MATCH (a)-[r]->(b) WHERE a:Experience OR a:Evidence OR a:Belief "
                "RETURN a.id,labels(a)[0],a.statement,a.action_taken,a.outcome,a.status,a.confidence_score, "
                "b.id,labels(b)[0],b.statement,b.action_taken,b.outcome,b.status,b.confidence_score,type(r),r.reason "
                "ORDER BY a.timestamp DESC LIMIT $limit", {"limit": 5000}
            )
            nodes: dict[str, dict] = {}
            edges = []
            for row in rows:
                aid, atype, astatement, aaction, aoutcome, astatus, aconfidence, bid, btype, bstatement, baction, boutcome, bstatus, bconfidence, relation, reason = row
                nodes[aid] = self._node_view(aid, atype, astatement, aaction, aoutcome, astatus, aconfidence)
                nodes[bid] = self._node_view(bid, btype, bstatement, baction, boutcome, bstatus, bconfidence)
                edges.append({"source": aid, "target": bid, "relation": relation, "reason": reason})
            return {"nodes": list(nodes.values()), "edges": edges}
        return {"nodes": list(self._nodes.values()), "edges": [
            {"source": source, "target": target, "relation": relation, **props}
            for (source, relation, target), props in self._edges.items()]}

    @staticmethod
    def _node_view(node_id, node_type, statement, action, outcome, status, confidence):
        return {"id": node_id, "type": node_type,
                "label": statement or action or node_type,
                "outcome": outcome, "status": status,
                "confidence_score": confidence}

    def neighborhood(self, query: str, limit: int = 24) -> dict:
        query_lower = query.casefold()
        state = self.graph_state()
        matched = {n["id"] for n in state["nodes"] if query_lower in n.get("label", "").casefold()}
        selected = set(matched)
        for edge in state["edges"]:
            if edge["source"] in matched or edge["target"] in matched:
                selected.update((edge["source"], edge["target"]))
        nodes = [n for n in state["nodes"] if n["id"] in selected][:limit]
        ids = {n["id"] for n in nodes}
        edges = [e for e in state["edges"] if e["source"] in ids and e["target"] in ids]
        return {"query": query, "nodes": nodes, "edges": edges}

    def history(self, limit: int = 100) -> list[dict]:
        if self.graph is not None:
            rows = self._query(
                "MATCH (x:Experience)-[:LED_TO]->(e:Evidence)-[:SUPPORTS]->(b:Belief) "
                "OPTIONAL MATCH (e)-[:CONTRADICTS]->(old:Belief) "
                "RETURN x.id,x.action_taken,x.context,x.outcome,x.timestamp,e.id,e.description,e.source_tool,e.metrics, "
                "'SUPPORTS',b.id,b.statement,b.confidence_score,b.status,old.id,old.statement "
                "ORDER BY x.timestamp DESC LIMIT $limit", {"limit": limit})
            return [dict(zip(("experience_id", "action_taken", "context", "outcome", "timestamp",
                              "evidence_id", "description", "source_tool", "metrics", "relation",
                              "belief_id", "belief", "confidence_score", "belief_status",
                              "contradicted_belief_id", "contradicted_belief"), row)) for row in rows]
        items = []
        for experience in reversed(list(self._nodes.values())):
            if experience["type"] != "Experience":
                continue
            evidence = next((node for node in self._nodes.values() if node["type"] == "Evidence" and
                             (experience["id"], "LED_TO", node["id"]) in self._edges), None)
            if evidence is None:
                continue
            link = next(((relation, self._nodes.get(target)) for (source, relation, target) in self._edges
                         if source == evidence["id"] and relation == "SUPPORTS"), (None, None))
            relation, belief = link
            contradicted = next((self._nodes.get(target) for (source, edge_type, target) in self._edges
                                 if source == evidence["id"] and edge_type == "CONTRADICTS"), None)
            items.append({"experience_id": experience["id"], "action_taken": experience["action_taken"],
                          "context": experience["context"], "outcome": experience["outcome"],
                          "timestamp": experience["timestamp"], "evidence_id": evidence["id"],
                          "description": evidence["description"], "source_tool": evidence["source_tool"],
                          "metrics": evidence["metrics"], "relation": relation,
                          "belief_id": belief.get("id") if belief else None,
                          "belief": belief.get("statement") if belief else None,
                          "confidence_score": belief.get("confidence_score") if belief else None,
                          "belief_status": belief.get("status") if belief else None,
                          "contradicted_belief_id": contradicted.get("id") if contradicted else None,
                          "contradicted_belief": contradicted.get("statement") if contradicted else None})
            if len(items) >= limit:
                break
        return items
