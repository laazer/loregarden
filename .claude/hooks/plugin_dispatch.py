#!/usr/bin/env python3
"""Run one of this directory's hooks on behalf of the loregarden plugin.

The plugin (`claude-plugin/`) reaches this directory through a symlink and
calls `plugin_dispatch.py <hook>` for every hook it ships. A repository that
carries its own copy of the hook (loregarden itself, through
`.claude/settings.json`) already runs it, so the plugin's run steps aside there
rather than check, and deny, every command twice.

Standard library only, Python 3.9: hooks run under the system `python3`.
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

#: The hooks the plugin ships. A name outside this set is a broken hooks.json.
PLUGIN_HOOKS = frozenset(
    {"git_signing_guard", "pr_duplicate_work_precheck", "pr_ux_section_precheck"}
)


def project_runs_its_own(name: str, project_dir: str) -> bool:
    """Whether the session's project ships this hook itself."""
    return bool(project_dir) and (Path(project_dir) / ".claude" / "hooks" / f"{name}.py").is_file()


def main(argv: list) -> int:
    if len(argv) != 2 or argv[1] not in PLUGIN_HOOKS:
        # Exit 1 is a non-blocking hook error: the session shows it, the command still runs.
        print(
            f"loregarden plugin: unknown hook {argv[1:]!r}; expected one of {sorted(PLUGIN_HOOKS)}",
            file=sys.stderr,
        )
        return 1
    name = argv[1]
    if project_runs_its_own(name, os.environ.get("CLAUDE_PROJECT_DIR", "")):
        return 0
    script = Path(__file__).resolve().with_name(f"{name}.py")
    sys.argv = [str(script)]
    # The hook reads stdin and ends in sys.exit; its exit code passes straight through.
    runpy.run_path(str(script), run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
