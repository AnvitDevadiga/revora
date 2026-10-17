"""The demo story: one belief, contradicted by a real mistake, revised safely.

Seeding uses the same primitives as live runs (`led_to`,
`link_evidence_to_belief`, `supersede_belief`) so the graph the judges inspect
has exactly the shape the agent produces at runtime. Timestamps are backdated
so the timeline panel reads like real history. Idempotent: a marker property
on the seeded Experience nodes prevents duplicates.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import schema

STORY_MARKER = "belief-evolution-v1"


def _stamp(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def seed_demo_story(engine, target: str = "payments") -> dict:
    """Insert the three-act story (green → contradicted → revised & re-supported)."""
    existing = [e for e in engine.backend.nodes(schema.LABEL_EXPERIENCE) if e.get("seed_story") == STORY_MARKER]
    if existing:
        return {"seeded": False, "reason": "demo story already present", "experiences": len(existing)}

    extra = {"origin": "demo-seed", "seed_story": STORY_MARKER}

    # -- Act 1: a successful canary builds an optimistic belief --------------
    exp1 = engine.add_experience(
        action_taken="canary deploy of payments 2.4.0 at 10% traffic split",
        context="routine weekday release window",
        outcome=schema.OUTCOME_SUCCESS,
        target=target,
        detail="Canary completed and the release promoted cleanly.",
        timestamp=_stamp(3.2),
        extra=extra,
    )
    ev1 = engine.add_evidence(
        description="All gates passed: health 200, p95 latency 18ms, error rate 0%.",
        source_tool="run_canary_check",
        metrics={"http_status": 200, "latency_ms": 18, "error_rate": 0.0, "gates_passed": 3, "gates_total": 3},
        target=target,
        timestamp=_stamp(3.2),
        extra=extra,
    )
    engine.led_to(exp1, ev1)
    belief_v1 = engine.create_belief(
        statement=(
            f"{target}: canary deploys at a 10% traffic split are low-risk while the standard gates stay green"
        ),
        confidence_score=0.72,
        target=target,
        risk_tolerance=0.62,
        action_scope="canary",
        version=1,
        timestamp=_stamp(3.2),
    )
    engine.link_evidence_to_belief(ev1, belief_v1, schema.REL_SUPPORTS)

    # -- Act 2: a real failure contradicts it — belief revised, risk lowered -
    exp2 = engine.add_experience(
        action_taken="canary deploy of payments 2.4.1 during Friday peak traffic",
        context="Friday 17:40 peak concurrency window",
        outcome=schema.OUTCOME_FAILURE,
        target=target,
        detail="Canary burned the latency and error budgets under peak load.",
        timestamp=_stamp(2.1),
        extra=extra,
    )
    ev2 = engine.add_evidence(
        description="Failed gates: latency and errors. p95 410ms (gate ≤120ms), error rate 6.2% (gate ≤5%).",
        source_tool="run_canary_check",
        metrics={"http_status": 200, "latency_ms": 410, "error_rate": 0.062, "gates_passed": 1, "gates_total": 3},
        target=target,
        timestamp=_stamp(2.1),
        extra=extra,
    )
    engine.led_to(exp2, ev2)
    engine.link_evidence_to_belief(ev2, belief_v1, schema.REL_CONTRADICTS)
    belief_v2 = engine.supersede_belief(
        old_belief=belief_v1,
        statement=(
            f"{target}: require an off-peak canary window and a p95 ≤120ms gate before any release; "
            "Friday peak traffic previously breached the error budget"
        ),
        confidence_score=0.45,
        risk_tolerance=0.25,
        reason="Friday peak concurrency breached the latency and error gates",
        timestamp=_stamp(2.1),
    )

    # -- Act 3: recovery with the revised guardrails re-supports the lesson --
    exp3 = engine.add_experience(
        action_taken="canary deploy of payments 2.4.1 retry in off-peak window with automatic rollback armed",
        context="Tuesday 09:20 off-peak window",
        outcome=schema.OUTCOME_SUCCESS,
        target=target,
        detail="Revised guardrails held: narrow canary, off-peak, rollback armed.",
        timestamp=_stamp(1.0),
        extra=extra,
    )
    ev3 = engine.add_evidence(
        description="All gates passed under the stricter policy: p95 96ms, error rate 1.1%.",
        source_tool="run_canary_check",
        metrics={"http_status": 200, "latency_ms": 96, "error_rate": 0.011, "gates_passed": 3, "gates_total": 3},
        target=target,
        timestamp=_stamp(1.0),
        extra=extra,
    )
    engine.led_to(exp3, ev3)
    engine.link_evidence_to_belief(ev3, belief_v2, schema.REL_SUPPORTS)
    engine.update_belief(
        belief_v2["id"],
        {
            "confidence_score": min(schema.CONFIDENCE_CEILING, belief_v2["confidence_score"] + schema.SUPPORT_CONFIDENCE_GROWTH),
            "risk_tolerance": min(schema.RISK_TOLERANCE_CEILING, belief_v2["risk_tolerance"] + schema.SUPPORT_RISK_GROWTH),
        },
    )

    return {
        "seeded": True,
        "target": target,
        "story": [
            "canary success built an optimistic belief (confidence 0.72)",
            "Friday peak failure contradicted it: CONTRADICTS edge + revision via UPDATES, risk 0.62→0.25",
            "off-peak recovery re-supported the revised belief (confidence 0.51)",
        ],
        "beliefs": 2,
        "experiences": 3,
        "evidence": 3,
    }
