"""FalkorDB connection setup shared by the active Revora subsystems."""
from __future__ import annotations

import os
from typing import Any


def connect_graph() -> tuple[Any, Any, str]:
    """Return (client, graph, graph_name), or an in-memory fallback when unset."""
    url = os.getenv("FALKORDB_URL")
    graph_name = os.getenv("FALKORDB_GRAPH", "revora")
    if not url:
        if (os.getenv("APP_ENV", "development").lower() in {"production", "prod"}
                and os.getenv("ALLOW_MEMORY_FALLBACK", "false").lower() != "true"):
            raise RuntimeError("FALKORDB_URL is required in production; in-memory fallback is development-only")
        return None, None, graph_name

    from falkordb import FalkorDB

    client = FalkorDB.from_url(url, socket_connect_timeout=3, socket_timeout=5,
                               health_check_interval=30)
    graph = client.select_graph(graph_name)
    graph.query("RETURN 1")
    return client, graph, graph_name
