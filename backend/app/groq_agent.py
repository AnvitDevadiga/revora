"""Groq local-tool agent with strict allowlisting and FalkorDB-backed outcomes."""
import json
import os
import re
from threading import BoundedSemaphore
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from .deployment import inspect_deployment, run_canary, promote_or_rollback

TOOLS = [
    {"type": "function", "function": {"name": "search_memory", "description": "Retrieve prior deployment outcomes for this exact service and scenario before planning.", "parameters": {"type": "object", "properties": {"service": {"type": "string"}, "scenario": {"type": "string", "enum": ["healthy", "readiness_failure", "canary_regression", "slow_release"]}}, "required": ["service", "scenario"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "inspect_deployment", "description": "Inspect the bounded rollout plan and safety gates.", "parameters": {"type": "object", "properties": {"scenario": {"type": "string", "enum": ["healthy", "readiness_failure", "canary_regression", "slow_release"]}}, "required": ["scenario"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "run_canary", "description": "Execute a real local HTTP health probe and evaluate the synthetic canary gates. Never operates a production system.", "parameters": {"type": "object", "properties": {"scenario": {"type": "string", "enum": ["healthy", "readiness_failure", "canary_regression", "slow_release"]}}, "required": ["scenario"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "record_outcome", "description": "Persist the observed canary outcome in FalkorDB after run_canary.", "parameters": {"type": "object", "properties": {"service": {"type": "string"}, "scenario": {"type": "string"}, "decision": {"type": "string", "enum": ["promote", "rollback"]}, "success": {"type": "boolean"}, "reason": {"type": "string"}}, "required": ["service", "scenario", "decision", "success", "reason"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "promote_or_rollback", "description": "Return a simulated release recommendation after recording evidence. No live routing or deployment operation executes.", "parameters": {"type": "object", "properties": {"service": {"type": "string"}, "decision": {"type": "string", "enum": ["promote", "rollback"]}}, "required": ["service", "decision"], "additionalProperties": False}}},
]

SYSTEM = """You are a cautious deployment agent in a disposable local sandbox.
Always call search_memory before planning. Call inspect_deployment, then run_canary.
After run_canary, call record_outcome with the observed decision and a concise reason. Then call promote_or_rollback using the exact observed decision.
Never claim production access. Never invent tool results. Prefer rollback if any gate fails.
Finish with a short explanation citing retrieved memory and observed gates. State that the final fixture action is simulated and no live routing changed."""


class GroqTransport:
    def __init__(self, key, model):
        self.key, self.model = key, model
    def complete(self, messages, tools):
        payload = {"model": self.model, "messages": messages,
                   "temperature": 0.1, "max_completion_tokens": 700}
        if tools:
            payload.update({"tools": tools, "tool_choice": "auto", "parallel_tool_calls": False})
        body = json.dumps(payload).encode()
        request = Request("https://api.groq.com/openai/v1/chat/completions", data=body,
                          headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json", "User-Agent": "Revora/1.0"})
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read())["choices"][0]["message"]
        except HTTPError as error:
            detail = error.read().decode()[:500]
            raise RuntimeError(f"Groq API returned {error.code}: {detail}") from error
        except URLError as error:
            raise RuntimeError(f"Groq API unavailable: {error.reason}") from error


class AgentCapacityError(RuntimeError):
    """The single stateful agent slot is already handling another run."""


class GroqAgent:
    def __init__(self, graph=None, transport=None, memory=None):
        self.graph = graph
        self.memory = memory
        model = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
        key = os.getenv("GROQ_API_KEY", "")
        self.transport = transport or (GroqTransport(key, model) if key else None)
        self.model = model
        self.last_canary = None
        self.last_record = None
        # Tool outcomes are kept on this instance, so concurrent runs would
        # corrupt one another. Fail fast until run state is request-scoped.
        configured_capacity = int(os.getenv("GROQ_MAX_CONCURRENT_RUNS", "1"))
        if configured_capacity != 1:
            raise RuntimeError("GROQ_MAX_CONCURRENT_RUNS must be 1 while agent state is instance-scoped")
        self.capacity = BoundedSemaphore(1)
        self.total_timeout = max(30, int(os.getenv("GROQ_TOTAL_TIMEOUT_SECONDS", "120")))

    @property
    def configured(self):
        return self.transport is not None

    def search_memory(self, service, scenario):
        if self.memory is not None:
            return self.memory.search(f"{service} {scenario}", service, scenario)
        if self.graph is None:
            return []
        rows = self.graph.query("MATCH (o:DeploymentOutcome {service:$service, scenario:$scenario}) RETURN o.decision,o.success,o.reason,o.created_at ORDER BY o.created_at DESC LIMIT 8", {"service": service, "scenario": scenario}).result_set
        return [{"decision": row[0], "success": row[1], "reason": row[2], "created_at": row[3]} for row in rows]

    def _reflect(self, service, scenario, outcome, canary):
        """Ask Groq how the belief should change; deterministic evidence owns the decision."""
        if not self.memory or not self.transport or outcome != "failure":
            return None
        previous = self.memory.active_belief(service, scenario)
        if not previous or previous.get("outcome") == outcome:
            return None
        prompt = (
            "A new deployment outcome conflicts with an active stored belief. "
            "Return only a JSON object with keys revised_statement and reason. "
            "Do not invent facts. The statement must preserve the observed gates and recommend a safer future rule.\n"
            + json.dumps({"active_belief": previous, "new_outcome": outcome,
                          "scenario": scenario, "observed_canary": canary}, ensure_ascii=False)
        )
        try:
            message = self.transport.complete([{"role": "user", "content": prompt}], [])
            content = message.get("content") or ""
            match = re.search(r"\{[\s\S]*\}", content)
            proposed = json.loads(match.group(0)) if match else {}
            statement = proposed.get("revised_statement")
            reason = proposed.get("reason")
            if not isinstance(statement, str) or not isinstance(reason, str):
                return None
            return {"revised_statement": statement.strip()[:400], "reason": reason.strip()[:400],
                    "provider": "Groq"}
        except Exception:
            # The observed canary is still recorded and revised by deterministic fallback rules.
            return None

    def record_outcome(self, service, scenario, decision, success, reason):
        if not self.last_canary or self.last_canary["scenario"] != scenario:
            raise ValueError("Run the canary before recording its outcome")
        if decision != self.last_canary["decision"] or success != self.last_canary["success"]:
            raise ValueError("Recorded outcome must match observed canary result")
        if self.memory is not None:
            reflection = self._reflect(service, scenario, "success" if success else "failure", self.last_canary)
            self.last_record = self.memory.record_deployment(service, scenario, decision, success, reason,
                                                             self.last_canary, reflection)
            return self.last_record
        record = {"id": f"{service}:{scenario}:{datetime.now(timezone.utc).isoformat()}",
                  "service": service, "scenario": scenario, "decision": decision,
                  "success": success, "reason": reason, "created_at": datetime.now(timezone.utc).isoformat()}
        if self.graph is not None:
            self.graph.query("CREATE (o:DeploymentOutcome {id:$id,service:$service,scenario:$scenario,decision:$decision,success:$success,reason:$reason,created_at:$created_at})", record)
        self.last_record = record
        return record

    def execute(self, name, arguments):
        if name == "search_memory": return self.search_memory(**arguments)
        if name == "inspect_deployment": return inspect_deployment(**arguments)
        if name == "run_canary":
            self.last_canary = run_canary(**arguments); return self.last_canary
        if name == "record_outcome": return self.record_outcome(**arguments)
        if name == "promote_or_rollback":
            if not self.last_record:
                raise ValueError("Record the observed outcome before applying the release decision")
            observed = self.last_canary
            recorded_service = self.last_record.get("experience", {}).get("service") or self.last_record.get("service")
            if (recorded_service and arguments["service"] != recorded_service
                    or arguments["decision"] != observed["decision"]):
                raise ValueError("Release action must match the recorded service and canary decision")
            return promote_or_rollback(**arguments)
        raise ValueError("Model requested an unknown tool")

    def run(self, goal, service, scenario, on_event=None):
        if not self.capacity.acquire(blocking=False):
            raise AgentCapacityError("Agent capacity reached; retry shortly")
        started = time.monotonic()
        try:
            return self._run(goal, service, scenario, on_event, started)
        finally:
            self.capacity.release()

    def _run(self, goal, service, scenario, on_event=None, started=None):
        if not self.configured:
            raise RuntimeError("GROQ_API_KEY is not configured")
        self.last_canary = None
        self.last_record = None
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": f"Goal: {goal}\nService: {service}\nScenario fixture: {scenario}"}]
        trace = []
        used = []
        sequence = ["search_memory", "inspect_deployment", "run_canary", "record_outcome", "promote_or_rollback"]
        for _ in range(8):
            if started is not None and time.monotonic() - started > self.total_timeout:
                raise RuntimeError("Agent run exceeded its total execution deadline")
            message = self.transport.complete(messages, TOOLS)
            messages.append(message)
            calls = message.get("tool_calls") or []
            if not calls:
                if used != sequence:
                    raise RuntimeError(f"Model ended before required tools: {sequence[len(used):]}")
                return {"answer": message.get("content") or "Completed.", "trace": trace,
                        "model": self.model, "scenario": scenario, "service": service,
                        "canary": self.last_canary}
            if len(calls) != 1:
                raise RuntimeError("The agent must execute exactly one tool per reasoning step")
            for call in calls:
                name = call["function"]["name"]
                arguments = json.loads(call["function"]["arguments"])
                expected = sequence[len(used)] if len(used) < len(sequence) else None
                if name != expected:
                    raise RuntimeError(f"Model requested {name}; expected {expected}")
                if ("scenario" in arguments and arguments["scenario"] != scenario) or ("service" in arguments and arguments["service"] != service):
                    raise RuntimeError("Model attempted to change the authorized service or scenario")
                if on_event:
                    on_event({"type": "tool_start", "tool": name, "arguments": arguments})
                result = self.execute(name, arguments)
                used.append(name); trace.append({"tool": name, "arguments": arguments, "result": result})
                if on_event:
                    on_event({"type": "tool_result", "tool": name, "result": result})
                    if name == "record_outcome" and result.get("contradiction_detected"):
                        on_event({"type": "belief_revision", "result": result})
                messages.append({"role": "tool", "tool_call_id": call["id"], "name": name, "content": json.dumps(result)})
        raise RuntimeError("Groq agent exceeded the eight-step tool limit")
