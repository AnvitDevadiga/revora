"use client";
import { useEffect, useState } from "react";
import "./release-ops.css";

export default function ReleaseOps({ request, api }) {
  const [version, setVersion] = useState("");
  const [mode, setMode] = useState("errors");
  const [live, setLive] = useState(null);
  const [traffic, setTraffic] = useState(null);
  const [result, setResult] = useState(null);
  const [history, setHistory] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [coordination, setCoordination] = useState(null);

  async function loadLive() {
    const response = await request(`${api}/api/release/live`);
    if (!response.ok) throw new Error(`live service unavailable (${response.status})`);
    setLive(await response.json());
  }
  async function loadHistory() {
    const response = await request(`${api}/api/release/history`);
    if (response.ok) setHistory((await response.json()).items);
  }
  async function probeTraffic() {
    const response = await request(`${api}/api/release/traffic`);
    const body = await response.json();
    setTraffic({ status: response.status, ...body });
  }
  useEffect(() => {
    setVersion(`demo-${Date.now().toString(36)}`);
    loadLive().catch(() => {});
    probeTraffic().catch(() => {});
    loadHistory().catch(() => {});
  }, []);

  async function run(forceRecheck = false) {
    setBusy(true); setError("");
    try {
      const response = await request(`${api}/api/release/run`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ version, mode: live?.target_mode === "configured_services" ? "healthy" : mode, force_recheck: forceRecheck }), timeoutMs: 30000,
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || `run failed (${response.status})`);
      setResult(body.recovery || body); setCoordination(body.recovery ? body : null);
      await loadLive();
      await probeTraffic();
      await loadHistory();
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }

  async function stage(path, payload) {
    setBusy(true); setError("");
    try {
      const response = await request(`${api}${path}`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify(payload), timeoutMs: 30000,
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || `action failed (${response.status})`);
      setResult(body.recovery || body); setCoordination(body.recovery ? body : null);
      await loadLive();
      await probeTraffic();
      await loadHistory();
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }

  async function park() {
    setBusy(true); setError("");
    try {
      const response = await request(`${api}/api/release/park`, { method: "POST" });
      if (!response.ok) throw new Error(`could not park traffic (${response.status})`);
      setLive(await response.json());
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }

  async function handoff() {
    if (!result?.id) return;
    setBusy(true); setError("");
    try {
      const response = await request(`${api}/api/coordination/run`, { method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ incident_id: result.id, use_memory: true }) });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || `handoff failed (${response.status})`);
      setCoordination(body);
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }

  const outcome = result?.decision === "introduced" ? "incident active — service is failing"
    : result?.decision === "blocked_from_memory" ? "known regression blocked"
    : result?.decision === "rollback" ? "live service recovered" : "candidate promoted";

  const route = live?.active_route;
  const gatewayOk = live?.gateway_probe?.status === 200;
  const blocked = result?.decision === "blocked_from_memory";
  const stableState = !live ? "idle" : route === "candidate" ? "idle" : gatewayOk ? "flowing" : "fault";
  const candidateState = !live ? "idle" : route === "candidate" ? (gatewayOk ? "flowing" : "fault") : blocked ? "blocked" : "idle";
  const stableLabel = !live ? "loading…" : stableState === "flowing" ? "receiving traffic" : stableState === "fault" ? "serving failures" : "standby";
  const candidateLabel = !live ? "loading…" : candidateState === "flowing" ? "receiving traffic" : candidateState === "fault" ? `serving HTTP ${live.gateway_probe.status}` : candidateState === "blocked" ? "retired for this version" : "not routed";
  const phase = result?.decision === "introduced" ? 2 : result?.id ? 3 : 1;

  return <section className="panel release-ops" id="release-ops" aria-labelledby="release-title">
    <div className="panelhead"><h2 id="release-title">a live incident. a measured recovery.</h2><span>real HTTP traffic · FalkorDB memory</span></div>
    <p>Break the project-owned service, see the real HTTP failure stay live, then let revora diagnose and restore it. The previous failure becomes graph memory that blocks the same bad version next time.</p>
    <p className="muted">{live?.target_mode === "configured_services" ? "Operator-configured services: park traffic on stable before updating the candidate. revora measures the candidate directly before routing users to it." : "This is an isolated, project-owned service. The fault is intentional; HTTP requests, measurements, route changes and recovery checks are real."}</p>

    <div className="ops-stage">
      <div className="ops-stage-head">
        <span className="section-kicker">route telemetry</span>
        {live && <span className="ops-probe-chip">public endpoint · HTTP {traffic?.status ?? live.gateway_probe.status}</span>}
      </div>
      <div className="route-lanes">
        <div className={`lane ${stableState}`} aria-label={`stable route ${stableState}`}>
          <div className="lane-head"><span className="lane-name">stable</span><span className="lane-version">{live?.gateway_probe?.version || "—"}</span></div>
          <div className="lane-track" aria-hidden="true"><i style={{ "--p": 0 }} /><i style={{ "--p": 1 }} /><i style={{ "--p": 2 }} /></div>
          <span className="lane-state">{stableLabel}</span>
        </div>
        <div className={`lane candidate ${candidateState}`} aria-label={`candidate route ${candidateState}`}>
          <div className="lane-head"><span className="lane-name">candidate</span><span className="lane-version">{result?.version || version || "—"}</span></div>
          <div className="lane-track" aria-hidden="true"><i style={{ "--p": 0 }} /><i style={{ "--p": 1 }} /><i style={{ "--p": 2 }} /></div>
          <span className="lane-state">{candidateLabel}</span>
          {blocked && <span className="lane-shield">blocked by memory · 0 candidate probes</span>}
        </div>
      </div>
      {live && <div className={`release-live ${live.gateway_probe.status !== 200 ? "release-live-failing" : ""}`} aria-live="polite">
        <span className="live-dot" aria-hidden="true" />live gateway
        <strong>{live.gateway_probe.status === 200 ? "healthy" : "failing"}</strong>
        <span>public endpoint: HTTP {traffic?.status ?? live.gateway_probe.status} · route: {live.active_route} · version: {live.gateway_probe.version}</span>
        <button onClick={() => { loadLive().catch(failure => setError(failure.message)); probeTraffic().catch(failure => setError(failure.message)); }}>probe again</button>
      </div>}
    </div>

    {live?.target_mode !== "configured_services" && <div className="step-rail" aria-label="incident flow">
      <span className={`step-node${phase > 1 ? " done" : ""}${phase === 1 ? " active" : ""}`}><i aria-hidden="true" />1 · introduce failure</span>
      <span className={`step-node${phase > 2 ? " done" : ""}${phase === 2 ? " active" : ""}`}><i aria-hidden="true" />2 · diagnose &amp; hand off</span>
      <span className={`step-node${phase === 3 ? " active" : ""}`}><i aria-hidden="true" />3 · retry same version</span>
    </div>}

    <div className="release-controls">
      <label>Candidate version<input value={version} onChange={event => setVersion(event.target.value)} maxLength={64} disabled={busy} /></label>
      {live?.target_mode !== "configured_services" && <label>Fault to introduce<select value={mode} onChange={event => setMode(event.target.value)} disabled={busy}>
        <option value="errors">Every request fails · HTTP 503</option>
        <option value="mixed">Intermittent HTTP 503</option><option value="slow">High response latency</option>
      </select></label>}
      {live?.target_mode === "configured_services" && <button onClick={park} disabled={busy}>park on stable</button>}
      {live?.target_mode === "configured_services" ? <button className="primary" onClick={() => run()} disabled={busy || !/^[a-zA-Z0-9._-]+$/.test(version)}>{busy ? "measuring…" : "gate candidate release"}</button>
        : live?.incident_active ? <button className="restore-button" onClick={() => stage("/api/coordination/recover", {})} disabled={busy}>{busy ? "measuring and restoring…" : "2. investigate, hand off & restore"}</button>
          : <button className="primary" onClick={() => stage("/api/release/inject", { version, mode })} disabled={busy || !/^[a-zA-Z0-9._-]+$/.test(version)}>{busy ? "introducing fault…" : "1. introduce failure"}</button>}
    </div>
    {error && <p className="error" role="alert">{error}</p>}

    {result && <div className="release-result" aria-live="polite">
      <div className={`release-verdict ${result.decision === "introduced" ? "is-failing" : ""}`}><small>{outcome}</small><strong>{result.reason}</strong><span>incident {result.id.slice(0, 8)} · candidate {result.version}</span></div>
      <div className="release-metrics">
        <article><small>before · live</small><strong>HTTP {result.before.status}</strong><span>{result.before.route || "stable"} route · {result.before.version}</span></article>
        <article className={result.during?.status !== 200 && result.during ? "stage-failing" : ""}><small>during · {result.measurement_mode === "candidate_preflight" ? "preflight" : "live"}</small><strong>{result.during ? `HTTP ${result.during.status}` : result.metrics ? `${Math.round(result.metrics.error_rate * 100)}% errors` : "blocked"}</strong><span>{result.metrics ? `${result.metrics.sample_count} requests · p95 ${result.metrics.p95_latency_ms} ms` : result.during ? `${result.during.route} route · ${result.during.version}` : "no candidate traffic"}</span></article>
        <article><small>after · live</small><strong>{result.after ? `HTTP ${result.after.status}` : "awaiting repair"}</strong><span>{result.after ? `${result.after.route} route · ${result.after.version}` : "the fault is still active"}</span></article>
      </div>
      {result.canary_samples.length > 0 && <div className="probe-strip" aria-label="Measured HTTP responses">{result.canary_samples.map((sample, index) => <span key={index} title={`Request ${index + 1}: HTTP ${sample.status}, ${sample.latency_ms} ms`} className={sample.status === 200 && sample.latency_ms <= 120 ? "probe-tick pass" : "probe-tick fail"} style={{ "--i": index, height: `${Math.max(10, Math.min(42, 8 + Math.round((sample.latency_ms || 0) / 6)))}px` }} />)}</div>}
      <p className="muted">{result.prior_evidence ? `decision linked to prior incident ${result.prior_evidence.id.slice(0, 8)}.` : "first observation for this candidate version."} {result.recovery_samples.length ? `${result.recovery_samples.length} healthy gateway requests verified after rollback.` : ""}</p>
      <div className="handoff-action"><div><strong>evidence-reviewed graph handoff</strong><small>Release Analyst → Evidence Reviewer → Recovery Planner; recovery stays blocked unless measurements pass review.</small></div><button className="primary" onClick={handoff} disabled={busy || result.decision === "introduced"}>{busy ? "reviewing handoff…" : "run agent handoff"}</button></div>
      {coordination && <div className="handoff-result" aria-live="polite"><strong>{coordination.plan.action.replaceAll("_", " ")}</strong><span>Release Analyst → Evidence Reviewer → Recovery Planner · based on incident {coordination.provenance.source_incident_id.slice(0, 8)}</span>{coordination.plan.evidence_review && <span>Evidence review: {coordination.plan.evidence_review.status.replaceAll("_", " ")}</span>}{coordination.plan.evidence_review?.requested_evidence.length > 0 && <span>Requested: {coordination.plan.evidence_review.requested_evidence.join("; ")}</span>}{coordination.plan.evidence_review?.contradictions.length > 0 && <span>Rejected: {coordination.plan.evidence_review.contradictions.join("; ")}</span>}<span>{coordination.plan.reason}</span>{coordination.execution && <span>Execution verified · {coordination.execution.recovery_samples.length} healthy recovery checks · outcome {coordination.execution.id.slice(0, 8)}</span>}<details><summary>inspect handoff evidence and graph record</summary><pre className="json-record">{JSON.stringify(coordination, null, 2)}</pre></details></div>}
      {result.decision !== "introduced" && <div className="release-actions"><button onClick={() => live?.target_mode === "configured_services" ? run() : stage("/api/release/inject", { version, mode })} disabled={busy || live?.incident_active}>3. retry same version</button><button onClick={() => run(true)} disabled={busy || live?.incident_active}>force fresh check</button></div>}
      <details><summary>inspect request measurements and graph evidence</summary><pre className="json-record">{JSON.stringify(result, null, 2)}</pre></details>
    </div>}

    {history.length > 0 && <div className="release-history"><h3>persistent incident trail</h3><p className="muted">Each retry links to the earlier failed release through a FalkorDB BASED_ON relationship.</p><div>{history.slice(0, 5).map(item => <button key={item.id} onClick={() => { setResult(item); setCoordination(null); }}><span>{item.version}</span><strong>{item.decision.replaceAll("_", " ")}</strong><small>{item.prior_evidence ? `BASED_ON ${item.prior_evidence.id.slice(0, 8)}` : item.timestamp.slice(11, 19)}</small></button>)}</div></div>}
  </section>;
}
