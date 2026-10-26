"""The type-scale gate, exercised as the thing it is: a node script.

Black-box over a real git repo and real `--scope` flags, like the theme and
motion gates' tests. Both failure directions are pinned: a gate that accuses
`font-size: 0.92em` or a Hive tile `calc()` is noise nobody keeps, and one that
misses `font: 600 10px var(--mono)` is a vacuous pass.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
from tests.repo_templates import from_template
from tests.stylesheet_gate_helpers import build_client_repo, clean_env, git

_ROOT = Path(__file__).resolve().parents[2]
_GATE = _ROOT / ".lefthook" / "scripts" / "ts_type_check.cjs"
_NODE_MODULES = _ROOT / "client" / "node_modules"

pytestmark = pytest.mark.skipif(
    not (_NODE_MODULES / "@typescript-eslint" / "typescript-estree").exists()
    or not (_NODE_MODULES / "postcss").exists()
    or shutil.which("node") is None,
    reason="needs node and client/node_modules (cd client && npm ci)",
)

_ROOT_STYLESHEET = """:root {
  --fs-micro: 11px;
  --fs-small: 12px;
  --fs-body: 13px;
  --fs-lead: 15px;
  --fs-title: 19px;
  --fs-display: 24px;
}
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A workspace whose index.css defines the --fs-* scale.

    Built in a fixture so a broken setup is an ERROR, not a gate that found nothing.
    """
    return from_template(
        tmp_path / "repo", "type_gate_tokens", lambda r: build_client_repo(r, _ROOT_STYLESHEET)
    )


@pytest.fixture
def bare_repo(tmp_path: Path) -> Path:
    """A workspace with no root stylesheet, so no scale to point at."""
    return from_template(tmp_path / "repo", "type_gate_bare", lambda r: build_client_repo(r, None))


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
        env=clean_env(),
    )


# --------------------------------------------------------------------------- #
# 1. stylesheets
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("value", "token"),
    [
        ("10px", "--fs-micro"),
        ("12.5px", "--fs-small"),
        ("14px", "--fs-body"),
        ("16px", "--fs-lead"),
        ("1.25rem", "--fs-title"),
        ("34px", "--fs-display"),
    ],
)
def test_a_hardcoded_font_size_names_the_nearest_step(repo: Path, value: str, token: str):
    _write(repo, "a.css", f".a {{ font-size: {value}; }}\n")
    result = _run(repo)
    assert result.returncode == 1
    assert f"hardcodes '{value}'" in result.stderr
    assert f"var({token})" in result.stderr


@pytest.mark.parametrize(
    "value", ["600 10px var(--mono)", "10px/1 monospace", "italic bold 12px/1.4 sans-serif"]
)
def test_the_size_in_a_font_shorthand_is_reported(repo: Path, value: str):
    _write(repo, "a.css", f".a {{ font: {value}; }}\n")
    result = _run(repo)
    assert result.returncode == 1
    assert "'font' hardcodes" in result.stderr


@pytest.mark.parametrize(
    "decl",
    [
        "font-size: var(--fs-body)",
        "font: 600 var(--fs-micro) var(--mono)",
        "font-size: 0.92em",
        "font-size: 90%",
        "font-size: inherit",
        "font-size: calc(var(--hive-tile) * 0.62)",
        "font: inherit",
        "line-height: 18px",
    ],
)
def test_scale_relative_and_computed_sizes_pass(repo: Path, decl: str):
    _write(repo, "a.css", f".a {{ {decl}; }}\n")
    result = _run(repo)
    assert result.returncode == 0, result.stderr


def test_the_token_definitions_themselves_pass(repo: Path):
    _write(repo, "a.css", ':root { font-size: 13px; }\n[data-theme="light"] { font-size: 13px; }\n')
    result = _run(repo)
    assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------- #
# 2. code
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value", ["12", '"12px"', "'0.75rem'"])
def test_a_hardcoded_font_size_in_a_style_object_is_reported(repo: Path, value: str):
    _write(repo, "A.tsx", f"export const A = () => <div style={{{{ fontSize: {value} }}}} />;\n")
    result = _run(repo)
    assert result.returncode == 1
    assert "fontSize hardcodes" in result.stderr


def test_a_token_in_a_style_object_passes(repo: Path):
    _write(
        repo, "A.tsx", 'export const A = () => <div style={{ fontSize: "var(--fs-small)" }} />;\n'
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr


def test_a_computed_font_size_is_left_alone(repo: Path):
    _write(
        repo,
        "A.tsx",
        "export const A = ({ s }: { s: number }) => <div style={{ fontSize: s }} />;\n",
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------- #
# 3. the display face
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "decl",
    [
        "font-family: var(--dp)",
        'font-family: var(--dp), "Sora", sans-serif',
        "font: 600 var(--fs-lead) var(--dp)",
    ],
)
def test_the_display_face_on_a_title_is_reported(repo: Path, decl: str):
    _write(repo, "a.css", f".title {{ {decl}; }}\n")
    result = _run(repo)
    assert result.returncode == 1
    assert "display face var(--dp)" in result.stderr


def test_the_display_face_in_a_style_object_is_reported(repo: Path):
    _write(repo, "A.tsx", 'export const A = () => <h1 style={{ fontFamily: "var(--dp)" }} />;\n')
    result = _run(repo)
    assert result.returncode == 1
    assert "fontFamily uses the display face" in result.stderr


def test_the_body_face_passes(repo: Path):
    _write(repo, "a.css", ".title { font-family: var(--ui); font-weight: 600; }\n")
    _write(repo, "A.tsx", 'export const A = () => <h1 style={{ fontFamily: "var(--ui)" }} />;\n')
    result = _run(repo)
    assert result.returncode == 0, result.stderr


def test_the_wordmark_keeps_the_display_face_with_a_waiver(repo: Path):
    _write(
        repo, "a.css", ".brand { font-family: var(--dp); /* type-ok: the loregarden wordmark */ }\n"
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------- #
# scoping, waivers, no scale, unreadable input
# --------------------------------------------------------------------------- #


def test_a_repo_with_no_scale_is_not_asked_for_one(bare_repo: Path):
    _write(bare_repo, "a.css", ".a { font-size: 10px; }\n")
    result = _run(bare_repo)
    assert result.returncode == 0, result.stderr


def test_an_untouched_line_is_not_reported(repo: Path):
    path = _write(repo, "a.css", ".a { font-size: 10px; }\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "inherited")
    path.write_text(path.read_text() + ".b { font-size: var(--fs-body); }\n")
    result = _run(repo)
    assert result.returncode == 0, result.stderr


def test_staged_scope_reads_the_index(repo: Path):
    _write(repo, "a.css", ".a { font-size: 10px; }\n")
    git(repo, "add", "-A")
    result = _run(repo, scope="staged")
    assert result.returncode == 1
    assert "hardcodes '10px'" in result.stderr


def test_a_waiver_with_a_reason_passes(repo: Path):
    _write(
        repo,
        "a.css",
        ".a { font-size: 34px; } /* type-ok: the Home greeting, a display size above the scale */\n",
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr


def test_a_waiver_without_a_reason_fails(repo: Path):
    _write(repo, "a.css", ".a { font-size: 34px; } /* type-ok: */\n")
    assert _run(repo).returncode == 1


def test_an_unparseable_stylesheet_is_reported_not_skipped(repo: Path):
    _write(repo, "a.css", ".a { font-size: 10px;\n")
    result = _run(repo)
    assert result.returncode == 1
    assert "could not parse" in result.stderr
