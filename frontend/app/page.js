"use client";
import { useEffect, useState } from "react";
import Laboratory from "./Laboratory";
import Runtime from "./Runtime";
import GroqAgent from "./GroqAgent";
import "./errors.css";
import "./laboratory.css";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function Home() {
  const [apiKey, setApiKey] = useState("");
  const [connection, setConnection] = useState("Checking local API…");
  async function request(url, options = {}) {
    return fetch(url, { ...options, headers: { ...options.headers, ...(apiKey ? { "x-api-key": apiKey } : {}) }, signal: AbortSignal.timeout(15000) });
  }
  async function connect() {
    try {
      const health = await request(`${API}/health`);
      if (!health.ok) throw new Error("Database is unavailable");
      const status = await health.json();
      const response = await request(`${API}/api/runtime/history`);
      if (response.status === 401) throw new Error("Enter the workspace API key and connect");
      if (!response.ok) throw new Error(`Connection failed (${response.status})`);
      setConnection(status.database === "falkordb" ? "Connected · persistent FalkorDB memory" : "Connected · temporary in-memory mode");
    } catch (error) { setConnection(error.message === "Failed to fetch" ? "Local API is offline. Start the local stack." : error.message); }
  }
  useEffect(() => { connect(); }, []);
  return <main>
    <header>
      <div className="eyebrow">GRAPH HACKS / AGENT MEMORY & COORDINATION</div>
      <h1>Living Memory<span>—</span></h1>
      <p>An agent makes a mistake. The graph remembers. Its next decision changes.</p>
      <p className="muted">AI remembers. Revora learns.</p>
      <label>Workspace API key (if required) <input type="password" value={apiKey} onChange={event => setApiKey(event.target.value)} autoComplete="off" /></label>
      <button onClick={connect}>Connect</button><p role="status">{connection}</p>
    </header>
    <Runtime request={request} api={API} />
    <GroqAgent request={request} api={API} />
    <Laboratory request={request} api={API} />
    <footer className="muted">Local research demo. One workspace, one API worker. SQLite executes only in disposable databases. Scenario replays are synthetic. Evidence scores are not safety guarantees.</footer>
  </main>;
}
