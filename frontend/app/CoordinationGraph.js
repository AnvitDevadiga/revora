"use client";
import { useEffect, useMemo, useState } from "react";
import "./coordination-graph.css";

// One column per agent stage, left to right, so a handoff reads like a pipeline.
const COLUMNS = [
  { type: "Component", label: "component", agent: "shipped dependency", x: 90 },
  { type: "ReleaseIncident", label: "incident", agent: "release controller", x: 400 },
  { type: "AgentHandoff", label: "handoff", agent: "release analyst", x: 630 },
  { type: "EvidenceReview", label: "review", agent: "evidence reviewer", x: 860 },
  { type: "CoordinationDecision", label: "decision", agent: "recovery planner · llm", x: 1090 },
];
const COLUMN = Object.fromEntries(COLUMNS.map(column => [column.type, column]));
const ROW = 64, TOP = 70, MAX_INCIDENTS = 6;

// The exact Cypher (from backend/app) that writes or reads each node type.
const CYPHER = {
  ReleaseIncident: `MERGE (i:ReleaseIncident {id:$id})
SET i.service=$service, i.version=$version, i.failed=$failed,
    i.decision=$decision, i.error_rate=$error_rate,
    i.p95_latency_ms=$p95_latency_ms, i.sample_count=$sample_count
WITH i OPTIONAL MATCH (prior:ReleaseIncident {id:$prior_id})
FOREACH (_ IN CASE WHEN prior IS NULL THEN [] ELSE [1] END |
  MERGE (i)-[:BASED_ON]->(prior))

// memory gate read before any candidate traffic:
MATCH (i:ReleaseIncident {service:$service, version:$version})
WHERE i.decision IN ['rollback','promote']
RETURN i.id, i.timestamp, i.failed
ORDER BY i.timestamp DESC LIMIT 1`,
  AgentHandoff: `MATCH (i:ReleaseIncident {id:$incident_id, service:$service, version:$version})
CREATE (h:AgentHandoff {id:$id, service:$service, version:$version,
  incident_id:$incident_id, payload:$payload})
CREATE (i)-[:HANDED_OFF]->(h)`,
  EvidenceReview: `MATCH (i:ReleaseIncident {id:$incident_id})-[:HANDED_OFF]->(h:AgentHandoff {id:$handoff_id})
CREATE (r:EvidenceReview {id:$review_id, agent:'evidence_reviewer', status:$review_status})
CREATE (h)-[:REVIEWED_BY]->(r)`,
  CoordinationDecision: `CREATE (d:CoordinationDecision {id:$id, agent:'recovery_planner',
  action:$action, review_status:$review_status, execution_status:$execution_status})
CREATE (r)-[:FORWARDED_TO]->(d)
CREATE (h)-[:HANDED_OFF_TO]->(d) CREATE (d)-[:BASED_ON]->(i)
// + RETRIEVED -> prior handoff, EXECUTED -> recovery outcome`,
};
CYPHER.Component = `// written with each incident, in the same mutation:
FOREACH (key IN $components |
  MERGE (c:Component {key:key}) MERGE (i)-[:USES]->(c))

// multi-hop memory gate: block a new version that shares a component
// with a failed release, unless that component ever shipped healthy
MATCH (c:Component) WHERE c.key IN $components
MATCH (c)<-[:USES]-(bad:ReleaseIncident {service:$service,
                                        decision:'rollback', failed:true})
WHERE bad.version <> $version
  AND NOT (c)<-[:USES]-(:ReleaseIncident {service:$service, decision:'promote'})
RETURN c.key, bad.id, bad.version`;
CYPHER.CoordinationDecision += `
// planner_source, guardrail, proposed_action record the LLM proposal
// and whether the evidence guardrail accepted or vetoed it`;
const READ_QUERY = `MATCH (i:ReleaseIncident {service:$service})
WITH i ORDER BY i.timestamp DESC LIMIT $limit
OPTIONAL MATCH (i)-[:HANDED_OFF|REVIEWED_BY|FORWARDED_TO|HANDED_OFF_TO
                   |BASED_ON|EXECUTED|RETRIEVED*1..4]-(n)
WITH collect(DISTINCT i) AS seeds, collect(DISTINCT n) AS reached
WITH seeds + [n IN reached WHERE NOT n IN seeds] AS ns
UNWIND ns AS a OPTIONAL MATCH (a)-[r]->(b) WHERE b IN ns
RETURN ns, collect(DISTINCT r)`;

const tone = node => node.type === "Component" ? "component"
  : node.type === "CoordinationDecision" && node.guardrail === "vetoed" ? "blocked"
  : node.type !== "ReleaseIncident" ? "agent"
  : node.status === "blocked_from_memory" ? "blocked"
    : node.status === "promote" ? "ok" : "fail";
const shortId = id => (id || "").slice(0, 8);
const title = node => node.type === "Component" ? node.version
  : node.type === "ReleaseIncident" ? `${node.version} · ${node.status === "blocked_from_memory" ? "memory block" : (node.status || "").replaceAll("_", " ")}`
  : (node.status || node.agent || COLUMN[node.type]?.label || "").replaceAll("_", " ");

function layoutGraph(graph) {
  const incidents = graph.nodes.filter(node => node.type === "ReleaseIncident")
    .sort((a, b) => (b.timestamp || "").localeCompare(a.timestamp || "")).slice(0, MAX_INCIDENTS);
  const row = new Map(incidents.map((node, index) => [node.id, index]));
  // Pipeline nodes take the row of the incident they descend from (BASED_ON is drawn separately).
  const flow = graph.edges.filter(edge => edge.relation !== "BASED_ON" && edge.relation !== "EXECUTED")
    .sort((a, b) => (row.get(a.source) ?? 99) - (row.get(b.source) ?? 99));
  for (let pass = 0; pass < 4; pass++) flow.forEach(edge => {
    if (row.has(edge.source) && !row.has(edge.target) && COLUMN[graph.nodes.find(n => n.id === edge.target)?.type]) row.set(edge.target, row.get(edge.source));
  });
  const slots = {};
  const nodes = graph.nodes.filter(node => row.has(node.id) && COLUMN[node.type]).map(node => {
    const key = `${node.type}:${row.get(node.id)}`;
    const offset = slots[key] = (slots[key] ?? -1) + 1;
    return { ...node, x: COLUMN[node.type].x + offset * 14, y: TOP + row.get(node.id) * ROW + offset * 8 };
  });
  // Components are shared across releases, so stack them in their own column order.
  nodes.filter(node => node.type === "Component").sort((a, b) => a.y - b.y)
    .forEach((node, index) => { node.x = COLUMN.Component.x; node.y = TOP + index * ROW; });
  const byId = Object.fromEntries(nodes.map(node => [node.id, node]));
  const edges = graph.edges.filter(edge => byId[edge.source] && byId[edge.target] && edge.source !== edge.target)
    .filter(edge => !(edge.relation === "BASED_ON" && byId[edge.source].type === "CoordinationDecision"));
  const components = nodes.filter(node => node.type === "Component").length;
  return { nodes, edges, byId, height: TOP + Math.max(1, incidents.length, components) * ROW };
}

function edgePath(from, to, relation) {
  if (relation === "BASED_ON") {
    // Memory links between incidents arc out to the left of the incident column.
    const bend = Math.min(120, 50 + Math.abs(to.y - from.y) * 0.3);
    return `M${from.x - 70} ${from.y} C${from.x - 70 - bend} ${from.y}, ${to.x - 70 - bend} ${to.y}, ${to.x - 70} ${to.y}`;
  }
  const mid = (from.x + to.x) / 2;
  if (to.x < from.x) return `M${from.x - 70} ${from.y} C${mid} ${from.y}, ${mid} ${to.y}, ${to.x + 70} ${to.y}`;
  return `M${from.x + 70} ${from.y} C${mid} ${from.y}, ${mid} ${to.y}, ${to.x - 70} ${to.y}`;
}

export default function CoordinationGraph({ request, api, refreshKey }) {
  const [graph, setGraph] = useState({ nodes: [], edges: [] });
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState("");
  const [seen, setSeen] = useState(() => new Set());
  const [fresh, setFresh] = useState(() => new Set());

  async function refresh() {
    try {
      const response = await request(`${api}/api/coordination/graph?limit=${MAX_INCIDENTS}`);
      if (response.status === 503) throw new Error("FalkorDB offline — retrying");
      if (!response.ok) throw new Error(`coordination graph unavailable (${response.status})`);
      const data = await response.json();
      setGraph(data); setError("");
      setSeen(previous => {
        const arrived = data.nodes.filter(node => !previous.has(node.id)).map(node => node.id);
        if (previous.size) setFresh(new Set(arrived));
        return new Set([...previous, ...arrived]);
      });
    } catch (failure) { setError(failure.message); }
  }
  useEffect(() => {
    let alive = true, timer;
    const poll = async () => { if (alive) await refresh(); if (alive) timer = setTimeout(poll, 5000); };
    poll(); return () => { alive = false; clearTimeout(timer); };
  }, [refreshKey]);

  const layout = useMemo(() => layoutGraph(graph), [graph]);
  const focus = selected && layout.byId[selected.id];
  const linked = useMemo(() => {
    if (!focus) return null;
    const ids = new Set([focus.id]);
    layout.edges.forEach(edge => { if (edge.source === focus.id) ids.add(edge.target); if (edge.target === focus.id) ids.add(edge.source); });
    return ids;
  }, [focus, layout]);
  const memoryLinks = layout.edges.filter(edge => edge.relation === "BASED_ON").length;

  return <section className="coord-graph" aria-labelledby="coord-graph-title">
    <div className="coord-head">
      <div><span className="section-kicker">falkordb · graph revora · live</span><h3 id="coord-graph-title">agent coordination graph</h3>
        <p className="muted">Every incident, handoff, review and decision is a node. Dashed arcs are <code>BASED_ON</code> memory links; amber <code>USES</code> edges connect releases to the components they ship, so a new version that shares a failed component is blocked before it serves traffic.</p></div>
      <div className="coord-stats"><span><strong>{graph.nodes.length}</strong> nodes</span><span><strong>{graph.edges.length}</strong> edges</span><span className="memory"><strong>{memoryLinks}</strong> memory links</span></div>
    </div>
    {error && <p className="coord-error" role="status"><i aria-hidden="true" />{error}</p>}
    <div className={`coord-body${focus ? " has-inspector" : ""}`}>
      {layout.nodes.length ? <svg className="coord-svg" viewBox={`0 0 1180 ${layout.height}`} role="img" aria-label="FalkorDB agent coordination graph">
        <defs>
          <marker id="coord-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#5d7088" /></marker>
          <marker id="coord-arrow-memory" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#c29aff" /></marker>
        </defs>
        {COLUMNS.map(column => <g key={column.type} className="coord-column"><text x={column.x} y="22">{column.label}</text><text className="agent" x={column.x} y="38">{column.agent}</text></g>)}
        {layout.edges.map((edge, index) => {
          const from = layout.byId[edge.source], to = layout.byId[edge.target];
          const memory = edge.relation === "BASED_ON";
          const dim = linked && !(linked.has(edge.source) && linked.has(edge.target));
          return <g key={`${edge.source}-${edge.relation}-${edge.target}`} className={`coord-edge${memory ? " memory" : ""}${edge.relation === "USES" ? " uses" : ""}${dim ? " dim" : ""}`} style={{ "--i": index }}>
            <path d={edgePath(from, to, edge.relation)} pathLength="100" markerEnd={`url(#${memory ? "coord-arrow-memory" : "coord-arrow"})`} />
            {memory && <text x={Math.min(from.x, to.x) - 120} y={(from.y + to.y) / 2 + 4}>BASED_ON</text>}
          </g>;
        })}
        {layout.nodes.map(node => <g key={node.id} tabIndex="0" role="button" aria-label={`${node.type} ${title(node)}`}
          className={`coord-node ${tone(node)}${fresh.has(node.id) ? " fresh" : ""}${focus?.id === node.id ? " selected" : ""}${linked && !linked.has(node.id) ? " dim" : ""}`}
          onClick={() => setSelected(focus?.id === node.id ? null : node)}
          onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelected(node); } }}>
          <rect x={node.x - 70} y={node.y - 20} width="140" height="40" rx="10" />
          <circle cx={node.x - 56} cy={node.y} r="4" />
          <text x={node.x - 46} y={node.y - 2}>{title(node).slice(0, 18)}</text>
          <text className="id" x={node.x - 46} y={node.y + 12}>{node.type === "Component" ? "dependency" : shortId(node.id)}</text>
        </g>)}
      </svg> : <div className="coord-empty"><strong>no coordination memory yet</strong><p>Introduce a failure and run the recovery. The incident, analyst handoff, review and planner decision will appear here as FalkorDB nodes.</p></div>}
      {focus && <aside className="coord-inspector" aria-label="node inspector">
        <div className="inspector-head"><span className={`badge ${tone(focus)}`}>{focus.type}</span><button onClick={() => setSelected(null)} aria-label="close inspector">×</button></div>
        <dl>{Object.entries(focus).filter(([key, value]) => !["x", "y", "type"].includes(key) && value != null && value !== "").map(([key, value]) =>
          <div key={key}><dt>{key}</dt><dd>{typeof value === "number" && key === "error_rate" ? `${Math.round(value * 100)}%` : String(value)}</dd></div>)}</dl>
        <div className="inspector-links"><span className="section-kicker">edges</span>{layout.edges.filter(edge => edge.source === focus.id || edge.target === focus.id).map(edge =>
          <button key={`${edge.source}-${edge.relation}-${edge.target}`} onClick={() => setSelected(layout.byId[edge.source === focus.id ? edge.target : edge.source])}>
            {edge.source === focus.id ? "→" : "←"} <b>{edge.relation}</b> {(other => `${other.type} ${other.type === "Component" ? other.version : shortId(other.id)}`)(layout.byId[edge.source === focus.id ? edge.target : edge.source])}</button>)}</div>
        <span className="section-kicker">cypher that wrote this node</span>
        <pre className="cypher">{CYPHER[focus.type]}</pre>
      </aside>}
    </div>
    <details className="coord-query"><summary>query powering this view</summary><pre className="cypher">{READ_QUERY}</pre></details>
  </section>;
}
