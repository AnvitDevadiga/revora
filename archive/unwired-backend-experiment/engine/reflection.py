"""Groq reflection service: classify new evidence against existing beliefs.

The model receives the active beliefs and one new evidence/experience pair and
must answer with a strict JSON document. Every field is validated server-side;
belief ids that were not in the prompt are discarded. Any failure (missing key,
HTTP error, malformed JSON) returns `None` and the caller falls back to the
deterministic heuristic — callers can therefore treat the reflector as a
best-effort enhancement, never a single point of failure.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("revora.reflection")

_SYSTEM = """You are Revora's belief-revision service for a deployment agent.
You receive (a) the agent's existing Belief nodes with ids and (b) one new piece of
Evidence from a real experience. Decide, for each belief, whether the evidence
contradicts it, supports it, or is unrelated. A contradiction requires a genuine
conflict: the evidence must make the belief's guidance unsafe or wrong to keep
acting on. For every contradicted belief, propose one successor belief that keeps
the useful knowledge but adds the safeguard the failure demands, with a lower or
equal risk tolerance.

Response contract (strict JSON, no markdown):
{
  "verdicts": [
    {"belief_id": "<exact id from input>", "verdict": "contradicts"|"supports"|"unrelated",
     "rationale": "<one sentence>"}
  ],
  "revisions": [
    {"belief_id": "<contradicted belief id>", "revised_statement": "<specific, actionable, <=200 chars>",
     "confidence_score": 0.0-1.0, "risk_tolerance": 0.0-1.0, "reason": "<one sentence>"}
  ]
}
Include a revision entry for every belief you marked "contradicts". Never invent ids."""


class GroqReflector:
    """Thin, validated wrapper around one Groq chat completion per reflection."""

    def __init__(self, client=None, model: str = ""):
        self.client = client
        self.model = model

    @property
    def available(self) -> bool:
        return self.client is not None

    def evaluate(
        self,
        *,
        target: str,
        action_taken: str,
        outcome: str,
        context: str,
        evidence: dict,
        beliefs: list[dict],
        consequence: bool = False,
    ) -> dict | None:
        """Return validated verdicts/revisions, or None when the LLM path fails."""
        if not self.available:
            return None
        belief_payload = [
            {
                "id": belief["id"],
                "statement": belief["statement"],
                "confidence_score": belief["confidence_score"],
                "risk_tolerance": belief.get("risk_tolerance", 0.5),
                "action_scope": belief.get("action_scope", "release"),
            }
            for belief in beliefs
        ]
        user = json.dumps(
            {
                "question": "Does this new evidence invalidate any existing Belief node?",
                "target": target,
                "experience": {"action_taken": action_taken, "outcome": outcome, "context": context},
                "new_evidence": {
                    "description": evidence.get("description"),
                    "source_tool": evidence.get("source_tool"),
                    "metrics": evidence.get("metrics", {}),
                },
                "consequence_of_previous_step": consequence,
                "rule_for_consequence": (
                    "This outcome is a compliant consequence (a rollback executed after a failed canary). "
                    "Do NOT treat it as new contradicting evidence; classify how it reflects on beliefs "
                    "(a guardrail that held is normally 'supports')."
                    if consequence
                    else "A failure is contradicting evidence when it genuinely conflicts with a belief's guidance."
                ),
                "existing_beliefs": belief_payload,
            }
        )
        try:
            message = self.client.complete(
                messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
                response_format={"type": "json_object"},
                max_tokens=900,
            )
            raw = message.get("content") or ""
            parsed = json.loads(raw)
        except json.JSONDecodeError as error:
            logger.warning("reflector returned non-JSON content: %s", error)
            return None
        except Exception as error:  # network, auth, rate limits — never fatal
            logger.warning("reflector call failed: %s", error)
            return None

        return self._validate(parsed, beliefs)

    @staticmethod
    def _validate(parsed: dict, beliefs: list[dict]) -> dict | None:
        if not isinstance(parsed, dict):
            return None
        known = {belief["id"]: belief for belief in beliefs}
        verdicts: list[dict] = []
        for item in parsed.get("verdicts", []) if isinstance(parsed.get("verdicts"), list) else []:
            if not isinstance(item, dict):
                continue
            belief_id = item.get("belief_id")
            verdict = item.get("verdict")
            if belief_id not in known or verdict not in ("contradicts", "supports", "unrelated"):
                continue
            verdicts.append(
                {
                    "belief_id": belief_id,
                    "verdict": verdict,
                    "rationale": str(item.get("rationale", ""))[:300],
                }
            )
        revisions: dict[str, dict] = {}
        for item in parsed.get("revisions", []) if isinstance(parsed.get("revisions"), list) else []:
            if not isinstance(item, dict) or item.get("belief_id") not in known:
                continue
            statement = str(item.get("revised_statement", "")).strip()
            if not 8 <= len(statement) <= 300:
                continue
            revisions[item["belief_id"]] = {
                "revised_statement": statement,
                "confidence_score": item.get("confidence_score"),
                "risk_tolerance": item.get("risk_tolerance"),
                "reason": str(item.get("reason", ""))[:300],
            }
        if not verdicts:
            return None
        return {"verdicts": verdicts, "revisions": revisions}
