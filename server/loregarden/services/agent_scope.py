"""Technical enforcement of each agent's declared file-path scope.

Agent role docs (e.g. backend_implementer_v1.md: "Modify only code within
`/server/**`") declare a directory boundary, but until now that was prompt
text only — nothing stopped the CLI from writing anywhere the OS allowed.
This gives that declaration a real backstop at the permission-bridge layer,
independent of whether the agent chooses to honor its own prompt.
"""

from __future__ import annotations

from pathlib import Path

# Tools whose tool_input carries a target file path to create/modify.
FILE_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})

FILE_PATH_KEYS = ("file_path", "notebook_path", "path")

# Agents restricted to a subtree of the repo, keyed by agent id, valued as
# repo-root-relative directory prefixes. Agents not listed here are
# unrestricted — most roles (planner, spec, reviewers, QA, ...) legitimately
# need broad read/write access. Only agents whose own role doc declares an
# explicit directory boundary are enforced here.
AGENT_PATH_SCOPES: dict[str, tuple[str, ...]] = {
    "backend_implementer": ("server",),
    "frontend_implementer": ("client",),
}


def extract_target_path(tool_input: dict) -> str | None:
    if not isinstance(tool_input, dict):
        return None
    for key in FILE_PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def relative_to_root(path: str, workspace_root: str) -> str | None:
    """Resolve `path` against `workspace_root` and return it as a
    root-relative, forward-slash path — or None if it falls outside the
    root entirely (e.g. an absolute path elsewhere on disk)."""
    try:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = Path(workspace_root) / candidate
        resolved = candidate.resolve()
        root = Path(workspace_root).resolve()
        return resolved.relative_to(root).as_posix()
    except (ValueError, OSError):
        return None


def is_path_in_scope(relative_path: str, allowed_prefixes: tuple[str, ...]) -> bool:
    return any(
        relative_path == prefix or relative_path.startswith(prefix + "/")
        for prefix in allowed_prefixes
    )


def scope_violation_message(
    *,
    agent_id: str,
    agent_name: str,
    tool_name: str,
    path: str,
    allowed_prefixes: tuple[str, ...],
) -> str:
    allowed = " or ".join(f"{prefix}/**" for prefix in allowed_prefixes)
    return (
        f"{agent_name} ({agent_id}) is scoped to {allowed} and cannot use "
        f"{tool_name} on '{path}'. This is a hard technical restriction, not "
        "a request that can be approved around — route this work to an "
        "agent whose scope covers this path instead."
    )


def check_agent_scope(
    *,
    agent_id: str,
    agent_name: str,
    tool_name: str,
    tool_input: dict,
    workspace_root: str,
) -> str | None:
    """Return a denial message if this tool call violates the agent's
    declared path scope, else None (call is unrestricted or in-scope)."""
    allowed_prefixes = AGENT_PATH_SCOPES.get(agent_id)
    if not allowed_prefixes or tool_name not in FILE_WRITE_TOOLS:
        return None

    target = extract_target_path(tool_input)
    if not target:
        return None

    relative = relative_to_root(target, workspace_root)
    if relative is None:
        # Can't place the path inside the repo at all — fail closed for a
        # scoped agent rather than silently letting an unresolvable target
        # (e.g. an absolute path elsewhere on disk) through.
        return scope_violation_message(
            agent_id=agent_id,
            agent_name=agent_name,
            tool_name=tool_name,
            path=target,
            allowed_prefixes=allowed_prefixes,
        )

    if is_path_in_scope(relative, allowed_prefixes):
        return None

    return scope_violation_message(
        agent_id=agent_id,
        agent_name=agent_name,
        tool_name=tool_name,
        path=target,
        allowed_prefixes=allowed_prefixes,
    )
