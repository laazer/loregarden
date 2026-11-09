#!/usr/bin/env python3
"""Refuse report, summary and stage-completion markdown an agent writes into the tree.

Loregarden reads none of these files. A report written to disk is invisible to
the control plane, and the orchestrator commits the whole working tree, so it
lands in whatever ticket's commit comes next. The rule used to be prompt-only
(`agent_context/agents/common_assets/loregarden_mcp_v1.md`), and loregarden's own
history holds 50+ such files that got past it: `TEST_BREAK_STAGE_COMPLETE.md`,
`TICKET_39_QA_SIGN_OFF.md`, `client/src/components/__tests__/TEST_BREAK_REPORT.md`.

Scope: markdown this change *adds* (new at its path, or untracked), outside the
directories that legitimately hold docs. A file is report-shaped when a word of
its name, or of its first heading, is one of `SHAPES`. Each refusal names the
loregarden tool that takes that content instead.

Waiver: `<!-- report-ok: <reason> -->` anywhere in the file, with a reason of at
least 12 characters. A marker with a thinner reason is itself a finding.

Usage: md_report_check.py [staged files...] | --repo <root> --scope <scope> [--base <ref>]
"""

import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_LEFTHOOK_SCRIPTS = Path(__file__).resolve().parent
if str(_LEFTHOOK_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_LEFTHOOK_SCRIPTS))

# Before any sibling import: on an interpreter older than 3.11 the gates raise at
# import time and exit 1, which reads as a finding. Refuse with EX_UNAVAILABLE.
from gate_python_guard import require_supported_python  # noqa: E402 - path set just above

require_supported_python(Path(__file__).name)

from precommit_git_diff import (  # noqa: E402 - sys.path is set up just above
    GateInvocation,
    GateRun,
    UnexaminableError,
    git_added_paths,
    parse_gate_argv,
    read_source_text,
    repo_relative_posix,
    resolve_gate_scope,
)


@dataclass(frozen=True)
class Destination:
    """Where content of one shape belongs instead of a file."""

    tool: str
    holds: str


ARTIFACT = Destination("loregarden_attach_artifact", "findings, analysis, test output, reports")
STAGE_REPORT = Destination(
    "the <<<LOREGARDEN_STAGE_REPORT>>> sentinel in your response "
    "(loregarden_complete_stage outside a stage run)",
    "a stage's outcome",
)
TICKET = Destination("loregarden_update_ticket", "a spec, description or acceptance criteria")
DOCUMENT = Destination("loregarden_write_document", "a plan or research write-up")
CHECKPOINT = Destination("loregarden_append_checkpoint", "an assumption you resolved alone")
LEARNING = Destination("loregarden_append_learning", "a learning or anti-pattern")

#: A word that marks a file as a report, and where that content goes. Drawn from
#: the report files loregarden's own history accumulated before this gate.
SHAPES: dict[str, Destination] = {
    "report": ARTIFACT,
    "reports": ARTIFACT,
    "summary": ARTIFACT,
    "findings": ARTIFACT,
    "analysis": ARTIFACT,
    "results": ARTIFACT,
    "verification": ARTIFACT,
    "signoff": ARTIFACT,
    "testdesign": ARTIFACT,
    "completion": STAGE_REPORT,
    "complete": STAGE_REPORT,
    "progress": STAGE_REPORT,
    "spec": TICKET,
    "specification": TICKET,
    "plan": DOCUMENT,
    "research": DOCUMENT,
    "checkpoint": CHECKPOINT,
    "checkpoints": CHECKPOINT,
    "learning": LEARNING,
    "learnings": LEARNING,
}

#: The words a first heading has to carry to mark a file as a report. Narrower
#: than `SHAPES`: a heading is prose, and "Execution Plan" or "Spec Exit Gate"
#: titles a legitimate doc as often as a report. Measured on blobert's tree, the
#: full vocabulary refused 21 execution-plan docs on their heading alone.
HEADING_SHAPES: frozenset[str] = frozenset(
    {
        "report",
        "reports",
        "summary",
        "findings",
        "completion",
        "signoff",
        "specification",
        "checkpoint",
        "checkpoints",
        "learnings",
    }
)

#: A path with any of these directories in it is documentation by convention.
#: `.loregarden` holds the handoff and spec evidence loregarden's own gates ask
#: agents to commit; `.agents` holds skills.
DOC_DIRS: frozenset[str] = frozenset(
    {
        "docs",
        "doc",
        "documentation",
        "blog",
        "agent_context",
        ".agents",
        ".claude",
        ".cursor",
        ".github",
        ".loregarden",
    }
)

#: Repository docs every project keeps, wherever they sit.
CONVENTIONAL_NAMES: frozenset[str] = frozenset(
    {"readme", "changelog", "contributing", "security", "license", "agents", "claude"}
)

WAIVER_MARKER = "report-ok:"
#: Same floor as the other gates' waivers: a reason a reviewer can check.
MIN_WAIVER_REASON_CHARS = 12

_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_CAMEL_RE = re.compile(r"([a-z0-9])([A-Z])")
_WORD_SPLIT_RE = re.compile(r"[^a-z0-9]+")

_HELP = f"""
Loregarden reads none of these files, and the orchestrator sweeps them into an
unrelated ticket's commit. Put the content where the control plane reads it, as
named above.

A doc that belongs in the repository goes under one of: {", ".join(sorted(DOC_DIRS))}.
A file that must stay where it is takes a waiver anywhere in it:

    <!-- {WAIVER_MARKER} <why this file belongs in the tree> -->
"""


def words(text: str) -> list[str]:
    """Lowercase words of a name or heading, plus each adjacent pair joined.

    `TICKET_39_QA_SIGN_OFF` reads as `sign`, `off` and `signoff`;
    `TestBreakReport` splits on its case changes.
    """
    tokens = [t for t in _WORD_SPLIT_RE.split(_CAMEL_RE.sub(r"\1 \2", text).lower()) if t]
    return tokens + [a + b for a, b in zip(tokens, tokens[1:])]


def first_heading(lines: Sequence[str]) -> str | None:
    """The text of the first ATX heading outside a fenced code block."""
    fenced = False
    for line in lines:
        if _FENCE_RE.match(line):
            fenced = not fenced
            continue
        match = None if fenced else _HEADING_RE.match(line)
        if match:
            return match.group(1)
    return None


def is_doc_location(rel: str) -> bool:
    parts = rel.split("/")
    if Path(parts[-1]).stem.lower() in CONVENTIONAL_NAMES:
        return True
    return any(part.lower() in DOC_DIRS for part in parts[:-1])


def waiver_reason(lines: Sequence[str]) -> str | None:
    """The reason on the file's `report-ok:` marker, or None without one."""
    for line in lines:
        if WAIVER_MARKER in line:
            reason = line.split(WAIVER_MARKER, 1)[1]
            return reason.replace("-->", "").strip(" -:\t")
    return None


@dataclass(frozen=True)
class Shape:
    """Why a file reads as a report: the word, where it was found, where it goes."""

    word: str
    found_in: str
    destination: Destination


def report_shape(name: str, lines: Sequence[str]) -> Shape | None:
    for word in words(Path(name).stem):
        if word in SHAPES:
            return Shape(word, "name", SHAPES[word])
    heading = first_heading(lines)
    for word in words(heading or ""):
        if word in HEADING_SHAPES:
            return Shape(word, f"heading {heading!r}", SHAPES[word])
    return None


def findings_for(path: Path, rel: str, *, repo: Optional[Path]) -> list[str]:
    """The refusals for one newly added markdown file; empty when it may stay."""
    if is_doc_location(rel):
        return []
    lines = read_source_text(path, repo=repo).splitlines()
    shape = report_shape(path.name, lines)
    if shape is None:
        return []
    reason = waiver_reason(lines)
    if reason is not None and len(reason) >= MIN_WAIVER_REASON_CHARS:
        return []
    if reason is not None:
        return [
            f"{rel}: the {WAIVER_MARKER} waiver needs a reason of at least "
            f"{MIN_WAIVER_REASON_CHARS} characters saying why this file belongs in the tree"
        ]
    where = shape.destination
    return [
        f"{rel}: its {shape.found_in} says {shape.word!r}, a report shape. "
        f"Use {where.tool} ({where.holds}) instead of a file"
    ]


def _markdown_files(
    repo: Optional[Path], candidates: Sequence[Path], discovered: bool
) -> list[Path]:
    del repo, discovered  # Markdown anywhere is in scope; the doc locations are judged per file.
    return [path for path in candidates if path.suffix.lower() == ".md"]


def new_paths(run: GateRun) -> frozenset[str] | None:
    """Relpaths this change adds; None outside a repository, where every file counts."""
    if run.repo is None:
        return None
    added = git_added_paths(run.repo, run.diff_scope, run.base_ref)
    return frozenset(added) | run.untracked


def main(argv: list[str]) -> int:
    invocation = parse_gate_argv(argv, suffix=".md")
    try:
        return _check(invocation)
    except UnexaminableError as exc:
        # A scope this run could not resolve, or a file it could not read, is not clean.
        print(f"{invocation.label}: cannot determine what to examine: {exc}")
        return 1


def _check(invocation: GateInvocation) -> int:
    run = resolve_gate_scope(
        label=invocation.label,
        repo=invocation.repo,
        diff_scope=invocation.diff_scope,
        base_ref=invocation.base_ref,
        explicit_files=invocation.files,
        select=_markdown_files,
    )
    if not run.files:
        return 0

    added = new_paths(run)
    findings: list[str] = []
    for path in run.files:
        rel = repo_relative_posix(path, run.repo)
        if added is not None and rel not in added:
            continue  # Already in the tree before this change: not this gate's to refuse.
        findings.extend(findings_for(path, rel, repo=run.repo))

    if not findings:
        print(f"{invocation.label}: report markdown check passed.")
        return 0

    print("❌ Report markdown written into the tree:")
    for finding in findings:
        print(f" - {finding}")
    print(_HELP)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
