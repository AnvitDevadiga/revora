"""Run against a disposable FalkorDB, never the user's saved graph."""
import os
from uuid import uuid4
from falkordb import FalkorDB
from app.laboratory import Laboratory

graph = FalkorDB.from_url(os.environ["FALKORDB_URL"]).select_graph("audit_" + uuid4().hex)
first = Laboratory(graph).run("deployment", "integration")
second = Laboratory(graph).run("deployment", "integration")
assert first == second, "Fresh engine must reload identical persisted result"
assert graph.query("MATCH (o:LabObservation) RETURN count(o)").result_set[0][0] == 5
assert graph.query("MATCH (v:LabRevision) RETURN count(v)").result_set[0][0] == 5
assert graph.query("MATCH (:LabObservation)-[:TRIGGERED]->(:LabRevision) RETURN count(*)").result_set[0][0] == 5
assert second["decision"]["action"] == "canary"
assert sum(not e["success"] for e in second["events"]) == 2
print("PASS: graph persistence, replay idempotency, revision links, failure retention")
