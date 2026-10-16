"""Groq local-tool agent with strict allowlisting and FalkorDB-backed outcomes."""
import json
import os
from threading import RLock
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from .deployment import inspect_deployment, run_canary

TOOLS = [
    {"type": "function", "function": {"name": "search_memory", "description": "Retrieve prior deployment outcomes for this exact service and scenario before planning.", "parameters": {"type": "object", "properties": {"service": {"type": "string"}, "scenario": {"type": "string", "enum": ["healthy", "readiness_failure", "canary_regression", "slow_release"]}}, "required": ["service", "scenario"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "inspect_deployment", "description": "Inspect the bounded rollout plan and safety gates.", "parameters": {"type": "object", "properties": {"scenario": {"type": "string", "enum": ["healthy", "readiness_failure", "canary_regression", "slow_release"]}}, "required": ["scenario"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "run_canary", "description": "Execute a real local HTTP health probe and evaluate the synthetic canary gates. Never operates a production system.", "parameters": {"type": "object", "properties": {"scenario": {"type": "string", "enum": ["healthy", "readiness_failure", "canary_regression", "slow_release"]}}, "required": ["scenario"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "record_outcome", "description": "Persist the observed canary outcome in FalkorDB after run_canary.", "parameters": {"type": "object", "properties": {"service": {"type": "string"}, "scenario": {"type": "string"}, "decision": {"type": "string", "enum": ["promote", "rollback"]}, "success": {"type": "boolean"}, "reason": {"type": "string"}}, "required": ["service", "scenario", "decision", "success", "reason"], "additionalProperties": False}}},
]

SYSTEM = """You are a cautious deployment agent in a disposable local sandbox.
Always call search_memory before planning. Call inspect_deployment, then run_canary.
After run_canary, call record_outcome with the observed decision and a concise reason.
Never claim production access. Never invent tool results. Prefer rollback if any gate fails.
Finish with a short explanation citing retrieved memory and observed gates."""


class GroqTransport:
    def __init__(self, key, model):
        self.key, self.model = key, model
    def complete(self, messages, tools):
        body = json.dumps({"model": self.model, "messages": messages, "tools": tools,
                           "tool_choice": "auto", "parallel_tool_calls": False,
                           "temperature": 0.1, "max_completion_tokens": 700}).encode()
        request = Request("https://api.groq.com/openai/v1/chat/completions", data=body,
                          headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read())["choices"][0]["message"]
        except HTTPError as error:
            detail = error.read().decode()[:500]
            raise RuntimeError(f"Groq API returned {error.code}: {detail}") from error
        except URLError as error:
            raise RuntimeError(f"Groq API unavailable: {error.reason}") from error


class GroqAgent:
    def __init__(self, graph=None, transport=None):
        self.graph = graph
        model = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
        key = os.getenv("GROQ_API_KEY", "")
        self.transport = transport or (GroqTransport(key, model) if key else None)
        self.model = model
        self.last_canary = None
        self.lock = RLock()

    @property
    def configured(self):
        return self.transport is not None

    def search_memory(self, service, scenario):
        if self.graph is None:
            return []
        rows = self.graph.query("MATCH (o:DeploymentOutcome {service:$service, scenario:$scenario}) RETURN o.decision,o.success,o.reason,o.created_at ORDER BY o.created_at DESC LIMIT 8", {"service": service, "scenario": scenario}).result_set
        return [{"decision": row[0], "success": row[1], "reason": row[2], "created_at": row[3]} for row in rows]

    def record_outcome(self, service, scenario, decision, success, reason):
        if not self.last_canary or self.last_canary["scenario"] != scenario:
            raise ValueError("Run the canary before recording its outcome")
        if decision != self.last_canary["decision"] or success != self.last_canary["success"]:
            raise ValueError("Recorded outcome must match observed canary result")
        record = {"id": f"{service}:{scenario}:{datetime.now(timezone.utc).isoformat()}",
                  "service": service, "scenario": scenario, "decision": decision,
                  "success": success, "reason": reason, "created_at": datetime.now(timezone.utc).isoformat()}
        if self.graph is not None:
            self.graph.query("CREATE (o:DeploymentOutcome {id:$id,service:$service,scenario:$scenario,decision:$decision,success:$success,reason:$reason,created_at:$created_at})", record)
        return record

    def execute(self, name, arguments):
        if name == "search_memory": return self.search_memory(**arguments)
        if name == "inspect_deployment": return inspect_deployment(**arguments)
        if name == "run_canary":
            self.last_canary = run_canary(**arguments); return self.last_canary
        if name == "record_outcome": return self.record_outcome(**arguments)
        raise ValueError("Model requested an unknown tool")

    def run(self, goal, service, scenario):
        with self.lock:
            return self._run(goal, service, scenario)

    def _run(self, goal, service, scenario):
        if not self.configured:
            raise RuntimeError("GROQ_API_KEY is not configured")
        self.last_canary = None
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": f"Goal: {goal}\nService: {service}\nScenario fixture: {scenario}"}]
        trace = []
        used = []
        sequence = ["search_memory", "inspect_deployment", "run_canary", "record_outcome"]
        for _ in range(8):
            message = self.transport.complete(messages, TOOLS)
            messages.append(message)
            calls = message.get("tool_calls") or []
            if not calls:
                if used != sequence:
                    raise RuntimeError(f"Model ended before required tools: {sequence[len(used):]}")
                return {"answer": message.get("content") or "Completed.", "trace": trace,
                        "model": self.model, "scenario": scenario, "service": service,
                        "canary": self.last_canary}
            for call in calls:
                name = call["function"]["name"]
                arguments = json.loads(call["function"]["arguments"])
                expected = sequence[len(used)] if len(used) < len(sequence) else None
                if name != expected:
                    raise RuntimeError(f"Model requested {name}; expected {expected}")
                if arguments.get("scenario") != scenario or ("service" in arguments and arguments["service"] != service):
                    raise RuntimeError("Model attempted to change the authorized service or scenario")
                result = self.execute(name, arguments)
                used.append(name); trace.append({"tool": name, "arguments": arguments, "result": result})
                messages.append({"role": "tool", "tool_call_id": call["id"], "name": name, "content": json.dumps(result)})
        raise RuntimeError("Groq agent exceeded the eight-step tool limit")
