"""The organization gate's whole-file line cap.

Test modules get a higher cap than production modules: a suite grows by
accumulating cases, and splitting one because it crossed a line count scatters
related coverage. Pin both halves, so neither the raised test cap nor the
unchanged production cap can drift silently.
"""

import importlib.util
from pathlib import Path

import pytest

_CHECKER_PATH = (
    Path(__file__).resolve().parents[2] / ".lefthook" / "scripts" / "py_organization_check.py"
)


def _load_checker():
    spec = importlib.util.spec_from_file_location("py_organization_check", _CHECKER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _write(tmp_path: Path, relative: str, line_count: int) -> Path:
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(f"x = {n}\n" for n in range(line_count)), encoding="utf-8")
    return target


def _length_errors(path: Path) -> list[str]:
    # net_growing=True: the whole-file cap only fires on a diff that lengthens
    # the file, so a shrinking edit to an over-cap file stays allowed.
    return [
        err
        for err in checker.check_file(path, touched_lines=set(), net_growing=True, repo=None)
        if "module is" in err
    ]


def test_test_cap_is_higher_than_production_cap():
    assert checker.MAX_TEST_FILE_LINES > checker.MAX_FILE_LINES


@pytest.mark.parametrize(
    "relative",
    ["tests/test_big.py", "tests/helpers/support.py", "test_big.py"],
)
def test_test_files_are_allowed_past_the_production_cap(tmp_path, relative):
    path = _write(tmp_path, relative, checker.MAX_FILE_LINES + 50)
    assert _length_errors(path) == []


@pytest.mark.parametrize(
    "relative",
    ["tests/test_big.py", "tests/helpers/support.py", "test_big.py"],
)
def test_test_files_still_have_a_cap(tmp_path, relative):
    path = _write(tmp_path, relative, checker.MAX_TEST_FILE_LINES + 50)
    errors = _length_errors(path)
    assert len(errors) == 1
    assert f"max {checker.MAX_TEST_FILE_LINES}" in errors[0]


def test_production_cap_is_unchanged(tmp_path):
    path = _write(tmp_path, "loregarden/services/big.py", checker.MAX_FILE_LINES + 50)
    errors = _length_errors(path)
    assert len(errors) == 1
    assert f"max {checker.MAX_FILE_LINES}" in errors[0]


def test_production_file_under_the_cap_is_clean(tmp_path):
    path = _write(tmp_path, "loregarden/services/small.py", checker.MAX_FILE_LINES - 50)
    assert _length_errors(path) == []


def test_shrinking_an_over_cap_file_is_allowed(tmp_path):
    path = _write(tmp_path, "loregarden/services/big.py", checker.MAX_FILE_LINES + 50)
    assert checker.check_file(path, touched_lines=set(), net_growing=False, repo=None) == []
