"""A test that hangs must fail, not stall the suite.

Not a test of pytest-timeout — that is the plugin's own business. This guards the
*configuration*, because losing it is silent in exactly the way that matters: the
suite keeps passing, and the next deadlock presents as a run that never returns.
It cost 25 minutes of diagnosing a healthy run on 2026-09-11, and the only reason
it was resolvable at all was `ps` showing 55% CPU.

Asserts the invariant, not the number. 120 is a judgement call that should be
free to move with the suite's slowest test; "there is a ceiling, and SIGALRM
fails the one test rather than killing the run" is the part that must not.
"""

from pathlib import Path

import tomllib

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _ini_options() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]


def test_a_per_test_wall_clock_ceiling_is_configured():
    timeout = _ini_options().get("timeout")
    assert isinstance(timeout, int) and timeout > 0, (
        "No pytest `timeout` in pyproject.toml: a hung test would stall the whole "
        "suite instead of failing, and a 44-minute run gives no signal either way."
    )


def test_the_ceiling_fails_one_test_rather_than_killing_the_run():
    """`thread` would abort the process and lose every result after the hang."""
    assert _ini_options().get("timeout_method") == "signal"


def test_the_ceiling_clears_the_slowest_test_by_a_wide_margin():
    """A ceiling tight enough to flake under load is worse than none: it teaches
    everyone to re-run. The slowest test measured was 9.55s."""
    assert _ini_options()["timeout"] >= 60


def test_the_plugin_backing_the_ceiling_is_a_declared_dependency():
    """Without the dependency the ini key is inert — pytest warns about an
    unknown option and every test runs uncapped."""
    raw = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    groups = [
        raw["project"]["optional-dependencies"]["dev"],
        raw["dependency-groups"]["dev"],
    ]
    for group in groups:
        assert any(spec.startswith("pytest-timeout") for spec in group), group
