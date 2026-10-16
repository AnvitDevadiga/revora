"use client";
import { useState } from "react";

export default function Runtime({ request, api }) {
  const [service, setService] = useState("contacts");
  const [workload, setWorkload] = useState("imported-contacts");
  const [values, setValues] = useState("alex@example.test\nalex@example.test\nsam@example.test");
  const [result, setResult] = useState(null);
  const [history, setHistory] = useState([]);
  const [report, setReport] = useState(null);
  const [selected, setSelected] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState(null);
  async function get(path, options) {
    const response = await request(`${api}${path}`, options);
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${response.status})`);
    }
    return response.json();
  }
  async function run() {
    setBusy(true); setError("");
    const input = pending || { request_id: crypto.randomUUID(), service, workload, environment: "sandbox", values: values.split("\n").map(v => v.trim()).filter(Boolean) };
    setPending(input);
    try {
      const answer = await get("/api/runtime/run", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(input) });
      setResult(answer); setSelected(0); setPending(null);
      setHistory(await get("/api/runtime/history"));
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }
  async function loadHistory() {
    setBusy(true); setError("");
    try { setHistory(await get("/api/runtime/history")); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  async function evaluate() {
    setBusy(true); setError("");
    try { setReport(await get("/api/evaluation")); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  const event = result?.events[selected];
  return <section className="panel lab" aria-labelledby="runtime-title">
    <div className="panelhead"><h2 id="runtime-title">An agent that learns from a real failure.</h2><span>Disposable SQLite sandbox</span></div>
    <p>Goal: create a unique email index. Run twice to see whether remembered outcomes change the first tool chosen.</p>
    <p className="muted">Only synthetic data belongs here. Cleanup keeps the first row per email and removes duplicates inside a temporary database.</p>
    <div className="grid">
      <div><label htmlFor="service">Service</label><input id="service" value={service} onChange={e => setService(e.target.value)} disabled={busy || !!pending} maxLength={64} />
        <label htmlFor="workload">Workload context</label><input id="workload" value={workload} onChange={e => setWorkload(e.target.value)} disabled={busy || !!pending} maxLength={64} />
        <label htmlFor="emails">Synthetic emails, one per row</label><textarea id="emails" value={values} onChange={e => setValues(e.target.value)} disabled={busy || !!pending} maxLength={200000} />
        <div className="actions"><button onClick={run} disabled={busy}>{busy ? "Working…" : pending ? "Retry same request" : "Run learning agent"}</button><button onClick={loadHistory} disabled={busy}>Load saved history</button></div>
      </div>
      <div aria-live="polite">
        {error && <p role="alert" className="error">{error}</p>}
        {result ? <><h3>{result.success ? "Goal achieved" : "Needs review"}</h3><p>{result.events.length} tool attempt(s) · {result.trace[0].evidence_count} prior observations retrieved</p>
          <p>First choice: <b>{result.initial.action}</b></p><p>Next recommendation: <b>{result.final.action}</b></p>
          <h3>Why did it change its mind?</h3>{result.why_changed.length ? result.why_changed.map(change => <p key={change.trigger}>{change.from} → {change.to}, triggered by observation {change.trigger.split(":").at(-1)}.</p>) : <p>The existing recommendation held up under this observation.</p>}
        </> : <p>First run: observe a database constraint failure and recovery. Second run: retrieve those outcomes before choosing a tool.</p>}
      </div>
    </div>
    {result && <>
      <h3 className="subheading">Decision → outcome → revised memory</h3>
      <svg viewBox={`0 0 760 ${result.events.length * 100}`} className="evidence-svg" role="img" aria-label="Select an observation to inspect its evidence">
        {result.events.map((item, i) => <g key={item.id}>
          <path d={`M 200 ${50+i*100} H 550`} stroke="#687c91" strokeWidth="2" />
          <rect x="10" y={20+i*100} width="220" height="60" rx="8" fill="#172d38" />
          <text x="20" y={55+i*100} fill="#e9eef5" fontSize="13">{item.action}</text>
          <g role="button" tabIndex="0" aria-label={`Inspect observation ${i+1}`} onClick={() => setSelected(i)} onKeyDown={e => { if (["Enter", " "].includes(e.key)) { e.preventDefault(); setSelected(i); } }}>
            <circle cx="385" cy={50+i*100} r="29" fill={item.outcome.success ? "#54e0b3" : "#ff7d72"} stroke={i === selected ? "white" : "transparent"} strokeWidth="4" />
            <text x="385" y={55+i*100} textAnchor="middle" fill="#0a0e13">{i+1}</text>
          </g>
          <rect x="540" y={20+i*100} width="210" height="60" rx="8" fill="#172d38" />
          <text x="550" y={55+i*100} fill="#e9eef5" fontSize="12">{item.after.action}</text>
        </g>)}
      </svg>
      <div className="belief-card" aria-live="polite"><strong>Observation {selected+1}: {event.outcome.success ? "Success" : "Failure retained"}</strong><span>{event.outcome.detail}</span><span>Rows: {event.outcome.rows_before} → {event.outcome.rows_after}; removed: {event.outcome.rows_removed}</span><span>{event.before.evidence.length} prior observations supported this decision.</span></div>
      <details><summary>Inspect complete decision record</summary><pre className="json-record">{JSON.stringify(result, null, 2)}</pre></details>
    </>}
    {history.length > 0 && <><h3 className="subheading">Saved runs</h3><div className="history-list">{history.slice().reverse().map(item => <button key={item.id} onClick={() => {setResult(item); setSelected(0);}}>{item.created_at.slice(11,19)} · {JSON.parse(item.context).service} · {item.events.length} attempt(s)</button>)}</div></>}
    <div className="panelhead subheading"><h3>Measured comparison</h3><button disabled={busy} onClick={evaluate}>Run held-out evaluation</button></div>
    {report && <><div className="lab-comparison"><article><small>DATABASE BASELINE</small><h3>{report.database.baseline_successes}/{report.database.count}</h3></article><article><small>DATABASE WITH MEMORY</small><h3>{report.database.memory_successes}/{report.database.count}</h3></article></div>
      <p>{report.database.method}</p><table><thead><tr><th>Held-out case</th><th>Baseline</th><th>With memory</th><th>Rows removed</th></tr></thead><tbody>{report.database.cases.map(item => <tr key={item.id}><td>{item.id}</td><td>{item.baseline_success ? "Success" : "Failed"}</td><td>{item.memory_success ? "Success" : "Failed"}</td><td>{item.memory_removed}</td></tr>)}</tbody></table>
      <div className="lab-comparison"><article><small>DEPLOYMENT DECISIONS</small><h3>{report.deployments.correct}/{report.deployments.count}</h3></article><article><small>UNFAMILIAR CONDITIONS</small><h3>{report.deployments.count}</h3></article></div><p>{report.deployments.method}</p><table><thead><tr><th>Scenario</th><th>Expected</th><th>Actual</th><th>Result</th></tr></thead><tbody>{report.deployments.cases.map(item => <tr key={item.scenario}><td>{item.scenario}</td><td>{item.expected}</td><td>{item.actual}</td><td>{item.correct ? "Correct" : "Wrong"}</td></tr>)}</tbody></table></>}
  </section>;
}
