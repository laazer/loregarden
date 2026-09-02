"""The type checker is checking (547).

`server/pyproject.toml` disabled 20 mypy error codes, and the effect was that
this type-checked clean against the repo's own config:

    def f() -> int:
        return "definitely not an int"

So "mypy clean" in a stage report, a review, or a pre-commit run was weak
evidence, and every agent citing it as proof of correctness was citing very
little. It also explains a stale `# type: ignore[arg-type]` surviving on
lg-improved-memory-183 until `warn_unused_ignores` flagged it: the code it
suppressed was never being checked.

These tests run the real `mypy` against the real config, because that is the
only thing that can answer this. A test asserting the *contents* of
`disable_error_code` would pass while mypy silently ignored the file — the same
mistake one level up, and this repo has produced that shape nine times.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_SERVER = Path(__file__).resolve().parents[1]

#: One violation per code, each the plainest possible instance. Fails as a
#: single mypy run so the whole set costs one process rather than eleven.
VIOLATIONS = {
    "return-value": "def f() -> int:\n    return 'no'\n",
    "call-arg": "def f(a: int) -> None: ...\nf(1, 2)\n",
    "list-item": "xs: list[int] = ['no']\n",
    "override": (
        "class A:\n    def f(self) -> int: ...\nclass B(A):\n    def f(self) -> str: ...\n"
    ),
    "valid-type": "def f(x: 3) -> None: ...\n",
}


def _mypy(source: str, tmp_path: Path) -> subprocess.CompletedProcess:
    target = tmp_path / "sample.py"
    target.write_text(source, encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            str(REPO_SERVER / "pyproject.toml"),
            str(target),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_SERVER,
        check=False,
    )


def test_the_headline_case_is_caught(tmp_path):
    """547's own example, verbatim. This passed before the ticket."""
    result = _mypy("def f() -> int:\n    return 'definitely not an int'\n", tmp_path)

    assert result.returncode != 0, result.stdout
    assert "return-value" in result.stdout, result.stdout


@pytest.mark.parametrize("code", sorted(VIOLATIONS))
def test_each_re_enabled_code_actually_fires(code, tmp_path):
    """A code in the config is not the same as a code that reports.

    Several of the twenty were inert for a second reason — `no-untyped-def` does
    nothing while `allow_untyped_defs` is true — so "we re-enabled it" is a
    claim about a list, not about behaviour. Each of these has a violation that
    must produce that code by name.
    """
    result = _mypy(VIOLATIONS[code], tmp_path)

    assert result.returncode != 0, f"{code} produced no error:\n{result.stdout}"
    assert f"[{code}]" in result.stdout, f"expected [{code}] in:\n{result.stdout}"


def test_the_checked_tree_is_clean(tmp_path):
    """Correct code still passes, so the config is not merely strict enough to fail.

    Without this, deleting the whole codebase would satisfy every test above.
    """
    result = _mypy("def f() -> int:\n    return 1\n", tmp_path)

    assert result.returncode == 0, result.stdout
