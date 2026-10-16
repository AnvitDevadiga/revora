"""Integration acceptance checks against an explicitly supplied test DB."""
import os
from uuid import uuid4
from falkordb import FalkorDB
from app.runtime import Runtime

graph = FalkorDB.from_url(os.environ["FALKORDB_URL"]).select_graph("runtime_acceptance_" + uuid4().hex)
request = {"request_id": "first", "service": "contacts", "environment": "sandbox", "workload": "import", "values": ["a", "a", "b"]}
first = Runtime(graph).run(request)
assert len(first["events"]) == 2 and first["success"]
assert Runtime(graph).run(request) == first
second = Runtime(graph).run({**request, "request_id": "second"})
assert len(second["events"]) == 1 and second["success"]
assert second["initial"]["evidence"] == ["first:1"]
assert graph.query("MATCH (o:AgentObservation) RETURN count(o)").result_set[0][0] == 3
assert graph.query("MATCH (o:AgentObservation {success:false}) RETURN count(o)").result_set[0][0] == 1
assert graph.query("MATCH (d:AgentDecision {id:'second:0'})-[:BASED_ON]->(o) RETURN o.id").result_set == [["first:1"]]
assert graph.query("MATCH (v:BeliefVersion) RETURN count(v)").result_set[0][0] == 3
print("PASS: real execution, restart learning, evidence traversal, failure retention, revisions, idempotency")
