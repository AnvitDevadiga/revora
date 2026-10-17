"""Run sessions: thread-safe event streams behind the SSE endpoint.

One `RunSession` per agent run. The worker thread calls `emit(event)`;
HTTP subscribers call `wait_after(index)` and receive new events, a heartbeat
signal (None) or the terminal marker. Reconnecting clients can resume from any
index — the full event log is retained for the session's lifetime, which also
makes past runs replayable through `GET /api/agent/runs/{run_id}`.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

from memory import utc_now


class RunSession:
    def __init__(self, run_id: str, config: dict):
        self.run_id = run_id
        self.config = config
        self.events: list[dict] = []
        self.result: dict | None = None
        self.error: str | None = None
        self.done = False
        self.created_at = utc_now()
        self._condition = threading.Condition()

    # -- producer side ------------------------------------------------------
    def emit(self, event: dict) -> dict:
        with self._condition:
            stamped = {"seq": len(self.events), "at": utc_now(), **event}
            self.events.append(stamped)
            self._condition.notify_all()
            return stamped

    def finish(self, result: dict | None = None, error: str | None = None) -> None:
        with self._condition:
            self.result = result
            self.error = error
            self.done = True
            self.events.append({"seq": len(self.events), "at": utc_now(), "type": "stream_end", "status": "failed" if error else "completed"})
            self._condition.notify_all()

    # -- consumer side ------------------------------------------------------
    def wait_after(self, index: int, timeout: float = 12.0) -> list[dict] | None:
        """Events after `index`, [] when finished, or None to request a heartbeat."""
        with self._condition:
            if index < len(self.events):
                return self.events[index:]
            if self.done:
                return []
            self._condition.wait(timeout)
            if index < len(self.events):
                return self.events[index:]
            if self.done:
                return []
            return None

    def snapshot(self, include_events: bool = True) -> dict:
        with self._condition:
            payload = {
                "run_id": self.run_id,
                "status": "failed" if self.error else ("completed" if self.done else "running"),
                "error": self.error,
                "created_at": self.created_at,
                "config": self.config,
                "result": self.result,
                "event_count": len(self.events),
            }
            if include_events:
                payload["events"] = list(self.events)
            return payload


class RunRegistry:
    """Bounded registry of recent runs (newest first)."""

    def __init__(self, capacity: int = 64):
        self.capacity = capacity
        self._sessions: OrderedDict[str, RunSession] = OrderedDict()
        self._lock = threading.RLock()

    def create(self, run_id: str, config: dict) -> RunSession:
        session = RunSession(run_id, config)
        with self._lock:
            self._sessions[run_id] = session
            while len(self._sessions) > self.capacity:
                self._sessions.popitem(last=False)
        return session

    def get(self, run_id: str) -> RunSession | None:
        with self._lock:
            return self._sessions.get(run_id)

    def summaries(self, limit: int = 20) -> list[dict]:
        with self._lock:
            sessions = list(self._sessions.values())[-limit:]
        return [session.snapshot(include_events=False) for session in reversed(sessions)]
