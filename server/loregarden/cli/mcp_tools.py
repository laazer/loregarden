"""`loregarden mcp` — run any MCP tool from a terminal, without the control-plane server.

The MCP surface is normally reached over HTTP (`POST /mcp`) or the stdio proxy, both of
which assume a running server. `execute_tool` itself only needs a DB session, so these
subcommands dispatch it in-process against the same SQLite file the server uses:

    loregarden mcp list
    loregarden mcp describe loregarden_get_ticket
    loregarden mcp call loregarden_get_ticket ticket_id=42

Arguments are `key=value` pairs coerced through the tool's own JSON Schema, or a whole
JSON object via `--json`. `key=@path` reads the value from a file (`key=@-` from stdin),
which is how long content gets in without pasting it onto a command line.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from loregarden.cli.errors import UsageError
from loregarden.db import session as db_session
from loregarden.mcp.tools import TOOL_DEFINITIONS, execute_tool
from sqlmodel import Session

TOOL_PREFIX = "loregarden_"


def _tool_def(name: str) -> dict[str, Any]:
    """Resolve a tool by name, accepting the name with or without its prefix."""
    candidates = {name, f"{TOOL_PREFIX}{name}"}
    for tool in TOOL_DEFINITIONS:
        if tool["name"] in candidates:
            return tool
    raise UsageError(f"Unknown tool: {name}. Run `list` to see the {len(TOOL_DEFINITIONS)} tools.")


def _properties(tool: dict[str, Any]) -> dict[str, Any]:
    return tool.get("inputSchema", {}).get("properties", {})


def _read_value_source(raw: str) -> str:
    """Expand a `@path` / `@-` value into its file or stdin contents."""
    if not raw.startswith("@"):
        return raw
    ref = raw[1:]
    if ref == "-":
        return sys.stdin.read()
    path = Path(ref)
    if not path.is_file():
        raise UsageError(f"No such file: {ref}")
    return path.read_text()


def _coerce_scalar(raw: str, *, key: str, kind: str) -> Any:
    if kind == "integer":
        try:
            return int(raw)
        except ValueError as exc:
            raise UsageError(f"{key} must be an integer: {raw!r}") from exc
    if kind == "number":
        try:
            return float(raw)
        except ValueError as exc:
            raise UsageError(f"{key} must be a number: {raw!r}") from exc
    if kind == "boolean":
        lowered = raw.strip().lower()
        if lowered in ("1", "true", "yes", "on"):
            return True
        if lowered in ("0", "false", "no", "off"):
            return False
        raise UsageError(f"{key} must be a boolean (true/false): {raw!r}")
    return raw


def _coerce_structured(raw: str, *, key: str, kind: str) -> Any:
    """Parse an array/object value: JSON when it looks like JSON, else a text fallback."""
    text = raw.strip()
    if text.startswith(("[", "{")):
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise UsageError(f"{key} is not valid JSON: {exc}") from exc
    if kind == "object":
        raise UsageError(f"{key} must be a JSON object")
    # Arrays accept comma- or newline-separated text; the tool layer re-normalizes.
    separator = "\n" if "\n" in raw else ","
    return [part.strip() for part in raw.split(separator) if part.strip()]


def _coerce_value(raw: str, *, key: str, schema: dict[str, Any]) -> Any:
    kind = schema.get("type", "string")
    if kind in ("array", "object"):
        return _coerce_structured(raw, key=key, kind=kind)
    return _coerce_scalar(raw, key=key, kind=kind)


def parse_pair_arguments(tool: dict[str, Any], pairs: list[str]) -> dict[str, Any]:
    """Turn `key=value` CLI pairs into a tool argument mapping, typed by the schema."""
    properties = _properties(tool)
    arguments: dict[str, Any] = {}
    for pair in pairs:
        key, separator, raw = pair.partition("=")
        key = key.strip()
        if not separator or not key:
            raise UsageError(f"Expected key=value, got {pair!r}")
        if key not in properties:
            known = ", ".join(sorted(properties)) or "(none)"
            raise UsageError(f"{tool['name']} has no argument {key!r}. Accepts: {known}")
        arguments[key] = _coerce_value(_read_value_source(raw), key=key, schema=properties[key])
    return arguments


def _parse_json_arguments(raw: str) -> dict[str, Any]:
    text = sys.stdin.read() if raw.strip() == "-" else _read_value_source(raw)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UsageError(f"--json is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise UsageError("--json must be a JSON object")
    return parsed


def build_arguments(tool: dict[str, Any], *, json_arg: str | None, pairs: list[str]) -> dict:
    """Merge `--json` and `key=value` arguments; pairs win on conflict."""
    arguments = _parse_json_arguments(json_arg) if json_arg else {}
    arguments.update(parse_pair_arguments(tool, pairs))
    return arguments


def _format_tool_list(*, as_json: bool) -> str:
    if as_json:
        return json.dumps(TOOL_DEFINITIONS, indent=2)
    width = max(len(tool["name"]) for tool in TOOL_DEFINITIONS)
    lines = [
        f"{tool['name']:<{width}}  {tool.get('description', '')}"
        for tool in sorted(TOOL_DEFINITIONS, key=lambda t: t["name"])
    ]
    return "\n".join(lines)


def _format_tool_schema(tool: dict[str, Any]) -> str:
    payload = {
        "name": tool["name"],
        "description": tool.get("description", ""),
        "inputSchema": tool.get("inputSchema", {}),
    }
    return json.dumps(payload, indent=2)


def _run_tool(name: str, arguments: dict[str, Any], *, orchestrated: bool) -> str:
    db_session.init_db()
    with Session(db_session.engine) as session:
        return execute_tool(session, name, arguments, orchestrated=orchestrated)


def register(sub: argparse._SubParsersAction) -> None:
    """Add the `mcp` tool subcommands to the root CLI's `mcp` group."""
    list_parser = sub.add_parser("list", help="List every MCP tool with its description.")
    list_parser.add_argument("--json", action="store_true", help="Emit full tool definitions.")
    list_parser.set_defaults(run=_run_list)

    describe_parser = sub.add_parser("describe", help="Print one tool's JSON Schema.")
    describe_parser.add_argument("tool")
    describe_parser.set_defaults(run=_run_describe)

    call_parser = sub.add_parser("call", help="Execute a tool and print its JSON result.")
    call_parser.add_argument("tool")
    call_parser.add_argument(
        "args",
        nargs="*",
        metavar="key=value",
        help="Tool arguments. `key=@path` reads the value from a file, `key=@-` from stdin.",
    )
    call_parser.add_argument(
        "--json",
        dest="json_args",
        metavar="JSON",
        help="Arguments as a JSON object; `-` reads stdin, `@path` reads a file.",
    )
    call_parser.add_argument(
        "--orchestrated",
        action="store_true",
        help="Apply the orchestrated-agent tool policy (denies pipeline-restricted tools).",
    )
    call_parser.set_defaults(run=_run_call)


_VALUE_TAKING_FLAGS = ("--json",)


def hoist_call_flags(argv: list[str]) -> list[str]:
    """Move `call`'s options ahead of its `key=value` arguments.

    argparse stops collecting a variadic positional at the first option, so
    `mcp call tool a=b --orchestrated` would fail as an unrecognized argument. Tool
    arguments are always `key=value` and never start with `-`, so reordering is
    unambiguous. Only tokens after `call` move; the rest of the command line is untouched.
    """
    if "call" not in argv:
        return argv
    split = argv.index("call") + 1
    head, rest = argv[:split], argv[split:]
    flags: list[str] = []
    positionals: list[str] = []
    index = 0
    while index < len(rest):
        token = rest[index]
        if token == "--":
            positionals.extend(rest[index + 1 :])
            break
        if token.startswith("-") and token != "-":
            flags.append(token)
            if token in _VALUE_TAKING_FLAGS and index + 1 < len(rest):
                index += 1
                flags.append(rest[index])
        else:
            positionals.append(token)
        index += 1
    return [*head, *flags, *positionals]


def _run_list(args: argparse.Namespace) -> str:
    return _format_tool_list(as_json=args.json)


def _run_describe(args: argparse.Namespace) -> str:
    return _format_tool_schema(_tool_def(args.tool))


def _run_call(args: argparse.Namespace) -> str:
    tool = _tool_def(args.tool)
    arguments = build_arguments(tool, json_arg=args.json_args, pairs=args.args)
    return _run_tool(tool["name"], arguments, orchestrated=args.orchestrated)
