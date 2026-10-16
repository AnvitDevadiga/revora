from types import SimpleNamespace
import pytest
from app.engine import MemoryEngine


def test_non_default_workspace_is_rejected_before_write():
    engine = MemoryEngine()
    with pytest.raises(ValueError):
        engine.ingest("Friday deploy", "failed", False, tenant_id="other")
    assert engine.episodes == []


def test_monday_is_not_friday():
    result = MemoryEngine().ingest("Monday deploy failed", "rollback", False)
    assert "Monday" in result["decision"]["action"]
    assert "Friday" not in result["decision"]["action"]


def test_unrelated_memory_does_not_recommend_a_deployment():
    result = MemoryEngine().ingest("Customer refund", "resolved", True)
    assert result["decision"]["action"].startswith("review")


def test_reload_keeps_belief_identity_and_restores_procedure():
    class Graph:
        def query(self, query, params=None):
            if "RETURN b.id, b.claim" in query:
                rows = [["belief-1", "Deploy on Friday is reliable", .26, "revised"]]
            elif "RETURN b.id, e.id" in query:
                rows = [["belief-1", "old-episode", None, "CONTRADICTS", "rollback", .24]]
            elif "RETURN p.id" in query:
                rows = [["plan-1", "deploy", "avoid", "check|review", "2026-10-15"]]
            else:
                rows = []
            return SimpleNamespace(result_set=rows)
    engine = MemoryEngine(graph=Graph())
    result = engine.ingest("Friday deploy succeeded", "healthy", True)
    assert result["belief"]["id"] == "belief-1"
    assert len(result["belief"]["evidence"]) == 2
    assert engine.procedures[0]["steps"] == ["check", "review"]
