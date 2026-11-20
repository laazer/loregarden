"""The provenance triple, and the one field a producer may not answer about itself.

An inference carries what inferred it (`inferred_by`), where that inferrer is
wired (`registered_at`), and an optional `confidence`. The substance of
lg-code-knowledge-1084's AC9 is the REGISTRY RULE: `registered_at` is always
resolved from a registry keyed by a stable synthesizer id — never a string
literal at the construction site, and never a value the producer reports about
itself. A producer that names its own wiring site is asserting something it
cannot check, and a hallucinated module path is worse than an absent one.

AC12 is the same rule read from the agent's side: the agent-facing
`FINDING_SCHEMA` deliberately offers `inferred_by` and `confidence` and NOT
`registered_at`.

`models/domain/provenance.py` does not exist until the implement stage, so
every test here is red until then.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from loregarden.mcp.tool_args import FINDING_SCHEMA
from loregarden.models.domain import provenance as provenance_module
from loregarden.models.domain.memory_enums import InferredGroupKind
from loregarden.models.domain.provenance import Provenance
from loregarden.services.operations.run_callbacks import StageFinding

# --------------------------------------------------------------------------- #
# AC9 — the value
# --------------------------------------------------------------------------- #


def test_the_triple_carries_what_inferred_it_where_it_is_wired_and_how_sure():
    item = Provenance(
        inferred_by="classify_path",
        registered_at="loregarden.services.code_signals.compute.reduce_files",
        confidence=0.75,
    )
    assert item.inferred_by == "classify_path"
    assert item.registered_at == "loregarden.services.code_signals.compute.reduce_files"
    assert item.confidence == 0.75


def test_confidence_is_optional_and_defaults_to_absent():
    """Shipping the field with no producer is honest; shipping a number nobody
    derived is not. Calibration is lg-code-knowledge-1140's."""
    assert Provenance(inferred_by="a", registered_at="b").confidence is None


def test_it_is_frozen():
    item = Provenance(inferred_by="a", registered_at="b")
    with pytest.raises(Exception):
        item.inferred_by = "someone else"


def test_an_unresolved_registration_site_is_recorded_as_absent_not_invented():
    """Unlike the gate's `Unclassified`, an empty `registered_at` is legal here:
    a payload that supplied only `inferred_by` must record the OMISSION rather
    than have a site guessed for it (AC10). The rule this module enforces is
    that nothing but a registry may FILL the field, not that it is never empty.
    """
    assert Provenance(inferred_by="an agent", registered_at="").registered_at == ""


# --------------------------------------------------------------------------- #
# AC9 — the registry rule
# --------------------------------------------------------------------------- #


def test_the_module_exports_the_registry_protocol_that_resolves_the_site():
    assert "Provenance" in provenance_module.__all__
    assert "SynthesizerRegistry" in provenance_module.__all__


def test_the_registry_is_keyed_by_a_stable_synthesizer_id():
    """The key is the id, not the producer: the same heuristic resolves to the
    same site from every call site that uses it."""
    resolve = provenance_module.SynthesizerRegistry.registered_at
    parameters = list(inspect.signature(resolve).parameters)
    assert parameters[1:] == ["synthesizer_id"], parameters


def test_the_inferred_group_docstring_points_here_rather_than_restating_the_rule():
    """`InferredGroupKind` states the fact-vs-inference rule in prose. A third
    restatement is a third thing that can drift; it gets a pointer instead."""
    doc = InferredGroupKind.__doc__ or ""
    assert "provenance" in doc.lower()


def test_no_caller_types_a_registration_site_as_a_literal_keyword():
    """The rule, enforced across the tree rather than asserted in one place.

    `Provenance(registered_at="…")` written at a construction site is exactly
    what this criterion forbids: the producer would be naming its own wiring.
    A value resolved from a registry arrives as a name or a call, not a string.
    """
    root = Path(__file__).resolve().parents[1] / "loregarden"
    offenders: list[str] = []
    for py in sorted(root.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        calls = [
            node
            for node in ast.walk(ast.parse(py.read_text(encoding="utf-8")))
            if type(node) is ast.Call
            and (getattr(node.func, "id", "") or getattr(node.func, "attr", "")) == "Provenance"
        ]
        for call in calls:
            sites = [k.value for k in call.keywords if k.arg == "registered_at"]
            if any(type(site) is ast.Constant and site.value for site in sites):
                offenders.append(f"{py}:{call.lineno}")
    assert offenders == [], "registered_at must come from the registry, not a literal"


# --------------------------------------------------------------------------- #
# AC12 — the agent-facing schema is deliberately asymmetric
# --------------------------------------------------------------------------- #


def test_an_agent_may_supply_the_two_fields_it_can_honestly_answer():
    properties = FINDING_SCHEMA["properties"]
    assert "inferred_by" in properties
    assert "confidence" in properties


def test_an_agent_may_not_supply_the_registration_site():
    """The control plane stamps it. Offering the field would invite a model to
    fill in a module path it has no way to know."""
    assert "registered_at" not in FINDING_SCHEMA["properties"]


def test_neither_new_field_is_required():
    """`_FindingPayload` already sets `extra='ignore'` with every field
    optional, so no agent's existing report breaks."""
    assert FINDING_SCHEMA["required"] == ["summary"]


def test_the_schema_the_tool_actually_advertises_carries_the_same_asymmetry():
    """`FINDING_SCHEMA` is the written contract; `StageFinding` is what
    `loregarden_complete_stage` puts in front of an agent (`items_like` renders
    it into the tool's JSON schema). A field only in the first is a field no
    agent is ever offered, so both are pinned.
    """
    properties = StageFinding.model_json_schema()["properties"]
    assert "inferred_by" in properties
    assert "confidence" in properties
    assert "registered_at" not in properties
    assert StageFinding.model_json_schema().get("required") == ["summary"]


# --------------------------------------------------------------------------- #
# AC9 — the registry rule, closed on the routes the keyword check leaves open
# --------------------------------------------------------------------------- #


def _provenance_calls(path: Path) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if type(node) is ast.Call
        and (getattr(node.func, "id", "") or getattr(node.func, "attr", "")) == "Provenance"
    ]


def _server_python_files() -> list[Path]:
    root = Path(__file__).resolve().parents[1] / "loregarden"
    return [py for py in sorted(root.rglob("*.py")) if "__pycache__" not in py.parts]


def test_no_caller_types_a_registration_site_as_a_literal_positional():
    """`Provenance("classify_path", "loregarden.x.y")` is the same forbidden
    claim written without the keyword, and the keyword check cannot see it.

    `registered_at` is the second field, so a second positional argument that is
    a non-empty string constant is a site typed at the construction site.
    """
    offenders: list[str] = []
    for py in _server_python_files():
        for call in _provenance_calls(py):
            if len(call.args) < 2:
                continue
            site = call.args[1]
            if type(site) is ast.Constant and site.value:
                offenders.append(f"{py}:{call.lineno}")
    assert offenders == [], "registered_at must come from the registry, not a literal"


def test_no_caller_reaches_the_field_through_a_copy_or_a_dict_update():
    """The third route: building the value honestly and then overwriting the
    site with a literal (`model_copy(update={"registered_at": "..."})`,
    `replace(p, registered_at="...")`, a dict literal fed to the model)."""
    offenders: list[str] = []
    for py in _server_python_files():
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if type(node) is not ast.Dict:
                continue
            for key, value in zip(node.keys, node.values):
                if type(key) is not ast.Constant or key.value != "registered_at":
                    continue
                if type(value) is ast.Constant and value.value:
                    offenders.append(f"{py}:{node.lineno}")
    assert offenders == [], "a site smuggled in through a dict is still typed by its producer"
