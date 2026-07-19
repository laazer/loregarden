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


MAX_TOOL_ROUNDS = 25


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


def _chat_with_tools(
    *,
    client: httpx.Client,
    base_url: str,
    model: str,
    prompt: str,
    bridge: McpBridge,
    tools: list[dict],
) -> str:
    """Run the model until it stops asking for tools, then return its answer.

    Non-streaming: tool_calls arrive as deltas that have to be reassembled when
    streaming, and the terminal output is what matters here, not liveness.
    """
    messages: list[dict] = [{"role": "user", "content": prompt}]

    for _ in range(MAX_TOOL_ROUNDS):
        response = client.post(
            f"{base_url}/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "tools": tools,
                "tool_choice": "auto",
                "stream": False,
            },
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        message = response.json().get("choices", [{}])[0].get("message", {}) or {}
        calls = message.get("tool_calls") or []

        if not calls:
            content = str(message.get("content") or "")
            print(content)
            return content

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

    # Out of rounds. The stage report parser reads stdout, so emit what we have
    # rather than nothing.
    print(f"[WARN] stopped after {MAX_TOOL_ROUNDS} tool rounds", file=sys.stderr)
    last = next((m.get("content") for m in reversed(messages) if m.get("role") == "assistant"), "")
    content = str(last or "")
    print(content)
    return content


def run_chat(
    *,
    prompt: str,
    base_url: str,
    model: str,
    stream: bool,
    mcp_url: str = "",
    run_id: str = "",
    workspace_slug: str = "",
    granted_tools: list[str] | None = None,
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
                return _chat_with_tools(
                    client=client,
                    base_url=normalized,
                    model=resolved_model,
                    prompt=prompt,
                    bridge=bridge,
                    tools=tools,
                )

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
    parser.add_argument("--mcp-url", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--workspace-slug", default="")
    parser.add_argument(
        "--tools", default="", help="Comma-separated MCP tools this agent may call."
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
            mcp_url=args.mcp_url,
            run_id=args.run_id,
            workspace_slug=args.workspace_slug,
            granted_tools=[t for t in args.tools.split(",") if t.strip()],
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
