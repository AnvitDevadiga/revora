"""An agent's bounded HTTP adapter for one operator-configured recovery endpoint."""
import json
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class HTTPRecoveryTool:
    def __init__(self, origin, api_key=""):
        target = urlsplit(origin)
        if (target.scheme not in {"http", "https"} or not target.hostname or target.username or
                target.password or target.query or target.fragment or target.path not in {"", "/"}):
            raise ValueError("Recovery tool requires one configured HTTP(S) origin")
        if target.scheme != "https" and target.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Remote recovery tool transport requires HTTPS")
        self.url = origin.rstrip("/") + "/api/coordination/tool/recover"
        self.api_key = api_key

    def recover_handoff(self, evidence, handoff_id):
        # The server reloads authoritative evidence from the graph. The worker
        # sends only its handoff ID, never arbitrary URLs or release commands.
        request = Request(self.url, data=json.dumps({"handoff_id": handoff_id}).encode(),
                          headers={"Content-Type": "application/json", **({"x-api-key": self.api_key} if self.api_key else {})})
        with urlopen(request, timeout=30) as response:
            return json.load(response)
