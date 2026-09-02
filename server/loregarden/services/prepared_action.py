"""Handing a person a prepared action instead of a description of one.

When an agent cannot finish something itself it blocks and writes prose saying
what a person should do. The person then reconstructs the work: figure out the
command, run it, read the output, decide what mattered, retype the result into
the ticket. Most of that is work the agent could have done.

The example this was built from, blobert milestone 14 on 2026-08-15. Ticket 22
blocked with: *a human/operator must capture real display-backed Godot editor GPU
profiler timings and target-mobile GPU frame timings for baseline and spike
scenes, then attach measured baseline GPU ms/frame, spike GPU ms/frame, shader
delta, budget comparison, and final go/no-go decision.* Accurate, and about as
much work as it could possibly be — an open-ended procedure with five outputs to
transcribe. The agent had already built and run a CPU-side probe over those exact
scenes, and handed over none of it.

WHAT A BLOCK OWES. `HumanActionTier` is a ladder the agent works down, stopping
at the first rung it can honestly claim: it tried, or it committed a script
someone can run with one click, or a person genuinely has to be there. Each rung
has to be earned, and `assess_handover` below is what says so out loud.

WHY ONE_CLICK RUNS A PATH AND NOT A STRING. The command a block carries was
written by an agent. Running arbitrary text from it would make the control plane
execute whatever an agent typed, so the runnable rung takes a script committed to
the workspace repository, and this module refuses anything that does not resolve
to a real file inside that repository. The command line is still shown to the
person — it just is not what gets executed.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from loregarden.models.domain import HumanActionTier
from loregarden.services.git_subprocess import scrubbed_git_env
from pydantic import BaseModel, Field

#: Long enough for a build or a capture, short enough that a wedged script does
#: not hold the request open forever. Mirrors the gate runner's posture.
PREPARED_ACTION_TIMEOUT_SECONDS = 900

#: `evidence_kind` for the artifact a run produces, so the captured output is
#: findable as evidence rather than as one more context blob.
PREPARED_ACTION_EVIDENCE_KIND = "human_action_result"

#: A handover with more numbered or bulleted steps than this, and no script to
#: run them, is the shape ticket 22 produced: a procedure, not an action.
_PROSE_STEP_LIMIT = 2


class PreparedAction(BaseModel):
    """What the agent did about a step it could not finish.

    Modelled rather than passed as loose keys because it crosses the MCP
    boundary, and the whole point is that a caller cannot supply "the human
    should do X" and nothing else.
    """

    tier: HumanActionTier

    #: What the agent already ran, and why the result does not answer the
    #: question. This is the field that makes a block a report rather than a
    #: shrug: "I could not do this" is only complete paired with what was tried.
    attempted: str = ""

    #: What the agent built to reduce the remaining work — a harness, a scene, a
    #: fixture, a scripted capture.
    prepared: str = ""

    #: The invocation to show a person. Displayed for every tier; executed for
    #: none of them directly (see `script_path`).
    command: str = ""

    #: Repository-relative path of the committed script `command` invokes. The
    #: only thing this module will run, and required at ONE_CLICK.
    script_path: str = ""

    #: What running it produces, so a person knows what they are getting and the
    #: captured output can be judged against it.
    captures: list[str] = Field(default_factory=list)

    def is_runnable(self) -> bool:
        return self.tier is HumanActionTier.ONE_CLICK and bool(self.script_path.strip())


class HandoverAssessment(BaseModel):
    """Whether a block earned the rung it stopped at."""

    ok: bool
    findings: list[str] = Field(default_factory=list)


def _prose_steps(message: str) -> int:
    """How many discrete steps a handover message spells out in prose.

    Counts list markers rather than sentences: "then" and "and" appear in
    single-step instructions constantly, while a numbered or bulleted list is
    someone writing a procedure. Deliberately crude — this feeds a warning a
    person reads, not a decision the machine makes alone.
    """
    steps = 0
    for raw in (message or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line[:2] in {"- ", "* "} or (line[:1].isdigit() and line[1:3] in {". ", ") "}):
            steps += 1
    return steps


def assess_handover(*, message: str, action: PreparedAction | None) -> HandoverAssessment:
    """Whether this block hands over an action or a chore.

    AC6 of lg-workflow-integrity-460: an unprepared multi-step handover has to be
    *visible as such*, rather than left to each agent's judgement. Returns
    findings rather than raising, because refusing the block outright would
    strand work whose only sin is being described badly — and a blocked ticket
    nobody can file is worse than a badly filed one.
    """
    findings: list[str] = []
    if action is None:
        findings.append(
            "This block hands over human work with no prepared action. State what you "
            "already tried, what you built to reduce the remaining work, and the command "
            "to run — see HumanActionTier."
        )
        if _prose_steps(message) > _PROSE_STEP_LIMIT:
            findings.append(
                f"The message spells out {_prose_steps(message)} steps as prose. A "
                "procedure that long is usually a script somebody has to write anyway; "
                "writing it here means writing it once."
            )
        return HandoverAssessment(ok=False, findings=findings)

    if not action.attempted.strip():
        findings.append(
            "`attempted` is empty. A block is only complete when it says what was already "
            "run and why the result does not answer the question."
        )
    if action.tier is HumanActionTier.MANUAL and _prose_steps(message) > _PROSE_STEP_LIMIT:
        findings.append(
            f"Tier is MANUAL with {_prose_steps(message)} steps spelled out and no script. "
            "MANUAL is for the part that needs a person present; the steps around it are "
            "what ONE_CLICK exists for."
        )
    if action.tier is HumanActionTier.ONE_CLICK and not action.script_path.strip():
        findings.append(
            "Tier is ONE_CLICK but no `script_path` was committed, so there is nothing to "
            "run. Commit the script and name its repository-relative path."
        )
    return HandoverAssessment(ok=not findings, findings=findings)


class PreparedActionRun(BaseModel):
    """The result of running a prepared action."""

    ok: bool
    exit_code: int = 0
    output: str = ""
    error: str = ""


def resolve_script(repo_path: str, script_path: str) -> Path | None:
    """The committed script this action names, or None if it is not one.

    Refuses anything that escapes the workspace repository. `resolve()` collapses
    `..` before the containment check, so a path traversing out of the tree fails
    here rather than at exec time — which matters because the value came from an
    agent.
    """
    root_raw, candidate_raw = (repo_path or "").strip(), (script_path or "").strip()
    if not root_raw or not candidate_raw:
        return None
    try:
        root = Path(root_raw).resolve()
        candidate = (root / candidate_raw).resolve()
    except OSError:
        return None
    if not candidate.is_relative_to(root):
        return None
    return candidate if candidate.is_file() else None


def run_prepared_action(
    *, repo_path: str, action: PreparedAction, timeout_seconds: int | None = None
) -> PreparedActionRun:
    """Run a ONE_CLICK action's committed script and capture what it said.

    Only ever executes `script_path`. `command` is carried for a person to read;
    an agent that wrote something clever into it changes nothing about what runs
    here. Extra arguments from `command` are passed through, because a capture
    script usually takes the scene or the target to measure — they are arguments
    to a resolved file, not a command line of their own.
    """
    script = resolve_script(repo_path, action.script_path)
    if script is None:
        return PreparedActionRun(
            ok=False,
            error=(
                f"No committed script at {action.script_path!r} inside the workspace "
                "repository. A one-click action runs a file in the repo, never the "
                "command string."
            ),
        )
    try:
        extra = shlex.split(action.command)[1:] if action.command.strip() else []
    except ValueError as exc:
        return PreparedActionRun(ok=False, error=f"malformed command: {exc}")

    try:
        completed = subprocess.run(
            [str(script), *extra],
            cwd=repo_path,
            # Same reason the gate runner scrubs: an inherited GIT_DIR overrides
            # cwd, and a capture script that shells out to git would silently
            # measure another repository.
            env=scrubbed_git_env(),
            capture_output=True,
            text=True,
            timeout=timeout_seconds or PREPARED_ACTION_TIMEOUT_SECONDS,
            check=False,
        )
    except OSError as exc:
        # Not on PATH, missing execute bit, or any other exec-level failure.
        return PreparedActionRun(ok=False, error=str(exc))
    except subprocess.TimeoutExpired:
        return PreparedActionRun(
            ok=False,
            error=f"Prepared action timed out after "
            f"{timeout_seconds or PREPARED_ACTION_TIMEOUT_SECONDS}s",
        )

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    return PreparedActionRun(
        ok=completed.returncode == 0,
        exit_code=completed.returncode,
        output=stdout,
        error="" if completed.returncode == 0 else (stderr or stdout or "no output"),
    )
