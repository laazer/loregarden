"""LM Studio runner — OpenAI-compatible chat completions against a local LM Studio server."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
from loregarden.services.lmstudio_discovery import is_chat_lmstudio_model

DEFAULT_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_TIMEOUT_SECONDS = 600.0
# Thinking models (Qwen3, etc.) spend most of the budget on reasoning_content.
# LM Studio's default max_tokens is often too low for a real Ticket Studio scope
# reply, which then returns empty content and looks like "local models don't work".
DEFAULT_MAX_TOKENS = int(os.environ.get("LOREGARDEN_LMSTUDIO_MAX_TOKENS") or "8192")

# Print-mode idle budget resets on stdout. Tool rounds and non-stream completions
# can sit quiet for minutes; emit a line on this interval so the run is not killed.
_HEARTBEAT_SECONDS = float(os.environ.get("LOREGARDEN_LMSTUDIO_HEARTBEAT_SECONDS") or "20")


MAX_TOOL_ROUNDS = 25


@contextmanager
def _stdout_heartbeat(label: str):
    """Keep print-mode's idle timeout alive during a blocking HTTP completion."""
    if _HEARTBEAT_SECONDS <= 0:
        yield
        return
    stop = threading.Event()

    def beat() -> None:
        n = 0
        while not stop.wait(_HEARTBEAT_SECONDS):
            n += 1
            elapsed = int(n * _HEARTBEAT_SECONDS)
            print(f"[WAIT] {label} ({elapsed}s)…", flush=True)

    thread = threading.Thread(target=beat, name="lmstudio-heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=0.2)


def _assistant_text(message: dict) -> str:
    """Prefer ``content``; fall back to JSON (or text) buried in reasoning.

    Qwen3-class models under LM Studio often fill ``reasoning_content`` and leave
    ``content`` empty when the token budget is tight. Stage/studio parsers only
    read stdout, so empty content means a silent failed turn.
    """
    content = str(message.get("content") or "").strip()
    if content:
        return content

    reasoning = str(message.get("reasoning_content") or message.get("reasoning") or "").strip()
    if not reasoning:
        return ""

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", reasoning, re.DOTALL)
    if fenced:
        return fenced.group(1).strip()

    start = reasoning.rfind("{")
    end = reasoning.rfind("}")
    if start >= 0 and end > start:
        candidate = reasoning[start : end + 1].strip()
        try:
            json.loads(candidate)
        except json.JSONDecodeError:
            pass
        else:
            return candidate

    return reasoning


def _effort_attempts(body: dict, effort: str) -> list[dict]:
    """Request bodies to try in order: with the effort hint, then without.

    ``reasoning_effort`` is only meaningful to reasoning models, and LM Studio
    answers 400 for a loaded model that does not know the field. Losing a whole
    run over an optional hint is worse than running at the model's own default,
    so every call site falls back rather than propagating that 400.
    """
    if not effort:
        return [body]
    return [{**body, "reasoning_effort": effort}, body]


def _warn_effort_rejected(effort: str) -> None:
    print(
        f"[WARN] LM Studio rejected reasoning_effort={effort!r}; retrying at the model's default",
        file=sys.stderr,
    )


def _post_chat(client: httpx.Client, base_url: str, body: dict, effort: str) -> httpx.Response:
    """POST /chat/completions, dropping ``reasoning_effort`` if the server rejects it."""
    response = None
    for attempt_body in _effort_attempts(body, effort):
        response = client.post(
            f"{base_url}/chat/completions", json=attempt_body, timeout=DEFAULT_TIMEOUT_SECONDS
        )
        if response.status_code == 400 and "reasoning_effort" in attempt_body:
            _warn_effort_rejected(effort)
            continue
        break
    assert response is not None  # _effort_attempts is never empty
    response.raise_for_status()
    return response


class McpBridge:
    """Proxies the model's tool calls to Loregarden's MCP endpoint.

    The Claude and Cursor CLIs speak MCP themselves; LM Studio does not, so the
    loop lives here. Calls go over the same HTTP endpoint those CLIs use rather
    than importing execute_tool, keeping the subprocess boundary intact — this
    process has no database session.
    """

    def __init__(self, client: httpx.Client, url: str, run_id: str, workspace_slug: str) -> None:
        self._client = client
        self._url = url.rstrip("/")
        self._run_id = run_id
        self._workspace_slug = workspace_slug
        self._next_id = 0

    def _rpc(self, method: str, params: dict) -> dict:
        self._next_id += 1
        response = self._client.post(
            self._url,
            json={"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params},
            headers={"X-Loregarden-Orchestrated": "1"},
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json().get("result") or {}

    def tools(self, granted: list[str]) -> list[dict]:
        """Advertised tools the agent may use, in OpenAI function form.

        Filtered to the agent's grant so a small model is not handed twenty
        tools it has no business calling.
        """
        advertised = self._rpc("tools/list", {}).get("tools") or []
        allowed = set(granted)
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}},
                },
            }
            for tool in advertised
            if not allowed or tool.get("name") in allowed
        ]

    def call(self, name: str, arguments: dict) -> str:
        # run_id and workspace_slug are filled in here rather than left to the
        # model: they identify the run it is already executing, and a local
        # model that omits or invents one fails every run-scoped call.
        arguments.setdefault("run_id", self._run_id)
        if self._workspace_slug:
            arguments.setdefault("workspace_slug", self._workspace_slug)
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        parts = [
            str(item.get("text", ""))
            for item in (result.get("content") or [])
            if item.get("type") == "text"
        ]
        return "\n".join(parts) or json.dumps(result)


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
    for entry in models:
        model_id = str(entry.get("id") or entry.get("name") or "").strip()
        if model_id and is_chat_lmstudio_model(model_id):
            return model_id
    raise RuntimeError(
        "LM Studio has no loaded chat models (only embeddings/other); "
        "load a chat model or set lmstudio_model"
    )


def _chat_completion(
    *,
    client: httpx.Client,
    base_url: str,
    model: str,
    prompt: str,
    stream: bool,
    effort: str = "",
) -> str:
    body: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": stream,
        "max_tokens": DEFAULT_MAX_TOKENS,
    }
    if stream:
        # Same drop-the-hint fallback as _post_chat: the status arrives with the
        # headers, before any chunk, so a rejected effort costs nothing to retry.
        for attempt_body in _effort_attempts(body, effort):
            text_parts: list[str] = []
            with client.stream(
                "POST",
                f"{base_url}/chat/completions",
                json=attempt_body,
                timeout=DEFAULT_TIMEOUT_SECONDS,
            ) as response:
                if response.status_code == 400 and "reasoning_effort" in attempt_body:
                    _warn_effort_rejected(effort)
                    continue
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    chunk_raw = line[6:].strip()
                    if chunk_raw == "[DONE]":
                        break
                    chunk = json.loads(chunk_raw)
                    delta = chunk.get("choices", [{}])[0].get("delta", {}) or {}
                    piece = delta.get("content") or delta.get("reasoning_content")
                    if piece:
                        text_parts.append(piece)
                        print(piece, end="", flush=True)
            print()
            return "".join(text_parts)
        return ""

    with _stdout_heartbeat("lmstudio completion"):
        response = _post_chat(client, base_url, body, effort)
    payload = response.json()
    message = payload.get("choices", [{}])[0].get("message", {}) or {}
    content = _assistant_text(message)
    print(content)
    return content


@dataclass(frozen=True)
class _IterationResult:
    """What one fresh-context iteration produced, and whether it was done.

    `finished` distinguishes "the model stopped asking for tools" from "this
    conversation ran out of rounds". They produce the same kind of text and mean
    opposite things: the first is an answer, the second is a conversation that
    hit its ceiling mid-thought. Collapsing them is how an exhausted loop reads
    as a successful stage.
    """

    text: str
    finished: bool


def _run_iterations(
    *,
    client: httpx.Client,
    base_url: str,
    model: str,
    prompt: str,
    bridge: McpBridge,
    tools: list[dict],
    effort: str,
    max_iterations: int,
) -> str:
    """Drive the model across fresh-context iterations until the stage is done.

    A small local model drowns in an ever-growing conversation long before it
    finishes a real stage. Each iteration therefore starts a NEW message list
    from the same task prompt, and the state a fresh start needs — what is done,
    what remains, what the last attempt reported — already lives outside the
    conversation, in the ticket row, the checkpoints and the working tree, which
    the model reads back through its tools.

    Two caps, deliberately not the same one. `MAX_TOOL_ROUNDS` bounds the
    back-and-forth WITHIN one conversation; `max_iterations` bounds how many
    conversations one stage gets. Conflating them would make a long-but-
    progressing stage indistinguishable from a stuck one, which is the
    distinction lg-workflow-integrity-455 exists to make elsewhere.

    An exhausted cap RAISES. The stage-report parser reads stdout, and every
    iteration prints, so returning the last text quietly would let a stage that
    never finished read as one that did.
    """
    last_text = ""
    for iteration in range(1, max_iterations + 1):
        print(f"[ITERATION] {iteration}/{max_iterations}", flush=True)
        result = _chat_with_tools(
            client=client,
            base_url=base_url,
            model=model,
            prompt=prompt,
            bridge=bridge,
            tools=tools,
            effort=effort,
        )
        last_text = result.text
        if result.finished:
            print(f"[ITERATION] stage answered on iteration {iteration}", flush=True)
            return last_text
        print(
            f"[ITERATION] {iteration} ended without answering; restarting with a fresh context",
            file=sys.stderr,
        )

    raise RuntimeError(
        f"LM Studio stage did not finish within {max_iterations} fresh-context "
        f"iteration(s); each ran out of its {MAX_TOOL_ROUNDS} tool rounds. Raise "
        f"LOREGARDEN_LMSTUDIO_MAX_ITERATIONS or the workspace setting if the stage "
        f"genuinely needs more, but a stage that never converges is the thing this "
        f"cap exists to surface."
    )


def _chat_with_tools(
    *,
    client: httpx.Client,
    base_url: str,
    model: str,
    prompt: str,
    bridge: McpBridge,
    tools: list[dict],
    effort: str = "",
) -> _IterationResult:
    """Run the model until it stops asking for tools, then return its answer.

    Completions stay non-streaming so tool_calls arrive whole (streaming would
    need delta reassembly). Heartbeat lines on stdout keep the stage idle
    budget alive while each round waits on the model.
    """
    messages: list[dict] = [{"role": "user", "content": prompt}]

    for round_index in range(MAX_TOOL_ROUNDS):
        print(f"[ROUND] {round_index + 1}/{MAX_TOOL_ROUNDS}", flush=True)
        with _stdout_heartbeat(f"lmstudio round {round_index + 1}"):
            response = _post_chat(
                client,
                base_url,
                {
                    "model": model,
                    "messages": messages,
                    "tools": tools,
                    "tool_choice": "auto",
                    "stream": False,
                    "max_tokens": DEFAULT_MAX_TOKENS,
                },
                effort,
            )
        message = response.json().get("choices", [{}])[0].get("message", {}) or {}
        calls = message.get("tool_calls") or []

        if not calls:
            # The model stopped asking for tools, which is this iteration
            # answering rather than running out of room.
            content = _assistant_text(message)
            print(content)
            return _IterationResult(text=content, finished=True)

        messages.append(message)
        for call in calls:
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            print(f"[TOOL] {name}", flush=True)
            try:
                result = bridge.call(name, arguments)
            except Exception as exc:  # noqa: BLE001 - report to the model, not the operator
                # Handed back rather than raised: a wrong call is something the
                # model can correct on the next round, and killing the run over
                # it loses the work already done.
                result = f"Tool call failed: {exc}"
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": result[:8000],
                }
            )

    # Out of rounds *within this iteration*. The stage report parser reads
    # stdout, so emit what we have rather than nothing. Reported as unfinished so
    # the caller can decide whether another fresh-context iteration is worth
    # spending — this cap bounds one conversation, not the stage.
    print(f"[WARN] stopped after {MAX_TOOL_ROUNDS} tool rounds", file=sys.stderr)
    last = next((m for m in reversed(messages) if m.get("role") == "assistant"), {})
    content = _assistant_text(last if isinstance(last, dict) else {})
    print(content)
    return _IterationResult(text=content, finished=False)


def run_chat(
    *,
    prompt: str,
    base_url: str,
    model: str,
    stream: bool,
    effort: str = "",
    mcp_url: str = "",
    run_id: str = "",
    workspace_slug: str = "",
    granted_tools: list[str] | None = None,
    max_iterations: int = 1,
) -> str:
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

        if mcp_url and run_id:
            bridge = McpBridge(client, mcp_url, run_id, workspace_slug)
            try:
                tools = bridge.tools(granted_tools or [])
            except httpx.HTTPError as exc:
                # Without tools the model can still produce a stage report on
                # stdout, so degrade to plain chat rather than failing the run.
                print(f"[WARN] MCP unavailable, running without tools: {exc}", file=sys.stderr)
                tools = []
            if tools:
                return _run_iterations(
                    client=client,
                    base_url=normalized,
                    model=resolved_model,
                    prompt=prompt,
                    bridge=bridge,
                    tools=tools,
                    effort=effort,
                    max_iterations=max_iterations,
                )

        return _chat_completion(
            client=client,
            base_url=normalized,
            model=resolved_model,
            prompt=prompt,
            stream=stream,
            effort=effort,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Loregarden LM Studio runner")
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default="")
    parser.add_argument("--effort", default="", help="OpenAI-compatible reasoning_effort.")
    parser.add_argument("--mcp-url", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--workspace-slug", default="")
    parser.add_argument(
        "--tools", default="", help="Comma-separated MCP tools this agent may call."
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=1,
        help=(
            "Fresh-context iterations this stage may take. Resolved and validated "
            "by the caller (services.cli_settings.resolve_lmstudio_max_iterations); "
            "the default of 1 keeps a direct invocation behaving as before."
        ),
    )
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
            effort=args.effort,
            mcp_url=args.mcp_url,
            run_id=args.run_id,
            workspace_slug=args.workspace_slug,
            granted_tools=[t for t in args.tools.split(",") if t.strip()],
            max_iterations=args.max_iterations,
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
