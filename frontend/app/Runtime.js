"use client";
import { useState } from "react";
import "./runtime.css";

export default function Runtime({ request, api }) {
  const [service, setService] = useState("contacts");
  const [schemaVersion, setSchemaVersion] = useState("customers-v1");
  const [mergeAuthorized, setMergeAuthorized] = useState(true);
  const [workload, setWorkload] = useState("imported-contacts");
  const [values, setValues] = useState("alex@example.test\nalex@example.test\nsam@example.test");
  const [result, setResult] = useState(null);
  const [history, setHistory] = useState([]);
  const [report, setReport] = useState(null);
  const [selected, setSelected] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState(null);
  const [proof, setProof] = useState(null);
  const [ablation, setAblation] = useState(null);
  const [adverse, setAdverse] = useState(null);
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
    const input = pending || { request_id: crypto.randomUUID(), service, workload, schema_version: schemaVersion, merge_authorized: mergeAuthorized, environment: "sandbox", values: values.split("\n").map(v => v.trim()).filter(Boolean) };
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
  async function runProof() {
    setBusy(true); setError(""); setProof(null);
    try { setProof(await get("/api/runtime/proof", { method: "POST", headers: { "content-type": "application/json" }, body: "{}", timeoutMs: 30000 })); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }
  async function runAblation() {
    setBusy(true); setError(""); setAblation(null);
    try { setAblation(await get("/api/runtime/ablation", { method: "POST", headers: { "content-type": "application/json" }, body: "{}", timeoutMs: 30000 })); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  async function runAdverse() {
    setBusy(true); setError("");
    try { setAdverse(await get("/api/evaluation/adversarial", {method: "POST", timeoutMs: 30000})); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  const event = result?.events[selected];
  return <section className="panel lab" id="runtime" aria-labelledby="runtime-title">
    <div className="panelhead"><h2 id="runtime-title">A migration agent that learns without losing invoices.</h2><span>real SQLite · disposable sandbox</span></div>
    <p>revora upgrades a customer registry with duplicate identities while preserving every linked invoice, its value, and referential integrity. One click runs a cold attempt, records failure and recovery, then tests a larger unseen workload in the same fresh memory scope.</p>
    <p className="muted">Only synthetic records are used. Every invoice reference and total is checked before and after migration.</p>

    <div className="proof-launch"><div><span className="section-kicker">two-round learning proof</span><strong>does stored evidence change the next plan?</strong><small>cold failure → audited recovery → unfamiliar warm trial → no-memory baseline</small></div><button className="primary" onClick={runProof} disabled={busy}>{busy ? "running isolated proof…" : "run learning proof"}</button></div>
    {error && <p role="alert" className="error">{error}</p>}
    {proof && <div className="proof-result" aria-live="polite">
      <div className={`proof-verdict ${proof.proof.passed ? "passed" : "failed"}`}><span>{proof.proof.passed ? "learning proof passed" : "proof did not pass"}</span><strong>{proof.proof.passed ? "The recorded failure changed the next plan." : "Inspect the run details before claiming learning."}</strong><small>fresh cohort {proof.cohort} · {proof.proof.prior_observations_retrieved} prior observations retrieved</small></div>
      <div className="proof-grid">
        <article><small>round 1 · cold</small><strong>{proof.proof.cold_first_action.replaceAll("_", " ")}</strong><span>Failed safely, then {proof.proof.cold_recovery_action.replaceAll("_", " ")}</span></article>
        <article><small>round 2 · learned</small><strong>{proof.proof.learned_first_action.replaceAll("_", " ")}</strong><span>one attempt · {proof.proof.learned_customer_rows} identities; different workload size</span></article>
        <article><small>round 2 · no-memory baseline</small><strong>{proof.unlearned_baseline.success ? "Passed" : "Failed"}</strong><span>same workload, direct index creation only</span></article>
      </div>
      <h3 className="subheading">safety invariants · learned run</h3>
      <div className="proof-invariants">{Object.entries(proof.learned_run.events[0].outcome.invariants || {}).map(([name, passed]) => <span className={passed ? "invariant-pass" : "invariant-fail"} key={name}><i>{passed ? "✓" : "!"}</i>{name.replaceAll("_", " ")}</span>)}</div>
      <p className="muted">{proof.limitations}</p>
      <details><summary>inspect both runs and their recorded evidence</summary><pre className="json-record">{JSON.stringify(proof, null, 2)}</pre></details>
    </div>}

    <div className="proof-launch"><div><span className="section-kicker">memory ablation</span><strong>does graph retrieval change the result?</strong><small>paired fixed workloads · fresh contexts · retrieval enabled vs disabled</small></div><button className="primary" onClick={runAblation} disabled={busy}>{busy ? "comparing conditions…" : "run memory ablation"}</button></div>
    {ablation && <div className="proof-result" aria-live="polite"><div className="proof-verdict passed"><span>paired ablation complete</span><strong>Action changed in {ablation.memory_changed_action_cases}/{ablation.count} cases; outcome changed in {ablation.memory_changed_outcome_cases}/{ablation.count}.</strong><small>{ablation.method}</small></div><div className="comparison-scroll"><table><thead><tr><th>Case</th><th>No-memory action</th><th>With-memory action</th><th>Run outcomes</th><th>Retrieved</th></tr></thead><tbody>{ablation.cases.map(item => <tr key={item.id}><td>{item.id}</td><td>{item.without_memory.first_action}</td><td>{item.with_memory.first_action}</td><td>No memory: {item.without_memory.success ? "pass" : "fail"}; memory: {item.with_memory.success ? "pass" : "fail"}; direct baseline: {item.with_memory.no_memory_baseline_success ? "pass" : "fail"}</td><td>{item.with_memory.retrieved_observations}</td></tr>)}</tbody></table></div><details><summary>inspect paired run results</summary><pre className="json-record">{JSON.stringify(ablation, null, 2)}</pre></details></div>}

    <div className="proof-launch"><div><span className="section-kicker">adverse cases &amp; competent baselines</span><strong>when should memory be refused?</strong><small>changed schema · unrelated service · denied merge authorization · static policy comparison</small></div><button className="primary" onClick={runAdverse} disabled={busy}>run adverse evaluation</button></div>
    {adverse && <div className="proof-result"><p>{adverse.conclusion}</p><div className="comparison-scroll"><table><thead><tr><th>Case</th><th>Memory first action</th><th>Without retrieval</th><th>Static precondition policy</th><th>Rejected memories</th></tr></thead><tbody>{adverse.cases.map(c => <tr key={c.id}><td>{c.id}</td><td>{c.memory.first_action}</td><td>{c.no_memory_recovery.first_action}</td><td>{c.static_precondition_aware.first_action}</td><td>{c.memory.rejected_memories.map(r => r.reason).filter((v,i,a) => a.indexOf(v) === i).join(", ") || "none"}</td></tr>)}</tbody></table></div><details><summary>inspect adverse cases and baseline results</summary><pre className="json-record">{JSON.stringify(adverse, null, 2)}</pre></details></div>}

    <h3 className="subheading">custom synthetic migration</h3><p className="muted">The recovery keeps the first identity, reassigns child invoices, audits each merge, and only then creates a unique index.</p>
    <div className="grid">
      <div><label htmlFor="service">Service</label><input id="service" value={service} onChange={e => setService(e.target.value)} disabled={busy || !!pending} maxLength={64} />
        <label htmlFor="workload">Workload context</label><input id="workload" value={workload} onChange={e => setWorkload(e.target.value)} disabled={busy || !!pending} maxLength={64} />
        <label htmlFor="schema-version">Schema compatibility version</label><input id="schema-version" value={schemaVersion} onChange={e => setSchemaVersion(e.target.value)} disabled={busy || !!pending} maxLength={64} />
        <label><input type="checkbox" checked={mergeAuthorized} onChange={e => setMergeAuthorized(e.target.checked)} disabled={busy || !!pending} /> Allow identity reconciliation in this disposable task</label>
        <label htmlFor="emails">Synthetic customer identities, one per row</label><textarea id="emails" value={values} onChange={e => setValues(e.target.value)} disabled={busy || !!pending} maxLength={200000} />
        <div className="actions"><button className="primary" onClick={run} disabled={busy}>{busy ? "Working…" : pending ? "Retry same request" : "Run learning agent"}</button><button onClick={loadHistory} disabled={busy}>Load saved history</button></div>
      </div>
      <div aria-live="polite">
        {error && <p role="alert" className="error">{error}</p>}
        {result ? <><h3>{result.success ? "Goal achieved" : "Needs review"}</h3><p className="run-stats">{result.events.length} tool attempt(s) · {result.trace[0].evidence_count} prior observations retrieved</p>
          <p className="run-choice"><small>first choice</small><b>{result.initial.action}</b></p><p className="run-choice"><small>next recommendation</small><b>{result.final.action}</b></p>
          <h3 className="subheading">why did it change its mind?</h3>{result.why_changed.length ? result.why_changed.map(change => <p className="change-line" key={change.trigger}>{change.from} → {change.to}, triggered by observation {change.trigger.split(":").at(-1)}.</p>) : <p className="muted">The existing recommendation held up under this observation.</p>}
        </> : <p className="muted">First run: observe a database constraint failure and recovery. Second run: retrieve those outcomes before choosing a tool.</p>}
      </div>
    </div>
    {result && <>
      <h3 className="subheading">decision → outcome → revised memory</h3>
      <svg viewBox={`0 0 760 ${result.events.length * 100}`} className="evidence-svg" role="img" aria-label="Select an observation to inspect its evidence">
        {result.events.map((item, i) => <g className="obs-row" key={item.id} style={{ "--r": i }}>
          <path d={`M 200 ${50+i*100} H 550`} stroke="#2b394a" strokeWidth="2" />
          <rect x="10" y={20+i*100} width="220" height="60" rx="10" fill="#0e141c" stroke="#1f2a38" />
          <text x="20" y={55+i*100} fill="#e9eef5" fontSize="13">{item.action}</text>
          <g className="obs-hit" role="button" tabIndex="0" aria-label={`Inspect observation ${i+1}`} onClick={() => setSelected(i)} onKeyDown={e => { if (["Enter", " "].includes(e.key)) { e.preventDefault(); setSelected(i); } }}>
            <circle cx="385" cy={50+i*100} r="29" fill={item.outcome.success ? "#54e0b3" : "#ff756d"} stroke={i === selected ? "#eef3f9" : "transparent"} strokeWidth="4" />
            <text x="385" y={55+i*100} textAnchor="middle" fill="#0a0e13">{i+1}</text>
          </g>
          <rect x="540" y={20+i*100} width="210" height="60" rx="10" fill="#0e141c" stroke="#1f2a38" />
          <text x="550" y={55+i*100} fill="#a7b6c9" fontSize="12">{item.after.action}</text>
        </g>)}
      </svg>
      <div className="belief-card" aria-live="polite"><strong>Observation {selected+1}: {event.outcome.review_required ? "Review required" : event.outcome.success ? "Success" : "Failure retained"}</strong><span>{event.outcome.detail}</span><span>Tool {event.outcome.executed === false ? "withheld; no database changed. " : ""}Identities: {event.outcome.rows_before ?? "—"} → {event.outcome.rows_after ?? "—"}; invoice references re-linked: {event.outcome.invoice_references_relinked ?? 0}</span><span>Invoice total: {event.outcome.invoice_total_cents_before ?? 0}¢ → {event.outcome.invoice_total_cents_after ?? 0}¢</span><span>{event.before.evidence.length} prior observations supported this decision.</span></div>
      {result.rejected_memories?.length > 0 && <details><summary>why prior memories were rejected ({result.rejected_memories.length})</summary><pre className="json-record">{JSON.stringify(result.rejected_memories, null, 2)}</pre></details>}
      <details><summary>inspect complete decision record</summary><pre className="json-record">{JSON.stringify(result, null, 2)}</pre></details>
    </>}
    {history.length > 0 && <><h3 className="subheading">Saved runs</h3><div className="history-list">{history.slice().reverse().map(item => <button key={item.id} onClick={() => {setResult(item); setSelected(0);}}>{item.created_at.slice(11,19)} · {JSON.parse(item.context).service} · {item.events.length} attempt(s)</button>)}</div></>}
    <div className="panelhead subheading"><h3>Measured comparison</h3><button onClick={evaluate} disabled={busy}>Run held-out evaluation</button></div>
    {report && <><div className="lab-comparison"><article><small>database baseline</small><h3>{report.database.baseline_successes}/{report.database.count}</h3></article><article><small>database with memory</small><h3>{report.database.memory_successes}/{report.database.count}</h3></article></div>
      <p>Fixed guarded policy without memory: {report.database.fixed_guarded_successes}/{report.database.count}. Memory is not shown to outperform that baseline.</p><p className="muted">{report.database.method}</p><div className="comparison-scroll"><table><thead><tr><th>Held-out case</th><th>Baseline</th><th>With memory</th><th>Rows removed</th></tr></thead><tbody>{report.database.cases.map(item => <tr key={item.id}><td>{item.id}</td><td>{item.baseline_success ? "Success" : "Failed"}</td><td>{item.memory_success ? "Success" : "Failed"}</td><td>{item.memory_removed}</td></tr>)}</tbody></table></div>
      <div className="lab-comparison"><article><small>deployment decisions</small><h3>{report.deployments.correct}/{report.deployments.count}</h3></article><article><small>unfamiliar conditions</small><h3>{report.deployments.count}</h3></article></div><p className="muted">{report.deployments.method}</p><div className="comparison-scroll"><table><thead><tr><th>Scenario</th><th>Expected</th><th>Actual</th><th>Result</th></tr></thead><tbody>{report.deployments.cases.map(item => <tr key={item.scenario}><td>{item.scenario}</td><td>{item.expected}</td><td>{item.actual}</td><td>{item.correct ? "Correct" : "Wrong"}</td></tr>)}</tbody></table></div></>}
  </section>;
}
