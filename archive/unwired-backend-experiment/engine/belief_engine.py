"""BeliefEngine — turns a new outcome into graph-level belief evolution.

Flow for every recorded outcome:

1. Load the active beliefs for the deployment target.
2. First belief ever?  -> bootstrap one from the experience.
3. Otherwise ask the Groq reflector (strict JSON) which beliefs the new
   evidence contradicts or supports. If the LLM is unavailable or answers
   badly, a transparent keyword+scope heuristic decides instead.
4. Persist the consequences on the graph:
   * `(:Evidence)-[:CONTRADICTS]->(:Belief)` for contradicted beliefs,
   * a successor belief linked with `(:Belief)-[:UPDATES {reason}]->(:Belief)`,
   * the contradicted belief is marked `revised` (history is never erased),
   * risk tolerance decays multiplicatively — a mistake keeps shaping future
     caution even after the belief is re-supported later.
   * `(:Evidence)-[:SUPPORTS]->(:Belief)` nudges confidence and tolerance up.
"""

from __future__ import annotations

import logging
import re
import threading

from memory import schema
from memory import queries

logger = logging.getLogger("revora.beliefs")

_HEURISTIC_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "was", "were", "has",
    "have", "into", "over", "after", "before", "does", "should", "would",
    "could", "will", "are", "its", "not", "but", "all", "any",
}


def derive_scope(action_taken: str) -> str:
    """Map an action description onto the belief scope it informs."""
    text = (action_taken or "").lower()
    if "canary" in text:
        return "canary"
    if any(word in text for word in ("release", "promote", "rollback", "deploy")):
        return "release"
    return "general"


class BeliefEngine:
    """Contradiction detection + belief revision, wired to the memory engine."""

    def __init__(self, memory, reflector=None):
        self.memory = memory
        self.reflector = reflector
        self.lock = threading.RLock()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def evaluate_outcome(
        self,
        *,
        target: str,
        action_taken: str,
        outcome: str,
        context: str,
        experience: dict,
        evidence: dict,
        consequence: bool = False,
    ) -> dict:
        """Classify the new evidence and persist all belief consequences.

        `consequence=True` marks an outcome that follows from a previous step of
        the same run (e.g. a rollback executed because the canary gates failed).
        It is never treated as *new* contradicting evidence — instead it is
        classified like a success: the safeguard ran exactly as the revised
        belief demanded, which validates the cautious guidance rather than
        punishing it twice for one incident.
        """
        with self.lock:
            active = self.memory.active_beliefs(target)
            if not active:
                return self._bootstrap(target, action_taken, outcome, evidence)

            classification = None
            if self.reflector is not None and getattr(self.reflector, "available", False):
                classification = self.reflector.evaluate(
                    target=target,
                    action_taken=action_taken,
                    outcome=outcome,
                    context=context,
                    evidence=evidence,
                    beliefs=active,
                    consequence=consequence,
                )
            if classification is None:
                classification = self._heuristic(action_taken, outcome, evidence, active, consequence=consequence)
                classifier = "heuristic"
            else:
                classifier = "groq"

            return self._apply(
                target=target,
                classification=classification,
                classifier=classifier,
                outcome=outcome,
                action_taken=action_taken,
                evidence=evidence,
                active=active,
            )

    # ------------------------------------------------------------------
    # Classification strategies
    # ------------------------------------------------------------------
    def _bootstrap(self, target: str, action_taken: str, outcome: str, evidence: dict) -> dict:
        scope = derive_scope(action_taken)
        if outcome == schema.OUTCOME_SUCCESS:
            statement = (
                f"{target}: {action_taken} completed successfully; treat it as a viable "
                "approach while the standard gates keep passing"
            )
            confidence, risk = 0.5, 0.5
        else:
            statement = (
                f"{target}: {action_taken} failed; require a passing gate before repeating it"
            )
            confidence, risk = 0.4, 0.3
        belief = self.memory.create_belief(
            statement=statement,
            confidence_score=confidence,
            target=target,
            risk_tolerance=risk,
            action_scope=scope,
        )
        self.memory.link_evidence_to_belief(evidence, belief, schema.REL_SUPPORTS if outcome == schema.OUTCOME_SUCCESS else schema.REL_CONTRADICTS)
        return {
            "classifier": "bootstrap",
            "summary": f"First belief for {target} created from this outcome.",
            "verdicts": [],
            "supports": [],
            "contradictions": [],
            "revisions": [],
            "bootstrapped": [belief],
            "risk_posture": queries.risk_posture(self.memory, target),
        }

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-z0-9]{4,}", (text or "").lower())
            if token not in _HEURISTIC_STOPWORDS
        }

    def _heuristic(self, action_taken: str, outcome: str, evidence: dict, active: list[dict], consequence: bool = False) -> dict:
        """Deterministic fallback: scope match or keyword overlap decides."""
        scope = derive_scope(action_taken)
        evidence_tokens = self._tokens(
            f"{evidence.get('description', '')} {action_taken} {evidence.get('metrics', {})}"
        )
        scored = []
        for belief in active:
            overlap = len(evidence_tokens & self._tokens(belief["statement"]))
            scope_match = belief.get("action_scope") == scope
            if overlap >= schema.HEURISTIC_MIN_OVERLAP or scope_match:
                scored.append((overlap + (2 if scope_match else 0), belief))
        scored.sort(key=lambda pair: (pair[0], belief_confidence(pair[1])), reverse=True)

        # A consequence outcome (a rollback demanded by a failed canary) counts
        # as support for the cautious belief that required the safeguard.
        if outcome == schema.OUTCOME_FAILURE and not consequence:
            if not scored:
                return {"verdicts": [], "revisions": {}}
            _, belief = scored[0]
            return {
                "verdicts": [
                    {
                        "belief_id": belief["id"],
                        "verdict": "contradicts",
                        "rationale": (
                            f"Observed {outcome} under the conditions this belief covers "
                            f"(scope {scope}, keyword overlap)."
                        ),
                    }
                ],
                "revisions": {},
            }

        supports = [
            {
                "belief_id": belief["id"],
                "verdict": "supports",
                "rationale": (
                    "Compliant rollback: the safeguard this belief requires ran exactly as demanded."
                    if consequence
                    else "Successful outcome consistent with this belief."
                ),
            }
            for _, belief in scored[:2]
        ]
        return {"verdicts": supports, "revisions": {}}

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _apply(
        self,
        *,
        target: str,
        classification: dict,
        classifier: str,
        outcome: str,
        action_taken: str,
        evidence: dict,
        active: list[dict],
    ) -> dict:
        by_id = {belief["id"]: belief for belief in active}
        verdicts = classification.get("verdicts", [])
        llm_revisions = classification.get("revisions", {}) or {}

        supports: list[dict] = []
        contradictions: list[dict] = []
        revisions: list[dict] = []

        for verdict in verdicts:
            belief = by_id.get(verdict["belief_id"])
            if belief is None:
                continue
            if verdict["verdict"] == "contradicts":
                contradictions.append({"belief": belief, "rationale": verdict.get("rationale", "")})
            elif verdict["verdict"] == "supports":
                supports.append({"belief": belief, "rationale": verdict.get("rationale", "")})

        # -- supports: strengthen the belief a little --------------------------
        support_effects = []
        for item in supports:
            belief = item["belief"]
            confidence_after = min(
                schema.CONFIDENCE_CEILING,
                float(belief["confidence_score"]) + schema.SUPPORT_CONFIDENCE_GROWTH,
            )
            risk_after = min(
                schema.RISK_TOLERANCE_CEILING,
                float(belief.get("risk_tolerance", 0.5)) + schema.SUPPORT_RISK_GROWTH,
            )
            self.memory.link_evidence_to_belief(evidence, belief, schema.REL_SUPPORTS)
            self.memory.update_belief(belief["id"], {"confidence_score": confidence_after, "risk_tolerance": risk_after})
            support_effects.append(
                {
                    "belief_id": belief["id"],
                    "statement": belief["statement"],
                    "rationale": item["rationale"],
                    "confidence_before": belief["confidence_score"],
                    "confidence_after": round(confidence_after, 3),
                    "risk_before": belief.get("risk_tolerance"),
                    "risk_after": round(risk_after, 3),
                }
            )

        # -- contradictions: link, revise, decay risk tolerance ----------------
        contradiction_effects = []
        for item in contradictions:
            belief = item["belief"]
            self.memory.link_evidence_to_belief(evidence, belief, schema.REL_CONTRADICTS)
            proposal = llm_revisions.get(belief["id"], {})
            reason = proposal.get("reason") or item["rationale"] or (
                f"{action_taken} produced a {outcome} that conflicts with this guidance."
            )
            statement = proposal.get("revised_statement") or self._revised_statement(belief, action_taken, evidence)
            confidence = proposal.get("confidence_score")
            if not isinstance(confidence, (int, float)):
                confidence = float(belief["confidence_score"]) * 0.65
            risk = proposal.get("risk_tolerance")
            if not isinstance(risk, (int, float)):
                risk = float(belief.get("risk_tolerance", 0.5)) * schema.CONTRADICTION_RISK_DECAY
            successor = self.memory.supersede_belief(
                old_belief=belief,
                statement=statement,
                confidence_score=float(confidence),
                risk_tolerance=float(risk),
                reason=reason,
            )
            contradiction_effects.append(
                {"belief_id": belief["id"], "statement": belief["statement"], "rationale": item["rationale"]}
            )
            revisions.append(
                {
                    "old_belief_id": belief["id"],
                    "new_belief_id": successor["id"],
                    "previous_statement": belief["statement"],
                    "new_statement": successor["statement"],
                    "previous_confidence": belief["confidence_score"],
                    "new_confidence": successor["confidence_score"],
                    "risk_tolerance_before": belief.get("risk_tolerance"),
                    "risk_tolerance_after": successor["risk_tolerance"],
                    "version": successor["version"],
                    "reason": reason,
                }
            )

        if contradictions:
            summary = (
                f"Contradiction detected: {len(contradictions)} belief(s) revised, "
                f"risk tolerance lowered for {target}."
            )
        elif support_effects:
            summary = f"Evidence supports {len(support_effects)} active belief(s); confidence increased."
        else:
            summary = "No active belief was implicated by this evidence."

        logger.info("belief evaluation (%s): %s", classifier, summary)
        return {
            "classifier": classifier,
            "summary": summary,
            "verdicts": [
                {
                    "belief_id": verdict["belief_id"],
                    "verdict": verdict["verdict"],
                    "rationale": verdict.get("rationale", ""),
                    "belief_statement": (by_id.get(verdict["belief_id"]) or {}).get("statement", ""),
                }
                for verdict in verdicts
                if verdict["belief_id"] in by_id
            ],
            "supports": support_effects,
            "contradictions": contradiction_effects,
            "revisions": revisions,
            "bootstrapped": [],
            "risk_posture": queries.risk_posture(self.memory, target),
        }

    @staticmethod
    def _revised_statement(old_belief: dict, action_taken: str, evidence: dict) -> str:
        detail = str(evidence.get("description", "")).strip()[:140]
        scope = old_belief.get("action_scope", "release")
        if scope == "canary":
            return (
                f"{old_belief['target']}: a canary must pass every gate before release — "
                f"prior assumption was contradicted by: {detail}"
            )
        return (
            f"{old_belief['target']}: require verified gates before {scope} actions — "
            f"prior assumption was contradicted by: {detail}"
        )


def belief_confidence(belief: dict) -> float:
    try:
        return float(belief.get("confidence_score", 0.0))
    except (TypeError, ValueError):
        return 0.0
