from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class MemoryEngine:
    """Evidence-backed memory loop; graph-backed state is reloaded from FalkorDB."""
    client: Any = None
    graph: Any = None
    graph_name: str = "engram"
    episodes: list[dict] = field(default_factory=list)
    beliefs: dict[str, dict] = field(default_factory=dict)
    procedures: list[dict] = field(default_factory=list)
    lock: Any = field(default_factory=RLock, repr=False)

    def _run(self, query: str, params: dict | None = None) -> Any:
        if self.graph is not None:
            return self.graph.query(query, params or {})
        return None

    @staticmethod
    def _result_rows(result: Any) -> list[list[Any]]:
        return list(getattr(result, "result_set", []) or [])

    def _reload(self) -> None:
        if self.graph is None:
            return
        episode_rows = self._result_rows(self._run(
            "MATCH (e:Episode) RETURN e.id, e.text, e.outcome, e.success, e.agent, e.created_at, e.source, e.impact, e.tenant_id ORDER BY e.created_at"
        ))
        episodes = [dict(zip(("id", "text", "outcome", "success", "agent", "created_at", "source", "impact", "tenant_id"), row)) for row in episode_rows]
        belief_rows = self._result_rows(self._run(
            "MATCH (b:Belief) RETURN b.id, b.claim, b.confidence, b.status"
        ))
        beliefs: dict[str, dict] = {}
        by_id: dict[str, dict] = {}
        for row in belief_rows:
            belief = dict(zip(("id", "claim", "confidence", "status"), row))
            belief["confidence"] = float(belief["confidence"])
            belief["evidence"] = []
            beliefs[self._key(f"demo:{belief['claim']}")] = belief
            by_id[belief["id"]] = belief
        evidence_rows = self._result_rows(self._run(
            "MATCH (e:Episode)-[r]->(b:Belief) RETURN b.id, e.id, r.kind, type(r), e.outcome, r.weight ORDER BY e.created_at"
        ))
        for belief_id, episode_id, legacy_relation, edge_type, outcome, weight in evidence_rows:
            belief = by_id.get(belief_id)
            if belief is not None:
                belief["evidence"].append({"episode_id": episode_id, "relation": legacy_relation or edge_type, "outcome": outcome, "weight": float(weight or 0.22)})
        self.episodes = episodes
        self.beliefs = beliefs
        rows = self._result_rows(self._run("MATCH (p:Procedure) RETURN p.id, p.goal, p.action, p.steps, p.created_at ORDER BY p.created_at"))
        self.procedures = [dict(zip(("id", "goal", "action", "steps", "created_at"), row)) for row in rows]
        for procedure in self.procedures:
            procedure["steps"] = (procedure["steps"] or "").split("|")

    def _persist_episode(self, episode: dict) -> None:
        self._run("""CREATE (e:Episode {id:$id, text:$text, outcome:$outcome, success:$success, agent:$agent, source:$source, impact:$impact, tenant_id:$tenant_id, created_at:$created_at})""", episode)

    def ingest(self, text: str, outcome: str, success: bool, agent: str = "demo-agent", source: str = "operator", impact: int = 3, tenant_id: str = "demo") -> dict:
        if tenant_id != "demo":
            raise ValueError("This deployment supports one workspace; use a separate graph and API for another workspace.")
        if not text.strip() or not outcome.strip():
            raise ValueError("Experience and outcome must contain text")
        with self.lock:
            self._reload()
            impact = max(1, min(5, int(impact)))
            episode = {"id": str(uuid.uuid4()), "text": text, "outcome": outcome, "success": success, "agent": agent, "source": source, "impact": impact, "tenant_id": tenant_id, "created_at": now()}
            self._persist_episode(episode)
            self.episodes.append(episode)
            belief_text = self._extract_belief(text)
            belief = None
            if belief_text:
                key = self._key(f"{tenant_id}:{belief_text}")
                belief = self.beliefs.setdefault(key, {"id": str(uuid.uuid4()), "claim": belief_text, "tenant_id": tenant_id, "confidence": 0.5, "status": "active", "evidence": []})
                relation = "SUPPORTS" if success else "CONTRADICTS"
                weight = round((0.12 + (impact * 0.04)) * (1.15 if source == "production" else 1.0), 3)
                belief["evidence"].append({"episode_id": episode["id"], "relation": relation, "outcome": outcome, "weight": weight})
                score = sum(e["weight"] if e["relation"] == "SUPPORTS" else -e["weight"] for e in belief["evidence"])
                belief["confidence"] = round(max(0.05, min(0.95, 0.5 + score)), 2)
                supports = sum(e["weight"] for e in belief["evidence"] if e["relation"] == "SUPPORTS")
                contradicts = sum(e["weight"] for e in belief["evidence"] if e["relation"] == "CONTRADICTS")
                belief["status"] = "revised" if contradicts else "active"
                belief["support"] = round(supports, 3)
                belief["contradiction"] = round(contradicts, 3)
                self._run("""MERGE (b:Belief {id:$id}) SET b.claim=$claim, b.confidence=$confidence, b.status=$status""", belief)
                self._run(f"MATCH (e:Episode {{id:$episode_id}}), (b:Belief {{id:$belief_id}}) CREATE (e)-[:{relation} {{weight:$weight}}]->(b)", {"episode_id": episode["id"], "belief_id": belief["id"], "weight": weight})
            return {"episode": episode, "belief": belief, "decision": self.decide()}

    def decide(self) -> dict:
        if not self.beliefs:
            return {"action": "explore", "reason": "No experience exists yet.", "evidence": []}
        belief = max(self.beliefs.values(), key=lambda b: len(b["evidence"]))
        avoid = belief["confidence"] < 0.5
        day = re.search(r"Deploy on (\w+) is reliable", belief["claim"])
        if day:
            action = (f"avoid unrestricted {day[1]} deployment; require a canary and rollback readiness" if avoid
                      else f"proceed with a guarded {day[1]} deployment; retain canary and rollback safeguards")
        else:
            action = "review evidence with an operator before acting"
        return {"action": action, "reason": f"{belief['claim']} (confidence {belief['confidence']:.2f}; {belief['status']})", "evidence": belief["evidence"][-5:], "belief": belief, "explanation": "The recommendation is computed from weighted SUPPORTS and CONTRADICTS edges, not from the latest message alone."}

    def run_agent(self, goal: str) -> dict:
        """A deterministic agent loop for demos; an LLM can replace only the planner step."""
        with self.lock:
            self._reload()
            decision = self.decide()
            action = decision["action"]
            if action == "explore":
                steps = ["retrieve relevant experience", "ask for a controlled canary", "record the outcome"]
            elif action.startswith("avoid"):
                steps = ["review contradictory deployment evidence", "require a controlled canary", "require rollback readiness", "record the observed outcome separately"]
            elif action.startswith("review"):
                steps = ["review linked evidence", "request operator assessment", "record the observed outcome separately"]
            else:
                steps = ["retrieve successful deployment evidence", "run the standard checklist", "deploy with a canary", "record the outcome"]
            procedure = {"id": str(uuid.uuid4()), "goal": goal, "action": action, "steps": steps, "created_at": now()}
            self.procedures.append(procedure)
            if self.graph is not None:
                self._run("""CREATE (p:Procedure {id:$id, goal:$goal, action:$action, steps:$steps, created_at:$created_at})""", {**procedure, "steps": "|".join(steps)})
                if decision.get("belief"):
                    self._run("""MATCH (p:Procedure {id:$procedure_id}), (b:Belief {id:$belief_id}) CREATE (p)-[:BASED_ON]->(b)""", {"procedure_id": procedure["id"], "belief_id": decision["belief"]["id"]})
            return {"goal": goal, "decision": decision, "procedure": procedure}

    def snapshot(self) -> dict:
        with self.lock:
            self._reload()
            return {"episodes": list(reversed(self.episodes)), "beliefs": list(self.beliefs.values()), "procedures": list(reversed(self.procedures)), "decision": self.decide()}

    @staticmethod
    def _key(claim: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", claim.lower()).strip("-")

    @staticmethod
    def _extract_belief(text: str) -> str:
        day = re.search(r"(friday|monday|tuesday|wednesday|thursday|saturday|sunday)", text, re.I)
        if day and re.search(r"deploy", text, re.I):
            return f"Deploy on {day.group(1).capitalize()} is reliable"
        return text.strip().split(".")[0][:140]


def build_engine() -> MemoryEngine:
    url = os.getenv("FALKORDB_URL")
    if not url:
        return MemoryEngine()
    from falkordb import FalkorDB
    client = FalkorDB.from_url(url)
    graph_name = os.getenv("FALKORDB_GRAPH", "engram")
    graph = client.select_graph(graph_name)
    graph.query("RETURN 1")
    return MemoryEngine(client=client, graph=graph, graph_name=graph_name)
