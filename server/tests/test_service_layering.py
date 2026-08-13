"""The service layer has no import cycles, and the seams that keep it that way.

Two capabilities genuinely sit above almost everything — starting a pipeline on
a thread (`run_service`) and starting a lane's work (`queue_dispatch`) — while
modules well below them need to invoke both. Importing upward for that is what
produced six mutual cycles held apart by function-local imports.

Both are now declared low and installed from above at import time. That works
only as long as nobody reintroduces an upward import, so the shape is asserted
rather than trusted.
"""

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

SERVICES = Path(__file__).resolve().parents[1] / "loregarden" / "services"

#: Cycles that predate this module and belong to unrelated subsystems. Listed so
#: a new one fails the test instead of hiding in a count.
KNOWN_CYCLES = {
    ("cli_settings", "codex_discovery"),
    ("cli_settings", "opencode_discovery"),
    ("codex_provider_usage", "usage_service"),
}


def _service_imports(path: Path) -> set[str]:
    """Runtime service-to-service imports, ignoring `TYPE_CHECKING` blocks.

    A `TYPE_CHECKING` import is not an edge — it never executes — and counting
    one is how `run_completion <-> orchestration` looked like a cycle it is not.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    type_checking_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = ast.unparse(node.test)
            if "TYPE_CHECKING" in test:
                for child in ast.walk(node):
                    if hasattr(child, "lineno"):
                        type_checking_lines.add(child.lineno)

    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.lineno in type_checking_lines:
            continue
        module = node.module or ""
        if module.startswith("loregarden.services."):
            found.add(module.split(".")[2])
        elif module == "loregarden.services":
            found.update(alias.name for alias in node.names)
    return found - {path.stem}


def test_the_service_layer_has_no_new_import_cycles():
    names = {p.stem for p in SERVICES.glob("*.py")}
    edges = {p.stem: _service_imports(p) & names for p in SERVICES.glob("*.py")}

    cycles = {
        tuple(sorted((module, target)))
        for module, targets in edges.items()
        for target in targets
        if module in edges.get(target, set())
    }

    assert cycles - KNOWN_CYCLES == set(), (
        "new import cycle(s) in the service layer; a function-local import to "
        "dodge one is not a fix"
    )


def test_the_queue_and_orchestrator_do_not_import_upward():
    """The two edges this seam exists to remove."""
    assert "orchestration_callbacks" not in _service_imports(SERVICES / "queue_lanes.py")
    assert "run_service" not in _service_imports(SERVICES / "queue_lanes.py")
    assert "run_service" not in _service_imports(SERVICES / "orchestration.py")
    assert "orchestration_callbacks" not in _service_imports(SERVICES / "orchestration.py")


@pytest.mark.parametrize(
    "entry_point",
    ["loregarden.main", "loregarden.cli.mcp_server", "loregarden.cli.main", "loregarden.mcp.tools"],
)
def test_every_entry_point_installs_the_scheduler(entry_point):
    """An uninstalled scheduler means an approved gate never resumes.

    A fresh interpreter, because importing an already-imported entry point is a
    no-op and would assert on wiring some earlier test did.
    """
    probe = (
        f"import {entry_point};"
        "from loregarden.services import scheduling;"
        "import sys; sys.exit(0 if scheduling._scheduler else 1)"
    )
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)

    assert result.returncode == 0, f"{entry_point} installs no scheduler\n{result.stderr}"


def test_an_uninstalled_scheduler_raises_rather_than_dropping_the_work():
    from loregarden.services import scheduling

    original = scheduling._scheduler
    scheduling.set_orchestration_scheduler(None)
    try:
        with pytest.raises(RuntimeError, match="No orchestration scheduler"):
            scheduling.schedule_orchestration("some-ticket")
    finally:
        scheduling.set_orchestration_scheduler(original)


def test_no_function_local_service_imports_in_the_queue_modules():
    """The tell-tale of a dodged cycle, in the modules this branch untangled."""
    offenders = []
    for name in ("queue_lanes", "queue_dispatch", "queue_admission", "ticket_rollup"):
        for number, line in enumerate((SERVICES / f"{name}.py").read_text().splitlines(), 1):
            if re.match(r"\s+from loregarden\.services", line):
                offenders.append(f"{name}.py:{number}: {line.strip()}")
    assert offenders == []
