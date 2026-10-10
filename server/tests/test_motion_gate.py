"""The motion gate, exercised as the thing it is: a node script.

Black-box over a real git repo and real `--scope` flags, like the theme gate's
tests. Both failure directions are pinned: a gate that accuses a colour fade or
a 1.4s pulse is noise nobody keeps, and one that misses `transition: width` is
a vacuous pass.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from tests.repo_templates import from_template

_ROOT = Path(__file__).resolve().parents[2]
_GATE = _ROOT / ".lefthook" / "scripts" / "ts_motion_check.cjs"
_NODE_MODULES = _ROOT / "client" / "node_modules"

pytestmark = pytest.mark.skipif(
    not (_NODE_MODULES / "postcss").exists() or shutil.which("node") is None,
    reason="needs node and client/node_modules (cd client && npm ci)",
)

_ROOT_STYLESHEET = """:root {
  --t-fast: 0.12s;
  --t-med: 0.2s;
  --t-slow: 0.3s;
}

@media (prefers-reduced-motion: reduce) {
  * {
    animation-duration: 0.01ms !important;
  }
}
"""


def _clean_env() -> dict[str, str]:
    # GIT_DIR/GIT_WORK_TREE beat cwd, and a run nested in a worktree's hook
    # inherits them pointing at the real repository.
    return {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=_clean_env())


def _build_repo(root: Path, *, tokens: bool) -> None:
    root.mkdir()
    _git(root, "init", "-q", "-b", "main", ".")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / "client" / "src").mkdir(parents=True)
    (root / "client" / "src" / ".keep").write_text("")
    if tokens:
        (root / "client" / "src" / "index.css").write_text(_ROOT_STYLESHEET)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A workspace whose index.css defines the --t-* tokens and a global reduced-motion rule.

    Built in a fixture so a broken setup is an ERROR, not a gate that found nothing.
    """
    return from_template(
        tmp_path / "repo", "motion_gate_tokens", lambda r: _build_repo(r, tokens=True)
    )


@pytest.fixture
def bare_repo(tmp_path: Path) -> Path:
    """A workspace with no root stylesheet: no tokens, no reduced-motion policy."""
    return from_template(
        tmp_path / "repo", "motion_gate_bare", lambda r: _build_repo(r, tokens=False)
    )


def _write(repo: Path, name: str, body: str) -> Path:
    path = repo / "client" / "src" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def _run(repo: Path, scope: str = "worktree") -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", str(_GATE), "--repo", str(repo), "--scope", scope],
        capture_output=True,
        text=True,
        env=_clean_env(),
    )


# --------------------------------------------------------------------------- #
# 1. layout properties
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("prop", ["width", "max-height", "left", "margin-top"])
def test_a_layout_transition_is_reported(repo: Path, prop: str):
    _write(repo, "a.css", f".a {{ transition: {prop} var(--t-med) ease; }}\n")
    result = _run(repo)
    assert result.returncode == 1
    assert f"transitions '{prop}'" in result.stderr


def test_a_layout_property_in_a_list_is_reported(repo: Path):
    _write(repo, "a.css", ".a { transition-property: opacity, height; }\n")
    result = _run(repo)
    assert result.returncode == 1
    assert "transitions 'height'" in result.stderr


def test_transition_all_is_reported(repo: Path):
    _write(repo, "a.css", ".a { transition: all var(--t-fast); }\n")
    result = _run(repo)
    assert result.returncode == 1
    assert "'transition: all'" in result.stderr


def test_transition_none_is_not_transition_all(repo: Path):
    _write(repo, "a.css", ".a { transition: none; }\n.b { transition-property: none; }\n")
    result = _run(repo)
    assert result.returncode == 0, result.stderr


def test_a_shorthand_with_no_property_means_all(repo: Path):
    _write(repo, "a.css", ".a { transition: var(--t-fast) ease-out; }\n")
    assert "'transition: all'" in _run(repo).stderr


def test_transform_and_opacity_pass(repo: Path):
    _write(
        repo,
        "a.css",
        ".a { transition: transform var(--t-med) var(--ease-out), opacity var(--t-fast); }\n",
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr


def test_a_layout_keyframe_is_reported(repo: Path):
    _write(repo, "a.css", "@keyframes slide {\n  from { left: 0; }\n  to { left: 10px; }\n}\n")
    result = _run(repo)
    assert result.returncode == 1
    assert "a keyframe animates 'left'" in result.stderr


def test_a_transform_keyframe_passes(repo: Path):
    _write(
        repo,
        "a.css",
        "@keyframes slide {\n  from { transform: translateX(0); }\n  to { transform: none; }\n}\n",
    )
    assert _run(repo).returncode == 0


def test_a_layout_property_set_outside_motion_passes(repo: Path):
    _write(repo, "a.css", ".a { width: 10px; left: 0; }\n")
    assert _run(repo).returncode == 0


# --------------------------------------------------------------------------- #
# 2. durations
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("value", "token"),
    [("0.12s", "var(--t-fast)"), ("200ms", "var(--t-med)"), (".35s", "var(--t-slow)")],
)
def test_a_hardcoded_ui_duration_names_its_token(repo: Path, value: str, token: str):
    _write(repo, "a.css", f".a {{ transition: opacity {value} ease; }}\n")
    result = _run(repo)
    assert result.returncode == 1
    assert f"hardcodes '{value}'" in result.stderr
    assert token in result.stderr


def test_every_layer_of_a_shorthand_is_read(repo: Path):
    _write(repo, "a.css", ".a { transition: opacity var(--t-fast), transform 0.2s; }\n")
    result = _run(repo)
    assert result.returncode == 1
    assert "hardcodes '0.2s'" in result.stderr


def test_an_animation_duration_is_read(repo: Path):
    _write(repo, "a.css", ".a { animation: fade 0.18s ease-out both; }\n")
    assert "hardcodes '0.18s'" in _run(repo).stderr


def test_a_delay_after_a_token_is_not_a_duration(repo: Path):
    _write(repo, "a.css", ".a { transition: opacity var(--t-fast) var(--ease-out) 0.3s; }\n")
    result = _run(repo)
    assert result.returncode == 0, result.stderr


def test_an_ambient_loop_is_left_alone(repo: Path):
    """No token describes a 1.4s pulse; asking for one is a finding with no fix."""
    _write(repo, "a.css", ".a { animation: pulse 1.4s ease-in-out infinite; }\n")
    result = _run(repo)
    assert result.returncode == 0, result.stderr


def test_a_repo_without_tokens_is_not_asked_for_them(bare_repo: Path):
    _write(
        bare_repo,
        "a.css",
        ".a { transition: opacity 0.2s; }\n"
        "@media (prefers-reduced-motion: reduce) { .a { transition: none; } }\n",
    )
    result = _run(bare_repo)
    assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------- #
# 3. reduced-motion policy
# --------------------------------------------------------------------------- #


def test_motion_with_no_policy_is_reported(bare_repo: Path):
    _write(bare_repo, "a.css", ".a { animation: spin 1s linear infinite; }\n")
    result = _run(bare_repo)
    assert result.returncode == 1
    assert "prefers-reduced-motion" in result.stderr


def test_a_policy_in_the_file_itself_passes(bare_repo: Path):
    _write(
        bare_repo,
        "a.css",
        ".a { animation: spin 1s linear infinite; }\n"
        "@media (prefers-reduced-motion: reduce) { .a { animation: none; } }\n",
    )
    result = _run(bare_repo)
    assert result.returncode == 0, result.stderr


def test_the_global_policy_covers_every_file(repo: Path):
    _write(repo, "a.css", ".a { animation: spin 1s linear infinite; }\n")
    result = _run(repo)
    assert result.returncode == 0, result.stderr


def test_a_colour_fade_moves_nothing(bare_repo: Path):
    _write(bare_repo, "a.css", ".a { transition: background-color 1s, opacity 1s; }\n")
    result = _run(bare_repo)
    assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------- #
# scoping, waivers, unreadable input
# --------------------------------------------------------------------------- #


def test_an_untouched_line_is_not_reported(repo: Path):
    path = _write(repo, "a.css", ".a { transition: width 0.2s; }\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "inherited")
    path.write_text(path.read_text() + ".b { opacity: 1; }\n")
    result = _run(repo)
    assert result.returncode == 0, result.stderr


def test_staged_scope_reads_the_index(repo: Path):
    _write(repo, "a.css", ".a { transition: width var(--t-med); }\n")
    _git(repo, "add", "-A")
    result = _run(repo, scope="staged")
    assert result.returncode == 1
    assert "transitions 'width'" in result.stderr


def test_a_waiver_with_a_reason_passes(repo: Path):
    _write(
        repo,
        "a.css",
        ".a { transition: width var(--t-med); } "
        "/* motion-ok: a progress bar inside a fixed track; nothing around it reflows */\n",
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr


def test_a_waiver_without_a_reason_fails(repo: Path):
    _write(repo, "a.css", ".a { transition: width var(--t-med); } /* motion-ok: */\n")
    assert _run(repo).returncode == 1


def test_an_unparseable_stylesheet_is_reported_not_skipped(repo: Path):
    _write(repo, "a.css", ".a { transition: width 0.2s;\n")
    result = _run(repo)
    assert result.returncode == 1
    assert "could not parse" in result.stderr
