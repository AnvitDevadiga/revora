"""The five deployment-agent tools, bound to FalkorDB memory and the sandbox.

Enforcement model (the same one the tests exercise):

* Tools must be called in a fixed order, exactly once each —
  `search_memory → inspect_deployment → run_canary_check →
   record_experience_and_update_belief → promote_or_rollback`.
* Arguments are validated server-side. The run's target/scenario are the
  authorized context; the model cannot change them mid-run.
* The agent cannot fabricate outcomes: `record_experience_and_update_belief`
  only accepts the decision the real canary produced, and the recorded
  evidence metrics are the *measured* probe values, with the model's summary
  kept as the human-readable description.
* `promote_or_rollback` refuses to promote when any gate failed.

Violations raise `ToolError`; the loop returns the message to the model as a
tool result so it can correct itself, and counts the violation.
"""

from __future__ import annotations

import re

from memory import queries, schema

from . import sandbox

TOOL_ORDER = (
    "search_memory",
    "inspect_deployment",
    "run_canary_check",
    "record_experience_and_update_belief",
    "promote_or_rollback",
)

_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_METRIC_KEY = re.compile(r"^[a-zA-Z0-9_]{1,32}$")

TOOL_SPECS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": (
                "Query FalkorDB memory for past experiences and active beliefs about the deployment "
                "target. Call this FIRST, before planning, and cite what you find in your final note."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search for, e.g. 'canary latency failures friday peak'",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_deployment",
            "description": (
                "Inspect the rollout plan and current status/logs of the authorized deployment target. "
                "Returns the canary strategy and the gates that will be enforced."
            ),
            "parameters": {
                "type": "object",
                "properties": {"target": {"type": "string", "description": "The deployment target name"}},
                "required": ["target"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_canary_check",
            "description": (
                "Execute the controlled traffic-split canary: a real readiness probe plus the gate "
                "evaluation (health, latency, error rate). Never operates a production system."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "config": {
                        "type": "object",
                        "description": "Canary knobs: traffic_split (1-25), duration_s (5-120), rollback_on_failure (bool).",
                        "properties": {
                            "traffic_split": {"type": "number"},
                            "duration_s": {"type": "number"},
                            "rollback_on_failure": {"type": "boolean"},
                        },
                        "additionalProperties": False,
                    }
                },
                "required": ["config"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_experience_and_update_belief",
            "description": (
                "Persist the observed canary outcome in FalkorDB as an Experience + Evidence pair, then "
                "run belief evolution: SUPPORTS/CONTRADICTS edges and, on contradiction, a successor "
                "belief via an UPDATES edge with a lowered risk tolerance. The outcome must match the "
                "real canary result; measured metrics are stored from the probe itself."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "Action taken, e.g. 'canary check at 10% traffic split'"},
                    "outcome": {"type": "string", "enum": ["success", "failure"]},
                    "evidence": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string", "description": "What was observed and why it matters (8-500 chars)"},
                            "metrics": {
                                "type": "object",
                                "description": "Optional extra numeric metrics; measured probe values are stored server-side.",
                            },
                        },
                        "required": ["description"],
                        "additionalProperties": False,
                    },
                },
                "required": ["action", "outcome", "evidence"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "promote_or_rollback",
            "description": (
                "Execute the final release decision. Promoting is only allowed when every canary gate "
                "passed; otherwise roll back. Records the release outcome and updates memory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "decision": {"type": "string", "enum": ["promote", "rollback"]},
                    "rationale": {"type": "string", "description": "Operator-grade rationale citing gates and remembered beliefs."},
                },
                "required": ["decision", "rationale"],
                "additionalProperties": False,
            },
        },
    },
]


class ToolError(ValueError):
    """A correctable violation — fed back to the model as a tool result."""


class ToolBox:
    """Executes the allowlisted tools for exactly one agent run."""

    def __init__(self, memory, belief_engine, run: dict):
        self.memory = memory
        self.beliefs = belief_engine
        self.run = run  # {goal, target, scenario, mode}
        self.stage = 0  # index into TOOL_ORDER
        self.last_canary: dict | None = None
        self.records: list[dict] = []

    # ------------------------------------------------------------------
    # Enforcement helpers
    # ------------------------------------------------------------------
    @property
    def next_expected(self) -> str | None:
        return TOOL_ORDER[self.stage] if self.stage < len(TOOL_ORDER) else None

    @property
    def completed(self) -> bool:
        return self.stage >= len(TOOL_ORDER)

    def _validate_call(self, name: str, arguments: dict) -> None:
        if not isinstance(arguments, dict):
            raise ToolError("Tool arguments must be a JSON object.")
        if name != self.next_expected:
            if name in TOOL_ORDER:
                raise ToolError(
                    f"Step order violation: expected {self.next_expected!r} next, got {name!r}. "
                    f"Call the required tools in order: {' → '.join(TOOL_ORDER[self.stage:])}."
                )
            raise ToolError(f"Unknown tool {name!r}. Allowed tools: {', '.join(TOOL_ORDER)}.")

    def _require_target(self, target) -> None:
        if not isinstance(target, str) or not _ID_PATTERN.match(target):
            raise ToolError("target must be a short identifier (letters, digits, - or _).")
        if target != self.run["target"]:
            raise ToolError(
                f"This run is authorized for target {self.run['target']!r} only; "
                f"you requested {target!r}."
            )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def execute(self, name: str, arguments: dict) -> dict:
        self._validate_call(name, arguments)
        extra = set(arguments) - set(self._spec_properties(name))
        if extra:
            raise ToolError(f"Unexpected argument(s) {sorted(extra)} for {name}.")
        result = getattr(self, f"_tool_{name}")(**arguments)
        self.stage += 1
        self.records.append({"tool": name, "arguments": arguments, "ok": True, "result": result})
        return result

    @staticmethod
    def _spec_properties(name: str) -> dict:
        for spec in TOOL_SPECS:
            if spec["function"]["name"] == name:
                return spec["function"]["parameters"]["properties"]
        return {}

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------
    def _tool_search_memory(self, query: str) -> dict:
        if not isinstance(query, str) or not 3 <= len(query) <= 300:
            raise ToolError("query must be a string between 3 and 300 characters.")
        found = self.memory.search_memory(query, target=self.run["target"])
        paths = queries.humanize_paths(found["related_paths"])
        return {
            "counts": found["counts"],
            "keywords": found["keywords"],
            "active_beliefs": [
                {
                    "id": belief["id"],
                    "statement": belief["statement"],
                    "confidence_score": belief["confidence_score"],
                    "risk_tolerance": belief.get("risk_tolerance"),
                    "status": belief["status"],
                    "version": belief.get("version", 1),
                }
                for belief in found["beliefs"]
                if belief.get("status") == schema.STATUS_ACTIVE
            ],
            "recent_experiences": [
                {
                    "id": experience["id"],
                    "action_taken": experience["action_taken"],
                    "outcome": experience["outcome"],
                    "timestamp": experience["timestamp"],
                    "detail": experience.get("detail", "")[:200],
                }
                for experience in found["experiences"][:6]
            ],
            "evidence": [
                {
                    "id": item["id"],
                    "description": item["description"][:220],
                    "source_tool": item["source_tool"],
                    "metrics": item.get("metrics", {}),
                }
                for item in found["evidence"][:6]
            ],
            "neighborhood_paths": paths,
            "guidance": (
                "Lower risk_tolerance means the agent has previously revised a belief in this area "
                "after a failure; weigh it when choosing your canary configuration and final decision."
            ),
        }

    def _tool_inspect_deployment(self, target: str) -> dict:
        self._require_target(target)
        return sandbox.inspect_deployment(target, self.run["scenario"])

    def _tool_run_canary_check(self, config: dict) -> dict:
        try:
            result = sandbox.run_canary_check(self.run["target"], self.run["scenario"], config)
        except ValueError as error:
            raise ToolError(str(error)) from error
        self.last_canary = result
        return result

    def _tool_record_experience_and_update_belief(self, action: str, outcome: str, evidence: dict) -> dict:
        if self.last_canary is None:
            raise ToolError("No canary has run yet; call run_canary_check first.")
        if not isinstance(action, str) or not 5 <= len(action) <= 200:
            raise ToolError("action must be a string between 5 and 200 characters.")
        if outcome not in schema.OUTCOMES:
            raise ToolError("outcome must be 'success' or 'failure'.")
        observed = schema.OUTCOME_SUCCESS if self.last_canary["success"] else schema.OUTCOME_FAILURE
        if outcome != observed:
            raise ToolError(
                f"Recorded outcome must match the observed canary result ({observed!r}). "
                "Never record an outcome you did not observe."
            )
        description, extra_metrics = self._validate_evidence(evidence)

        measured = sandbox.canary_evidence(self.last_canary)["metrics"]
        metrics = {**self._sanitize_metrics(extra_metrics or {}), **measured}
        experience = self.memory.add_experience(
            action_taken=action,
            context=f"goal={self.run['goal']} | scenario={self.run['scenario']}",
            outcome=outcome,
            target=self.run["target"],
            detail=description,
        )
        evidence_node = self.memory.add_evidence(
            description=description,
            source_tool="run_canary_check",
            metrics=metrics,
            target=self.run["target"],
        )
        self.memory.led_to(experience, evidence_node)
        effects = self.beliefs.evaluate_outcome(
            target=self.run["target"],
            action_taken=action,
            outcome=outcome,
            context=experience["context"],
            experience=experience,
            evidence=evidence_node,
        )
        return {"experience": experience, "evidence": evidence_node, "belief_effects": effects}

    def _tool_promote_or_rollback(self, decision: str, rationale: str) -> dict:
        if self.last_canary is None:
            raise ToolError("No canary has run yet; call run_canary_check first.")
        if decision not in ("promote", "rollback"):
            raise ToolError("decision must be 'promote' or 'rollback'.")
        if not isinstance(rationale, str) or not 10 <= len(rationale) <= 500:
            raise ToolError("rationale must be a string between 10 and 500 characters.")
        gates = self.last_canary["gates"]
        if decision == "promote" and not all(gates.values()):
            failed = [name for name, ok in gates.items() if not ok]
            raise ToolError(
                f"Cannot promote: canary gates failed ({', '.join(failed)}). "
                "Roll back, or fix the underlying cause and re-run the canary."
            )

        outcome = schema.OUTCOME_SUCCESS if decision == "promote" else schema.OUTCOME_FAILURE
        posture = queries.risk_posture(self.memory, self.run["target"])
        guards = "standard canary + rollback readiness"
        if float(posture.get("average_risk_tolerance") or 1.0) < 0.4:
            guards = "heightened: canary required, automatic rollback armed, off-peak window only"

        measured = sandbox.canary_evidence(self.last_canary)
        metrics = {
            **measured["metrics"],
            "decision": decision,
            "risk_tolerance_after": posture.get("average_risk_tolerance"),
        }
        description = (
            f"Release {decision} for {self.run['target']} — {rationale[:240]} "
            f"(gates: {sum(bool(v) for v in gates.values())}/{len(gates)} passed)"
        )
        experience = self.memory.add_experience(
            action_taken=f"release {decision} ({self.run['scenario']})",
            context=f"goal={self.run['goal']} | scenario={self.run['scenario']}",
            outcome=outcome,
            target=self.run["target"],
            detail=rationale,
        )
        evidence_node = self.memory.add_evidence(
            description=description,
            source_tool="promote_or_rollback",
            metrics=metrics,
            target=self.run["target"],
        )
        self.memory.led_to(experience, evidence_node)
        effects = self.beliefs.evaluate_outcome(
            target=self.run["target"],
            action_taken=experience["action_taken"],
            outcome=outcome,
            context=experience["context"],
            experience=experience,
            evidence=evidence_node,
            # A rollback is the compliant consequence of failed gates, not new
            # contradicting evidence — it validates the cautious belief.
            consequence=(decision == "rollback"),
        )
        return {
            "decision": decision,
            "outcome": outcome,
            "guards": guards,
            "gates": gates,
            "experience": experience,
            "evidence": evidence_node,
            "belief_effects": effects,
            "risk_posture": posture,
        }

    # ------------------------------------------------------------------
    # Evidence validation (anti-fabrication)
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_evidence(evidence: dict) -> tuple[str, dict]:
        if not isinstance(evidence, dict):
            raise ToolError("evidence must be an object with a description.")
        extra_keys = set(evidence) - {"description", "metrics"}
        if extra_keys:
            raise ToolError(f"Unexpected evidence keys: {sorted(extra_keys)}.")
        description = evidence.get("description")
        if not isinstance(description, str) or not 8 <= len(description) <= 500:
            raise ToolError("evidence.description must be a string between 8 and 500 characters.")
        metrics = evidence.get("metrics") or {}
        if not isinstance(metrics, dict):
            raise ToolError("evidence.metrics must be an object.")
        return description.strip(), metrics

    @staticmethod
    def _sanitize_metrics(metrics: dict) -> dict:
        clean: dict = {}
        for key, value in list(metrics.items())[:8]:
            if not isinstance(key, str) or not _METRIC_KEY.match(key):
                raise ToolError(f"Metric name {key!r} is not allowed.")
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise ToolError(f"Metric {key!r} must be a number or short string.")
            if isinstance(value, str) and len(value) > 64:
                raise ToolError(f"Metric {key!r} string must be 64 characters or fewer.")
            clean[key] = value
        return clean
