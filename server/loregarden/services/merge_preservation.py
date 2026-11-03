"""Did a merged result keep everything the target contributed? (lg-milestone-that-1513)

A green suite over a merged tree proves the tree it was given, not that the
merge kept the target's work: a conflict resolution that drops a line main
added, then passes, lands a revert nobody asked for. So a step that produced a
merged tree is followed by this check, computed from git's own commits and not
from what the step reports about itself.

**Stronger than the prior art, on purpose.** proactive-agent/workbench @
f556b336 (`src/main/ai/runners/github/pr-merger.ts:61-83`, MIT) checks only the
conflicted files, skips lines of pure punctuation, and tests whether a
*trimmed* line appears anywhere in the result — so a moved, re-indented or
coincidentally duplicated line passes, and a loss in a file that merged cleanly
is never looked at. Here:

- every file the target changed since the merge base is examined, not only the
  conflicted subset;
- lines are compared exactly and *counted* (a multiset per file), so a line
  the target added twice and the result kept once is a loss;
- a line the source itself deleted since the merge base is the source's
  change, not a loss, and is excluded by the same count.

**The escape hatch is itemised.** A step that drops a target line on purpose
declares it as a `DroppedLine` — file, exact line, and why. Only declared
lines are subtracted; an unexplained loss cannot be waived, and there is no
boolean that waives everything.

**Structural checks run first**, so this is never the only thing between a bad
merge and a landing: the result's first parent is the target, and no file the
result changed carries a conflict marker.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from loregarden.services.git_merge_noco import merge_base
from loregarden.services.git_subprocess import run_git

#: A conflict marker git writes: seven `<` or `>` then a space or line end.
_CONFLICT_MARKER = re.compile(r"^(<{7}|>{7})( |$)", re.MULTILINE)


@dataclass(frozen=True)
class DroppedLine:
    """A target line a step left out deliberately, and the stated reason."""

    file: str
    line: str
    reason: str


@dataclass(frozen=True)
class LostLine:
    file: str
    line: str
    #: How many more copies the target contributed than the result holds.
    missing: int = 1


@dataclass(frozen=True)
class PreservationReport:
    ok: bool
    lost: tuple[LostLine, ...] = field(default_factory=tuple)
    structural: tuple[str, ...] = field(default_factory=tuple)

    @property
    def detail(self) -> str:
        if self.ok:
            return "every line the target contributed is in the result"
        parts = list(self.structural)
        if self.lost:
            shown = "; ".join(f"{lost.file}: {lost.line.strip()[:80]!r}" for lost in self.lost[:10])
            more = f" (and {len(self.lost) - 10} more)" if len(self.lost) > 10 else ""
            parts.append(f"{len(self.lost)} target line(s) missing from the result: {shown}{more}")
        return "; ".join(parts)


def _git(repo_root: Path, *args: str) -> str:
    result = run_git(list(args), cwd=str(repo_root), check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"git {args[0]} failed: {(result.stderr or result.stdout or '').strip()}"
        )
    return result.stdout


def target_preserved(
    repo_root: Path,
    *,
    target_sha: str,
    source_sha: str,
    result_sha: str,
    declared: Sequence[DroppedLine] = (),
) -> PreservationReport:
    """Check ``result_sha`` against what ``target_sha`` contributed since the merge base.

    Raises `RuntimeError` when git cannot answer; that is "could not be
    determined", which a caller must never read as preserved.
    """
    structural = _structural(repo_root, target_sha=target_sha, result_sha=result_sha)
    if structural:
        return PreservationReport(ok=False, structural=tuple(structural))
    base = merge_base(repo_root, target_sha, source_sha)
    if not base:
        raise RuntimeError(
            f"{target_sha[:12]} and {source_sha[:12]} share no history; nothing to compare from"
        )
    added_by_target = _changed_lines(repo_root, base, target_sha, sign="+")
    removed_by_source = _changed_lines(repo_root, base, source_sha, sign="-")
    waived: dict[str, Counter[str]] = {}
    for drop in declared:
        waived.setdefault(drop.file, Counter())[drop.line] += 1
    lost: list[LostLine] = []
    for path, added in sorted(added_by_target.items()):
        held = Counter(_file_lines(repo_root, result_sha, path))
        expected = added - removed_by_source.get(path, Counter()) - waived.get(path, Counter())
        for line, count in expected.items():
            if held[line] < count:
                lost.append(LostLine(file=path, line=line, missing=count - held[line]))
    return PreservationReport(ok=not lost, lost=tuple(lost))


def _structural(repo_root: Path, *, target_sha: str, result_sha: str) -> list[str]:
    failures: list[str] = []
    parents = _git(repo_root, "rev-list", "--parents", "-n", "1", result_sha).split()[1:]
    if not parents or parents[0] != target_sha:
        failures.append(
            f"the result's first parent is {parents[0][:12] if parents else 'missing'}, "
            f"not the target {target_sha[:12]}: it was not merged onto the target"
        )
    changed = [
        p
        for p in _git(
            repo_root, "diff", "--name-only", "--no-renames", target_sha, result_sha
        ).splitlines()
        if p
    ]
    for path in changed:
        if _CONFLICT_MARKER.search("\n".join(_file_lines(repo_root, result_sha, path))):
            failures.append(f"{path} carries a conflict marker in the result")
    return failures


def _changed_lines(repo_root: Path, old: str, new: str, *, sign: str) -> dict[str, Counter[str]]:
    """Per file, the lines a diff from ``old`` to ``new`` adds (``+``) or removes (``-``)."""
    diff = _git(repo_root, "diff", "-U0", "--no-renames", "--no-color", old, new)
    #: The side of the diff whose name a line is filed under: the new file for
    #: what was added, the old one for what was removed.
    header = "+++ " if sign == "+" else "--- "
    lines: dict[str, Counter[str]] = {}
    current = ""
    in_header = False
    for raw in diff.splitlines():
        if raw.startswith("diff --git "):
            current, in_header = "", True
        elif in_header and raw.startswith(header):
            name = raw[len(header) :]
            current = "" if name == "/dev/null" else name[2:]
        elif raw.startswith("@@"):
            in_header = False
        elif not in_header and current and raw.startswith(sign):
            lines.setdefault(current, Counter())[raw[1:]] += 1
    return lines


def _file_lines(repo_root: Path, commit: str, path: str) -> list[str]:
    """``path``'s lines at ``commit``; none when the file is absent there."""
    result = run_git(
        ["show", f"{commit}:{path}"],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        if "does not exist" in result.stderr or "exists on disk, but not in" in result.stderr:
            return []  # silent-ok: an absent file holds no lines; every expected one is then lost
        raise RuntimeError(f"git show {commit[:12]}:{path} failed: {result.stderr.strip()}")
    return result.stdout.splitlines()
