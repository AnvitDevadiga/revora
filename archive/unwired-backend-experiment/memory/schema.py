"""Revora graph schema — the single source of truth for node labels, edge types and invariants.

The graph models an agent's learned beliefs about a deployment target:

    (:Experience)-[:LED_TO]->(:Evidence)
    (:Evidence)-[:SUPPORTS|:CONTRADICTS]->(:Belief)
    (new :Belief)-[:UPDATES {reason}]->(old :Belief)   # versioning over time

Every write path in the codebase imports its vocabulary from here so the
schema cannot silently drift between the memory engine, the belief engine,
the agent tools and the API.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# Node labels
# ---------------------------------------------------------------------------
LABEL_BELIEF: Final = "Belief"
LABEL_EXPERIENCE: Final = "Experience"
LABEL_EVIDENCE: Final = "Evidence"

NODE_LABELS: Final[tuple[str, ...]] = (LABEL_BELIEF, LABEL_EXPERIENCE, LABEL_EVIDENCE)

# ---------------------------------------------------------------------------
# Relationship types
# ---------------------------------------------------------------------------
REL_LED_TO: Final = "LED_TO"              # (:Experience)-[:LED_TO]->(:Evidence)
REL_SUPPORTS: Final = "SUPPORTS"           # (:Evidence)-[:SUPPORTS]->(:Belief)
REL_CONTRADICTS: Final = "CONTRADICTS"     # (:Evidence)-[:CONTRADICTS]->(:Belief)
REL_UPDATES: Final = "UPDATES"             # (:Belief)-[:UPDATES {reason}]->(:Belief)

BELIEF_RELATIONS: Final[tuple[str, ...]] = (REL_SUPPORTS, REL_CONTRADICTS)
EDGE_TYPES: Final[tuple[str, ...]] = (REL_LED_TO, REL_SUPPORTS, REL_CONTRADICTS, REL_UPDATES)

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------
STATUS_ACTIVE: Final = "active"
STATUS_STALE: Final = "stale"
STATUS_REVISED: Final = "revised"
BELIEF_STATUSES: Final[tuple[str, ...]] = (STATUS_ACTIVE, STATUS_STALE, STATUS_REVISED)

OUTCOME_SUCCESS: Final = "success"
OUTCOME_FAILURE: Final = "failure"
OUTCOMES: Final[tuple[str, ...]] = (OUTCOME_SUCCESS, OUTCOME_FAILURE)

# ---------------------------------------------------------------------------
# Belief risk-tolerance policy
# ---------------------------------------------------------------------------
# Confidence expresses how much the agent trusts a statement (0..1).
# Risk tolerance expresses how much freedom the agent gives itself when acting
# on it (0..1). A contradiction decays tolerance multiplicatively and revision
# never raises it above the *revised* cap, so a mistake today keeps shaping
# tomorrow's cautiousness even if the belief is later re-supported.
RISK_TOLERANCE_FLOOR: Final = 0.05
RISK_TOLERANCE_CEILING: Final = 0.95
CONTRADICTION_RISK_DECAY: Final = 0.5     # a contradiction halves risk tolerance
SUPPORT_RISK_GROWTH: Final = 0.05         # a support nudges it up slowly
SUPPORT_CONFIDENCE_GROWTH: Final = 0.06   # and nudges confidence up
CONFIDENCE_FLOOR: Final = 0.05
CONFIDENCE_CEILING: Final = 0.95

# A contradiction requires at least this keyword overlap when the LLM
# reflector is unavailable and the heuristic fallback classifies evidence.
HEURISTIC_MIN_OVERLAP: Final = 1

# ---------------------------------------------------------------------------
# Index setup
# ---------------------------------------------------------------------------
# FalkorDB supports the modern `CREATE INDEX FOR` syntax; the legacy
# `CREATE INDEX ON` form is kept as a fallback for older builds. Both error
# when the index already exists, so the caller swallows those errors.
INDEX_STATEMENTS: Final[tuple[tuple[str, ...], ...]] = tuple(
    (
        f"CREATE INDEX FOR (n:{label}) ON (n.id)",
        f"CREATE INDEX ON :{label}(id)",
    )
    for label in NODE_LABELS
)


def ensure_schema(graph, logger) -> None:
    """Create id indexes so node lookups stay O(1) as memory grows.

    Safe to call on every startup: `CREATE INDEX` conflicts are expected and
    intentionally ignored.
    """
    for candidates in INDEX_STATEMENTS:
        for statement in candidates:
            try:
                graph.query(statement)
                break
            except Exception:  # index already exists or syntax variant unsupported
                continue
    logger.debug("schema ensured for graph")
