from __future__ import annotations

import json
import os

_BACKEND = os.environ.get("LLM_BACKEND", "anthropic").lower()


def chat(system: str, user: str, max_tokens: int) -> str:
    """Single-shot chat. Always streams under the hood — large rewrite batches
    can exceed the API's non-streaming deadline, and a slow Ollama instance
    benefits from bytes-flowing keepalive through any reverse proxy."""
    if _BACKEND == "ollama":
        return _ollama(system, user, max_tokens)
    return _anthropic(system, user, max_tokens)


def _anthropic(system: str, user: str, max_tokens: int) -> str:
    import anthropic

    client = anthropic.Anthropic()
    with client.messages.stream(
        model=os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5"),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    ) as stream:
        final = stream.get_final_message()
    return final.content[0].text


def _ollama(system: str, user: str, max_tokens: int) -> str:
    import httpx

    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    model = os.environ.get("OLLAMA_MODEL", "mistral")
    timeout = float(os.environ.get("OLLAMA_TIMEOUT", "1800"))
    parts: list[str] = []
    with httpx.stream(
        "POST",
        f"{host}/api/chat",
        json={
            "model": model,
            "stream": True,
            "options": {"num_predict": max_tokens},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=timeout,
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            if msg := chunk.get("message"):
                parts.append(msg.get("content", ""))
            if chunk.get("done"):
                break
    return "".join(parts)
