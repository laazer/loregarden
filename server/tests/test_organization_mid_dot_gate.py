"""The organization gate's mid-dot f-string detector.

Hand-rolled ``f"a · {b}"`` clusters were slipping past the duplicate-body DRY
check. Pin both halves: what it must catch, and what Dot-based code must leave
alone.
"""

import ast
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


def _errors_for(source: str, *, touched: set[int] | None = None) -> list[str]:
    tree = ast.parse(source)
    # When touched is None, treat every mid-dot line as staged so the check
    # exercises the smell detector without a git diff.
    if touched is None:
        sites: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                sites.update(checker._mid_dot_sites_in_function(node))
        touched = sites
    return checker.mid_dot_fstring_errors(Path("service.py"), tree, touched)


FLAGGED = """
def format_events(payload):
    thread_id = payload.get("thread_id") or ""
    if thread_id:
        return f"codex thread · {thread_id}"
    status = payload.get("status") or ""
    suffix = f" · {status}" if status else ""
    model = payload.get("model") or "default"
    return f"session init · {model}{suffix}"
"""

CLEAN_DOT = """
from loregarden.dot_line import Dot

def format_events(payload):
    thread_id = payload.get("thread_id") or ""
    if thread_id:
        return str(Dot("codex thread") / thread_id)
    status = payload.get("status") or ""
    model = payload.get("model") or "default"
    return str(Dot("session init") / model / status)
"""

BELOW_THRESHOLD = """
def format_events(payload):
    thread_id = payload.get("thread_id") or ""
    if thread_id:
        return f"codex thread · {thread_id}"
    model = payload.get("model") or "default"
    return f"session init · {model}"
"""

# The shape that was in run_log_stream before Dot — three mid-dot f-strings in
# one formatter, which is what slipped past duplicate-body DRY.
CODEX_SHAPED = r"""
def _format_codex_stream_payload(msg_type, payload):
    if msg_type == "thread.started":
        thread_id = payload.get("thread_id") or ""
        return "SYS", f"codex thread · {thread_id}" if thread_id else "codex thread started"
    if msg_type == "turn.completed":
        usage = payload.get("usage") or {}
        if isinstance(usage, dict) and usage:
            return "SYS", (
                "codex turn done · "
                f"in={usage.get('input_tokens', '?')} out={usage.get('output_tokens', '?')}"
            )
        return "SYS", "codex turn done"
    status = str(payload.get("status") or "").strip()
    label = "command"
    suffix = f" · {status}" if status else ""
    return "TOOL", f"$ {label}{suffix}"
"""


@pytest.mark.parametrize("source", [FLAGGED, CODEX_SHAPED])
def test_mid_dot_cluster_is_flagged(source):
    errors = _errors_for(source)
    assert errors
    assert "mid-dot" in errors[0]
    assert "Dot" in errors[0]


def test_dot_based_formatter_is_clean():
    assert _errors_for(CLEAN_DOT) == []


def test_below_threshold_is_clean():
    assert _errors_for(BELOW_THRESHOLD) == []


def test_untouched_cluster_is_ignored():
    # Pre-existing debt: sites exist but none overlap the staged lines.
    assert _errors_for(FLAGGED, touched=set()) == []


def test_tests_are_exempt():
    tree = ast.parse(FLAGGED)
    errors = checker.mid_dot_fstring_errors(Path("tests/test_foo.py"), tree, {1, 2, 3, 4, 5})
    assert errors == []
