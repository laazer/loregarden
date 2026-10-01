"""The theme gate, exercised as the thing it is: a node script.

Black-box over a real git repo and real `--scope` flags, like the
user-experience gate's tests, because diff scoping is half of what the gate
does. Both failure directions are pinned: a gate that accuses `animation: tan`
or `fill="url(#g)"` is noise nobody keeps, and one that misses `color: white`
is a vacuous pass.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_GATE = _ROOT / ".lefthook" / "scripts" / "ts_theme_check.cjs"
_NODE_MODULES = _ROOT / "client" / "node_modules"

pytestmark = pytest.mark.skipif(
    not (_NODE_MODULES / "@typescript-eslint" / "typescript-estree").exists()
    or not (_NODE_MODULES / "postcss").exists()
    or shutil.which("node") is None,
    reason="needs node and client/node_modules (cd client && npm ci)",
)


def _clean_env() -> dict[str, str]:
    # GIT_DIR/GIT_WORK_TREE beat cwd, and a run nested in a worktree's hook
    # inherits them pointing at the real repository.
    return {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=_clean_env())


def _init(root: Path, *, primitives: bool) -> Path:
    _git(root, "init", "-q", "-b", "main", ".")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / "client" / "src").mkdir(parents=True)
    (root / "client" / "src" / ".keep").write_text("")
    if primitives:
        ui = root / "client" / "src" / "components" / "ui"
        ui.mkdir(parents=True)
        (ui / "Button.tsx").write_text("export const Button = () => null;\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A workspace with themed primitives: `client/src/components/ui/Button.tsx`.

    Built in a fixture so a broken setup is an ERROR, not a gate that found nothing.
    """
    return _init(tmp_path, primitives=True)


@pytest.fixture
def bare_repo(tmp_path: Path) -> Path:
    """A workspace with no components/ui primitives to point anyone at."""
    return _init(tmp_path, primitives=False)


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


def _findings(result: subprocess.CompletedProcess) -> str:
    return result.stderr


# --------------------------------------------------------------------------- #
# 1. raw form controls
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("tag", "replacement"),
    [("button", "Button"), ("input", "Input"), ("select", "Select"), ("textarea", "Textarea")],
)
def test_a_raw_control_is_reported(repo: Path, tag: str, replacement: str):
    _write(repo, "A.tsx", f"export const A = () => <{tag} />;\n")
    result = _run(repo)
    assert result.returncode == 1
    assert f"raw <{tag}>" in _findings(result)
    assert f"<{replacement}>" in _findings(result)


def test_the_themed_primitive_passes(repo: Path):
    _write(repo, "A.tsx", 'export const A = () => <Button variant="primary">Save</Button>;\n')
    result = _run(repo)
    assert result.returncode == 0, _findings(result)


def test_a_hidden_input_is_not_a_surface(repo: Path):
    _write(repo, "A.tsx", 'export const A = () => <input type="hidden" value={id} />;\n')
    assert _run(repo).returncode == 0


def test_the_primitives_themselves_render_the_raw_element(repo: Path):
    _write(repo, "components/ui/Select.tsx", "export const Select = (p) => <select {...p} />;\n")
    result = _run(repo)
    assert result.returncode == 0, _findings(result)


def test_a_repo_without_primitives_is_not_asked_for_them(bare_repo: Path):
    """Pointing at <Button> where none exists is a finding with no fix."""
    _write(bare_repo, "A.tsx", "export const A = () => <button>Save</button>;\n")
    result = _run(bare_repo)
    assert result.returncode == 0, _findings(result)


# --------------------------------------------------------------------------- #
# 2. colours in stylesheets
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("declaration", "colour"),
    [
        ("background: #fff;", "#fff"),
        ("border: 1px solid #23324aff;", "#23324aff"),
        ("box-shadow: 0 2px 12px rgba(111, 174, 143, 0.3);", "rgba("),
        ("color: oklch(70% 0.1 150);", "oklch("),
        ("color: white;", "white"),
        ("--card-bg: ghostwhite;", "ghostwhite"),
        ("color: var(--fg2, #8a8f98);", "#8a8f98"),
        ("background: color-mix(in srgb, var(--ac) 30%, white);", "white"),
    ],
)
def test_a_hardcoded_colour_is_reported(repo: Path, declaration: str, colour: str):
    _write(repo, "A.css", f".card {{\n  {declaration}\n}}\n")
    result = _run(repo)
    assert result.returncode == 1
    assert f"hardcodes '{colour}" in _findings(result)
    assert "A.css:2:" in _findings(result)


@pytest.mark.parametrize(
    "declaration",
    [
        "background: var(--bg2);",
        "background: color-mix(in srgb, var(--ac) 30%, transparent);",
        "color: currentColor;",
        "border-color: transparent;",
        "animation: tan 1s;",  # an animation name, not a colour
        "grid-area: navy;",
        "fill: url(#gradient);",
        'content: "#1";',
    ],
)
def test_a_token_or_a_non_colour_passes(repo: Path, declaration: str):
    _write(repo, "A.css", f".card {{\n  {declaration}\n}}\n")
    result = _run(repo)
    assert result.returncode == 0, _findings(result)


@pytest.mark.parametrize("selector", [":root", ':root[data-theme="light"]', '[data-theme="light"]'])
def test_colours_are_defined_in_a_theme_block(repo: Path, selector: str):
    _write(repo, "A.css", f"{selector} {{\n  --bg0: #0b0f16;\n  --tx: white;\n}}\n")
    result = _run(repo)
    assert result.returncode == 0, _findings(result)


def test_a_prefers_color_scheme_branch_is_reported(repo: Path):
    _write(
        repo,
        "A.css",
        ".card {\n  background: var(--bg2);\n}\n"
        "@media (prefers-color-scheme: dark) {\n  .card {\n    color: var(--tx);\n  }\n}\n",
    )
    result = _run(repo)
    assert result.returncode == 1
    assert "A.css:4:" in _findings(result)
    assert "prefers-color-scheme" in _findings(result)


def test_an_unparseable_stylesheet_is_a_finding_not_a_pass(repo: Path):
    _write(repo, "A.css", ".card {\n  color: white;\n")
    result = _run(repo)
    assert result.returncode == 1
    assert "could not parse" in _findings(result)


# --------------------------------------------------------------------------- #
# 3 and 4. colours and OS-preference queries in code
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "jsx",
    [
        '<Box style={{ color: "#fff" }} />',
        '<Box style={{ border: "1px solid rgba(255, 106, 84, 0.35)" }} />',
        '<Box style={on ? { backgroundColor: "white" } : undefined} />',
        '<svg><path fill="#000" /></svg>',
        '<svg><stop stopColor="red" /></svg>',
    ],
)
def test_a_colour_in_code_is_reported(repo: Path, jsx: str):
    _write(repo, "A.tsx", f"export const A = () => {jsx};\n")
    result = _run(repo)
    assert result.returncode == 1
    assert "hardcodes" in _findings(result)


@pytest.mark.parametrize(
    "jsx",
    [
        '<Box style={{ color: "var(--tx)" }} />',
        '<Box style={{ gridArea: "tan" }} />',
        '<svg><path fill="currentColor" stroke="none" /></svg>',
        '<Box title="#12 is open" />',
    ],
)
def test_tokens_and_non_colours_in_code_pass(repo: Path, jsx: str):
    _write(repo, "A.tsx", f"export const A = () => {jsx};\n")
    result = _run(repo)
    assert result.returncode == 0, _findings(result)


def test_a_match_media_on_the_os_scheme_is_reported(repo: Path):
    _write(
        repo,
        "a.ts",
        'export const dark = matchMedia("(prefers-color-scheme: dark)").matches;\n',
    )
    result = _run(repo)
    assert result.returncode == 1
    assert "prefers-color-scheme" in _findings(result)


# --------------------------------------------------------------------------- #
# waivers and scoping
# --------------------------------------------------------------------------- #


def test_a_substantive_css_waiver_clears_the_finding(repo: Path):
    _write(
        repo,
        "A.css",
        ".video {\n  background: #000; /* theme-ok: letterbox bars are black in every theme */\n}\n",
    )
    result = _run(repo)
    assert result.returncode == 0, _findings(result)


def test_a_waiver_in_the_comment_block_above_applies(repo: Path):
    _write(
        repo,
        "A.tsx",
        "export const A = () => (\n"
        "  <>\n"
        "    {/* theme-ok: never painted; the Upload button opens the picker\n"
        "        and owns every visible pixel */}\n"
        '    <input type="file" hidden />\n'
        "  </>\n"
        ");\n",
    )
    result = _run(repo)
    assert result.returncode == 0, _findings(result)


def test_a_waiver_with_no_reason_is_itself_the_finding(repo: Path):
    _write(repo, "A.css", ".a {\n  color: #fff; /* theme-ok: fine */\n}\n")
    result = _run(repo)
    assert result.returncode == 1
    assert "no substantive reason" in _findings(result)


def test_an_untouched_colour_does_not_fail_a_change_elsewhere(repo: Path):
    """Inherited debt is not this commit's — but touching the line makes it so."""
    _write(repo, "A.css", ".a {\n  color: #fff;\n  padding: 1px;\n}\n")
    # Control: uncommitted, the same line is reported, so the passes below
    # mean "correctly scoped" and not "read nothing".
    assert _run(repo).returncode == 1

    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "inherit the colour")
    _write(repo, "A.css", ".a {\n  color: #fff;\n  padding: 2px;\n}\n")
    result = _run(repo)
    assert result.returncode == 0, _findings(result)

    _write(repo, "A.css", ".a {\n  color: #eee;\n  padding: 2px;\n}\n")
    assert _run(repo).returncode == 1


def test_staged_scope_reads_the_files_lefthook_passes(repo: Path):
    """Pre-commit hands over explicit paths, `.css` included."""
    path = _write(repo, "A.css", ".a {\n  color: #fff;\n}\n")
    _git(repo, "add", "-A")
    result = subprocess.run(
        ["node", str(_GATE), str(path.relative_to(repo))],
        cwd=repo,
        capture_output=True,
        text=True,
        env=_clean_env(),
    )
    assert result.returncode == 1
    assert "pre-commit: theme check failed" in result.stderr


def test_tests_are_not_gated(repo: Path):
    _write(repo, "A.test.tsx", "it('x', () => { render(<button>Go</button>); });\n")
    assert _run(repo).returncode == 0
