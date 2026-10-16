import json
import pytest
from app.groq_agent import GroqAgent
from app.deployment import evaluate_deployments, run_canary
from app import deployment


@pytest.fixture(autouse=True)
def no_socket(monkeypatch):
    monkeypatch.setattr(deployment, "probe_status", lambda status: (status, 0.2))


class ScriptedTransport:
    def __init__(self):
        self.step = 0
    def complete(self, messages, tools):
        script = [
            ("search_memory", {"service": "payments", "scenario": "canary_regression"}),
            ("inspect_deployment", {"scenario": "canary_regression"}),
            ("run_canary", {"scenario": "canary_regression"}),
            ("record_outcome", {"service": "payments", "scenario": "canary_regression", "decision": "rollback", "success": False, "reason": "Latency and error gates failed"}),
        ]
        if self.step < len(script):
            name, args = script[self.step]; self.step += 1
            return {"role": "assistant", "content": None, "tool_calls": [{"id": f"call-{self.step}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
        return {"role": "assistant", "content": "Rollback: canary latency and error gates failed."}


def test_llm_agent_runs_required_memory_and_deployment_tools():
    agent = GroqAgent(transport=ScriptedTransport())
    result = agent.run("Deploy safely", "payments", "canary_regression")
    assert [step["tool"] for step in result["trace"]] == ["search_memory", "inspect_deployment", "run_canary", "record_outcome"]
    assert result["canary"]["decision"] == "rollback"
    assert result["answer"].startswith("Rollback")


def test_record_cannot_forge_observed_canary():
    agent = GroqAgent(transport=ScriptedTransport())
    agent.last_canary = run_canary("healthy")
    with pytest.raises(ValueError):
        agent.record_outcome("payments", "healthy", "rollback", False, "forged")


def test_unfamiliar_deployment_fixtures_cover_distinct_failures():
    report = evaluate_deployments()
    assert report["correct"] == report["count"] == 4
    by_name = {case["scenario"]: case for case in report["cases"]}
    assert by_name["readiness_failure"]["gates"]["health"] is False
    assert by_name["canary_regression"]["gates"]["errors"] is False
    assert by_name["slow_release"]["gates"]["latency"] is False


def test_model_cannot_skip_memory_or_change_authorized_context():
    class BadOrder:
        def complete(self, *_):
            return {"role": "assistant", "tool_calls": [{"id": "bad", "type": "function", "function": {"name": "run_canary", "arguments": '{"scenario":"healthy"}'}}]}
    with pytest.raises(RuntimeError, match="expected search_memory"):
        GroqAgent(transport=BadOrder()).run("Deploy", "payments", "healthy")

    class WrongService:
        def complete(self, *_):
            return {"role": "assistant", "tool_calls": [{"id": "bad", "type": "function", "function": {"name": "search_memory", "arguments": '{"service":"other","scenario":"healthy"}'}}]}
    with pytest.raises(RuntimeError, match="authorized service"):
        GroqAgent(transport=WrongService()).run("Deploy", "payments", "healthy")
