"""Agent-config primitives shared by the Studio service and its view builders.

Small, dependency-light pieces both sides need: the role preamble, the MCP tool
defaults, and reading a role file off disk. They live here so
``studio_agent_views`` can use them without importing the service that imports
it.
"""

from __future__ import annotations

import json
import re

from loregarden.config import settings
from loregarden.mcp.tool_ids import (
    MEMORY_DEFAULT_MCP_TOOLS,
    STAGE_DEFAULT_MCP_TOOLS,
    mcp_tool_values,
)

_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*(?:\n|$)", re.DOTALL)


def parse_markdown_frontmatter(text: str) -> dict[str, str]:
    body = (text or "").lstrip("\ufeff")
    if not body.startswith("---"):
        return {}
    match = _FRONTMATTER_RE.match(body)
    if not match:
        return {}
    block = match.group(0)
    inner = block.strip().removeprefix("---").removesuffix("---").strip()
    result: dict[str, str] = {}
    for line in inner.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        result[key.strip()] = value.strip()
    return result


def frontmatter_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"true", "yes", "1"}:
        return True
    if normalized in {"false", "no", "0"}:
        return False
    return None


def strip_markdown_frontmatter(text: str) -> str:
    """Remove YAML frontmatter — `---` fences break markdown preview (setext headings)."""
    body = (text or "").lstrip("\ufeff")
    if not body.startswith("---"):
        return text
    match = _FRONTMATTER_RE.match(body)
    if not match:
        return text
    return body[match.end() :].lstrip("\n")


STUDIO_ROLE_PREAMBLE = """**Loregarden MCP:** Use MCP tools per `agent_context/agents/common_assets/loregarden_mcp_v1.md` for ticket workflow state.

**Memory protocol:** Read `agent_context/agents/common_assets/memory_protocol_v1.md` — use MCP for memory, learnings, and blog posts (Obsidian + SQLite graph); always pass `workspace_slug`; never write vault or SQLite files directly.
"""


def merge_tool_lists(*groups: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for name in group:
            if name not in seen:
                seen.add(name)
                out.append(name)
    return out


def default_mcp_tools() -> list[str]:
    return merge_tool_lists(
        mcp_tool_values(STAGE_DEFAULT_MCP_TOOLS),
        mcp_tool_values(MEMORY_DEFAULT_MCP_TOOLS),
    )


def resolve_studio_mcp_tools(raw_tools: list[str] | None, *, mcp_enabled: bool) -> list[str]:
    if not mcp_enabled:
        return []
    base = raw_tools if raw_tools else default_mcp_tools()
    return merge_tool_lists(base, mcp_tool_values(MEMORY_DEFAULT_MCP_TOOLS))


def ensure_studio_role_preamble(role_body: str) -> str:
    body = (role_body or "").strip()
    if "memory_protocol_v1.md" in body:
        return body
    if body:
        return f"{STUDIO_ROLE_PREAMBLE}\n{body}"
    return STUDIO_ROLE_PREAMBLE.strip()


def parse_json_list(raw: str, model_cls):
    data = json.loads(raw or "[]")
    return [model_cls.model_validate(item) for item in data]


def load_role_body(role_file: str) -> tuple[str, str]:
    if not role_file:
        return "", ""
    path = settings.agent_context_dir / role_file
    if not path.is_file():
        return "", role_file
    text = path.read_text(encoding="utf-8")
    # Prefer the frontmatter `description:`; otherwise the first prose line of the
    # body (frontmatter fences stripped so `---` never becomes the description).
    description = parse_markdown_frontmatter(text).get("description", "")
    if not description:
        for line in strip_markdown_frontmatter(text).splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                description = stripped[:240]
                break
    return text, description
