"""LM Studio runner — OpenAI-compatible chat completions against a local LM Studio server."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_TIMEOUT_SECONDS = 600.0


def _normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def _resolve_model(client: httpx.Client, base_url: str, model: str) -> str:
    if model.strip():
        return model.strip()
    response = client.get(f"{base_url}/models")
    response.raise_for_status()
    payload = response.json()
    models = payload.get("data") or []
    if not models:
        raise RuntimeError("LM Studio has no loaded models; load a model or set lmstudio_model")
    return str(models[0].get("id") or models[0].get("name") or "")


def _chat_completion(
    *,
    client: httpx.Client,
    base_url: str,
    model: str,
    prompt: str,
    stream: bool,
) -> str:
    body: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": stream,
    }
    if stream:
        text_parts: list[str] = []
        with client.stream(
            "POST",
            f"{base_url}/chat/completions",
            json=body,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                chunk_raw = line[6:].strip()
                if chunk_raw == "[DONE]":
                    break
                chunk = json.loads(chunk_raw)
                delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content")
                if delta:
                    text_parts.append(delta)
                    print(delta, end="", flush=True)
        print()
        return "".join(text_parts)

    response = client.post(
        f"{base_url}/chat/completions",
        json=body,
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    message = payload.get("choices", [{}])[0].get("message", {})
    content = str(message.get("content") or "")
    print(content)
    return content


def run_chat(*, prompt: str, base_url: str, model: str, stream: bool) -> str:
    stub = os.environ.get("LOREGARDEN_LMSTUDIO_STUB_RESPONSE")
    if stub is not None:
        print(stub)
        return stub

    if os.environ.get("LOREGARDEN_FORCE_AGENT_FAIL") == "1":
        print("agent run forced to fail (LOREGARDEN_FORCE_AGENT_FAIL=1)", file=sys.stderr)
        raise RuntimeError("forced agent failure")

    normalized = _normalize_base_url(base_url)
    with httpx.Client() as client:
        resolved_model = _resolve_model(client, normalized, model)
        if not resolved_model:
            raise RuntimeError("Could not resolve LM Studio model id")
        return _chat_completion(
            client=client,
            base_url=normalized,
            model=resolved_model,
            prompt=prompt,
            stream=stream,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Loregarden LM Studio runner")
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default="")
    parser.add_argument(
        "--stream",
        action="store_true",
        default=os.environ.get("LOREGARDEN_LMSTUDIO_STREAM", "").lower() in {"1", "true", "yes"},
    )
    args = parser.parse_args()

    prompt_path = Path(args.prompt_file)
    if not prompt_path.is_file():
        print(f"prompt file not found: {prompt_path}", file=sys.stderr)
        return 2

    prompt = prompt_path.read_text(encoding="utf-8")
    try:
        run_chat(
            prompt=prompt,
            base_url=args.base_url,
            model=args.model,
            stream=args.stream,
        )
    except httpx.HTTPError as exc:
        print(f"LM Studio request failed: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
