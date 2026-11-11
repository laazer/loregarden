"""Reading another project's source for research: list, grep and read at a commit.

A research claim is only ``verified`` when it names a file at a commit, so the
agent needs a way to read source that reports the commit it read. This reuses
the reference cache's shallow clone (`reference_repo_service.clone_or_refresh`),
so a repository researched today is already cloned when a survey pass reads it.

The clone holds HEAD only. ``head`` refreshes it; every other action reads the
clone as it stands (cloning it first if absent) and returns the commit it read,
which is the commit a claim must cite. `verify_evidence` is the check the claim
writer runs: the clone is at that commit, the path exists in it, and the cited
lines are in the file. It returns those lines, read here rather than taken from
the agent, so the claim card shows exactly what the file said.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

from loregarden.config import settings
from loregarden.services.git_subprocess import run_git
from loregarden.services.reference_repo_service import (
    ParsedRepoUrl,
    ReferenceRepoError,
    clone_or_refresh,
    clone_path_for,
    is_cloned,
    parse_repo_url,
)
from loregarden.services.reference_survey.errors import SurveyError

#: Caps that keep one tool reply inside an agent's context.
MAX_TREE_ENTRIES = 400
MAX_GREP_LINES = 150
MAX_READ_LINES = 400
_MAX_LINE_CHARS = 300
#: The shortest commit prefix a claim may cite.
MIN_COMMIT_PREFIX = 7
#: The most lines one claim may cite; a claim points at the code, not a module.
MAX_EVIDENCE_LINES = 60


@dataclass(frozen=True)
class Evidence:
    """What backs a verified claim, or why nothing does (``refusal`` set)."""

    refusal: str = ""
    #: The cited lines, read from the file at the commit.
    excerpt: str = ""
    #: The file at the full commit, anchored to the lines; "" for an unknown host.
    url: str = ""


@dataclass(frozen=True)
class RepoCheckout:
    parsed: ParsedRepoUrl
    path: Path
    commit: str


def _parse(url: str) -> ParsedRepoUrl:
    try:
        return parse_repo_url(url)
    except ReferenceRepoError as exc:
        raise SurveyError(f"Not a repository URL research can read: {exc}") from exc


def _git(path: Path, args: list[str]) -> tuple[int, str, str]:
    result = run_git(
        args,
        cwd=path,
        capture_output=True,
        text=True,
        timeout=settings.reference_repo_clone_timeout,
    )
    return result.returncode, result.stdout or "", result.stderr or ""


def _head(path: Path) -> str:
    code, out, err = _git(path, ["rev-parse", "HEAD"])
    if code != 0:
        raise SurveyError(f"Could not read the clone's commit: {err.strip() or f'exit {code}'}")
    return out.strip()


def checkout(url: str, *, refresh: bool) -> RepoCheckout:
    """The repository's clone and the commit it holds; refreshed when asked or absent."""
    parsed = _parse(url)
    path = clone_path_for(parsed)
    if refresh or not is_cloned(path):
        try:
            path, _branch, commit = clone_or_refresh(parsed)
        except ReferenceRepoError as exc:
            raise SurveyError(f"Could not clone {parsed.url}: {exc}") from exc
        return RepoCheckout(parsed=parsed, path=path, commit=commit)
    return RepoCheckout(parsed=parsed, path=path, commit=_head(path))


def blob_url(parsed: ParsedRepoUrl, commit: str, path: str) -> str:
    """A link a person can open to the file at the commit, or "" for an unknown host."""
    base = f"https://{parsed.host}/{parsed.owner}/{parsed.name}"
    if parsed.host == "github.com":
        return f"{base}/blob/{commit}/{path}"
    if parsed.host == "gitlab.com":
        return f"{base}/-/blob/{commit}/{path}"
    if parsed.host == "codeberg.org":
        return f"{base}/src/commit/{commit}/{path}"
    return ""


def _line_anchor(host: str, start: int, end: int) -> str:
    if host == "gitlab.com":
        return f"#L{start}-{end}"
    return f"#L{start}-L{end}"


def tree(url: str, pattern: str = "") -> dict:
    repo = checkout(url, refresh=False)
    code, out, err = _git(repo.path, ["ls-files"])
    if code != 0:
        raise SurveyError(f"Could not list {repo.parsed.url}: {err.strip() or f'exit {code}'}")
    files = [line for line in out.splitlines() if not pattern or fnmatch.fnmatch(line, pattern)]
    return {
        "url": repo.parsed.url,
        "commit": repo.commit,
        "total": len(files),
        "files": files[:MAX_TREE_ENTRIES],
        "truncated": len(files) > MAX_TREE_ENTRIES,
    }


def grep(url: str, pattern: str, path_glob: str = "") -> dict:
    if not pattern.strip():
        raise SurveyError("grep needs a pattern")
    repo = checkout(url, refresh=False)
    args = ["grep", "-n", "-I", "-i", "-E", "-e", pattern]
    if path_glob:
        args += ["--", path_glob]
    code, out, err = _git(repo.path, args)
    # git grep exits 1 when nothing matched, which is an answer, not a failure.
    if code not in (0, 1):
        raise SurveyError(f"grep failed in {repo.parsed.url}: {err.strip() or f'exit {code}'}")
    lines = [line[:_MAX_LINE_CHARS] for line in out.splitlines()]
    return {
        "url": repo.parsed.url,
        "commit": repo.commit,
        "matches": lines[:MAX_GREP_LINES],
        "total": len(lines),
        "truncated": len(lines) > MAX_GREP_LINES,
    }


def _confined(repo: RepoCheckout, relative: str) -> Path:
    root = repo.path.resolve()
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        raise SurveyError(f"{relative} is outside the repository")
    if ".git" in target.relative_to(root).parts:
        raise SurveyError("Reading git's own files is not research")
    return target


def read(url: str, path: str, start: int = 1, end: int = 0) -> dict:
    repo = checkout(url, refresh=False)
    target = _confined(repo, path)
    if not target.is_file():
        raise SurveyError(f"{path} is not a file at {repo.commit[:12]} in {repo.parsed.url}")
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    first = max(start, 1)
    last = min(end or len(lines), len(lines), first + MAX_READ_LINES - 1)
    return {
        "url": repo.parsed.url,
        "commit": repo.commit,
        "path": path,
        "blob_url": blob_url(repo.parsed, repo.commit, path),
        "start": first,
        "end": last,
        "total_lines": len(lines),
        "text": "\n".join(f"{n}: {lines[n - 1]}" for n in range(first, last + 1)),
    }


def head(url: str) -> dict:
    repo = checkout(url, refresh=True)
    return {"url": repo.parsed.url, "commit": repo.commit}


def verify_evidence(url: str, commit: str, path: str, start: int, end: int) -> Evidence:
    """What backs a verified claim at this file, commit and line range, or why nothing does.

    Only the clone this module reads is consulted: a commit research never read
    is refused, so "verified" always means a file an agent actually opened.
    """
    if len(commit) < MIN_COMMIT_PREFIX or not path:
        return Evidence(refusal="a verified claim names the file and the commit it was read at")
    if start < 1 or end < start:
        return Evidence(
            refusal="a verified claim cites the lines that do it (evidence_start, evidence_end)"
        )
    if end - start + 1 > MAX_EVIDENCE_LINES:
        return Evidence(
            refusal=f"cite at most {MAX_EVIDENCE_LINES} lines: the code that does it, not the file"
        )
    try:
        parsed = parse_repo_url(url)
    except ReferenceRepoError:
        return Evidence(
            refusal="only a repository's source can verify a claim; use docs_only for a site"
        )
    clone = clone_path_for(parsed)
    if not is_cloned(clone):
        return Evidence(
            refusal=f"{parsed.url} was never read; read it with loregarden_research_repo first"
        )
    current = _head(clone)
    if not current.startswith(commit):
        return Evidence(
            refusal=f"{commit[:12]} is not the commit research read ({current[:12]}); "
            "re-read the file and cite that commit"
        )
    relative = path.lstrip("/")
    code, out, _err = _git(clone, ["show", f"HEAD:{relative}"])
    if code != 0:
        return Evidence(refusal=f"{path} does not exist at {current[:12]}")
    lines = out.splitlines()
    if end > len(lines):
        return Evidence(
            refusal=f"{path} has {len(lines)} lines at {current[:12]}; "
            f"lines {start}-{end} are not in it"
        )
    link = blob_url(parsed, current, relative)
    return Evidence(
        excerpt="\n".join(line[:_MAX_LINE_CHARS] for line in lines[start - 1 : end]),
        url=link + _line_anchor(parsed.host, start, end) if link else "",
    )
