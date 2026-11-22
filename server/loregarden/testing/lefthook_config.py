"""Read lefthook's hook config the way lefthook does, for tests that inspect it.

A hook is either a `commands` map or a `jobs` list whose entries may be a
`group` of further jobs. Tests asked `config["pre-commit"]["commands"][name]`,
which stops working the day a hook moves to `jobs` (pre-commit did, so its
checks could run after the formatter rather than racing it).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def hook_jobs(config_path: Path, hook: str) -> dict[str, dict[str, Any]]:
    """Every runnable entry of ``hook`` by name, groups flattened, in run order."""
    section = yaml.safe_load(config_path.read_text(encoding="utf-8"))[hook]
    if "commands" in section:
        return dict(section["commands"])
    found: dict[str, dict[str, Any]] = {}
    _collect(section["jobs"], found)
    return found


def _collect(jobs: list[dict[str, Any]], found: dict[str, dict[str, Any]]) -> None:
    for job in jobs:
        if "group" in job:
            _collect(job["group"]["jobs"], found)
        else:
            found[job["name"]] = job
