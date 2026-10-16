"use client";
import { useEffect, useState } from "react";

export default function GroqAgent({ request, api }) {
  const [status, setStatus] = useState(null), [service, setService] = useState("payments"), [scenario, setScenario] = useState("canary_regression"), [result, setResult] = useState(null), [busy, setBusy] = useState(false), [error, setError] = useState("");
  async function refresh() { try { const r = await request(`${api}/api/llm/status`); if (r.ok) setStatus(await r.json()); } catch { setStatus(null); } }
  useEffect(() => { refresh(); }, []);
  async function run() { setBusy(true); setError(""); setResult(null); try { const r = await request(`${api}/api/llm/run`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ goal: "Evaluate this release and make the safest deployment decision", service, scenario }) }); const body = await r.json(); if (!r.ok) throw new Error(body.detail || `Agent failed (${r.status})`); setResult(body); } catch (e) { setError(e.message); } finally { setBusy(false); } }
  return <section className="panel lab" aria-labelledby="groq-title"><div className="panelhead"><h2 id="groq-title">Groq reasoning agent</h2><span>{status?.configured ? `${status.model} · ready` : "API key required"}</span></div><p>The model must retrieve FalkorDB memory, inspect the rollout, execute a disposable HTTP canary, and save the observed outcome.</p>
    {!status?.configured && <p className="muted">Add <code>GROQ_API_KEY</code> to the project’s root <code>.env</code>, then rebuild the backend. Never paste the key into this page or commit it.</p>}
    <div className="grid"><div><label htmlFor="groq-service">Service</label><input id="groq-service" value={service} onChange={e => setService(e.target.value)} /><label htmlFor="groq-scenario">Unfamiliar deployment condition</label><select id="groq-scenario" value={scenario} onChange={e => setScenario(e.target.value)}><option value="healthy">Healthy release</option><option value="readiness_failure">Readiness endpoint failure</option><option value="canary_regression">Latency and error regression</option><option value="slow_release">Slow release</option></select><button onClick={run} disabled={busy || !status?.configured}>{busy ? "Agent is using tools…" : "Run Groq deployment agent"}</button></div>
    <div aria-live="polite">{error && <p role="alert" className="error">{error}</p>}{result ? <><h3>{result.canary.decision === "promote" ? "Promote" : "Rollback"}</h3><p>{result.answer}</p><p className="muted">{result.canary.execution_mode}</p></> : <p>The model has no shell or production access. Every allowed tool call will appear here.</p>}</div></div>
    {result && <div className="tool-trace">{result.trace.map((step, i) => <article className="belief-card" key={`${step.tool}-${i}`}><strong>{i + 1}. {step.tool}</strong><span>{JSON.stringify(step.result)}</span></article>)}</div>}
  </section>;
}
