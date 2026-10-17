"""DeploymentAgent — the Groq tool-use loop (plus an honest deterministic twin).

The Groq path drives `openai/gpt-oss-20b` (configurable) through the five
tools with strict step enforcement: out-of-order or fabricated calls are
rejected server-side and bounced back to the model as tool errors. The loop
requires every tool to execute before it accepts a final answer — the model
can reason in words, but the *facts* always come from real tool executions.

Without `GROQ_API_KEY` the same five tools run through a scripted planner that
labels itself `deterministic` in every event and in the stored run record.
Only the wording is templated; probes, graph writes and belief revisions are
the real code paths. Nothing is ever presented as model output when it isn't.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Callable

from . import sandbox
from .tools import TOOL_ORDER, TOOL_SPECS, ToolBox, ToolError

logger = logging.getLogger("revora.agent")

MAX_TURNS = 12
MAX_NUDGES = 2
MAX_VIOLATIONS = 4

SYSTEM_PROMPT = f"""You are Revora's deployment agent in a disposable local sandbox.
You are evaluating a release for one authorized deployment target. You have five tools and
you MUST call them in this exact order, exactly once each:

 1. search_memory(query)                                  — retrieve past experiences and active beliefs
 2. inspect_deployment(target)                            — read the rollout plan, gates and logs
 3. run_canary_check(config)                              — execute the controlled canary (real readiness probe)
 4. record_experience_and_update_belief(action, outcome, evidence)
                                                          — persist the observed outcome; belief evolution runs
 5. promote_or_rollback(decision, rationale)              — execute the final release decision

Hard rules:
- Never invent tool results. Only cite values returned by tools.
- The recorded outcome must equal the real canary result. Promote only if every gate passed.
- If memory contains a contradicted/revised belief with lowered risk tolerance for this target,
  say how it influenced your canary configuration or safeguards.
- Never claim production access; this sandbox only probes disposable localhost targets.
- Do not retry a failed tool order; follow the feedback in tool error messages.

Finish with a short operator note (3-5 sentences): the decision, the gate values that justify it,
and which remembered beliefs or revisions influenced you."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DeploymentAgent:
    """Runs one deployment evaluation per invocation; fully stateless across runs."""

    def __init__(self, memory, belief_engine, groq=None, model: str = ""):
        self.memory = memory
        self.beliefs = belief_engine
        self.groq = groq
        self.model = model

    @property
    def groq_available(self) -> bool:
        return self.groq is not None

    def resolve_mode(self, requested: str) -> str:
        if requested == "groq":
            if not self.groq_available:
                raise RuntimeError("GROQ_API_KEY is not configured; use mode 'deterministic' or add a key.")
            return "groq"
        if requested == "deterministic":
            return "deterministic"
        return "groq" if self.groq_available else "deterministic"

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    def run(
        self,
        *,
        run_id: str,
        goal: str,
        target: str,
        scenario: str,
        mode: str = "auto",
        emit: Callable[[dict], None],
    ) -> dict:
        resolved = self.resolve_mode(mode)
        started = time.perf_counter()
        self._last_decision = None
        toolbox = ToolBox(self.memory, self.beliefs, {"goal": goal, "target": target, "scenario": scenario, "mode": resolved})
        emit(
            {
                "type": "run_started",
                "run_id": run_id,
                "mode": resolved,
                "model": self.model if resolved == "groq" else "deterministic-planner",
                "goal": goal,
                "target": target,
                "scenario": scenario,
                "started_at": _now(),
            }
        )
        try:
            if resolved == "groq":
                answer = self._run_groq(run_id, goal, target, scenario, toolbox, emit)
            else:
                answer = self._run_deterministic(target, scenario, toolbox, emit)
        except Exception as error:
            emit({"type": "error", "message": str(error)})
            raise

        effects = self._aggregate_effects(toolbox)
        decision = self._last_decision or {}
        result = {
            "run_id": run_id,
            "status": "completed",
            "mode": resolved,
            "model": self.model if resolved == "groq" else "deterministic-planner",
            "goal": goal,
            "target": target,
            "scenario": scenario,
            "answer": answer,
            "decision": decision,
            "canary": toolbox.last_canary,
            "steps": [
                {"step": index + 1, "tool": record["tool"], "ok": record["ok"], "summary": record.get("summary", "")}
                for index, record in enumerate(toolbox.records)
            ],
            "belief_effects": effects,
            "risk_posture": effects.get("risk_posture"),
            "started_at": _now(),
            "finished_at": _now(),
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        summary = self._summary_line(result)
        emit({"type": "run_finished", "status": "completed", "summary": summary, "decision": decision.get("decision")})
        return result

    # ------------------------------------------------------------------
    # Groq-driven path
    # ------------------------------------------------------------------
    def _run_groq(self, run_id: str, goal: str, target: str, scenario: str, toolbox: ToolBox, emit: Callable) -> str:
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Goal: {goal}\nAuthorized target: {target}\n"
                    f"Deployment scenario fixture: {scenario}\n"
                    "Begin by calling search_memory, then proceed through every required tool."
                ),
            },
        ]
        nudges = violations = 0
        for _ in range(MAX_TURNS):
            message = self.groq.complete(messages, tools=TOOL_SPECS, temperature=0.2, max_tokens=1100)
            content = (message.get("content") or "").strip()
            if content:
                emit({"type": "assistant_message", "content": content})
            calls = message.get("tool_calls") or []

            if calls:
                messages.append({k: v for k, v in message.items() if k in ("role", "content", "tool_calls")})
                if len(calls) != 1:
                    violations += 1
                    self._check_violations(violations)
                    messages.append({"role": "tool", "tool_call_id": calls[0].get("id", "call"), "content": "Error: call exactly one tool per turn, in the required order."})
                    continue
                call = calls[0]
                name = (call.get("function") or {}).get("name", "")
                try:
                    arguments = json.loads((call.get("function") or {}).get("arguments") or "{}")
                except json.JSONDecodeError:
                    violations += 1
                    self._check_violations(violations)
                    messages.append({"role": "tool", "tool_call_id": call.get("id", "call"), "content": "Error: arguments were not valid JSON. Provide a JSON object matching the schema."})
                    continue

                step = len(toolbox.records) + 1
                emit({"type": "tool_call", "tool": name, "arguments": arguments, "step": step})
                try:
                    result = toolbox.execute(name, arguments)
                except ToolError as error:
                    violations += 1
                    self._check_violations(violations)
                    emit({"type": "tool_result", "tool": name, "step": step, "ok": False, "error": str(error)})
                    messages.append({"role": "tool", "tool_call_id": call.get("id", "call"), "content": f"Error: {error}"})
                    continue

                self._after_tool(name, result, step, emit)
                emit({"type": "tool_result", "tool": name, "step": step, "ok": True, "result": result})
                messages.append({"role": "tool", "tool_call_id": call.get("id", "call"), "content": json.dumps(result, default=str)})
                continue

            # No tool call: accept only a complete run.
            if toolbox.completed and content:
                return content
            if toolbox.completed:
                return "Run completed; all tools executed."
            nudges += 1
            if nudges > MAX_NUDGES:
                remaining = " → ".join(TOOL_ORDER[toolbox.stage:])
                raise RuntimeError(f"Agent stopped before completing required tools ({remaining}).")
            messages.append({"role": "assistant", "content": content or ""})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "You must still execute these tools before finishing: "
                        f"{' → '.join(TOOL_ORDER[toolbox.stage:])}. Call the next one now."
                    ),
                }
            )
        raise RuntimeError(f"Agent exceeded the {MAX_TURNS}-turn limit without completing the tool sequence.")

    @staticmethod
    def _check_violations(count: int) -> None:
        if count > MAX_VIOLATIONS:
            raise RuntimeError("Agent repeatedly violated the tool contract (order, schema or arguments).")

    # ------------------------------------------------------------------
    # Deterministic path (no GROQ_API_KEY)
    # ------------------------------------------------------------------
    def _run_deterministic(self, target: str, scenario: str, toolbox: ToolBox, emit: Callable) -> str:
        def step(name: str, arguments: dict, reasoning: str):
            index = len(toolbox.records) + 1
            emit({"type": "assistant_message", "content": f"[deterministic planner] {reasoning}"})
            emit({"type": "tool_call", "tool": name, "arguments": arguments, "step": index})
            result = toolbox.execute(name, arguments)
            self._after_tool(name, result, index, emit)
            emit({"type": "tool_result", "tool": name, "step": index, "ok": True, "result": result})
            return result

        search = step(
            "search_memory",
            {"query": f"{target} canary gate failures latency error risk"},
            "Retrieving remembered outcomes and active beliefs for this target before touching the release.",
        )
        prior = search.get("active_beliefs", [])
        cautious = any(float(belief.get("risk_tolerance") or 1.0) < 0.4 for belief in prior) or any(
            experience.get("outcome") == "failure" for experience in search.get("recent_experiences", [])
        )
        traffic_split = 5 if cautious else 10
        step(
            "inspect_deployment",
            {"target": target},
            f"Inspecting the rollout plan and gates. {'Memory shows prior failures/low risk tolerance — narrowing the canary.' if cautious else 'No prior failure signal — standard canary width.'}",
        )
        canary = step(
            "run_canary_check",
            {"config": {"traffic_split": traffic_split, "duration_s": 30, "rollback_on_failure": True}},
            f"Running the canary at {traffic_split}% traffic split against a disposable localhost target.",
        )
        outcome = "success" if canary["success"] else "failure"
        recorded = step(
            "record_experience_and_update_belief",
            {
                "action": f"canary check at {traffic_split}% traffic split",
                "outcome": outcome,
                "evidence": {"description": sandbox.canary_evidence(canary)["description"]},
            },
            "Persisting the observed outcome as Experience + Evidence, then running belief evolution.",
        )
        release = step(
            "promote_or_rollback",
            {
                "decision": canary["decision"],
                "rationale": (
                    f"Canary gates: " + ", ".join(f"{name}={'pass' if ok else 'fail'}" for name, ok in canary["gates"].items())
                    + f". Recorded effects: {recorded['belief_effects']['summary']}"
                ),
            },
            "Executing the final release decision that the observed gates justify.",
        )
        revisions = release["belief_effects"].get("revisions", [])
        memory_note = (
            f"Memory was revised ({len(revisions)} successor belief(s) linked via UPDATES)."
            if revisions
            else "Memory was updated (SUPPORTS edges; no contradiction detected)."
        )
        return (
            f"[deterministic planner] Decision: {release['decision']} for {target}. "
            f"Gates: {sum(bool(v) for v in canary['gates'].values())}/{len(canary['gates'])} passed. "
            f"Guards: {release['guards']}. {memory_note} "
            "This run used the scripted planner because no GROQ_API_KEY is configured."
        )

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------
    def _after_tool(self, name: str, result: dict, step: int, emit: Callable) -> None:
        """Emit derived events (belief evolution, final decision) after a tool."""
        if isinstance(result, dict) and result.get("belief_effects"):
            emit({"type": "belief_update", "step": step, "tool": name, "effects": result["belief_effects"]})
        if name == "promote_or_rollback":
            self._last_decision = {
                "decision": result["decision"],
                "outcome": result["outcome"],
                "guards": result["guards"],
                "gates": result["gates"],
                "risk_posture": result["risk_posture"],
            }
            emit({"type": "decision", "decision": result["decision"], "gates": result["gates"], "guards": result["guards"]})
        if name == "record_experience_and_update_belief":
            for record in result["belief_effects"].get("revisions", []):
                emit({"type": "belief_revision", "step": step, "revision": record})

    @staticmethod
    def _aggregate_effects(toolbox: ToolBox) -> dict:
        """Fold the belief effects of every tool call into one run-level record."""
        aggregate = {
            "classifiers": [],
            "summary": "",
            "revisions": [],
            "supports": [],
            "contradictions": [],
            "bootstrapped": [],
            "risk_posture": None,
        }
        for record in toolbox.records:
            effects = (record.get("result") or {}).get("belief_effects")
            if not effects:
                continue
            if effects.get("classifier") and effects["classifier"] not in aggregate["classifiers"]:
                aggregate["classifiers"].append(effects["classifier"])
            for key in ("revisions", "supports", "contradictions", "bootstrapped"):
                aggregate[key].extend(effects.get(key) or [])
            if effects.get("risk_posture"):
                aggregate["risk_posture"] = effects["risk_posture"]
        stats = toolbox.memory.history(target=toolbox.run["target"])["stats"]
        aggregate["summary"] = (
            f"{stats['beliefs_by_status'].get('active', 0)} active belief(s), "
            f"{stats['revisions']} revision(s), {stats['contradictions']} contradiction(s) "
            f"in memory for {toolbox.run['target']}."
        )
        return aggregate

    @staticmethod
    def _summary_line(result: dict) -> str:
        decision = (result.get("decision") or {}).get("decision", "unknown")
        gates = (result.get("canary") or {}).get("gates") or {}
        passed = sum(bool(value) for value in gates.values())
        return f"{decision.upper()} · gates {passed}/{len(gates) or '?'} · mode={result['mode']}"
