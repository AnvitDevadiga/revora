"""A live, project-owned HTTP gateway with measured incident recovery.

The candidate's faults are intentional demo inputs. Requests, routing changes,
health measurements, recovery and FalkorDB memory are real operations.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import RLock, Thread
from collections import deque
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from urllib.parse import urlsplit
from uuid import uuid4


MODES = {"healthy", "errors", "slow", "mixed"}
SAMPLE_COUNT = 12
MAX_ERROR_RATE = 0.05
MAX_P95_MS = 120
logger = logging.getLogger("revora.release")

def nearest_rank_percentile(values, percentile=0.95):
    """Nearest-rank percentile (ceil(p*n)); for 12 samples p95 is the max."""
    if not values or not 0 < percentile <= 1:
        raise ValueError("Percentile requires samples and a value in (0, 1]")
    ordered = sorted(values)
    return ordered[math.ceil(percentile * len(ordered)) - 1]

def measure_gates(samples):
    """Compute release evidence once for both controller and handoff reviewer."""
    if not samples:
        raise ValueError("At least one health sample is required")
    statuses, latencies = [], []
    for sample in samples:
        status, latency = sample.get("status"), sample.get("latency_ms")
        if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
            raise ValueError("Health samples contain an invalid HTTP status")
        if isinstance(latency, bool) or not isinstance(latency, (int, float)) or not math.isfinite(latency) or latency < 0:
            raise ValueError("Health samples contain an invalid latency")
        statuses.append(status)
        latencies.append(float(latency))
    error_rate = sum(status != 200 for status in statuses) / len(statuses)
    p95 = nearest_rank_percentile(latencies)
    failed = error_rate > MAX_ERROR_RATE or p95 > MAX_P95_MS
    return {"sample_count": len(samples), "error_rate": error_rate,
            "p95_latency_ms": p95, "percentile_method": "nearest_rank",
            "max_error_rate": MAX_ERROR_RATE, "max_p95_latency_ms": MAX_P95_MS,
            "failed": failed}


class ReleaseController:
    def __init__(self, graph=None, stable_url=None, candidate_url=None, service=None):
        self.graph = graph
        self.service = service or os.getenv("REVORA_SERVICE_NAME", "revora-demo")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", self.service):
            raise ValueError("REVORA_SERVICE_NAME must be 1–64 letters, digits, underscores or hyphens")
        # One re-entrant lock owns every route and incident transition. All
        # public operations take it before reading or mutating controller state.
        self.lock = RLock()
        # Serializes multi-step operator actions. Never hold it while probing a
        # target: HTTP handlers also need the state lock to answer those probes.
        self.operation_lock = RLock()
        self.route = "stable"
        self.candidate_mode = "healthy"
        self.candidate_version = "v0"
        self.candidate_calls = 0
        self.pending = None
        self.local_incidents = deque(maxlen=500)
        self.servers = []
        stable_url = stable_url or os.getenv("REVORA_STABLE_URL", "")
        candidate_url = candidate_url or os.getenv("REVORA_CANDIDATE_URL", "")
        if bool(stable_url) != bool(candidate_url):
            raise ValueError("Configure both REVORA_STABLE_URL and REVORA_CANDIDATE_URL")
        self.external = bool(stable_url)
        if self.external:
            for address in (stable_url, candidate_url):
                parsed = urlsplit(address)
                if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                        or parsed.username or parsed.password or parsed.query or parsed.fragment):
                    raise ValueError("Release target URLs must be plain HTTP(S) base URLs")
            self.stable_url = stable_url.rstrip("/")
            self.candidate_url = candidate_url.rstrip("/")
        self._start_servers()

    def _serve(self, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.servers.append((server, worker))
        return f"http://127.0.0.1:{server.server_port}"

    def _start_servers(self):
        owner = self

        class Target(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/health":
                    self.send_error(404)
                    return
                is_candidate = self.server.role == "candidate"
                with owner.lock:
                    mode = owner.candidate_mode if is_candidate else "healthy"
                    version = owner.candidate_version if is_candidate else "stable"
                    if is_candidate:
                        owner.candidate_calls += 1
                        call = owner.candidate_calls
                    else:
                        call = 0
                if mode == "slow":
                    time.sleep(0.18)
                failed = mode == "errors" or (mode == "mixed" and call % 4 == 0)
                body = json.dumps({"service": owner.service, "version": version,
                                   "healthy": not failed}).encode()
                self.send_response(503 if failed else 200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                return

        class Gateway(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/health":
                    self.send_error(404)
                    return
                with owner.lock:
                    route = owner.route
                target = owner.candidate_url if route == "candidate" else owner.stable_url
                try:
                    with urlopen(target + "/health", timeout=2) as response:
                        status, body = response.status, response.read()
                        content_type = response.headers.get("Content-Type", "application/octet-stream")
                except HTTPError as error:
                    status, body = error.code, error.read()
                    content_type = error.headers.get("Content-Type", "application/octet-stream")
                except (URLError, TimeoutError, OSError) as error:
                    status = 503
                    body = json.dumps({"service": owner.service, "version": "unknown",
                                       "healthy": False, "error": "target_unreachable",
                                       "cause": type(error).__name__}).encode()
                    content_type = "application/json"
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("X-Revora-Route", route)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                return

        if not self.external:
            self.stable_url = self._serve(Target)
            self.servers[-1][0].role = "stable"
            self.candidate_url = self._serve(Target)
            self.servers[-1][0].role = "candidate"
        self.gateway_url = self._serve(Gateway)
        self._restore_state()

    def _state_payload(self):
        return json.dumps({"version": 1, "route": self.route,
                           "candidate_mode": self.candidate_mode,
                           "candidate_version": self.candidate_version,
                           "pending": self.pending}, separators=(",", ":"))

    def _persist_state(self):
        """Persist route and in-flight incident state so a worker can resume after restart."""
        if self.graph is None:
            return
        self.graph.query(
            "MERGE (s:ReleaseControllerState {service:$service}) "
            "SET s.payload=$payload,s.updated_at=$updated_at RETURN s.service",
            {"service": self.service, "payload": self._state_payload(),
             "updated_at": datetime.now(timezone.utc).isoformat()})
        logger.info("controller_state_persisted service=%s route=%s incident_active=%s",
                    self.service, self.route, self.pending is not None)

    def _restore_state(self):
        if self.graph is None:
            return
        rows = self.graph.query(
            "MATCH (s:ReleaseControllerState {service:$service}) RETURN s.payload LIMIT 1",
            {"service": self.service}).result_set
        if not rows:
            return
        try:
            state = json.loads(rows[0][0])
            if state.get("version") != 1 or state.get("route") not in {"stable", "candidate"}:
                raise ValueError("unsupported controller state")
            mode = state.get("candidate_mode", "healthy")
            candidate_version = state.get("candidate_version", "v0")
            pending = state.get("pending")
            if mode not in MODES or not isinstance(candidate_version, str) or len(candidate_version) > 64:
                raise ValueError("invalid candidate state")
            if pending is not None and (not isinstance(pending, dict) or not pending.get("id")
                                        or pending.get("service") != self.service):
                raise ValueError("invalid pending incident")
        except (TypeError, KeyError, json.JSONDecodeError, ValueError) as error:
            raise RuntimeError("Stored release controller state is invalid; refusing to guess a route") from error
        self.route = state["route"]
        self.candidate_mode = mode
        self.candidate_version = candidate_version
        self.pending = pending
        if not self.external and self.route == "candidate":
            self.candidate_calls = 0

    @staticmethod
    def _probe(url):
        started = time.perf_counter()
        try:
            with urlopen(url + "/health", timeout=3) as response:
                status = response.status
                raw = response.read()
                route = response.headers.get("X-Revora-Route")
        except HTTPError as error:
            status = error.code
            raw = error.read()
            route = error.headers.get("X-Revora-Route")
        except (URLError, TimeoutError, OSError) as error:
            return {"status": 503, "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    "version": "unknown", "route": None,
                    "probe_error": type(error).__name__}
        try:
            body = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        return {"status": status, "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "version": body.get("version", "unknown") if isinstance(body, dict) else "unknown",
                "route": route}

    def snapshot(self):
        """Return controller state without issuing network traffic."""
        with self.lock:
            return {"service": self.service, "active_route": self.route,
                    "candidate_version": self.candidate_version,
                    "candidate_mode": self.candidate_mode,
                    "incident_active": self.pending is not None,
                    "target_mode": "configured_services" if self.external else "project_demo",
                    "traffic_endpoint": "/api/release/traffic"}

    def status(self):
        snapshot = self.snapshot()
        snapshot["gateway_probe"] = self._probe(self.gateway_url)
        return snapshot

    def park(self):
        """Move the project gateway to stable before updating a candidate."""
        with self.operation_lock:
            with self.lock:
                self.route = "stable"
                onset = self.pending
            if onset is not None:
                aborted = {**onset, "id": str(uuid4()),
                           "timestamp": datetime.now(timezone.utc).isoformat(),
                           "prior_evidence": {"id": onset["id"], "timestamp": onset["timestamp"]},
                           "decision": "manual_park", "failed": True,
                           "after": self._probe(self.gateway_url), "recovered": True,
                           "reason": "Operator parked traffic on stable before automated diagnosis."}
                self._record(aborted)
                with self.lock:
                    self.pending = None
            self._persist_state()
            return self.status()

    def _prior_failure(self, service, version):
        if self.graph is not None:
            rows = self.graph.query(
                "MATCH (i:ReleaseIncident {service:$service, version:$version}) "
                "WHERE i.decision IN ['rollback','promote'] "
                "RETURN i.id,i.timestamp,i.failed ORDER BY i.timestamp DESC LIMIT 1",
                {"service": service, "version": version}).result_set
            return {"id": rows[0][0], "timestamp": rows[0][1]} if rows and rows[0][2] else None
        latest = next((item for item in reversed(self.local_incidents)
                       if item["service"] == service and item["version"] == version
                       and item["decision"] in {"rollback", "promote"}), None)
        return {"id": latest["id"], "timestamp": latest["timestamp"]} if latest and latest["failed"] else None

    @staticmethod
    def _components(components):
        """Validate the dependency/config identifiers a candidate version ships with."""
        items = sorted({str(item).strip() for item in (components or []) if str(item).strip()})
        if len(items) > 8 or any(not re.fullmatch(r"[a-zA-Z0-9._@:/-]{1,80}", item) for item in items):
            raise ValueError("Provide up to 8 components such as payments-sdk@2.1 or config@a41")
        return items

    def _suspect_components(self, service, version, components):
        """Multi-hop memory: find components this candidate shares with a failed release.

        A component is implicated only if it shipped in a measured rollback of a
        different version and never in a promoted release, so shared healthy
        baseline dependencies do not block unrelated candidates.
        """
        if not components:
            return []
        if self.graph is not None:
            rows = self.graph.query(
                "MATCH (c:Component) WHERE c.key IN $components "
                "MATCH (c)<-[:USES]-(bad:ReleaseIncident {service:$service, decision:'rollback', failed:true}) "
                "WHERE bad.version <> $version "
                "AND NOT (c)<-[:USES]-(:ReleaseIncident {service:$service, decision:'promote'}) "
                "RETURN c.key, bad.id, bad.version, bad.timestamp ORDER BY bad.timestamp DESC",
                {"components": components, "service": service, "version": version}).result_set
            matches = [{"component": r[0], "incident_id": r[1], "version": r[2], "timestamp": r[3]} for r in rows]
        else:
            healthy = {c for i in self.local_incidents if i["service"] == service and i.get("decision") == "promote"
                       for c in i.get("components", [])}
            matches = [{"component": c, "incident_id": i["id"], "version": i["version"], "timestamp": i["timestamp"]}
                       for i in reversed(self.local_incidents)
                       if i["service"] == service and i.get("decision") == "rollback" and i.get("failed")
                       and i["version"] != version
                       for c in i.get("components", []) if c in components and c not in healthy]
        seen, unique = set(), []
        for match in matches:
            if match["component"] not in seen:
                seen.add(match["component"])
                unique.append(match)
        return unique

    def _memory_block(self, incident, components, force_recheck=False):
        """Return the graph evidence that blocks this candidate, or None."""
        prior = incident["prior_evidence"]
        if prior:
            incident["memory_match"] = "exact_version"
            return "FalkorDB contains a measured failure for this exact version; it was not activated."
        suspects = [] if force_recheck else self._suspect_components(incident["service"], incident["version"], components)
        if not suspects:
            return None
        first = suspects[0]
        incident["memory_match"] = "shared_component"
        incident["implicated_components"] = suspects
        incident["prior_evidence"] = {"id": first["incident_id"], "timestamp": first["timestamp"],
                                      "version": first["version"], "component": first["component"]}
        names = ", ".join(item["component"] for item in suspects)
        return (f"Shares {names} with failed release {first['version']}; that component has never shipped "
                "in a healthy release, so the candidate was not activated.")

    def _record(self, incident):
        incident.setdefault("onset_id", (incident.get("prior_evidence") or {}).get("id", ""))
        if self.graph is not None:
            self.graph.query(
                "MERGE (i:ReleaseIncident {id:$id}) SET i.service=$service,i.version=$version,"
                "i.failed=$failed,i.decision=$decision,i.timestamp=$timestamp,i.onset_id=$onset_id,"
                "i.handoff_id=$handoff_id,i.sample_count=$sample_count,"
                "i.error_rate=$error_rate,i.p95_latency_ms=$p95_latency_ms,"
                "i.payload=$payload "
                "WITH i OPTIONAL MATCH (prior:ReleaseIncident {id:$prior_id}) "
                "FOREACH (_ IN CASE WHEN prior IS NULL THEN [] ELSE [1] END | "
                "MERGE (i)-[:BASED_ON]->(prior)) "
                "FOREACH (key IN $components | MERGE (c:Component {key:key}) MERGE (i)-[:USES]->(c))",
                {"id": incident["id"], "service": incident["service"],
                 "version": incident["version"], "failed": incident["failed"],
                 "decision": incident["decision"], "timestamp": incident["timestamp"],
                 "payload": json.dumps(incident), "onset_id": incident.get("onset_id", ""),
                 "handoff_id": incident.get("handoff_id", ""),
                 # Gate metrics are first-class properties so Cypher can filter on them.
                 "sample_count": (incident.get("metrics") or {}).get("sample_count"),
                 "error_rate": (incident.get("metrics") or {}).get("error_rate"),
                 "p95_latency_ms": (incident.get("metrics") or {}).get("p95_latency_ms"),
                 "prior_id": (incident.get("prior_evidence") or {}).get("id", ""),
                 "components": incident.get("components", [])})
        else:
            self.local_incidents.append(incident)
        logger.info("incident_recorded service=%s incident=%s onset=%s handoff=%s decision=%s",
                    incident.get("service"), incident.get("id"), incident.get("onset_id"),
                    incident.get("handoff_id"), incident.get("decision"))

    def history(self, limit=12):
        if self.graph is not None:
            rows = self.graph.query(
                "MATCH (i:ReleaseIncident {service:$service}) "
                "RETURN i.payload ORDER BY i.timestamp DESC LIMIT $limit",
                {"service": self.service, "limit": limit}).result_set
            return [json.loads(row[0]) for row in rows]
        return list(reversed(list(self.local_incidents)[-limit:]))

    def inject(self, version, mode, components=None):
        """Leave an intentional fault active so its HTTP failure is observable."""
        if self.external:
            raise ValueError("Fault injection is available only for the project-owned demo service")
        if mode not in {"errors", "mixed", "slow"} or not re.fullmatch(r"[a-zA-Z0-9._-]{1,64}", version):
            raise ValueError("Choose a valid candidate version and failing mode")
        with self.operation_lock:
            with self.lock:
                if self.pending is not None:
                    raise ValueError("Recover the current incident before introducing another")
            components = self._components(components)
            prior = self._prior_failure(self.service, version)
            before = self._probe(self.gateway_url)
            incident = {"id": str(uuid4()), "service": self.service, "version": version,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "measurement_mode": "live_gateway", "before": before, "components": components,
                        "prior_evidence": prior, "canary_samples": [], "recovery_samples": []}
            block_reason = self._memory_block(incident, components)
            if block_reason:
                incident.update({"decision": "blocked_from_memory", "failed": True,
                                 "after": self._probe(self.gateway_url), "reason": block_reason})
                self._record(incident)
                return incident
            with self.lock:
                self.candidate_version = version
                self.candidate_mode = mode
                self.candidate_calls = 0
                self.route = "candidate"
            try:
                during = self._probe(self.gateway_url)
                incident.update({"decision": "introduced", "failed": True, "during": during,
                                 "reason": "Candidate fault is active on the live gateway. Recovery has not run."})
                self._record(incident)
                with self.lock:
                    self.pending = incident
                    self._persist_state()
                return incident
            except Exception:
                # _probe converts expected network failures into measured 503s.
                # Any other failure must still leave the live route safe, then
                # propagate unchanged so programming and storage errors surface.
                with self.lock:
                    self.route = "stable"
                    self.pending = None
                raise

    def diagnose(self):
        """Investigator measures the incident without changing its route."""
        with self.operation_lock:
            with self.lock:
                if self.pending is None:
                    raise ValueError("No active incident to diagnose")
                onset = self.pending
            samples = [self._probe(self.gateway_url) for _ in range(SAMPLE_COUNT)]
            metrics = measure_gates(samples)
            assessment = {**onset, "id": str(uuid4()), "decision": "diagnosed",
                          "timestamp": datetime.now(timezone.utc).isoformat(),
                          "onset_id": onset["id"], "canary_samples": samples, "metrics": metrics,
                          "failed": metrics["failed"],
                          "prior_evidence": {"id": onset["id"], "timestamp": onset["timestamp"]},
                          "reason": "Investigator measured live incident; no recovery action has executed."}
            self._record(assessment)
            return assessment

    def recover_handoff(self, evidence, handoff_id):
        """Allowlisted tool: validate the exact persisted diagnosis before acting."""
        with self.operation_lock:
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", handoff_id or ""):
                raise ValueError("Invalid handoff ID")
            if self.graph is not None:
                rows = self.graph.query(
                    "MATCH (i:ReleaseIncident {service:$service,handoff_id:$handoff_id}) "
                    "RETURN i.payload ORDER BY i.timestamp DESC LIMIT 1",
                    {"service": self.service, "handoff_id": handoff_id}).result_set
                previous = json.loads(rows[0][0]) if rows else None
            else:
                previous = next((i for i in reversed(self.local_incidents)
                                 if i.get("handoff_id") == handoff_id), None)
            if previous:
                # Repair only the crash window where the persisted state still
                # names the exact onset recovered by this durable handoff.
                with self.lock:
                    active = self.pending
                    if active and previous.get("onset_id") == active.get("id"):
                        self.pending = None
                        self.route = "candidate" if previous.get("decision") == "promote" else "stable"
                        self._persist_state()
                # A replay for an older onset is otherwise strictly read-only.
                return previous
            if self.graph is not None:
                rows = self.graph.query(
                    "MATCH (i:ReleaseIncident {id:$incident_id,service:$service}) RETURN i.payload LIMIT 1",
                    {"incident_id": evidence.get("incident_id", ""), "service": self.service}).result_set
                assessment = json.loads(rows[0][0]) if rows else None
            else:
                assessment = next((i for i in self.local_incidents
                                   if i["id"] == evidence.get("incident_id")), None)
            if (not isinstance(evidence, dict) or not isinstance(assessment, dict)
                    or assessment.get("decision") != "diagnosed"
                    or assessment.get("service") != evidence.get("service")
                    or assessment.get("version") != evidence.get("version")):
                raise ValueError("Recovery requires the matching measured diagnosis")
            return self.recover(expected_onset_id=assessment["onset_id"],
                                handoff_id=handoff_id, assessment=assessment)

    def recover(self, expected_onset_id=None, handoff_id=None, assessment=None):
        """Measure the active incident, restore service, and retain the evidence."""
        with self.operation_lock:
            if self.pending is None:
                raise ValueError("No active incident to recover")
            onset = self.pending
            if expected_onset_id is not None and onset["id"] != expected_onset_id:
                raise ValueError("Stale handoff: the active incident has changed")
            result = {**onset, "id": str(uuid4()),
                      "timestamp": datetime.now(timezone.utc).isoformat(),
                      "prior_evidence": {"id": onset["id"], "timestamp": onset["timestamp"]}}
            result["handoff_id"] = handoff_id or ""
            result["onset_id"] = onset["id"]
            if assessment is not None:
                if (not isinstance(assessment, dict) or assessment.get("decision") != "diagnosed"
                        or not isinstance(assessment.get("id"), str) or not assessment.get("id")
                        or not isinstance(assessment.get("timestamp"), str) or not assessment.get("timestamp")
                        or assessment.get("onset_id") != onset["id"]
                        or assessment.get("service") != onset.get("service")
                        or assessment.get("version") != onset.get("version")):
                    raise ValueError("Recovery diagnosis does not match the active incident")
                samples = assessment.get("canary_samples")
                if not isinstance(samples, list) or len(samples) != SAMPLE_COUNT:
                    raise ValueError(f"Recovery diagnosis must contain exactly {SAMPLE_COUNT} canary samples")
                try:
                    metrics = measure_gates(samples)
                except (AttributeError, TypeError, ValueError) as error:
                    raise ValueError("Recovery diagnosis contains invalid canary samples") from error
                reported = assessment.get("metrics")
                if (not isinstance(reported, dict)
                        or reported.get("sample_count") != metrics["sample_count"]
                        or assessment.get("failed") != metrics["failed"]):
                    raise ValueError("Recovery diagnosis metrics contradict its canary samples")
                try:
                    reported_rate = float(reported["error_rate"])
                    reported_p95 = float(reported["p95_latency_ms"])
                except (KeyError, TypeError, ValueError) as error:
                    raise ValueError("Recovery diagnosis is missing measured gate metrics") from error
                if (not math.isfinite(reported_rate) or not math.isfinite(reported_p95)
                        or abs(reported_rate - metrics["error_rate"]) > .001
                        or abs(reported_p95 - metrics["p95_latency_ms"]) > 1):
                    raise ValueError("Recovery diagnosis metrics contradict its canary samples")
                result["diagnosis_id"] = assessment["id"]
                result["prior_evidence"] = {"id": assessment["id"], "timestamp": assessment["timestamp"]}
            else:
                samples = [self._probe(self.gateway_url) for _ in range(SAMPLE_COUNT)]
                metrics = measure_gates(samples)
            failed = metrics["failed"]
            result["canary_samples"] = samples
            result["metrics"] = metrics
            if failed:
                with self.lock:
                    self.route = "stable"
                result["recovery_samples"] = [self._probe(self.gateway_url) for _ in range(3)]
            result["after"] = self._probe(self.gateway_url)
            result["failed"] = failed
            result["decision"] = "rollback" if failed else "promote"
            result["recovered"] = result["after"]["status"] == 200 and (
                not failed or all(sample["status"] == 200 for sample in result["recovery_samples"]))
            result["reason"] = ("Measured the live failure, restored the stable route, and verified healthy requests."
                                if failed else "Candidate passed the measured gates and remains active.")
            self._record(result)
            with self.lock:
                self.pending = None
                self._persist_state()
            return result

    def run(self, version, mode, force_recheck=False, components=None):
        """Operate only the Revora-owned gateway and its two local targets."""
        if mode not in MODES or not version or len(version) > 64:
            raise ValueError("Invalid candidate version or mode")
        with self.operation_lock:
            if self.pending is not None:
                raise ValueError("Recover the active incident before starting another release")
            service = self.service
            components = self._components(components)
            prior = self._prior_failure(service, version)
            before = self._probe(self.gateway_url)
            incident = {"id": str(uuid4()), "service": service, "version": version,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "measurement_mode": "candidate_preflight", "components": components,
                        "before": before, "prior_evidence": prior,
                        "canary_samples": [], "recovery_samples": []}
            block_reason = self._memory_block(incident, components, force_recheck)
            if block_reason and not prior:
                with self.lock:
                    self.route = "stable"
                incident.update({"decision": "blocked_from_memory", "failed": True,
                                 "after": self._probe(self.gateway_url), "reason": block_reason})
                self._record(incident)
                self._persist_state()
                return incident
            if prior:
                # A memory override is never allowed to put a known failed version
                # on the live route. Explicit rechecks stay behind stable preflight.
                with self.lock:
                    self.route = "stable"
                if not force_recheck:
                    incident.update({"decision": "blocked_from_memory", "failed": True,
                                     "after": self._probe(self.gateway_url),
                                     "reason": "Prior measured failure for this exact candidate version."})
                    self._record(incident)
                    self._persist_state()
                    return incident
                incident["recheck_override"] = True
                incident.pop("memory_match", None)

            # Every candidate is checked off-route. A passing result is the only
            # path that can change the caller-visible route to candidate.
            with self.lock:
                self.route = "stable"

            # Direct candidate probes leave caller-visible traffic on stable.
            with self.lock:
                self.candidate_version = version
                self.candidate_mode = mode
                self.candidate_calls = 0
                self.route = "stable"
            sample_url = self.candidate_url
            samples = [self._probe(sample_url) for _ in range(SAMPLE_COUNT)]
            if self.external:
                for sample in samples:
                    sample["route"] = "candidate"
            incident["canary_samples"] = samples
            metrics = measure_gates(samples)
            failed = metrics["failed"]
            incident["metrics"] = metrics
            incident["failed"] = failed
            incident["decision"] = "rollback" if failed else "promote"
            if failed:
                with self.lock:
                    self.route = "stable"
                incident["recovery_samples"] = [self._probe(self.gateway_url) for _ in range(3)]
            else:
                with self.lock:
                    self.route = "candidate"
            incident["after"] = self._probe(self.gateway_url)
            if not failed and incident["after"]["status"] != 200:
                with self.lock:
                    self.route = "stable"
                failed = True
                incident["failed"] = True
                incident["decision"] = "rollback"
                incident["recovery_samples"] = [self._probe(self.gateway_url) for _ in range(3)]
                incident["after"] = self._probe(self.gateway_url)
            incident["recovered"] = (not failed or all(sample["status"] == 200 and sample["route"] == "stable"
                                                   for sample in incident["recovery_samples"]))
            if force_recheck and prior:
                incident["reason"] = ("Explicit fresh preflight passed; candidate was measured off-route before promotion."
                                      if not failed else "Explicit fresh preflight failed; candidate remained off-route and stable traffic was retained.")
            if failed:
                incident["reason"] = ("Candidate failed measured preflight; live route remained on stable."
                                      if self.external else "Measured candidate breached the error or latency gate; live route restored to stable.")
            else:
                incident["reason"] = ("Candidate passed measured preflight and was routed live."
                                      if self.external else "Measured candidate met both gates; live route remains on candidate.")
            self._record(incident)
            self._persist_state()
            return incident

    def close(self):
        for server, worker in self.servers:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
