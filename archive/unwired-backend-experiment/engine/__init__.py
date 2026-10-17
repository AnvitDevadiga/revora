"""Revora belief-evolution engine.

When a new Experience+Evidence pair lands in the graph, this package answers
the core Revora question:

    "Does this new evidence invalidate any existing :Belief node?"

`reflection.GroqReflector` asks a Groq-hosted LLM (strict JSON contract).
`belief_engine.BeliefEngine` orchestrates the graph writes: CONTRADICTS /
SUPPORTS edges, successor beliefs via UPDATES, and risk-tolerance decay.
When no LLM is configured or the call fails, a transparent keyword heuristic
classifies the evidence instead — the demo never silently fabricates.
"""

from .belief_engine import BeliefEngine
from .reflection import GroqReflector

__all__ = ["BeliefEngine", "GroqReflector"]
