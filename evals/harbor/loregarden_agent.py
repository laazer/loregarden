"""Harbor agent: Claude Code given the prompt loregarden's own implement stage would give it.

    PYTHONPATH=. harbor run -p .build -a loregarden_agent:LoregardenImplement \\
        -m claude-sonnet-5-5 -o jobs --job-name lg-impl -n 2 -y

The bare `claude-code` agent gets the task's instruction and nothing else. This one
gets the same instruction filed as a loregarden bug, wrapped in the implement-stage
prompt the control plane renders for it (`render_stage.py`, run inside the
container against a fresh database): run context, CLI transport, stage-report
contract, role body, tool catalog, memory protocol. Same model, same container,
same verifier; the prompt is the only variable, which is the comparison the eval
exists to make.

**Which loregarden renders it.** Each task's `/app` is loregarden at some old
commit, because loregarden is also the repository the tasks fix. The harness under
test is a different thing: by default it is this checkout's `HEAD`
(`LOREGARDEN_EVAL_HARNESS=<ref>` picks another), archived and installed at
`/opt/lg`. Its CLI runs with `LOREGARDEN_REPO_ROOT=/app`, so the database and the
workspace are the task's while the code and the `agent_context/` prompt sources
are the harness's. For the run, `/app/scripts/loregarden-cli.sh` (the path the
prompt names) becomes a shim to it, and `/app/agent_context/` is the harness's;
both are restored from `/app`'s own commit before the verifier runs. None of the
tasks changes either path. `LOREGARDEN_EVAL_HARNESS=snapshot` renders with the
task's own code instead.

The agent works in `/app`, where the verifier grades. Loregarden also cuts a
ticket worktree when it checks the stage out, but the rendered prompt names no
path in it, so nothing has to be carried back.
"""

from __future__ import annotations

import functools
import os
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from harbor.agents.installed.claude_code import ClaudeCode
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
REMOTE = PurePosixPath("/tmp/lg-eval")
HARNESS_DIR = PurePosixPath("/opt/lg")
HARNESS_PATHS = ("server", "scripts", "agent_context")
SNAPSHOT = "snapshot"
OVERLAID = "agent_context scripts/loregarden-cli.sh"

INSTALL_HARNESS = f"""\
set -e
mkdir -p {HARNESS_DIR}
tar -xzf {REMOTE}/harness.tar.gz -C {HARNESS_DIR}
cd {HARNESS_DIR}
git init -q && git add -A && git -c user.email=eval@localhost -c user.name=eval commit -qm harness
cd server && uv sync --frozen -q
cd /app
rm -rf agent_context && cp -a {HARNESS_DIR}/agent_context agent_context
cat > scripts/loregarden-cli.sh <<'__SHIM__'
#!/usr/bin/env bash
export LOREGARDEN_REPO_ROOT=/app
exec {HARNESS_DIR}/scripts/loregarden-cli.sh "$@"
__SHIM__
chmod +x scripts/loregarden-cli.sh
"""

# From the image's baseline commit, not HEAD: an agent that commits with `git add -A`
# would otherwise have committed the overlay, and HEAD would restore it.
RESTORE_APP = (
    "cd /app && base=$(git rev-list --max-parents=0 HEAD) "
    f"&& git checkout -q $base -- {OVERLAID} && git clean -fdq -- agent_context"
)


@functools.cache
def harness_archive(ref: str) -> tuple[bytes, str]:
    """`git archive` of the harness paths at `ref`, and the commit it resolved to."""
    sha = subprocess.run(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    data = subprocess.run(
        ["git", "archive", "--format=tar.gz", sha, *HARNESS_PATHS],
        cwd=REPO,
        check=True,
        capture_output=True,
    ).stdout
    return data, sha


class LoregardenImplement(ClaudeCode):
    STAGE_KEY = "implement"

    @staticmethod
    def name() -> str:
        return "loregarden-implement"

    async def _install_harness(self, environment: BaseEnvironment, local: Path) -> str:
        ref = os.environ.get("LOREGARDEN_EVAL_HARNESS", "HEAD")
        if ref == SNAPSHOT:
            return SNAPSHOT
        data, sha = harness_archive(ref)
        (local / "harness.tar.gz").write_bytes(data)
        await environment.upload_file(
            local / "harness.tar.gz", str(REMOTE / "harness.tar.gz")
        )
        await self.exec_as_agent(environment, command=INSTALL_HARNESS)
        return sha

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        await self.exec_as_agent(environment, command=f"mkdir -p {REMOTE}")
        await environment.upload_file(
            HERE / "render_stage.py", str(REMOTE / "render_stage.py")
        )
        harness = SNAPSHOT
        try:
            with tempfile.TemporaryDirectory(prefix="lg-eval-") as tmp:
                local = Path(tmp)
                harness = await self._install_harness(environment, local)
                (local / "instruction.md").write_text(instruction)
                await environment.upload_file(
                    local / "instruction.md", str(REMOTE / "instruction.md")
                )
                # Raises on a non-zero exit, so a trial never runs on an unrendered prompt.
                await self.exec_as_agent(
                    environment,
                    command=(
                        f"python3 {REMOTE}/render_stage.py --stage {self.STAGE_KEY} "
                        f"--instruction {REMOTE}/instruction.md --out {REMOTE}"
                    ),
                    cwd="/app",
                )
                await environment.download_file(
                    str(REMOTE / "prompt.txt"), local / "prompt.txt"
                )
                await environment.download_file(
                    str(REMOTE / "stage.json"), local / "stage.json"
                )
                prompt = (local / "prompt.txt").read_text()
                self.logs_dir.mkdir(parents=True, exist_ok=True)
                (self.logs_dir / "loregarden-prompt.txt").write_text(prompt)
                (self.logs_dir / "loregarden-stage.json").write_text(
                    (local / "stage.json").read_text()
                )
                (self.logs_dir / "loregarden-harness.txt").write_text(harness + "\n")
            await super().run(prompt, environment, context)
        finally:
            if harness != SNAPSHOT:
                # Raises if it cannot restore: grading a tree that still carries the
                # harness's agent_context would grade something the task never had.
                await self.exec_as_agent(environment, command=RESTORE_APP)
