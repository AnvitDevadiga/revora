"use client";
import { useRef, useState } from "react";
import Laboratory from "./Laboratory";
import Runtime from "./Runtime";
import GroqAgent from "./GroqAgent";
import GraphDashboard from "./GraphDashboard";
import ReleaseOps from "./ReleaseOps";
import EvidenceReplay from "./EvidenceReplay";
import "./shell.css";

export default function Home() {
  const [view, setView] = useState("replay");
  const [api, setApi] = useState(process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000");
  const [apiKey, setApiKey] = useState("");
  const apiKeyRef = useRef(apiKey);
  apiKeyRef.current = apiKey;
  const [connection, setConnection] = useState("connect your local or hosted backend to run fresh experiments.");
  const [connected, setConnected] = useState(false);
  const [connecting, setConnecting] = useState(false);
  async function request(url, options = {}) {
    const { timeoutMs = 15000, ...fetchOptions } = options;
    return fetch(url, { ...fetchOptions, headers: { ...fetchOptions.headers, ...(apiKeyRef.current ? { "x-api-key": apiKeyRef.current } : {}) }, signal: fetchOptions.signal || AbortSignal.timeout(timeoutMs) });
  }
  async function connect() {
    setConnecting(true); setConnected(false);
    try {
      const target = new URL(api);
      if (!["http:", "https:"].includes(target.protocol) || target.username || target.password || target.search || target.hash) throw new Error("enter a plain HTTP or HTTPS backend URL.");
      const base = api.replace(/\/+$/, "");
      const health = await request(`${base}/health`);
      if (!health.ok) throw new Error("database is unavailable.");
      const status = await health.json();
      const response = await request(`${base}/api/runtime/history`);
      if (response.status === 401) throw new Error("enter the workspace API key and connect.");
      if (!response.ok) throw new Error(`connection failed (${response.status}).`);
      setApi(base); setConnected(true);
      setConnection(status.database === "falkordb" ? "connected · persistent FalkorDB memory · fresh executions enabled" : "connected · temporary memory · results will not survive a restart");
    } catch (error) { setConnection(error.message === "Failed to fetch" ? "cannot reach the backend. start the local stack or enter a reachable HTTPS backend URL." : error.message); }
    finally { setConnecting(false); }
  }
  return <main>
    <nav className="site-nav" aria-label="main navigation">
      <a href="/" className="brand" aria-label="revora home"><span className="brand-mark" aria-hidden="true"><svg viewBox="0 0 32 32" focusable="false"><path d="M10 26V10" /><path d="M10 18C12 12 17 9.5 22 11.5" /></svg></span><span className="brand-word">revora</span></a>
      <span className="track-label">graph hacks / memory &amp; coordination</span>
      <div className="view-switch" data-view={view} role="group" aria-label="view">
        <button aria-pressed={view === "replay"} onClick={() => setView("replay")}>Verified replay</button>
        <button aria-pressed={view === "live"} onClick={() => setView("live")}>Live workspace</button>
      </div>
      {connected && <span className="nav-live" role="img" aria-label="backend connected"><i aria-hidden="true" />live</span>}
    </nav>
    {view === "replay" ? <EvidenceReplay onLive={() => setView("live")} /> : <>
      <section className="workspace-connection" aria-label="connect live workspace">
        <div className="connect-grid">
          <label>Backend URL<input type="url" value={api} onChange={e => { setApi(e.target.value); setConnected(false); }} spellCheck="false" /></label>
          <label>Workspace API key (if required)<input type="password" value={apiKey} onChange={e => { setApiKey(e.target.value); setConnected(false); }} autoComplete="off" /></label>
          <button className="primary" onClick={connect} disabled={connecting}>{connecting ? "Connecting…" : "Connect"}</button>
        </div>
        <p role="status" className="connect-status"><i className={`dot${connected ? " ok" : ""}`} aria-hidden="true" />{connection}</p>
      </section>
      {connected && <>
        <div className="live-bar">
          <span className="live-bar-label">workspace</span>
          <a href="#release-ops">release ops</a>
          <a href="#runtime">runtime</a>
          <a href="#supporting-flows">supporting flows</a>
          <span className="live-bar-host">{api.replace(/^https?:\/\//, "")}</span>
        </div>
        <ReleaseOps request={request} api={api} />
        <Runtime request={request} api={api} />
        <details className="supporting-flows" id="supporting-flows">
          <summary>Explore the optional Groq agent, belief graph, and replay laboratory</summary>
          <GroqAgent request={request} api={api} />
          <GraphDashboard request={request} api={api} />
          <Laboratory request={request} api={api} />
        </details>
      </>}
    </>}
    <footer className="app-footer"><span>revora · ai remembers. revora learns.</span><span>{view === "replay" ? "recorded local acceptance evidence · deterministic bounded policies" : "one workspace · one api worker · controlled synthetic experiments"}</span></footer>
  </main>;
}
