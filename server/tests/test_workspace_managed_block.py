"""Both workspace installers refuse a file they cannot edit safely.

The hooks and AGENTS.md installers rewrite a marker-delimited block in a file
another repository owns. Each case here is one where the old installers damaged
or escaped that file: a missing END marker deleted the workspace's own content
and reported the result as current; a symlink was written through to a file
outside the repository; a CRLF file came back with every line changed; a
non-UTF-8 file surfaced to the page as "Traceback (most recent call last):".

Run against both installers, because both share the helper that decides.
"""

import importlib.util
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


hooks = _load("install_workspace_hooks")
docs = _load("install_workspace_agents_doc")


@dataclass(frozen=True)
class Installer:
    module: object
    file_name: str
    flag: str
    #: A file the installer accepts, with content of the workspace's own.
    seed: str
    #: The workspace's own line, which must survive every refusal.
    own: str


HOOKS = Installer(
    hooks,
    "lefthook.yml",
    "--config",
    "pre-commit:\n  commands:\n    mine:\n      run: my-own-gate\n"
    "post-checkout:\n  commands:\n    keep:\n      run: echo keep\n",
    "my-own-gate",
)
DOCS = Installer(
    docs,
    "AGENTS.md",
    "--agents-file",
    "# Workspace\n\n## Our own section\n\nOur own notes.\n",
    "Our own notes.",
)
BOTH = pytest.mark.parametrize("installer", [HOOKS, DOCS], ids=["hooks", "docs"])


def _run(installer: Installer, target: Path, *, check: bool = False) -> int:
    argv = [installer.flag, str(target), "--loregarden-root", str(_ROOT)]
    if check:
        argv.append("--check")
    saved = sys.argv
    sys.argv = [installer.file_name, *argv]
    try:
        return installer.module.main()
    finally:
        sys.argv = saved


def _with_unmatched_begin(installer: Installer) -> str:
    """The workspace's file with a managed block whose END marker was deleted by hand."""
    lines = installer.seed.splitlines()
    # Inside the pre-commit commands map for hooks; ahead of the own section for docs.
    at = 2 if installer is HOOKS else 1
    return "\n".join([*lines[:at], f"    {installer.module.BEGIN_MARKER}", *lines[at:]]) + "\n"


@BOTH
@pytest.mark.parametrize("check", [False, True], ids=["install", "check"])
def test_an_unmatched_marker_is_refused_not_rewritten(
    installer: Installer, check: bool, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / installer.file_name
    damaged = _with_unmatched_begin(installer)
    target.write_text(damaged, encoding="utf-8")

    assert _run(installer, target, check=check) == 1

    assert target.read_text(encoding="utf-8") == damaged
    err = capsys.readouterr().err
    assert err.startswith("skip: ")
    assert "begin marker on line" in err


@BOTH
@pytest.mark.parametrize(
    "markers",
    [("END",), ("END", "BEGIN"), ("BEGIN", "END", "BEGIN", "END")],
    ids=["end-only", "reversed", "twice"],
)
def test_any_other_marker_shape_is_refused(
    installer: Installer, markers: tuple[str, ...], tmp_path: Path
) -> None:
    target = tmp_path / installer.file_name
    lines = [f"    {getattr(installer.module, f'{m}_MARKER')}" for m in markers]
    text = installer.seed + "\n".join(lines) + "\n"
    target.write_text(text, encoding="utf-8")

    assert _run(installer, target) == 1
    assert target.read_text(encoding="utf-8") == text


@BOTH
def test_a_well_formed_block_still_refreshes_in_place(installer: Installer, tmp_path: Path) -> None:
    target = tmp_path / installer.file_name
    target.write_text(installer.seed, encoding="utf-8")
    assert _run(installer, target) == 0
    stale = (
        target.read_text(encoding="utf-8")
        .replace("(loregarden)", "(old)")
        .replace("## Loregarden control plane", "## Old heading")
    )
    target.write_text(stale, encoding="utf-8")

    assert _run(installer, target, check=True) == 1
    assert _run(installer, target) == 0

    text = target.read_text(encoding="utf-8")
    assert text.count(installer.module.BEGIN_MARKER) == 1
    assert installer.own in text
    assert _run(installer, target, check=True) == 0


@BOTH
@pytest.mark.parametrize("dangling", [False, True], ids=["to-a-file", "dangling"])
def test_a_symlink_is_not_written_through(
    installer: Installer, dangling: bool, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside" / "SHARED"
    outside.parent.mkdir()
    if not dangling:
        outside.write_text(installer.seed, encoding="utf-8")
    target = repo / installer.file_name
    target.symlink_to(outside)

    assert _run(installer, target, check=True) == 1
    assert _run(installer, target) == 1

    assert target.is_symlink()
    if dangling:
        assert not outside.exists()
    else:
        assert outside.read_text(encoding="utf-8") == installer.seed
    assert "is a symlink" in capsys.readouterr().err


@BOTH
def test_crlf_line_endings_are_kept(installer: Installer, tmp_path: Path) -> None:
    target = tmp_path / installer.file_name
    target.write_bytes(installer.seed.replace("\n", "\r\n").encode("utf-8"))

    assert _run(installer, target) == 0

    data = target.read_bytes()
    assert data.count(b"\n") == data.count(b"\r\n")
    assert _run(installer, target, check=True) == 0


@BOTH
def test_a_non_utf8_file_is_refused_in_a_sentence(
    installer: Installer, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / installer.file_name
    data = installer.seed.encode("utf-8") + b"# caf\xe9\n"
    target.write_bytes(data)

    assert _run(installer, target) == 1

    assert target.read_bytes() == data
    err = capsys.readouterr().err
    assert err.startswith("skip: ")
    assert "not UTF-8" in err


@BOTH
def test_a_rewrite_keeps_the_files_permissions_and_leaves_no_temp_file(
    installer: Installer, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / installer.file_name
    target.write_text(installer.seed, encoding="utf-8")
    target.chmod(0o640)

    assert _run(installer, target) == 0

    assert target.stat().st_mode & 0o777 == 0o640
    assert sorted(p.name for p in repo.iterdir()) == [installer.file_name]


def test_a_created_agents_file_gets_ordinary_permissions(tmp_path: Path) -> None:
    """mkstemp creates 0600; a new AGENTS.md must not be unreadable to the team."""
    target = tmp_path / "AGENTS.md"
    umask = os.umask(0o022)
    try:
        assert _run(DOCS, target) == 0
    finally:
        os.umask(umask)
    assert target.stat().st_mode & 0o777 == 0o644


def test_the_page_shows_the_refusal_not_a_traceback(tmp_path: Path) -> None:
    """The service reports the wrapper's first stderr line; it must be the reason."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "AGENTS.md").write_bytes(b"# caf\xe9\n")
    result = subprocess.run(
        [str(_SCRIPTS / "install-workspace-docs.sh"), str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    reason = next(line for line in result.stderr.splitlines() if not line.startswith("note: "))
    assert reason.startswith("skip: ")
    assert "not UTF-8" in reason
