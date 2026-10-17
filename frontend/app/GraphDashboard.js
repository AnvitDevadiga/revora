"use client";
import { useEffect, useMemo, useState } from "react";
import "./memory-dashboard.css";

const palette = { Experience: "#ff9378", Evidence: "#82a9ff", Belief: "#54e0b3" };
const short = (value, size = 27) => (value || "").length > size ? `${value.slice(0, size - 1)}…` : value || "";

function GraphCanvas({ graph }) {
  const [hover, setHover] = useState(null);
  const layout = useMemo(() => {
    const groups = { Experience: [], Evidence: [], Belief: [] };
    graph.nodes.filter(node => groups[node.type]).forEach(node => groups[node.type].push(node));
    const x = { Experience: 165, Evidence: 500, Belief: 835 };
    const positioned = {};
    Object.entries(groups).forEach(([type, items]) => items.slice(0, 8).forEach((node, index) => {
      const count = Math.min(items.length, 8);
      positioned[node.id] = { ...node, x: x[type], y: count === 1 ? 220 : 52 + (index * 336) / (count - 1) };
    }));
    const edges = graph.edges.filter(edge => positioned[edge.source] && positioned[edge.target]);
    return { nodes: Object.values(positioned), edges };
  }, [graph]);
  const connected = useMemo(() => {
    if (!hover) return null;
    const ids = new Set([hover]);
    graph.edges.forEach(edge => { if (edge.source === hover) ids.add(edge.target); if (edge.target === hover) ids.add(edge.source); });
    return ids;
  }, [hover, graph]);
  const beliefNodes = layout.nodes.filter(item => item.type === "Belief");
  const pulseId = beliefNodes.length ? beliefNodes[beliefNodes.length - 1].id : null;

  if (!graph.nodes.length) return <div className="graph-empty"><div className="empty-orbit" aria-hidden="true"><span>r</span></div><strong>your evidence graph will appear here</strong><p>Run a deployment agent. Its experience, evidence, and beliefs will connect here.</p></div>;

  return <svg className="memory-graph" viewBox="0 0 1000 440" role="img" aria-label="FalkorDB experience, evidence, and belief graph">
    <defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#53667c" /></marker></defs>
    <g className="graph-columns"><text x="165" y="24">experiences</text><text x="500" y="24">evidence</text><text x="835" y="24">beliefs</text></g>
    {layout.edges.map((edge, index) => {
      const from = layout.nodes.find(n => n.id === edge.source), to = layout.nodes.find(n => n.id === edge.target);
      const stroke = edge.relation === "CONTRADICTS" ? "#ff756d" : edge.relation === "UPDATES" ? "#c29aff" : "#53667c";
      const dim = hover && edge.source !== hover && edge.target !== hover;
      return <g key={`${edge.source}-${edge.relation}-${edge.target}-${index}`} className={`graph-edge${edge.relation === "CONTRADICTS" ? " contradicts" : ""}${dim ? " dimmed" : ""}`} style={{ "--i": index }}>
        <path className="edge-path" pathLength="100" d={`M${from.x + 72} ${from.y} C${(from.x + to.x) / 2} ${from.y}, ${(from.x + to.x) / 2} ${to.y}, ${to.x - 72} ${to.y}`} fill="none" stroke={stroke} strokeWidth="1.6" markerEnd="url(#arrow)" />
        <text className={`edge-label ${edge.relation.toLowerCase()}`} x={(from.x + to.x) / 2} y={(from.y + to.y) / 2 - 8}>{edge.relation}</text>
      </g>;
    })}
    {layout.nodes.map((node, index) => <g key={node.id} className={`graph-node${node.id === pulseId ? " pulse" : ""}${hover && connected && !connected.has(node.id) ? " dimmed" : ""}`} tabIndex="0" style={{ "--i": index }} onMouseEnter={() => setHover(node.id)} onMouseLeave={() => setHover(null)} onFocus={() => setHover(node.id)} onBlur={() => setHover(null)}>
      <title>{node.type}: {node.label}{node.status ? ` · ${node.status}` : ""}{node.confidence_score != null ? ` · confidence ${node.confidence_score}` : ""}</title>
      {node.id === pulseId && <circle className="node-halo" cx={node.x} cy={node.y} r="30" />}
      <rect x={node.x - 73} y={node.y - 23} width="146" height="46" rx="12" fill="#111a25" stroke={node.status === "revised" || node.status === "stale" ? "#c29aff" : palette[node.type]} strokeWidth="1.5" />
      <circle cx={node.x - 58} cy={node.y} r="4" fill={node.outcome === "failure" ? "#ff756d" : palette[node.type]} />
      <text x={node.x - 48} y={node.y + 4}>{short(node.label, 20)}</text>
      <text className="node-sub" x={node.x - 58} y={node.y + 38}>{node.status || node.outcome || node.type}</text>
    </g>)}
  </svg>;
}

export default function GraphDashboard({ request, api }) {
  const [graph, setGraph] = useState({ nodes: [], edges: [] });
  const [history, setHistory] = useState([]);
  const [error, setError] = useState("");
  const [updated, setUpdated] = useState(null);
  async function refresh() {
    try {
      const [graphResponse, historyResponse] = await Promise.all([
        request(`${api}/api/graph/state`), request(`${api}/api/memories/history?limit=30`),
      ]);
      if (!graphResponse.ok || !historyResponse.ok) throw new Error("Memory graph could not be loaded");
      const graphData = await graphResponse.json(), historyData = await historyResponse.json();
      setGraph(graphData); setHistory(historyData.items || []); setUpdated(new Date()); setError("");
    } catch (e) { setError(e.message); }
  }
  useEffect(() => {
    let alive = true, timer;
    const poll = async () => { if (alive) await refresh(); if (alive) timer = setTimeout(poll, 7000); };
    poll(); return () => { alive = false; clearTimeout(timer); };
  }, []);
  const revisions = graph.nodes.filter(node => node.type === "Belief");
  const staleCount = revisions.filter(node => node.status === "stale" || node.status === "revised").length;
  const activeCount = revisions.filter(node => node.status === "active").length;
  return <section className="memory-dashboard" aria-labelledby="memory-title">
    <div className="memory-heading">
      <div><div className="section-kicker">the memory engine</div><h2 id="memory-title">experience becomes evidence.</h2><p>Every outcome is connected to the belief it supports or changes.</p></div>
      <div className="live-indicator"><i aria-hidden="true" />live graph<span>{updated ? `synced ${updated.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "connecting"}</span></div>
    </div>
    {error && <p role="status" className="dashboard-error">{error}</p>}
    <div className="memory-layout">
      <section className="graph-card" aria-label="evidence graph visualization">
        <div className="graph-card-head"><div><span className="section-kicker">falkordb / revora memory</span><h3>connected evidence</h3><span className="graph-subtitle">Latest paths · up to 8 nodes per type shown</span></div><span className="graph-count">{graph.nodes.length} nodes <i>·</i> {graph.edges.length} links</span></div>
        <div className="graph-legend"><span><i className="legend-experience" aria-hidden="true" /> experience</span><span><i className="legend-evidence" aria-hidden="true" /> evidence</span><span><i className="legend-belief" aria-hidden="true" /> active belief</span><span><i className="legend-conflict" aria-hidden="true" /> contradiction</span></div>
        <GraphCanvas graph={graph} />
      </section>
      <aside className="belief-sidebar">
        <div className="belief-summary"><span className="section-kicker">belief state</span><strong>{revisions.length}</strong><span>belief versions</span><div className="belief-state-foot"><span>{activeCount} active</span><span>{staleCount} revised / stale</span></div></div>
        <div className="history-card"><div className="section-kicker">recent experience</div><h3>what the agent learned</h3>
          {history.length ? history.slice(0, 6).map(item => <article className="history-item" key={item.experience_id}>
            <span className={`history-dot ${item.outcome === "failure" ? "failure" : "success"}`} aria-hidden="true" />
            <div><strong>{short(item.action_taken, 68)}</strong><p>{item.description}</p><small>{item.contradicted_belief ? "contradicts prior belief" : item.relation || "linked"} · {item.belief_status || "belief recorded"}{item.confidence_score != null ? ` · ${Math.round(item.confidence_score * 100)}% confidence` : ""}</small><time>{item.timestamp ? new Date(item.timestamp).toLocaleString() : ""}</time></div>
          </article>) : <p className="history-empty">no agent experiences yet. start a run to create the first memory.</p>}
        </div>
      </aside>
    </div>
  </section>;
}
