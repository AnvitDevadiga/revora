"""LLM recovery planner that reasons over FalkorDB context behind a deterministic guardrail.

The model proposes one allowlisted action and explains it, citing graph node IDs.
The guardrail accepts the proposal only when it matches the action implied by the
measured, reviewed evidence; otherwise the proposal is recorded as vetoed. The model
never chooses a tool, target, or command, so a wrong answer cannot change routing.
"""
from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger("revora.planner")

ACTIONS = ("rollback_and_verify", "keep_stable_and_verify", "continue_candidate_monitoring",
           "request_evidence", "reject_handoff")

SYSTEM = """You are the Recovery Planner in a release-safety team of agents.
The Release Analyst measured a live HTTP incident and the Evidence Reviewer validated it.
You also receive graph memory retrieved from FalkorDB: earlier incidents for this service,
the components each release shipped with, and earlier planner decisions.
Choose exactly one action from: rollback_and_verify, keep_stable_and_verify,
continue_candidate_monitoring, request_evidence, reject_handoff.
Rules: if the reviewer did not accept the evidence, mirror its status (needs_evidence -> request_evidence,
rejected -> reject_handoff). A measured live failure that is still active needs rollback_and_verify.
A failure that is already contained (blocked or rolled back) needs keep_stable_and_verify.
Use the graph memory to explain *why*, citing incident IDs (first 8 chars) and components.
Reply with JSON only: {"action": "...", "rationale": "<= 60 words", "cited": ["<id or component>", ...]}"""


class LLMPlanner:
    def __init__(self, transport, model: str):
        self.transport, self.model = transport, model

    @classmethod
    def from_env(cls):
        """Build a planner when GROQ_API_KEY is set; otherwise planning stays deterministic."""
        key = os.getenv("GROQ_API_KEY", "")
        if not key:
            return None
        from .groq_agent import GroqTransport
        model = os.getenv("GROQ_PLANNER_MODEL") or os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
        return cls(GroqTransport(key, model), model)

    def propose(self, evidence: dict, review: dict, context: dict) -> dict:
        """Return {action, rationale, cited, model}. Raises RuntimeError on unusable output."""
        brief = {"incident": {key: evidence.get(key) for key in (
                     "incident_id", "service", "version", "decision", "failed", "metrics",
                     "status_before", "status_during", "status_after", "sample_count")},
                 "evidence_review": {"status": review.get("status"),
                                     "contradictions": review.get("contradictions", []),
                                     "requested_evidence": review.get("requested_evidence", [])},
                 "graph_memory": context}
        message = self.transport.complete([
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(brief, default=str)}], tools=None)
        text = (message or {}).get("content") or ""
        match = re.search(r"\{.*\}", text, re.S)
        try:
            proposal = json.loads(match.group(0)) if match else {}
        except json.JSONDecodeError as error:
            raise RuntimeError("Planner returned malformed JSON") from error
        action = proposal.get("action")
        if action not in ACTIONS:
            raise RuntimeError(f"Planner proposed a non-allowlisted action: {action!r}")
        cited = [str(item)[:80] for item in (proposal.get("cited") or [])][:8]
        return {"action": action, "rationale": str(proposal.get("rationale") or "")[:600],
                "cited": cited, "model": self.model}


def guard(proposal: dict | None, safe_action: str, error: str | None = None) -> dict:
    """Combine an optional model proposal with the evidence-derived action."""
    if proposal is None:
        return {"source": "deterministic", "guardrail": "not_configured" if error is None else "planner_error",
                "error": error, "proposed_action": None, "final_action": safe_action}
    accepted = proposal["action"] == safe_action
    return {"source": "llm", "model": proposal["model"], "proposed_action": proposal["action"],
            "rationale": proposal["rationale"], "cited": proposal["cited"],
            "guardrail": "accepted" if accepted else "vetoed", "final_action": safe_action,
            "veto_reason": None if accepted else
            f"Measured evidence requires {safe_action}; the proposal {proposal['action']} was not executed."}
