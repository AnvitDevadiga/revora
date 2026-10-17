"""Revora memory engine — belief/experience/evidence storage on FalkorDB.

Design
------
`MemoryEngine` exposes the high-level graph API used by the agent tools, the
belief engine and the HTTP layer. It never talks to a database directly;
it delegates *storage primitives* to a backend that implements the tiny
`GraphBackend` protocol:

* `FalkorBackend`  — real FalkorDB over Cypher (the shipping path).
* `MemoryBackend`  — an in-process mirror of the same primitives, so the API,
  the agent and the test suite run with zero infrastructure. The UI labels
  this mode clearly ("memory mode") — it is a development convenience, not a
  second database product.

All high-level semantics (contradiction detection hooks, revision chains,
search ranking, neighborhood traversal) live once in `MemoryEngine`, so both
backends behave identically and are covered by the same tests.

Cypher notes: every query below sticks to the conservative FalkorDB subset
(MATCH / MERGE / CREATE / SET map merge / properties() / type() / labels()).
Property maps are built from whitelisted keys only; values are always bound
as parameters. Complex values (`metrics`) are JSON-encoded into string
properties because graph engines store primitives, not nested maps.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from . import schema

logger = logging.getLogger("revora.memory")

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
_SEARCH_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "have", "has", "was",
    "were", "into", "over", "after", "before", "does", "should", "would", "could",
    "will", "canary", "deploy", "deployment", "release", "target", "service",
}


def utc_now() -> str:
    """ISO-8601 UTC timestamps sort correctly as plain strings."""
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Storage backends
# ---------------------------------------------------------------------------
class GraphBackend(Protocol):
    """The minimal storage contract both FalkorDB and the in-memory mirror obey."""

    mode: str

    def ping(self) -> bool: ...
    def upsert_node(self, label: str, props: dict) -> dict: ...
    def update_node(self, label: str, node_id: str, props: dict) -> dict | None: ...
    def get_node(self, label: str, node_id: str) -> dict | None: ...
    def nodes(self, label: str) -> list[dict]: ...
    def add_edge(self, rel: str, source: tuple[str, str], target: tuple[str, str], props: dict) -> dict: ...
    def edges(self) -> list[dict]: ...
    def incident_edges(self, node_id: str) -> list[dict]: ...
    def clear(self) -> None: ...


def _encode_props(props: dict) -> dict:
    """JSON-encode nested values so any backend can store them."""
    encoded: dict[str, Any] = {}
    for key, value in props.items():
        if not _IDENTIFIER.match(key):
            raise ValueError(f"Invalid property name: {key!r}")
        encoded[key] = json.dumps(value) if isinstance(value, (dict, list)) else value
    return encoded


def _decode_props(props: dict | None) -> dict:
    """Best-effort JSON decoding of stored string properties."""
    decoded: dict[str, Any] = {}
    for key, value in (props or {}).items():
        if isinstance(value, str) and value[:1] in "{[":
            try:
                decoded[key] = json.loads(value)
                continue
            except json.JSONDecodeError:
                pass
        decoded[key] = value
    return decoded


class FalkorBackend:
    """Cypher-backed storage for a real FalkorDB instance."""

    mode = "falkordb"

    def __init__(self, graph, graph_name: str):
        self.graph = graph
        self.graph_name = graph_name

    # -- helpers ------------------------------------------------------------
    def _query(self, cypher: str, params: dict | None = None) -> list[list]:
        return list(self.graph.query(cypher, params or {}).result_set)

    @staticmethod
    def _edge_rows(rows: list[list]) -> list[dict]:
        return [
            {
                "source_label": (row[0] or [None])[0],
                "source": row[1],
                "target_label": (row[3] or [None])[0],
                "target": row[2],
                "relation": row[4],
                "properties": _decode_props(row[5]),
            }
            for row in rows
        ]

    # -- primitives ----------------------------------------------------------
    def ping(self) -> bool:
        self._query("RETURN 1")
        return True

    def upsert_node(self, label: str, props: dict) -> dict:
        payload = _encode_props(props)
        self._query(
            f"MERGE (n:{label} {{id:$id}}) SET n += $props",
            {"id": payload["id"], "props": payload},
        )
        return props

    def update_node(self, label: str, node_id: str, props: dict) -> dict | None:
        payload = _encode_props(props)
        rows = self._query(
            f"MATCH (n:{label} {{id:$id}}) SET n += $props RETURN properties(n)",
            {"id": node_id, "props": payload},
        )
        return _decode_props(rows[0][0]) if rows else None

    def get_node(self, label: str, node_id: str) -> dict | None:
        rows = self._query(
            f"MATCH (n:{label} {{id:$id}}) RETURN properties(n)", {"id": node_id}
        )
        return _decode_props(rows[0][0]) if rows else None

    def nodes(self, label: str) -> list[dict]:
        rows = self._query(f"MATCH (n:{label}) RETURN properties(n)")
        return [_decode_props(row[0]) for row in rows]

    def add_edge(
        self,
        rel: str,
        source: tuple[str, str],
        target: tuple[str, str],
        props: dict,
    ) -> dict:
        payload = _encode_props(props)
        pairs = ", ".join(f"{key}: ${key}" for key in payload)
        props_clause = f" {{{pairs}}}" if payload else ""
        self._query(
            f"MATCH (a:{source[0]} {{id:$source_id}}), (b:{target[0]} {{id:$target_id}}) "
            f"CREATE (a)-[r:{rel}]{props_clause}]->(b) "
            f"RETURN properties(r)",
            {**payload, "source_id": source[1], "target_id": target[1]},
        )
        return props

    def edges(self) -> list[dict]:
        rows = self._query(
            "MATCH (a)-[r]->(b) "
            "RETURN labels(a), a.id, b.id, labels(b), type(r), properties(r)"
        )
        return [row for row in self._edge_rows(rows) if row["relation"] in schema.EDGE_TYPES]

    def incident_edges(self, node_id: str) -> list[dict]:
        # Direction-agnostic pattern: (a) is the known node, (b) the neighbour.
        rows = self._query(
            "MATCH (a)-[r]-(b) WHERE a.id = $id "
            "RETURN labels(a), a.id, b.id, labels(b), type(r), properties(r)",
            {"id": node_id},
        )
        return self._edge_rows(rows)

    def clear(self) -> None:
        self._query(
            "MATCH (n) WHERE n:Belief OR n:Experience OR n:Evidence DETACH DELETE n"
        )


class MemoryBackend:
    """In-process mirror of the storage contract, used when FalkorDB is absent."""

    mode = "memory"

    def __init__(self):
        self._nodes: dict[tuple[str, str], dict] = {}
        self._edges: list[dict] = []
        self._lock = threading.RLock()

    def ping(self) -> bool:
        return True

    def upsert_node(self, label: str, props: dict) -> dict:
        with self._lock:
            self._nodes[(label, props["id"])] = dict(props)
            return dict(props)

    def update_node(self, label: str, node_id: str, props: dict) -> dict | None:
        with self._lock:
            node = self._nodes.get((label, node_id))
            if node is None:
                return None
            node.update(props)
            return dict(node)

    def get_node(self, label: str, node_id: str) -> dict | None:
        with self._lock:
            node = self._nodes.get((label, node_id))
            return dict(node) if node else None

    def nodes(self, label: str) -> list[dict]:
        with self._lock:
            return [dict(node) for (node_label, _), node in self._nodes.items() if node_label == label]

    def add_edge(
        self,
        rel: str,
        source: tuple[str, str],
        target: tuple[str, str],
        props: dict,
    ) -> dict:
        with self._lock:
            self._edges.append(
                {
                    "source_label": source[0],
                    "source": source[1],
                    "target_label": target[0],
                    "target": target[1],
                    "relation": rel,
                    "properties": dict(props),
                }
            )
            return dict(props)

    def edges(self) -> list[dict]:
        with self._lock:
            return [dict(edge, properties=dict(edge["properties"])) for edge in self._edges]

    def incident_edges(self, node_id: str) -> list[dict]:
        with self._lock:
            return [
                dict(edge, properties=dict(edge["properties"]))
                for edge in self._edges
                if edge["source"] == node_id or edge["target"] == node_id
            ]

    def clear(self) -> None:
        with self._lock:
            self._nodes.clear()
            self._edges.clear()


# ---------------------------------------------------------------------------
# High-level engine
# ---------------------------------------------------------------------------
class MemoryEngine:
    """The graph's public API: writes, search, neighborhood traversal, history."""

    def __init__(self, backend: GraphBackend):
        self.backend = backend
        self.mode = backend.mode
        # Compound operations (write + link + evaluate) run under one lock so
        # concurrent API calls cannot interleave a half-written memory.
        self.lock = threading.RLock()

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------
    def add_experience(
        self,
        *,
        action_taken: str,
        context: str,
        outcome: str,
        target: str,
        detail: str = "",
        timestamp: str | None = None,
        extra: dict | None = None,
    ) -> dict:
        if outcome not in schema.OUTCOMES:
            raise ValueError(f"Outcome must be one of {schema.OUTCOMES}, got {outcome!r}")
        node = {
            "id": new_id(),
            "action_taken": action_taken,
            "context": context,
            "outcome": outcome,
            "target": target,
            "detail": detail,
            "timestamp": timestamp or utc_now(),
            **(extra or {}),
        }
        self.backend.upsert_node(schema.LABEL_EXPERIENCE, node)
        logger.info("experience %s recorded (%s/%s)", node["id"], target, outcome)
        return node

    def add_evidence(
        self,
        *,
        description: str,
        source_tool: str,
        metrics: dict | None,
        target: str,
        timestamp: str | None = None,
        extra: dict | None = None,
    ) -> dict:
        node = {
            "id": new_id(),
            "description": description,
            "source_tool": source_tool,
            "metrics": metrics or {},
            "target": target,
            "timestamp": timestamp or utc_now(),
            **(extra or {}),
        }
        self.backend.upsert_node(schema.LABEL_EVIDENCE, node)
        return node

    def create_belief(
        self,
        *,
        statement: str,
        confidence_score: float,
        target: str,
        risk_tolerance: float,
        action_scope: str,
        status: str = schema.STATUS_ACTIVE,
        version: int = 1,
        timestamp: str | None = None,
    ) -> dict:
        if status not in schema.BELIEF_STATUSES:
            raise ValueError(f"Unknown belief status {status!r}")
        node = {
            "id": new_id(),
            "statement": statement,
            "confidence_score": round(float(confidence_score), 3),
            "status": status,
            "target": target,
            "risk_tolerance": round(float(risk_tolerance), 3),
            "action_scope": action_scope,
            "version": int(version),
            "timestamp": timestamp or utc_now(),
        }
        self.backend.upsert_node(schema.LABEL_BELIEF, node)
        logger.info("belief v%s created for %s: %s", node["version"], target, statement[:80])
        return node

    def update_belief(self, belief_id: str, props: dict) -> dict | None:
        if "confidence_score" in props:
            props["confidence_score"] = round(float(props["confidence_score"]), 3)
        if "risk_tolerance" in props:
            props["risk_tolerance"] = round(float(props["risk_tolerance"]), 3)
        return self.backend.update_node(schema.LABEL_BELIEF, belief_id, props)

    def get_belief(self, belief_id: str) -> dict | None:
        return self.backend.get_node(schema.LABEL_BELIEF, belief_id)

    def get_experience(self, experience_id: str) -> dict | None:
        return self.backend.get_node(schema.LABEL_EXPERIENCE, experience_id)

    def get_evidence(self, evidence_id: str) -> dict | None:
        return self.backend.get_node(schema.LABEL_EVIDENCE, evidence_id)

    def active_beliefs(self, target: str | None = None) -> list[dict]:
        beliefs = [
            belief
            for belief in self.backend.nodes(schema.LABEL_BELIEF)
            if belief.get("status") == schema.STATUS_ACTIVE
            and (target is None or belief.get("target") == target)
        ]
        return sorted(beliefs, key=lambda belief: belief.get("timestamp", ""))

    def all_beliefs(self, target: str | None = None) -> list[dict]:
        beliefs = [
            belief
            for belief in self.backend.nodes(schema.LABEL_BELIEF)
            if target is None or belief.get("target") == target
        ]
        return sorted(beliefs, key=lambda belief: belief.get("timestamp", ""))

    # ------------------------------------------------------------------
    # Edges
    # ------------------------------------------------------------------
    def led_to(self, experience: dict, evidence: dict) -> dict:
        return self.backend.add_edge(
            schema.REL_LED_TO,
            (schema.LABEL_EXPERIENCE, experience["id"]),
            (schema.LABEL_EVIDENCE, evidence["id"]),
            {"id": new_id(), "timestamp": utc_now()},
        )

    def link_evidence_to_belief(self, evidence: dict, belief: dict, relation: str, **props) -> dict:
        if relation not in schema.BELIEF_RELATIONS:
            raise ValueError(f"Relation must be SUPPORTS or CONTRADICTS, got {relation!r}")
        return self.backend.add_edge(
            relation,
            (schema.LABEL_EVIDENCE, evidence["id"]),
            (schema.LABEL_BELIEF, belief["id"]),
            {"id": new_id(), "timestamp": utc_now(), **props},
        )

    def supersede_belief(
        self,
        *,
        old_belief: dict,
        statement: str,
        confidence_score: float,
        risk_tolerance: float,
        reason: str,
        timestamp: str | None = None,
    ) -> dict:
        """Version a belief: insert the successor and link (new)-[:UPDATES {reason}]->(old).

        The predecessor is marked `revised`; its text stays readable so the
        timeline can prove how memory evolved instead of rewriting history.
        """
        with self.lock:
            stamp = timestamp or utc_now()
            successor = self.create_belief(
                statement=statement,
                confidence_score=max(schema.CONFIDENCE_FLOOR, min(schema.CONFIDENCE_CEILING, confidence_score)),
                target=old_belief["target"],
                risk_tolerance=max(schema.RISK_TOLERANCE_FLOOR, min(schema.RISK_TOLERANCE_CEILING, risk_tolerance)),
                action_scope=old_belief.get("action_scope", "release"),
                version=int(old_belief.get("version", 1)) + 1,
                timestamp=stamp,
            )
            self.backend.add_edge(
                schema.REL_UPDATES,
                (schema.LABEL_BELIEF, successor["id"]),
                (schema.LABEL_BELIEF, old_belief["id"]),
                {
                    "id": new_id(),
                    "reason": reason,
                    "timestamp": stamp,
                    "confidence_delta": round(successor["confidence_score"] - old_belief["confidence_score"], 3),
                },
            )
            self.update_belief(old_belief["id"], {"status": schema.STATUS_REVISED, "revised_at": stamp})
            logger.info("belief %s superseded by %s: %s", old_belief["id"][:8], successor["id"][:8], reason[:90])
            return successor

    # ------------------------------------------------------------------
    # Search & traversal
    # ------------------------------------------------------------------
    @staticmethod
    def extract_keywords(query: str, limit: int = 8) -> list[str]:
        tokens = re.findall(r"[a-z0-9]{3,}", (query or "").lower())
        keywords: list[str] = []
        for token in tokens:
            if token in _SEARCH_STOPWORDS or token in keywords:
                continue
            keywords.append(token)
            if len(keywords) >= limit:
                break
        return keywords

    @staticmethod
    def _searchable(node: dict) -> str:
        return " ".join(
            str(node.get(key, ""))
            for key in ("statement", "action_taken", "context", "description", "detail", "target")
        ).lower()

    def search_memory(self, query: str, target: str | None = None, limit: int = 12) -> dict:
        """Keyword search over the graph, enriched with graph-neighborhood paths.

        Returns matching beliefs/experiences/evidence plus the edges touching
        the matches, so the caller sees *how* the facts connect — not just a
        flat list. This is what the agent's `search_memory` tool exposes.
        """
        keywords = self.extract_keywords(query)
        with self.lock:
            beliefs = [b for b in self.backend.nodes(schema.LABEL_BELIEF) if target is None or b.get("target") == target]
            experiences = [e for e in self.backend.nodes(schema.LABEL_EXPERIENCE) if target is None or e.get("target") == target]
            evidence = [ev for ev in self.backend.nodes(schema.LABEL_EVIDENCE) if target is None or ev.get("target") == target]

        def matched(nodes: list[dict]) -> list[dict]:
            if not keywords:
                return nodes
            hits = [node for node in nodes if any(keyword in self._searchable(node) for keyword in keywords)]
            # No keyword hits: fall back to the full target memory rather than an
            # empty result — the agent should never plan against a blank slate
            # when memories for the target exist.
            return hits if hits else nodes

        belief_hits = matched(beliefs)
        experience_hits = matched(experiences)
        evidence_hits = matched(evidence)

        # Rank: active beliefs first, then confidence, then recency.
        belief_hits.sort(
            key=lambda b: (b.get("status") != schema.STATUS_ACTIVE, -float(b.get("confidence_score", 0)), b.get("timestamp", "")),
        )
        experience_hits.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
        evidence_hits.sort(key=lambda ev: ev.get("timestamp", ""), reverse=True)

        belief_hits, experience_hits, evidence_hits = belief_hits[:limit], experience_hits[:limit], evidence_hits[:limit]
        path_nodes = belief_hits + experience_hits + evidence_hits
        related_paths = self._paths_around(path_nodes, max_hops=2)

        return {
            "query": query,
            "target": target,
            "keywords": keywords,
            "beliefs": belief_hits,
            "experiences": experience_hits,
            "evidence": evidence_hits,
            "related_paths": related_paths,
            "counts": {
                "beliefs": len(belief_hits),
                "experiences": len(experience_hits),
                "evidence": len(evidence_hits),
            },
        }

    def _summarize(self, label: str | None, node_id: str, props: dict) -> dict:
        if label == schema.LABEL_BELIEF:
            summary = props.get("statement", "")
        elif label == schema.LABEL_EXPERIENCE:
            summary = f"{props.get('action_taken', '')} → {props.get('outcome', '')}"
        else:
            summary = props.get("description", "")
        return {"id": node_id, "label": label, "summary": str(summary)[:120]}

    def _paths_around(self, nodes: list[dict], max_hops: int = 2) -> list[dict]:
        """BFS the neighborhood of the given nodes and return traversable edges."""
        frontier = {node["id"]: node for node in nodes}
        if not frontier:
            return []
        paths: list[dict] = []
        seen_edges: set[tuple] = set()
        seen_nodes = set(frontier)
        for _ in range(max(1, min(3, max_hops))):
            next_frontier: dict[str, dict] = {}
            for node_id in list(frontier):
                for edge in self.backend.incident_edges(node_id):
                    key = (edge["source"], edge["relation"], edge["target"])
                    if key in seen_edges:
                        continue
                    seen_edges.add(key)
                    source_props = self._node_props(edge["source_label"], edge["source"]) or {}
                    target_props = self._node_props(edge["target_label"], edge["target"]) or {}
                    paths.append(
                        {
                            "source": self._summarize(edge["source_label"], edge["source"], source_props),
                            "relation": edge["relation"],
                            "target": self._summarize(edge["target_label"], edge["target"], target_props),
                            "reason": edge["properties"].get("reason"),
                        }
                    )
                    for label, nid, props in (
                        (edge["source_label"], edge["source"], source_props),
                        (edge["target_label"], edge["target"], target_props),
                    ):
                        if nid not in seen_nodes and label in schema.NODE_LABELS:
                            seen_nodes.add(nid)
                            next_frontier[nid] = props or {}
            frontier = next_frontier
            if not frontier:
                break
        return paths[:60]

    def _node_props(self, label: str | None, node_id: str) -> dict | None:
        if label not in schema.NODE_LABELS:
            return None
        return self.backend.get_node(label, node_id)

    def neighborhood(self, node_id: str, depth: int = 2) -> dict:
        """Collect the sub-graph around one node — powers the UI inspector."""
        depth = max(1, min(3, int(depth)))
        nodes: dict[str, dict] = {}
        links: list[dict] = []
        frontier = [node_id]
        seen_edges: set[tuple] = set()
        for label in schema.NODE_LABELS:
            node = self.backend.get_node(label, node_id)
            if node:
                nodes[node_id] = {"id": node_id, "label": label, **node}
                break
        if not nodes:
            raise KeyError(f"No graph node with id {node_id!r}")
        for _ in range(depth):
            next_frontier: list[str] = []
            for current in frontier:
                for edge in self.backend.incident_edges(current):
                    key = (edge["source"], edge["relation"], edge["target"])
                    if key in seen_edges:
                        continue
                    seen_edges.add(key)
                    links.append(self._link_payload(edge))
                    for label, nid in ((edge["source_label"], edge["source"]), (edge["target_label"], edge["target"])):
                        if nid not in nodes and label in schema.NODE_LABELS:
                            props = self.backend.get_node(label, nid) or {}
                            nodes[nid] = {"id": nid, "label": label, **props}
                            next_frontier.append(nid)
            frontier = next_frontier
        return {"nodes": list(nodes.values()), "links": links, "center": node_id}

    @staticmethod
    def _link_payload(edge: dict) -> dict:
        return {
            "id": edge["properties"].get("id", f"{edge['source']}-{edge['relation']}-{edge['target']}"),
            "source": edge["source"],
            "target": edge["target"],
            "relation": edge["relation"],
            "reason": edge["properties"].get("reason"),
            "timestamp": edge["properties"].get("timestamp"),
        }

    # ------------------------------------------------------------------
    # Whole-graph views (UI + history)
    # ------------------------------------------------------------------
    def graph_state(self) -> dict:
        nodes = []
        for label in schema.NODE_LABELS:
            for props in self.backend.nodes(label):
                nodes.append({"id": props["id"], "label": label, **props})
        links = [self._link_payload(edge) for edge in self.backend.edges() if edge["relation"] in schema.EDGE_TYPES]
        known = {node["id"] for node in nodes}
        links = [link for link in links if link["source"] in known and link["target"] in known]
        return {
            "nodes": sorted(nodes, key=lambda node: node.get("timestamp", "")),
            "links": links,
            "mode": self.mode,
            "counts": {
                "nodes": len(nodes),
                "links": len(links),
                "beliefs": sum(node["label"] == schema.LABEL_BELIEF for node in nodes),
                "experiences": sum(node["label"] == schema.LABEL_EXPERIENCE for node in nodes),
                "evidence": sum(node["label"] == schema.LABEL_EVIDENCE for node in nodes),
            },
        }

    def history(self, target: str | None = None, limit: int = 200) -> dict:
        """Audit view: experiences, evidence, beliefs and every revision edge."""
        def for_target(node: dict) -> bool:
            return target is None or node.get("target") == target

        experiences = [e for e in self.backend.nodes(schema.LABEL_EXPERIENCE) if for_target(e)]
        evidence = [ev for ev in self.backend.nodes(schema.LABEL_EVIDENCE) if for_target(ev)]
        beliefs = [b for b in self.backend.nodes(schema.LABEL_BELIEF) if for_target(b)]
        experiences.sort(key=lambda node: node.get("timestamp", ""))
        evidence.sort(key=lambda node: node.get("timestamp", ""))
        beliefs.sort(key=lambda node: node.get("timestamp", ""))

        by_id = {belief["id"]: belief for belief in beliefs}
        revisions = []
        for edge in self.backend.edges():
            if edge["relation"] != schema.REL_UPDATES:
                continue
            new_belief, old_belief = by_id.get(edge["source"]), by_id.get(edge["target"])
            if new_belief is None or old_belief is None:
                continue
            revisions.append(
                {
                    "reason": edge["properties"].get("reason", ""),
                    "timestamp": edge["properties"].get("timestamp", new_belief["timestamp"]),
                    "confidence_delta": edge["properties"].get("confidence_delta", 0.0),
                    "new_belief": new_belief,
                    "old_belief": old_belief,
                }
            )
        revisions.sort(key=lambda revision: revision["timestamp"])

        counters = {"SUPPORTS": 0, "CONTRADICTS": 0}
        for edge in self.backend.edges():
            if edge["relation"] in counters:
                if target is None or (by_id.get(edge["target"]) or {}).get("target") == target or edge["relation"] == schema.REL_UPDATES:
                    counters[edge["relation"]] += 1

        return {
            "target": target,
            "mode": self.mode,
            "experiences": experiences[-limit:],
            "evidence": evidence[-limit:],
            "beliefs": beliefs[-limit:],
            "revisions": revisions[-limit:],
            "stats": {
                "beliefs_by_status": {
                    status: sum(belief.get("status") == status for belief in beliefs)
                    for status in schema.BELIEF_STATUSES
                },
                "experiences_by_outcome": {
                    outcome: sum(exp.get("outcome") == outcome for exp in experiences)
                    for outcome in schema.OUTCOMES
                },
                "supports": counters["SUPPORTS"],
                "contradictions": counters["CONTRADICTS"],
                "revisions": len(revisions),
                "targets": sorted({str(node.get("target")) for node in beliefs + experiences + evidence}),
            },
        }

    def reset(self) -> None:
        with self.lock:
            self.backend.clear()
        logger.warning("graph memory cleared (demo reset)")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def build_engine(url: str | None, graph_name: str = "revora", logger_: logging.Logger | None = None) -> MemoryEngine:
    """Connect to FalkorDB when configured, otherwise fall back to memory mode."""
    log = logger_ or logger
    if url:
        try:
            from falkordb import FalkorDB

            client = FalkorDB.from_url(url)
            graph = client.select_graph(graph_name)
            backend = FalkorBackend(graph, graph_name)
            backend.ping()
            schema.ensure_schema(graph, log)
            log.info("connected to FalkorDB graph %r", graph_name)
            return MemoryEngine(backend)
        except Exception as error:  # connection refused, bad URL, missing module
            log.error("FalkorDB unavailable (%s); falling back to in-memory mode", error)
    else:
        log.warning("FALKORDB_URL not set; using in-memory mode")
    return MemoryEngine(MemoryBackend())
