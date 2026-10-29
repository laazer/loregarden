"""The `loregarden_disk_usage` MCP tool: where the disk went, by name and size.

Read-only, and narrower than read-only: it returns the names, sizes and kinds
the disk scan recorded and nothing else. It cannot open a file, list a file's
contents, or delete anything. Cleanup is a person's action on Machine → Disk,
behind a preview and a confirmation; no tool reaches it.

`rescan` starts a fresh metadata walk in the background (the same scan the page
runs). It changes nothing on disk, so it does not make the tool a write.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlmodel import Session

from loregarden.mcp.tool_ids import McpTool
from loregarden.models.domain import REGENERABLE_KINDS, DiskKind
from loregarden.services.disk_usage.scanner import DiskNode
from loregarden.services.disk_usage.store import (
    find_node,
    latest_result,
    scan_status,
    start_scan,
    trimmed,
    volume_usage,
)

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
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path of a folder from an earlier answer. Omit for the scan roots.",
            },
            "depth": {
                "type": "integer",
                "minimum": 1,
                "maximum": 3,
                "description": "Levels below `path` to include. Default 2.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 30,
                "description": "Largest children per folder; the rest are summed into one entry. Default 12.",
            },
            "rescan": {
                "type": "boolean",
                "description": "Start a fresh scan in the background. Default false.",
            },
        },
        "additionalProperties": False,
    },
}


class DiskUsageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = ""
    depth: int = Field(default=2, ge=1, le=3)
    limit: int = Field(default=12, ge=1, le=30)
    rescan: bool = False


def _gib(size: int) -> str:
    for unit, scale in (("TB", 1 << 40), ("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if size >= scale:
            return f"{size / scale:.1f} {unit}"
    return f"{size} B"


def _shape(node: DiskNode) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": node.name,
        "path": node.path,
        "size": _gib(node.size_bytes),
        "size_bytes": node.size_bytes,
        "kind": node.kind.value,
        "files": node.file_count,
    }
    if node.kind in REGENERABLE_KINDS or node.kind is DiskKind.WORKTREE:
        out["cleanup"] = "candidate: a person can review it on Machine → Disk"
    if node.children:
        out["children"] = [_shape(child) for child in node.children]
    return out


def disk_usage_tool(session: Session, arguments: dict[str, Any]) -> str:
    try:
        request = DiskUsageRequest.model_validate(arguments)
    except ValidationError as exc:
        raise ValueError(f"invalid arguments for {McpTool.DISK_USAGE.value}: {exc}") from exc

    status = start_scan(session) if request.rescan else scan_status()
    volume = volume_usage()
    answer: dict[str, Any] = {
        "volume": {
            "free": _gib(volume.free_bytes),
            "used": _gib(volume.used_bytes),
            "total": _gib(volume.total_bytes),
        },
        "scan_status": status.model_dump(mode="json"),
    }
    result = latest_result()
    if result is None:
        answer["note"] = "No finished scan yet. Call again with rescan=true, then again to read it."
        return json.dumps(answer, indent=2)
    answer["scanned_at"] = result.finished_at.isoformat()
    if result.unreadable:
        answer["unreadable_folders"] = result.unreadable
    if result.truncated:
        answer["truncated"] = True
    if request.path:
        node = find_node(result, request.path)
        if node is None:
            raise ValueError(
                f"The last scan has no detail for {request.path}; pick a path from an earlier answer."
            )
        answer["tree"] = [_shape(trimmed(node, request.depth, request.limit))]
    else:
        answer["tree"] = [
            _shape(trimmed(root, request.depth, request.limit)) for root in result.tree
        ]
    return json.dumps(answer, indent=2)
