"""The `loregarden_disk_usage` MCP tool: where the disk went, by name and size.

Read-only, and narrower than read-only: it returns the names, sizes and kinds
the disk scan recorded and nothing else. It cannot open a file, list a file's
contents, or delete anything. Cleanup is a person's action on Machine → Disk,
behind a preview and a confirmation; no tool reaches it.

`rescan` starts a fresh metadata walk in the background (the same scan the page
runs). The tool runs the `disk_usage` operation
(`services/operations/disk_operations.py`); this module holds its schema.
"""

from __future__ import annotations

from typing import Any

from loregarden.mcp.tool_ids import McpTool

TOOL_DEFINITION: dict[str, Any] = {
    "name": McpTool.DISK_USAGE,
    "description": (
        "Find what is using disk space on this machine: every registered "
        "workspace, its worktrees (including ones outside the checkout), and "
        "folders a person added. Returns names, allocated sizes and a kind per "
        "folder (worktree, dependencies, build_output, cache, git_store, ...) from "
        "the last scan, largest first. Metadata only: it cannot read file "
        "contents or delete anything. To free space, tell the person which "
        "entries to review on Machine → Disk, where cleanup is checked and "
        "confirmed. Pass `path` to drill into one folder; `rescan` to start a "
        "fresh scan in the background (call again to read it)."
    ),
}
