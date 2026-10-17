"use client";
import { useEffect, useMemo, useState } from "react";
import "./showcase.css";

const stages = [
  { key: "baseline", title: "healthy baseline", verb: "observe", description: "the gateway serves the stable version before fault injection.", tone: "good" },
  { key: "onset", title: "failure observed", verb: "detect", description: "a deliberately faulty candidate stays at HTTP 503. this response was measured from the running local gateway.", tone: "bad" },
  { key: "recovered", title: "service restored", verb: "recover", description: "twelve failed requests trigger rollback. three subsequent requests verify that the stable route is healthy.", tone: "good" },
  { key: "blocked", title: "mistake prevented", verb: "remember", description: "on retry, the graph retrieves the earlier rollback and blocks the same version before any candidate probes.", tone: "good" },
];
const shortId = value => value ? value.slice(0, 8) : "—";
const human = value => (value || "").replaceAll("_", " ");

function ScrollSpine({ sections }) {
  const [progress, setProgress] = useState(0);
  const [active, setActive] = useState("incident-proof");
  useEffect(() => {
    let raf = 0;
    const update = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const total = document.documentElement.scrollHeight - window.innerHeight;
        setProgress(total > 0 ? Math.min(1, Math.max(0, window.scrollY / total)) : 0);
        let current = sections[0]?.id;
        for (const section of sections) {
          const element = document.getElementById(section.id);
          if (element && element.getBoundingClientRect().top <= window.innerHeight * 0.45) current = section.id;
        }
        setActive(current);
      });
    };
    update();
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    return () => { window.removeEventListener("scroll", update); window.removeEventListener("resize", update); cancelAnimationFrame(raf); };
  }, [sections]);
  return <nav className="spine" aria-label="section index">
    <span className="spine-line" aria-hidden="true" />
    <span className="spine-fill" aria-hidden="true" style={{ transform: `scaleY(${progress})` }} />
    {sections.map(section => <button key={section.id} type="button" className={active === section.id ? "active" : ""} data-label={section.label} aria-label={`go to ${section.label}`} onClick={() => document.getElementById(section.id)?.scrollIntoView({ behavior: "smooth", block: "start" })} />)}
  </nav>;
}

export default function EvidenceReplay({ onLive }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(0);
  const [node, setNode] = useState(null);
  useEffect(() => {
    const controller = new AbortController();
    fetch("/evidence.json", { signal: controller.signal }).then(response => {
      if (!response.ok) throw new Error("the evidence record could not be loaded.");
      return response.json();
    }).then(setData).catch(e => { if (e.name !== "AbortError") setError(e.message); });
    return () => controller.abort();
  }, []);
  const sections = useMemo(() => [
    { id: "incident-proof", label: "01 · observe → act → remember" },
    { id: "handoff-proof", label: "02 · shared memory" },
    ...(data?.release_memory_ablation ? [{ id: "release-ablation", label: "03 · persistence changes a decision" }] : []),
    { id: "ablation-proof", label: "04 · remove memory, compare" },
  ], [data]);
  if (error) return <div role="alert" className="evidence-error">{error}<button className="quiet-button" onClick={onLive}>open live workspace</button></div>;
  if (!data) return <p role="status" className="evidence-loading">loading the verified acceptance record…</p>;
  const stage = stages[selected];
  const release = data.release[stage.key];
  const observed = selected === 0 ? release.gateway_probe : selected === 1 ? release.during : release.after;
  const { handoff, plan } = data.coordination;
  const sample = data.release.diagnosis || data.release.recovered;
  const nodes = [
    { title: "measured diagnosis", kind: "ReleaseIncident", id: sample.id, detail: sample, label: "12 measured requests before recovery", tone: "bad" },
    { title: "release analyst", kind: "AgentHandoff", id: handoff.id, detail: handoff, label: "publishes observed evidence", tone: "blue" },
    { title: "evidence reviewer", kind: "EvidenceReview", id: data.coordination.plan.evidence_review_id, detail: data.coordination.plan.evidence_review, label: data.coordination.plan.evidence_review.status.replaceAll("_", " "), tone: "blue" },
    { title: "recovery planner", kind: "CoordinationDecision", id: plan.id, detail: plan, label: human(plan.action), tone: "good" },
  ];
  const relations = ["", "HANDED_OFF", "REVIEWED_BY", "FORWARDED_TO"];
  return <div className="showcase">
    <ScrollSpine sections={sections} />
    <section className="showcase-hero">
      <div className="hero-copy boot" style={{ "--d": "40ms" }}>
        <span className="showcase-kicker">experience infrastructure for agents</span>
        <h1>an agent fails once.<br /><em>the next one inherits the lesson.</em></h1>
        <p className="hero-sub">revora turns observed outcomes into connected memory. a later agent can trace the evidence, change its plan, and avoid repeating the same mistake.</p>
        <div className="hero-actions">
          <a href="#incident-proof" className="primary-link">explore the evidence <span aria-hidden="true">↘</span></a>
          <button className="quiet-button" onClick={onLive}>open live workspace ↗</button>
        </div>
      </div>
      <aside className="verification-card boot" style={{ "--d": "200ms" }} aria-label="verified local run summary">
        <span className="record-tag"><i aria-hidden="true" /> verified local run</span>
        <div className="morph-numerals" aria-label="HTTP 200 to HTTP 503 to HTTP 200">
          <span>200</span><b aria-hidden="true">→</b><span className="tone-fault">503</span><b aria-hidden="true">→</b><span>200</span>
        </div>
        <p>real HTTP measurements.<br />persistent FalkorDB evidence.<br />a fresh agent reads the handoff.</p>
        <a className="card-link" href="/evidence.json" download="revora-evidence.json">download acceptance record ↗</a>
      </aside>
    </section>

    <div className="replay-notice boot" style={{ "--d": "340ms" }}>
      <span>recorded evidence</span>
      <p>this is an interactive replay of a verified local run, captured {new Date(data.recorded_at).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "Asia/Kolkata" })}. The public site does not operate a hosted backend. Faults were intentionally injected into project-owned services; migration records are synthetic.</p>
    </div>

    <section className="proof-section" id="incident-proof" aria-labelledby="incident-proof-title">
      <div className="proof-section-title">
        <div><span className="showcase-kicker">01 / observe → act → remember</span><h2 id="incident-proof-title">a failure becomes a guardrail.</h2></div>
        <span className="small-label">select a recorded stage</span>
      </div>
      <div className="stage-tabs" role="group" aria-label="recorded incident stages">
        {stages.map((item, index) => <button key={item.key} aria-pressed={selected === index} onClick={() => setSelected(index)} className={`${selected === index ? "selected " : ""}tone-${item.tone}`}>
          <small>0{index + 1} / {item.verb}</small><strong>{item.title}</strong>
        </button>)}
      </div>
      <div className="stage-content" key={selected} aria-live="polite">
        <div className={`recorded-status ${stage.tone}`}>
          <small>recorded HTTP response</small>
          <strong>{observed.status}</strong>
          <span>{observed.route || "stable"} route · {observed.version}</span>
        </div>
        <div className="stage-explanation">
          <span className="showcase-kicker">{stage.verb}</span>
          <h3>{selected === 3 ? "the second attempt stops before traffic." : selected === 2 ? "measured rollback restores the stable route." : selected === 1 ? "the failure is observable and sustained." : "start with a healthy service."}</h3>
          <p>{stage.description}</p>
          {selected > 0 && <code>Incident {release.id} {release.prior_evidence ? `· BASED_ON ${release.prior_evidence.id}` : ""}</code>}
          {selected === 3 && <div className="saved-callout">0 candidate probes · prior incident {shortId(release.prior_evidence.id)}</div>}
        </div>
      </div>
      <details className="record-details"><summary>inspect the selected stage’s source record</summary><pre className="json-record">{JSON.stringify(release, null, 2)}</pre></details>
    </section>

    <details className="record-details walkthrough">
      <summary>watch the captioned live-state walkthrough</summary>
      <p>These two captures show the controlled HTTP 503 and the verified graph handoff recovery. They are separate checkpoints, not a continuous screen recording.</p>
      <video controls preload="none" aria-label="Captioned walkthrough of the verified local failure and recovery"><source src="/demo.mp4" type="video/mp4" /><track kind="captions" src="/demo.vtt" srcLang="en" label="English" default />Your browser does not support embedded video.</video>
    </details>

    <section className="proof-section" id="handoff-proof" aria-labelledby="handoff-proof-title">
      <div className="proof-section-title">
        <div><span className="showcase-kicker">02 / shared memory across agents</span><h2 id="handoff-proof-title">the handoff survives process restarts.</h2></div>
        <span className="check-badge">✓ verified in FalkorDB</span>
      </div>
      <p className="section-description">The analyst writes a handoff. A separate worker reads it from FalkorDB. The receiving evidence reviewer recomputes the release gate from raw HTTP statuses and latencies, requests missing samples or rejects contradictions, and records that review in the graph. Only accepted evidence reaches the bounded recovery planner. Acceptance also restarts FastAPI mid-incident and verifies the authenticated recovery tool. All three roles use deterministic policies.</p>
      <div className="handoff-path">
        <svg className="chain-line" viewBox="0 0 1000 4" preserveAspectRatio="none" aria-hidden="true">
          <line x1="0" y1="2" x2="1000" y2="2" stroke="rgba(148,178,209,0.16)" strokeWidth="1" />
          <line className="chain-packet" x1="0" y1="2" x2="1000" y2="2" stroke="#54e0b3" strokeWidth="2" />
        </svg>
        {nodes.map((item, index) => <div className="path-item" key={item.id} style={{ "--i": index }}>
          <span className="path-edge">{index > 0 ? `${relations[index]} →` : ""}</span>
          <button className={`path-node ${item.tone}`} aria-pressed={node?.id === item.id} onClick={() => setNode(item)}>
            <small>{item.kind}</small><strong>{item.title}</strong><span className={item.kind === "EvidenceReview" ? "verdict-stamp" : undefined}>{item.label}</span><code>{shortId(item.id)}</code>
          </button>
        </div>)}
      </div>
      <div className="evidence-return">↳ planner decision <code>{shortId(plan.id)}</code> — BASED_ON → measured incident <code>{shortId(sample.id)}</code></div>
      {node && <div className="node-inspector">
        <div><strong>{node.title}</strong><button className="quiet-button" onClick={() => setNode(null)} aria-label="close node inspector">close ×</button></div>
        <pre>{JSON.stringify(node.detail, null, 2)}</pre>
      </div>}
      <div className="graph-verdict">
        <span>✓ separate worker executes measured recovery</span>
        <span>✓ exact incident and handoff expiry checked</span>
        <span>✓ completed recovery is reused on retry</span>
      </div>
      <details className="record-details"><summary>inspect actual graph nodes and relationships ({data.graph.nodes.length} nodes · {data.graph.edges.length} links)</summary><pre className="json-record">{JSON.stringify(data.graph, null, 2)}</pre></details>
    </section>

    {data.release_memory_ablation && <section className="proof-section" id="release-ablation" aria-labelledby="release-ablation-title">
      <div className="proof-section-title">
        <div><span className="showcase-kicker">03 / persistence changes a live decision</span><h2 id="release-ablation-title">the repeat failure is blocked after restart.</h2></div>
      </div>
      <p className="section-description">The same candidate version and injected fault are tried after recovery. One controller restores graph memory after restart; the stateless control does not.</p>
      <div className="comparison-scroll"><table>
        <thead><tr><th>condition</th><th>retry decision</th><th>candidate probes</th><th>routed HTTP status</th></tr></thead>
        <tbody><tr><th>FalkorDB memory</th><td>{human(data.release_memory_ablation.memory.decision)}</td><td>{data.release_memory_ablation.memory.candidate_samples}</td><td>{data.release_memory_ablation.memory.routed_status}</td></tr><tr><th>Stateless restart</th><td>{human(data.release_memory_ablation.no_persistent_memory.decision)}</td><td>{data.release_memory_ablation.no_persistent_memory.candidate_probes}</td><td>{data.release_memory_ablation.no_persistent_memory.routed_status}</td></tr></tbody>
      </table></div>
      <p className="honest-result">Controlled local fault injection only. The stateless process has no persisted incident history; this demonstrates the consequence of the recorded memory in this fixture, not a production failure-rate estimate.</p>
    </section>}

    <section className="proof-section" id="ablation-proof" aria-labelledby="ablation-proof-title">
      <div className="proof-section-title">
        <div><span className="showcase-kicker">04 / remove memory. compare the decision.</span><h2 id="ablation-proof-title">fewer failed attempts. same task.</h2></div>
      </div>
      <p className="section-description">Both conditions use the same evaluation inputs and recovery policy. The memory condition gets one prior training run. Isolated scopes keep each comparison clean.</p>
      <div className="proof-stats">
        <article style={{ "--i": 0 }}><strong>{data.ablation.memory_changed_action_cases}/{data.ablation.count}</strong><span>first actions changed</span></article>
        <article style={{ "--i": 1 }}><strong>{data.ablation.failed_tool_attempts_avoided}</strong><span>failed attempts avoided across fixtures</span></article>
        <article style={{ "--i": 2 }}><strong>{data.ablation.memory_changed_outcome_cases}/{data.ablation.count}</strong><span>final success outcomes changed</span></article>
      </div>
      <div className="comparison-scroll"><table>
        <thead><tr><th>held-out case</th><th>without retrieval</th><th>with retrieval</th><th>attempts / result</th></tr></thead>
        <tbody>{data.ablation.cases.map(item => <tr key={item.id}><th>{human(item.id.replaceAll("-", " "))}</th><td>{human(item.without_memory.first_action)}<small style={{ display: "block", color: "var(--text-2)" }}>{item.without_memory.attempts} attempts · {item.without_memory.failed_tool_attempts} failed calls · 0 observations</small></td><td>{human(item.with_memory.first_action)}<small style={{ display: "block", color: "var(--text-2)" }}>{item.with_memory.attempts} attempts · {item.with_memory.failed_tool_attempts} failed calls · {item.with_memory.retrieved_observations} observations</small></td><td>{item.without_memory.success && item.with_memory.success ? "Both succeed" : "Inspect source outcomes"}</td></tr>)}</tbody>
      </table></div>
      <p className="honest-result">Memory avoids failed tool attempts on these duplicate fixtures. The no-memory policy can recover and reaches the same final outcomes. This is bounded evidence about the fixed policy and fixtures, not general learning.</p>
      {data.adversarial_evaluation && <>
        <h3 className="subheading" style={{ fontSize: "18px" }}>adverse cases &amp; competent baselines</h3>
        <p className="section-description">{data.adversarial_evaluation.conclusion}</p>
        <div className="comparison-scroll"><table>
          <thead><tr><th>case</th><th>memory action</th><th>static precondition policy</th><th>rejected evidence</th></tr></thead>
          <tbody>{data.adversarial_evaluation.cases.map(c => <tr key={c.id}><th>{c.id}</th><td>{human(c.memory.first_action)}</td><td>{human(c.static_precondition_aware.first_action)}</td><td>{[...new Set(c.memory.rejected_memories.map(r => r.reason))].map(human).join(", ") || "none"}</td></tr>)}</tbody>
        </table></div>
      </>}
      {data.graph_edge_ablation && <p className="honest-result">Graph edge ablation: removing the procedure’s VALIDATES paths changes the first action from <b>{human(data.graph_edge_ablation.before)}</b> to <b>{human(data.graph_edge_ablation.after)}</b>. The agent withholds the unsupported procedure.</p>}
      <div className="invariant-summary">
        <div>
          <span className="showcase-kicker">migration integrity</span>
          <h3>keep every invoice connected.</h3>
          <p>The separate learning proof reconciles identities, reassigns invoice references, and checks value and foreign-key integrity.</p>
        </div>
        <div>{Object.entries(data.learning_proof.learned_run.events[0].outcome.invariants).map(([name, passed]) => <span key={name}>{passed ? "✓" : "!"} {human(name)}</span>)}</div>
      </div>
    </section>

    <section className="reproduce-card" aria-labelledby="reproduce-title">
      <div>
        <span className="showcase-kicker">run it yourself</span>
        <h2 id="reproduce-title">inspectable by design.</h2>
        <p>Start the stack locally, run a fresh acceptance test, and inspect the graph. The replay record comes from the same executable acceptance script.</p>
        <code>docker compose up -d --build</code>
      </div>
      <div className="reproduce-links">
        <a href="/evidence.json" download="revora-evidence.json">acceptance evidence ↗</a>
        <a href="/setup.md">local setup &amp; verification ↗</a>
        <button onClick={onLive}>connect to a live backend ↗</button>
      </div>
    </section>
  </div>;
}
