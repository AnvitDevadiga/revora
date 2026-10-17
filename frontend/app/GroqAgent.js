"use client";
import { useEffect, useState } from "react";
import "./agent.css";

export default function GroqAgent({ request, api }) {
  const [status, setStatus] = useState(null), [service, setService] = useState("payments"), [scenario, setScenario] = useState("canary_regression"), [result, setResult] = useState(null), [trace, setTrace] = useState([]), [busy, setBusy] = useState(false), [error, setError] = useState("");
  async function refresh() { try { const r = await request(`${api}/api/llm/status`); if (r.ok) setStatus(await r.json()); } catch { setStatus(null); } }
  useEffect(() => { refresh(); }, []);
  async function run() {
    setBusy(true); setError(""); setResult(null); setTrace([]);
    try {
      const response = await request(`${api}/api/agent/stream`, { method: "POST", headers: { "content-type": "application/json" }, timeoutMs: 240000,
        body: JSON.stringify({ goal: "Evaluate this release and make the safest deployment decision", service, scenario }) });
      if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail || `Agent failed (${response.status})`); }
      const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = "";
      const handle = event => {
        if (event.type === "tool_start") setTrace(previous => [...previous, { tool: event.tool, status: "running", arguments: event.arguments }]);
        if (event.type === "tool_result") setTrace(previous => {
          const index = previous.findLastIndex(item => item.tool === event.tool && item.status === "running");
          if (index < 0) return [...previous, { tool: event.tool, status: "complete", result: event.result }];
          const next = [...previous]; next[index] = { ...next[index], status: "complete", result: event.result }; return next;
        });
        if (event.type === "belief_revision") setTrace(previous => [...previous, { tool: "belief revision", status: "complete", result: event.result }]);
        if (event.type === "complete") setResult(event.result);
        if (event.type === "error") throw new Error(event.message || "Agent run failed");
      };
      while (true) {
        const { value, done } = await reader.read(); if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n"); buffer = frames.pop() || "";
        for (const frame of frames) { const data = frame.split("\n").find(line => line.startsWith("data: "))?.slice(6); if (data) handle(JSON.parse(data)); }
      }
      if (buffer.trim()) { const data = buffer.split("\n").find(line => line.startsWith("data: "))?.slice(6); if (data) handle(JSON.parse(data)); }
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  return <section className="panel lab" aria-labelledby="groq-title">
    <div className="panelhead"><h2 id="groq-title">Groq reasoning agent</h2><span>{status?.configured ? `${status.model} · ready` : "api key required"}</span></div>
    <p>The model must retrieve FalkorDB memory, inspect the rollout, execute a disposable HTTP canary, and save the observed outcome.</p>
    {!status?.configured && <p className="muted">Add <code>GROQ_API_KEY</code> to the project’s root <code>.env</code>, then rebuild the backend. Never paste the key into this page or commit it.</p>}
    <div className="grid">
      <div>
        <label htmlFor="groq-service">Deployment target</label><input id="groq-service" value={service} onChange={e => setService(e.target.value)} />
        <label htmlFor="groq-scenario">Canary scenario</label><select id="groq-scenario" value={scenario} onChange={e => setScenario(e.target.value)}>
          <option value="healthy">Healthy release</option><option value="readiness_failure">Readiness endpoint failure</option><option value="canary_regression">Latency and error regression</option><option value="slow_release">Slow release</option>
        </select>
        <div className="actions"><button className="primary" onClick={run} disabled={busy || !status?.configured}>{busy ? "agent is using tools…" : "run deployment agent"}</button></div>
      </div>
      <div aria-live="polite">{error && <p role="alert" className="error">{error}</p>}{result ? <><h3 className={result.canary.decision === "promote" ? "decision-promote" : "decision-rollback"}>{result.canary.decision === "promote" ? "promote release" : "roll back release"}</h3><p>{result.answer}</p><p className="muted">{result.canary.execution_mode}</p></> : <p className="muted">{busy ? `executing required tools · ${trace.filter(item => item.status === "complete").length}/5 complete` : "five ordered tools. every result is observed and saved."}</p>}</div>
    </div>
    {(trace.length > 0 || busy) && <div className="tool-trace live-feed" aria-label="Live agent execution feed" aria-live="polite">
      <div className="feed-title"><span className="section-kicker">live execution feed</span>{busy && <span className="feed-pulse">running</span>}</div>
      {trace.map((step, i) => <article className={`feed-step ${step.status}`} key={`${step.tool}-${i}`}>
        <div className="feed-step-head"><i aria-hidden="true" /><strong>{step.tool.replaceAll("_", " ")}</strong><small>{step.status === "running" ? "running" : "complete"}</small></div>
        {step.result && <pre>{JSON.stringify(step.result, null, 2)}</pre>}
      </article>)}
    </div>}
  </section>;
}
