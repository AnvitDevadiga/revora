"use client";
import { useState } from "react";
import "./laboratory.css";

export default function Laboratory({ request, api }) {
  const [scenario, setScenario] = useState("deployment");
  const [result, setResult] = useState(null);
  const [selected, setSelected] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function replay() {
    setBusy(true); setError("");
    try {
      const response = await request(`${api}/api/lab/replay`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ scenario, replay_id: "judge-demo" }),
      });
      if (!response.ok) throw new Error(`Replay failed (${response.status}). Check your connection and workspace key.`);
      setResult(await response.json()); setSelected(0);
    } catch (error) { setError(error.message); }
    finally { setBusy(false); }
  }
  const step = result?.timeline[selected];
  return <section className="lab panel" aria-labelledby="lab-title">
    <div className="panelhead"><h2 id="lab-title">watch experience change a decision.</h2><span>memory laboratory · simulated</span></div>
    <p>Compare strategies in the same context. Inspect every change. Remove evidence to challenge the conclusion.</p>
    <div className="actions"><label htmlFor="scenario">Scenario</label><select id="scenario" value={scenario} onChange={e => setScenario(e.target.value)} disabled={busy}>
      <option value="deployment">The Friday deployment myth</option><option value="migration">The database migration incident</option>
    </select><button disabled={busy} onClick={replay}>{busy ? "Running experiment…" : "Replay experiment"}</button></div>
    {error && <p role="alert" className="error">{error}</p>}
    {result && <>
      <p className="muted">{result.title} · retries reopen the same saved experiment</p>
      <div className="lab-timeline" role="group" aria-label="Select an observation">
        {result.events.map((event, i) => <button key={event.id} aria-pressed={selected === i} onClick={() => setSelected(i)} className={event.success ? "success-event" : "failure-event"}>
          <small>OBSERVATION {i + 1}</small>{event.action}<span>{event.success ? "Healthy" : "Incident retained"}</span>
        </button>)}
      </div>
      <div className="lab-comparison" aria-live="polite">
        <article><small>BEFORE OBSERVATION {selected + 1}</small><h3>{step.before.action}</h3></article>
        <article><small>AFTER OBSERVATION {selected + 1}</small><h3>{step.after.action}</h3><p>{step.changed ? "Decision revised by this evidence" : "More evidence recorded; decision unchanged"}</p></article>
      </div>
      <div className="grid">
        <div><h3 className="lab-subhead">contextual evidence</h3>{step.after.beliefs.map(belief => <article className="belief-card" key={belief.action}>
          <strong>{belief.action}</strong><span>{belief.successes} successful / {belief.failures} failed</span>
          <meter min="0" max="1" value={belief.score} aria-label={`${belief.action} evidence score`} />
          <small>Evidence score {belief.score} · {belief.evidence.length} linked observations</small>
        </article>)}</div>
        <div><h3 className="lab-subhead">final sandbox run</h3>{result.trace.map(item => <div className="trace-row" key={item.role}><b>{item.role}</b><span>{typeof item.result === "string" ? item.result : `${item.result.action}: ${item.result.success ? "healthy" : "withheld / failed"} (simulated)`}</span></div>)}</div>
      </div>
      <details><summary>would the final decision survive missing evidence?</summary>
        <p>Each row recomputes the decision with one observation removed. This tests sensitivity; it does not prove causation.</p>
        {result.ablations.map((entry, i) => <div className="trace-row" key={entry.removed}><b>Without observation {i + 1}</b><span>{entry.action} · {entry.changed ? "decision changes" : "decision stable"}</span></div>)}
      </details>
      <p className="muted">{result.limitations}</p>
    </>}
  </section>;
}
