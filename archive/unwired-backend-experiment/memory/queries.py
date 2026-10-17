"""Named graph reads used by the API and the dashboard.

These helpers answer the questions the UI actually asks —
"what is the revision chain of this belief?", "how cautious is the agent for
this target?", "which evidence contradicts what?" — in one place, so the
HTTP layer stays thin and the traversal stays testable.
"""

from __future__ import annotations

from . import schema


def revision_chain(engine, belief_id: str) -> list[dict]:
    """Follow UPDATES edges from any belief to the full version chain (oldest first).

    Works in both directions: passing a stale belief or its successor returns
    the same chain, which is what the timeline panel needs.
    """
    by_id = {belief["id"]: belief for belief in engine.all_beliefs()}
    revisions = [
        edge
        for edge in engine.backend.edges()
        if edge["relation"] == schema.REL_UPDATES
    ]
    parent = {edge["source"]: edge for edge in revisions}   # successor -> edge to predecessor
    child = {edge["target"]: edge for edge in revisions}    # predecessor -> edge to successor
    if belief_id not in by_id:
        return []

    current = belief_id
    while current in child:  # walk back to the genesis belief
        current = child[current]["target"]
        if current not in by_id:
            break
    chain: list[dict] = []
    reasons = {edge["source"]: edge for edge in revisions}
    while current in by_id:
        chain.append(by_id[current])
        edge = child.get(current)  # predecessor -> successor edge
        if not edge:
            break
        current = edge["source"]
        if current not in reasons:
            break
    return chain


def revision_reason(engine, successor_id: str) -> str | None:
    """The reason recorded on the UPDATES edge that produced this belief."""
    for edge in engine.backend.edges():
        if edge["relation"] == schema.REL_UPDATES and edge["source"] == successor_id:
            return edge["properties"].get("reason")
    return None


def risk_posture(engine, target: str | None = None) -> dict:
    """Aggregate caution level from the risk tolerance of active beliefs."""
    beliefs = engine.active_beliefs(target)
    if not beliefs:
        return {"level": "unknown", "average_risk_tolerance": None, "beliefs": 0}
    average = sum(float(belief.get("risk_tolerance", 0.5)) for belief in beliefs) / len(beliefs)
    level = "cautious" if average < 0.3 else "guarded" if average < 0.6 else "confident"
    return {
        "level": level,
        "average_risk_tolerance": round(average, 3),
        "beliefs": len(beliefs),
    }


def contradiction_index(engine, target: str | None = None) -> list[dict]:
    """Every CONTRADICTS edge with both endpoints summarized for the UI."""
    beliefs = {belief["id"]: belief for belief in engine.all_beliefs(target)}
    evidence = {item["id"]: item for item in engine.backend.nodes(schema.LABEL_EVIDENCE)}
    contradictions = []
    for edge in engine.backend.edges():
        if edge["relation"] != schema.REL_CONTRADICTS:
            continue
        belief, source = beliefs.get(edge["target"]), evidence.get(edge["source"])
        if belief is None or source is None:
            continue
        contradictions.append(
            {
                "evidence_id": source["id"],
                "evidence_description": source.get("description", ""),
                "belief_id": belief["id"],
                "belief_statement": belief.get("statement", ""),
                "belief_status": belief.get("status"),
                "timestamp": source.get("timestamp"),
            }
        )
    contradictions.sort(key=lambda item: item.get("timestamp") or "")
    return contradictions


def humanize_paths(related_paths: list[dict], limit: int = 12) -> list[str]:
    """Render search results' neighborhood paths as readable strings for the agent feed.

    Accepts the `related_paths` shape produced by `MemoryEngine.search_memory`
    (summarized source/relation/target entries).
    """
    rendered = []
    for path in related_paths[:limit]:
        source = path.get("source") or {}
        target = path.get("target") or {}
        arrow = f"-{path.get('relation', 'RELATED')}->"
        if path.get("reason"):
            arrow = f"-{path.get('relation', 'RELATED')}({str(path['reason'])[:40]})->"
        rendered.append(
            f"{source.get('label', 'Node')}[{(source.get('id') or '')[:8]}] {arrow} "
            f"{target.get('label', 'Node')}[{(target.get('id') or '')[:8]}] — {str(target.get('summary', ''))[:60]}"
        )
    return rendered
