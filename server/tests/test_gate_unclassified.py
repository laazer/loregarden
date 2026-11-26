"""The shared vocabulary two Python gates use to name a construct they cannot classify.

`.lefthook/scripts/gate_unclassified.py` is the CONSTRUCT-level sibling of
`precommit_git_diff.UnexaminableFileError`, which is the FILE-level one. The
file-level case is already solved and pinned (`test_gate_parse_failures.py`);
this module answers the case a gate PARSED and then had no arm for, and today
flattens into a neighbouring arm.

These pin the module's own contract (lg-code-knowledge-1084, AC3, AC4, AC7c):

* three outcomes, named — CLASSIFIED, NOT-APPLICABLE, UNCLASSIFIED. Collapsing
  NOT-APPLICABLE into UNCLASSIFIED makes the gates fire on ordinary code;
  collapsing the other way reproduces today's bug under a new name;
* one formatter, so both gates word the same situation identically;
* an empty `registered_at` is refused at construction — the wiring site is the
  one field the producer cannot honestly leave blank;
* it imports no `loregarden`, no third-party package, and touches neither the
  filesystem nor git: `.lefthook/scripts/` runs in every workspace.

The module does not exist until the implement stage, so every test here is red
until then. That is the point of the stage.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest
from tests.repo_paths import REPO_ROOT

_ROOT = REPO_ROOT
_SCRIPTS = _ROOT / ".lefthook" / "scripts"
_MODULE_PATH = _SCRIPTS / "gate_unclassified.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Registered before exec: @dataclass resolves annotations through
    # sys.modules[cls.__module__], which is None for an unregistered module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


vocabulary = _load("gate_unclassified", _MODULE_PATH)


def _item(**overrides):
    fields = {
        "construct": "ast.Match",
        "path": "src/pkg/mod.py",
        "line": 12,
        "inferred_by": "py_silent_except_check._paths_through",
        "registered_at": "py_silent_except_check.violations_in",
    }
    fields.update(overrides)
    return vocabulary.Unclassified(**fields)


# --------------------------------------------------------------------------- #
# AC4 — a new module, not an addition to the 1303-line scope resolver
# --------------------------------------------------------------------------- #


def test_the_module_is_its_own_file():
    assert _MODULE_PATH.is_file(), f"{_MODULE_PATH} should exist"


#: What a module that runs in every workspace, before any dependency is
#: installed, is allowed to need. `loregarden` is excluded by name because the
#: whole point of `.lefthook/scripts/` is that it is workspace-agnostic.
_ALLOWED_IMPORT_ROOTS = {"__future__", "dataclasses", "enum", "typing"}

#: Reaching the disk or git from here would make the vocabulary module a second
#: place a gate can fail to examine something, which is what it exists to name.
_FORBIDDEN_IMPORT_ROOTS = {"os", "pathlib", "shutil", "subprocess", "tempfile", "glob", "io"}


def _import_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_it_imports_nothing_it_may_not():
    roots = _import_roots(ast.parse(_MODULE_PATH.read_text(encoding="utf-8")))
    assert "loregarden" not in roots, "`.lefthook/scripts/` is workspace-agnostic"
    assert roots & _FORBIDDEN_IMPORT_ROOTS == set(), "no filesystem, no subprocess, no git"
    assert roots <= _ALLOWED_IMPORT_ROOTS, (
        f"unexpected imports: {sorted(roots - _ALLOWED_IMPORT_ROOTS)}"
    )


def test_it_opens_no_files():
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "open" not in called


# --------------------------------------------------------------------------- #
# AC3 — the three-valued contract, written once
# --------------------------------------------------------------------------- #


def test_exactly_three_outcomes_are_named():
    assert {member.name for member in vocabulary.Outcome} == {
        "CLASSIFIED",
        "NOT_APPLICABLE",
        "UNCLASSIFIED",
    }


def test_the_three_outcomes_are_distinct_values():
    """NOT-APPLICABLE aliased onto UNCLASSIFIED is one of the two collapses
    this criterion forbids, and an alias would pass a membership check."""
    assert len({member.value for member in vocabulary.Outcome}) == 3


# --------------------------------------------------------------------------- #
# AC4 / AC7c — the value, and the one field a producer cannot leave blank
# --------------------------------------------------------------------------- #


def test_it_carries_the_five_parts_a_reader_needs():
    item = _item()
    assert item.construct == "ast.Match"
    assert item.path == "src/pkg/mod.py"
    assert item.line == 12
    assert item.inferred_by == "py_silent_except_check._paths_through"
    assert item.registered_at == "py_silent_except_check.violations_in"


def test_it_is_frozen():
    with pytest.raises(Exception):
        _item().line = 99


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_an_empty_registered_at_is_refused(blank):
    """A finding that cannot say where its classifier is wired is worse than
    absent: it reads as provenance and carries none."""
    with pytest.raises(ValueError):
        _item(registered_at=blank)


def test_a_real_registered_at_is_accepted():
    assert _item(registered_at="py_organization_check.isinstance_errors")


# --------------------------------------------------------------------------- #
# AC4 — one formatter, so two gates cannot drift apart
# --------------------------------------------------------------------------- #


def test_the_label_is_the_criterion_s_own_words():
    """`test_silent_except_gate.py` and `test_organization_isinstance_gate.py`
    spell this literally so their existing coverage survives a missing module;
    this is the assertion that keeps the two in step."""
    assert vocabulary.UNCLASSIFIED_LABEL == "unclassified construct at"


def test_the_message_names_the_construct_and_its_own_file_and_line():
    message = vocabulary.unclassified_message(_item())
    assert vocabulary.UNCLASSIFIED_LABEL in message
    assert "src/pkg/mod.py:12" in message
    assert "ast.Match" in message


def test_the_message_labels_the_provenance_fields():
    """The labels are the seam the conformance test uses to compare two gates'
    wording with the provenance masked out; unlabelled provenance would make
    "same words" unmeasurable."""
    message = vocabulary.unclassified_message(_item())
    assert "inferred_by=py_silent_except_check._paths_through" in message
    assert "registered_at=py_silent_except_check.violations_in" in message


def test_the_message_has_the_shape_both_gates_are_compared_on():
    """The shape, not the prose.

    `<label> <path>:<line>: <construct>; <one wording> (inferred_by=…, registered_at=…)`
    is what lets `test_gate_scope_visibility` mask the four parts a criterion
    allows to differ and compare what is left byte for byte. Without a pinned
    shape "the same words on both surfaces" is not a measurable claim.
    """
    item = _item()
    message = vocabulary.unclassified_message(item)
    prefix = f"{vocabulary.UNCLASSIFIED_LABEL} {item.path}:{item.line}: {item.construct}; "
    suffix = f"(inferred_by={item.inferred_by}, registered_at={item.registered_at})"

    assert message.startswith(prefix)
    assert message.endswith(suffix)
    assert message[len(prefix) : -len(suffix)].strip(), "there is a wording between them"


def test_the_wording_between_the_parts_does_not_depend_on_the_parts():
    """Change every variable part; the sentence in the middle must not move."""
    one = _item()
    other = _item(
        construct="isinstance(x, *types)",
        path="other/place.py",
        line=4,
        inferred_by="py_organization_check._isinstance_targets",
        registered_at="py_organization_check.isinstance_errors",
    )

    def _middle(item):
        text = vocabulary.unclassified_message(item)
        prefix = f"{vocabulary.UNCLASSIFIED_LABEL} {item.path}:{item.line}: {item.construct}; "
        suffix = f"(inferred_by={item.inferred_by}, registered_at={item.registered_at})"
        return text[len(prefix) : -len(suffix)]

    assert _middle(one) == _middle(other)


def test_the_message_is_one_line():
    """It is appended to a gate's finding list, which prints one line each."""
    assert "\n" not in vocabulary.unclassified_message(_item())


# --------------------------------------------------------------------------- #
# AC4 / AC3 — "imported by both gates", asserted directly
#
# The conformance test in `test_gate_scope_visibility.py` infers the shared
# module from two gates producing byte-identical wording, which a gate that
# copied the sentence also satisfies until someone edits one copy. These assert
# the import itself, and that the three-valued contract is wired rather than
# shipped as a decorative enum nothing returns — R1, the ticket's own defect
# applied to its fix.
# --------------------------------------------------------------------------- #

_ADOPTERS = ("py_silent_except_check", "py_organization_check")


def _source_of(name: str) -> str:
    return (_SCRIPTS / f"{name}.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("adopter", _ADOPTERS)
def test_both_gates_import_the_vocabulary_module(adopter):
    tree = ast.parse(_source_of(adopter))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "gate_unclassified" in imported, f"{adopter} must word this through the shared module"


@pytest.mark.parametrize("adopter", _ADOPTERS)
def test_neither_gate_spells_the_label_itself(adopter):
    """A second copy of the wording is a second thing that can drift, which is
    what one vocabulary module exists to prevent."""
    assert vocabulary.UNCLASSIFIED_LABEL not in _source_of(adopter)
