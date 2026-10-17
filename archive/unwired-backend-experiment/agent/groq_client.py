"""Minimal Groq chat-completions client (OpenAI-compatible function calling).

Why not an SDK: the agent loop needs exactly one endpoint with predictable
retry semantics and small payloads. `httpx` is already a dependency, and every
failure mode maps onto a typed `GroqError` so the API can translate it into an
honest HTTP response instead of a stack trace.
"""

from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger("revora.groq")

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class GroqError(RuntimeError):
    """Typed transport/model error surfaced to the agent loop and the API."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"Groq API error {status_code or 'network'}: {message}")


class GroqClient:
    """One-shot completions with bounded retries and Retry-After support."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 60.0,
        attempts: int = 3,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.attempts = max(1, attempts)

    def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        response_format: dict | None = None,
        temperature: float | None = None,
        max_tokens: int = 1024,
    ) -> dict:
        """Return the assistant message dict ({content, tool_calls, ...})."""
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "max_completion_tokens": max_tokens,
        }
        if tools:
            payload.update(
                {
                    "tools": tools,
                    "tool_choice": "auto",
                    # One tool per turn keeps the enforced step order auditable.
                    "parallel_tool_calls": False,
                }
            )
        if response_format:
            payload["response_format"] = response_format
        if temperature is not None:
            payload["temperature"] = temperature

        last_error: GroqError | None = None
        for attempt in range(self.attempts):
            try:
                response = httpx.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=self.timeout,
                )
            except httpx.HTTPError as error:
                last_error = GroqError(0, f"network failure: {error}")
                logger.warning("groq request failed (attempt %d): %s", attempt + 1, error)
                if attempt < self.attempts - 1:
                    time.sleep(min(1.5**attempt, 8))
                continue

            if response.status_code == 200:
                try:
                    return response.json()["choices"][0]["message"]
                except (KeyError, IndexError, ValueError) as error:
                    raise GroqError(0, f"unexpected response shape: {error}") from error

            detail = response.text[:400]
            last_error = GroqError(response.status_code, detail)
            if response.status_code in RETRYABLE_STATUS and attempt < self.attempts - 1:
                retry_after = response.headers.get("retry-after")
                delay = min(float(retry_after), 15) if (retry_after or "").replace(".", "", 1).isdigit() else min(1.5**attempt, 8)
                logger.warning(
                    "groq %d (attempt %d), retrying in %.1fs: %s",
                    response.status_code, attempt + 1, delay, detail[:160],
                )
                time.sleep(delay)
                continue
            raise last_error

        raise last_error or GroqError(0, "request failed")
