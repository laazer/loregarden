"""A ticket's workspace has one answer: `workspace_for_ticket` (lg-milestone-that-1418).

`Ticket.workspace_id` is NULL **iff** the work item is an initiative —
`models/domain/tables.py:139` enforces the pair with `ck_tickets_workspace_binding`.
So `session.get(Workspace, ticket.workspace_id)` is a primary-key lookup for a NULL
primary key on any initiative-reachable path, and SQLAlchemy answers that with

    SAWarning: fully NULL primary key identity cannot load any object.
    This condition may raise an error in a future release.

64 sites in the package re-derived that rule locally: 49 bare, 8 spelled as a
truthiness ternary (one of which guards the wrong operand), 7 carrying the correct
inline `is not None` guard. The fix is one read-side accessor,
`services.ticket_workspace_binding.workspace_for_ticket`, and this census.

Why a shape-spelled AST walk and not a grep. The ticket's own verification grep is
spelled against the variable name `ticket`, and 8 of the 64 sites name their receiver
`parent`, `milestone`, `ticket_row`, `root`, `original` or `dep`. A detector that can
be defeated by renaming a local is not a standing guard, so the receiver's type is
resolved from its local binding — a `session.get(Ticket, …)` assignment, a parameter
annotated `Ticket`, a `select(Ticket)` iteration, a callee whose return annotation
says `Ticket` — and never from the literal name. The same walk is wrap-invariant,
which matters because appending the inline guard pushes 47 of the 49 lines past
`line-length = 100` and ruff-format then splits 14 of them across three lines.

And it **fails closed**. A receiver whose type this walk cannot resolve fails
`test_every_workspace_lookup_receiver_resolves_to_a_class`, naming the site. A
detector that silently skips what it cannot parse is the exact failure this module
exists to prevent: it would report zero and read as green.

Scoped by nullability, not by `Ticket` (lg-milestone-that-1420). `Ticket` is not the
only receiver that can carry this defect — `DomainEvent.workspace_id` is nullable too,
and a `session.get(Workspace, event.workspace_id)` site is the identical NULL-primary-key
read for the identical reason. The ban therefore filters on the set of classes the modules under
`models/domain/` declare a nullable `workspace_id` on, derived at test time by
`nullable_workspace_id_classes`, which is also what
`test_the_model_package_declares_exactly_the_known_nullable_workspace_id_classes`
asserts over. One derivation, two readers: a third nullable column cannot make the
census say so while the ban keeps covering `Ticket` alone. The derivation is scoped by
the *property* and by the directory that declares row types, not by one filename:
`tables.py` is not the only module there with a nullable `workspace_id` column. `NON_NULLABLE_RECEIVER_LOOKUPS` pins the complement,
so widening the ban is distinguishable from the resolver's reach moving.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import NamedTuple

import pytest
from loregarden.services import ticket_workspace_binding as binding_module

#: The module that owns the answer, and so the one place allowed the raw lookup.
#: The allowlist holds the accessor module alone: leaving the canonical idiom
#: inlined anywhere else leaves a template for the next copy.
ACCESSOR_MODULE = Path("services/ticket_workspace_binding.py")
ACCESSOR_NAME = "workspace_for_ticket"

#: The row type the accessor answers for, and so the one this census can name a
#: guard for. It is **not** the scope of the ban: what makes a
#: `session.get(Workspace, X.workspace_id)` call this defect is that `X`'s
#: `workspace_id` is *nullable*, and that set is derived from `MODEL_PACKAGE` at
#: test time by `nullable_workspace_id_classes`. `Ticket` is one member of it.
TICKET_CLASS = "Ticket"

#: Where the nullable receiver set comes from: every module under this directory,
#: not one file inside it. Read once, in `package_scan`, inside the loop that
#: already walks the package. Scoping the derivation to `tables.py` alone was the
#: same defect one level out — `DockerLease` (`docker_tables.py`), `AgentSlot`
#: (`queue_tables.py`) and `EventView` (`schemas.py`) each declare a nullable
#: `workspace_id` and were invisible to it.
MODEL_PACKAGE = Path("models/domain")

#: The modules under `MODEL_PACKAGE` the scan must actually read. A derivation
#: that reached one module would read exactly as green as one that reached all of
#: them, so the floor is a pin rather than a comment. 54 were there when written.
MIN_MODEL_MODULES_SCANNED = 20

#: The detector scans the whole package; a scan that found nothing because it
#: looked nowhere is the failure mode this floor exists to catch. 648 `.py` files
#: were under the package when this was written.
MIN_FILES_SCANNED = 500

#: The lookups keyed on a row whose `workspace_id` is **not** nullable: re-measured
#: at HEAD `ed2fd03d` as 27 of 27 resolved lookups, 0 of them keyed on a nullable
#: receiver (0 `Ticket`, 0 `DomainEvent`). A non-nullable receiver cannot carry this
#: defect, so this number does not move when the ban widens — which makes it the one
#: pin that says whether the walk's *reach* changed.
#:
#: It was 26 at `4ec80c15` and `8c97ab2b`. The 27th is
#: `services/reference_survey/queue.py:397`, a `SurveySource` receiver
#: (`workspace_id: str`, `models/domain/survey_tables.py:82` — not nullable, so it
#: cannot carry this defect), added by merge `ed2fd03d`. That is the package
#: growing a site, not the walk's reach moving: at both HEADs the walk resolved
#: every lookup it found, and the narrow and widened filters agreed exactly.
#:
#: 32 on `integration/lg-milestone-that-491` at `73a70f6a` (2026-11-15), when this
#: branch was rebuilt onto it: 32 of 32 resolved lookups, 0 nullable-keyed, the
#: walk's own count unchanged by the rebuild. Main added six sites, every one on a
#: non-nullable row, and removed one: `chat_feature_loop` (`BaxterChatSession`),
#: `worktree_receipt` (`Worktree`), `merge_queue` and `merge_queue_reports`
#: (`MergeQueueEntry`), `reference_survey/areas` (`SurveyArea`) and
#: `reference_survey/research_library` (`SurveyResearchQuestion`) arrived;
#: `reference_survey/feeder`'s `SurveySource` lookup left with the code that held it.
#: Package growth again, not reach: the list differs from `ed2fd03d`'s by exactly
#: those seven files.
#: Move this number only with that distinction measured and written down.
NON_NULLABLE_RECEIVER_LOOKUPS = 32

#: Receiver prefixes the walk must see through, because real sites use them.
#: Spelled as a comment rather than a list: `self.ticket`, `orch.ticket` and
#: `builtin.ticket` resolve through their owner's class attributes like any
#: other attribute receiver.

# --- type resolution --------------------------------------------------------
#
# A deliberately small, deliberately honest inference pass. It knows the half
# dozen ways this package binds a model row to a name and resolves nothing else;
# everything it does not know becomes an unresolved site and fails the suite,
# rather than being waved through as "probably not a Ticket".


class Ty(NamedTuple):
    """A resolved type: a class name, and whether the name holds many of them."""

    name: str
    iterable: bool


#: Subscripts that wrap an element type rather than naming one.
_ITERABLE_WRAPPERS = frozenset(
    {"list", "List", "Sequence", "Iterable", "Iterator", "Collection", "set", "Set", "frozenset"}
)
_OPTIONAL_WRAPPERS = frozenset({"Optional"})

#: Annotations that name no row class, so they answer nothing about a receiver.
#: Refused rather than resolved: `ticket: Any` would otherwise read as "resolved,
#: and not a `Ticket`", which silences this whole census with one annotation —
#: the quietest way there is to add site 65.
_OPAQUE_ANNOTATIONS = frozenset(
    {"Any", "object", "Never", "NoReturn", "None", "Self", "AnyStr", "Hashable"}
)

#: Query-builder methods that pass the element type through unchanged.
_CHAINED = frozenset(
    {"where", "order_by", "limit", "offset", "options", "join", "distinct", "group_by", "having"}
)
#: Methods that hand back many rows.
_MANY = frozenset({"all", "unique", "scalars", "fetchall"})
#: Methods that hand back one row.
_ONE = frozenset(
    {"first", "one", "one_or_none", "scalar", "scalar_one", "scalar_one_or_none", "fetchone"}
)


def _name_of(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _is_class_name(name: str) -> bool:
    """Does `name` read as a class rather than a value?

    `session.get(Ticket, pk)` and `cache.get(key)` are the same expression shape,
    so the first argument's spelling is all there is to tell a model lookup from
    a mapping read. CamelCase it is: `key` and `CACHE_KEY` are refused, and a
    refusal here becomes an unresolved receiver, which fails the suite.
    """
    stem = name.rsplit(".", 1)[-1]
    return bool(stem) and stem[0].isupper() and any(character.islower() for character in stem)


def _string_annotation_type(node: ast.Constant) -> Ty | None:
    """A quoted annotation (`"Ticket"`), parsed and resolved like a bare one."""
    if not isinstance(node.value, str):
        return None
    try:
        parsed = ast.parse(node.value, mode="eval")
    except SyntaxError:
        return None
    return _annotation_type(parsed.body)


def _subscript_annotation_type(node: ast.Subscript) -> Ty | None:
    """`Optional[Ticket]` and `list[Ticket]`; anything else names no class."""
    base = _name_of(node.value)
    inner = node.slice
    if isinstance(inner, ast.Tuple) and inner.elts:
        inner = inner.elts[0]
    if base in _OPTIONAL_WRAPPERS:
        return _annotation_type(inner)
    if base not in _ITERABLE_WRAPPERS:
        return None
    element = _annotation_type(inner)
    return Ty(element.name, True) if element else None


def _annotation_type(node: ast.AST | None) -> Ty | None:
    """The class an annotation names, with `| None` and `list[…]` unwrapped."""
    if isinstance(node, ast.Constant):
        return _string_annotation_type(node)
    if isinstance(node, (ast.Name, ast.Attribute)):
        name = _name_of(node)
        if not name or name in _OPAQUE_ANNOTATIONS:
            return None
        return Ty(name, False)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _annotation_type(node.left) or _annotation_type(node.right)
    if isinstance(node, ast.Subscript):
        return _subscript_annotation_type(node)
    return None


def _declares_nullable(annotation: ast.AST | None) -> bool:
    """Does this annotation admit `None`? `str | None` and `Optional[str]` do.

    Deliberately **not** `_annotation_type`. That function answers *which class*
    an annotation names, and for a `BinOp`/`BitOr` it returns
    `_annotation_type(node.left) or _annotation_type(node.right)` — it unwraps
    and discards the `| None`, answering `Ty('str')` for `str | None`, and the
    same for `Optional[str]`. It is structurally incapable of answering this
    question: routed through it, every annotated `workspace_id` column reads as
    nullable and the ban widens to all 18 of them at once.
    """
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return any(
            isinstance(operand, ast.Constant) and operand.value is None
            for operand in (annotation.left, annotation.right)
        )
    if isinstance(annotation, ast.Subscript):
        return _name_of(annotation.value) in _OPTIONAL_WRAPPERS
    return False


def nullable_workspace_id_classes(source: str) -> frozenset[str]:
    """The classes declaring a nullable `workspace_id`, read off one module's source.

    The one home of the ban's scope: `package_scan` calls this on **every** module
    under `MODEL_PACKAGE` and unions the results, and both the census test and the
    ban read that. One edit to any `models/domain/` module therefore widens the ban
    and makes the census test say so, with no second walk and no per-class allowlist
    to keep in step.

    Takes source **text** rather than a `Path`, which is what lets the derivation be
    exercised on a synthetic declaration — the only way to prove a further nullable
    class is picked up without editing a real model module — and what makes it
    accumulable over a directory without knowing any filename.
    """
    tree = ast.parse(source)
    nullable: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for child in node.body:
            if not isinstance(child, ast.AnnAssign) or not isinstance(child.target, ast.Name):
                continue
            if child.target.id != "workspace_id":
                continue
            if _declares_nullable(child.annotation):
                nullable.add(node.name)
    return frozenset(nullable)


def nullable_receiver_names(index: TypeIndex, roots: frozenset[str]) -> frozenset[str]:
    """Every spelling of a nullable receiver: each root, its subclasses, its aliases.

    The ban filters on this and not on the roots themselves, because a subclass
    inherits the nullable column and a module-level alias renames it, and both
    are the same defect under another spelling. Lifted out of `package_scan`'s
    wiring so the expansion can be exercised on a synthetic index: at this HEAD
    `names_for` is the identity on both real roots (`{'Ticket'}`, `{'DomainEvent'}`),
    so no assertion over the real package can tell this recipe from `roots`.
    """
    if not roots:
        return frozenset()
    return frozenset().union(*(index.names_for(root) for root in roots))


class TypeIndex:
    """What the package declares about its own return types and attributes.

    Keyed by simple name, not by import path. Two declarations of one name that
    disagree collapse to "ambiguous", which resolves to nothing and so fails
    closed — it never silently picks one.
    """

    def __init__(self) -> None:
        self._returns: dict[str, Ty | None] = {}
        self._ambiguous_returns: set[str] = set()
        self._attributes: dict[str, dict[str, Ty]] = {}
        self._methods: dict[str, dict[str, Ty]] = {}
        #: Every class the package declares, as `name -> its base names`. A
        #: resolution to a name that is not in here is a guess, not an answer.
        self._bases: dict[str, list[str]] = {}
        #: Module-level `TicketRow = Ticket`, as `alias -> target`. Without this
        #: one assignment renames `Ticket` out of the census's reach.
        self._aliases: dict[str, str] = {}

    def add_module(self, tree: ast.Module) -> None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._add_return(node)
            elif isinstance(node, ast.ClassDef):
                self._add_class(node)
        self._add_aliases(tree)

    def _add_aliases(self, tree: ast.Module) -> None:
        """Module-level rebindings of one bare name to another, and import-as."""
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name):
                for target in node.targets:
                    if isinstance(target, ast.Name) and _is_class_name(node.value.id):
                        self._aliases[target.id] = node.value.id
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.asname and _is_class_name(alias.name):
                        self._aliases[alias.asname] = alias.name.rsplit(".", 1)[-1]

    def _add_return(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        resolved = _annotation_type(node.returns)
        if resolved is None:
            return
        previous = self._returns.get(node.name)
        if previous is not None and previous != resolved:
            self._ambiguous_returns.add(node.name)
            return
        self._returns[node.name] = resolved

    def _add_class(self, node: ast.ClassDef) -> None:
        self._methods.setdefault(node.name, {}).update(_declared_method_returns(node))
        self._attributes.setdefault(node.name, {}).update(_declared_attributes(node))
        self._bases.setdefault(node.name, []).extend(
            name for name in (_name_of(base) for base in node.bases) if name
        )

    def declares(self, name: str) -> bool:
        """Does the package declare a class by this simple name?"""
        return name in self._bases

    def declared_classes(self) -> frozenset[str]:
        return frozenset(self._bases)

    def names_for(self, root: str) -> frozenset[str]:
        """`root`, every class the package derives from it, and every alias of either.

        A subclass of `Ticket` inherits its nullable `workspace_id`, and a
        module-level `TicketRow = Ticket` renames it; both are the same defect
        under another spelling, and an exact match on `"Ticket"` misses both.
        """
        names = {root}
        while True:
            grown = {
                name
                for name, bases in self._bases.items()
                if names.intersection(bases) and name not in names
            }
            grown |= {
                alias
                for alias, target in self._aliases.items()
                if target in names and alias not in names
            }
            if not grown:
                return frozenset(names)
            names |= grown

    def return_type(self, name: str) -> Ty | None:
        if name in self._ambiguous_returns:
            return None
        return self._returns.get(name)

    def attribute_type(self, class_name: str, attribute: str) -> Ty | None:
        return self._attributes.get(class_name, {}).get(attribute)

    def method_return_type(self, class_name: str, method: str) -> Ty | None:
        """A method's declared return type, looked up on its own class.

        Preferred over `return_type` whenever the receiver's class is known: two
        unrelated `get_ticket` declarations (one returning `Ticket`, one a
        `TicketDetail` response model) make the package-wide name ambiguous, and
        the receiver's own class is the answer that is not a guess.
        """
        return self._methods.get(class_name, {}).get(method)


def _declared_method_returns(node: ast.ClassDef) -> dict[str, Ty]:
    """Every method of `node` whose return annotation names a class."""
    returns: dict[str, Ty] = {}
    for child in node.body:
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        resolved = _annotation_type(child.returns)
        if resolved is not None:
            returns[child.name] = resolved
    return returns


def _declared_attributes(node: ast.ClassDef) -> dict[str, Ty]:
    """`node`'s annotated attributes: `x: T` at class level, `self.x: T` in a method.

    A local `rows: list[Ticket] = []` inside a method is not an attribute, so the
    class body and the method bodies are read separately rather than by one walk.
    """
    attributes: dict[str, Ty] = {}
    for child in node.body:
        if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
            resolved = _annotation_type(child.annotation)
            if resolved is not None:
                attributes[child.target.id] = resolved
    for child in node.body:
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(child):
            named = _self_attribute_target(sub)
            if named is None:
                continue
            resolved = _annotation_type(named[1])
            if resolved is not None:
                attributes[named[0]] = resolved
    return attributes


def _self_attribute_target(node: ast.AST) -> tuple[str, ast.AST] | None:
    """`self.x: T = …` inside a method, as `(x, T)`."""
    if not isinstance(node, ast.AnnAssign):
        return None
    target = node.target
    if not isinstance(target, ast.Attribute):
        return None
    if not (isinstance(target.value, ast.Name) and target.value.id == "self"):
        return None
    return target.attr, node.annotation


def _selected_classes(expr: ast.AST) -> list[str]:
    """The classes a `select(...)` inside `expr` names, in order."""
    for node in ast.walk(expr):
        if isinstance(node, ast.Call) and _name_of(node.func) == "select":
            return [_name_of(argument) for argument in node.args]
    return []


def _element(resolved: Ty | None) -> Ty | None:
    return Ty(resolved.name, False) if resolved else None


class Scope:
    """One function body's name bindings, resolved as far as this walk can."""

    def __init__(self, index: TypeIndex, class_name: str = "") -> None:
        self.index = index
        self.class_name = class_name
        self.names: dict[str, Ty] = {}
        #: Names bound to a multi-column select's rows, as the classes selected.
        #: `rows = session.exec(select(Ticket, Worktree)).all()` is not a list of
        #: tickets, and only the unpacking below says which element is which.
        self.rows: dict[str, list[str]] = {}

    # -- building ---------------------------------------------------------

    def bind_parameters(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        args = node.args
        for argument in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            resolved = _annotation_type(argument.annotation)
            if resolved is not None:
                self.names[argument.arg] = resolved
        if self.class_name and args.args and args.args[0].arg == "self":
            self.names.setdefault("self", Ty(self.class_name, False))

    def bind_body(self, nodes: list[ast.AST]) -> None:
        """Bind every assignment, loop target and `with … as` in the scope.

        Flow-insensitive on purpose: a rebinding to a different class in a later
        branch makes the name ambiguous and therefore unresolved, which fails
        closed. Two passes, so a binding that reads a name bound further down
        still resolves.
        """
        for _ in range(2):
            for node in nodes:
                for name, resolved in self._bindings(node):
                    existing = self.names.get(name)
                    if existing is not None and existing != resolved:
                        self.names.pop(name, None)
                        continue
                    self.names[name] = resolved

    def _bindings(self, node: ast.AST) -> list[tuple[str, Ty]]:
        if isinstance(node, ast.AnnAssign):
            declared = _annotation_type(node.annotation) or self.type_of(node.value)
            return self._one_binding(node.target, declared)
        if isinstance(node, ast.NamedExpr):
            return self._one_binding(node.target, self.type_of(node.value))
        if isinstance(node, ast.Assign):
            return self._assign_bindings(node)
        if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            return self._loop_bindings(node)
        if isinstance(node, ast.withitem):
            return self._one_binding(node.optional_vars, self.type_of(node.context_expr))
        return []

    @staticmethod
    def _one_binding(target: ast.AST | None, resolved: Ty | None) -> list[tuple[str, Ty]]:
        if not isinstance(target, ast.Name) or resolved is None:
            return []
        return [(target.id, resolved)]

    def _assign_bindings(self, node: ast.Assign) -> list[tuple[str, Ty]]:
        selected = _selected_classes(node.value)
        if len(selected) > 1 and all(selected):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.rows[target.id] = selected
        resolved = self.type_of(node.value)
        if resolved is None:
            return []
        return [(target.id, resolved) for target in node.targets if isinstance(target, ast.Name)]

    def _loop_bindings(
        self, node: ast.For | ast.AsyncFor | ast.comprehension
    ) -> list[tuple[str, Ty]]:
        if isinstance(node.target, ast.Tuple):
            return self._row_bindings(node.target, node.iter)
        return self._one_binding(node.target, _element(self.type_of(node.iter)))

    def _row_bindings(self, target: ast.Tuple, iterator: ast.AST) -> list[tuple[str, Ty]]:
        """`for ticket, worktree in rows:` over a multi-column select.

        Only an exact arity match binds anything; a row this walk cannot line up
        with its classes resolves to nothing and so fails closed.
        """
        selected = _selected_classes(iterator)
        if not selected and isinstance(iterator, ast.Name):
            selected = self.rows.get(iterator.id, [])
        if len(selected) != len(target.elts) or not all(selected):
            return []
        return [
            (element.id, Ty(class_name, False))
            for element, class_name in zip(target.elts, selected)
            if isinstance(element, ast.Name)
        ]

    # -- resolving --------------------------------------------------------

    def type_of(self, node: ast.AST | None) -> Ty | None:
        if isinstance(node, ast.Name):
            return self.names.get(node.id)
        if isinstance(node, ast.Attribute):
            return self._attribute_type(node)
        if isinstance(node, ast.Call):
            return self._call_type(node)
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            return self._comprehension_type(node)
        return self._passthrough_type(node)

    def _passthrough_type(self, node: ast.AST | None) -> Ty | None:
        """Expressions that carry some operand's type rather than naming one."""
        if isinstance(node, ast.IfExp):
            return self.type_of(node.body) or self.type_of(node.orelse)
        if isinstance(node, ast.BoolOp):
            for value in node.values:
                resolved = self.type_of(value)
                if resolved is not None:
                    return resolved
            return None
        if isinstance(node, ast.Subscript):
            return _element(self.type_of(node.value))
        if isinstance(node, ast.Await):
            return self.type_of(node.value)
        return None

    def _comprehension_type(self, node: ast.ListComp | ast.SetComp | ast.GeneratorExp) -> Ty | None:
        inner = Scope(self.index, self.class_name)
        inner.names.update(self.names)
        inner.bind_body(list(node.generators))
        resolved = inner.type_of(node.elt)
        return Ty(resolved.name, True) if resolved else None

    def _attribute_type(self, node: ast.Attribute) -> Ty | None:
        """`self.ticket`, `orch.ticket`, `builtin.ticket` — via the owner's class."""
        owner = self.type_of(node.value)
        if owner is None:
            return None
        return self.index.attribute_type(owner.name, node.attr)

    def _call_type(self, node: ast.Call) -> Ty | None:
        callee = _name_of(node.func)
        # `session.get(Ticket, pk)` — the first argument names the class. A
        # mapping read (`cache.get(key)`) is the same shape, and resolving it
        # would hand a receiver a class name like `key`: not a `Ticket`, so
        # silently cleared, and resolved, so invisible to the fail-closed test.
        if callee == "get" and node.args and _is_class_name(_name_of(node.args[0])):
            return Ty(_name_of(node.args[0]), False)
        # A single-column `select(Ticket)` is an iterable of that class; a
        # multi-column one is an iterable of rows, and only `_row_bindings`
        # can unpack it, so it names no class here.
        if callee == "select":
            if len(node.args) != 1 or not isinstance(node.args[0], ast.Name):
                return None
            return Ty(node.args[0].id, True)
        if isinstance(node.func, ast.Attribute):
            return self._method_call_type(node, node.func, callee)
        return self.index.return_type(callee)

    def _method_call_type(self, node: ast.Call, func: ast.Attribute, callee: str) -> Ty | None:
        """A call on a receiver: the query-builder chain first, then declarations."""
        if callee == "exec" and node.args:
            return self.type_of(node.args[0])
        if callee in _CHAINED:
            return self.type_of(func.value)
        if callee in _MANY:
            resolved = self.type_of(func.value)
            return Ty(resolved.name, True) if resolved else None
        if callee in _ONE:
            return _element(self.type_of(func.value))
        owner = self.type_of(func.value)
        on_class = self.index.method_return_type(owner.name, callee) if owner else None
        return on_class if on_class is not None else self.index.return_type(callee)


# --- the detector -----------------------------------------------------------


class Lookup(NamedTuple):
    """One `session.get(Workspace, <receiver>.workspace_id)` call in the tree."""

    site: str
    receiver: str
    resolved: str


def _receiver_spelling(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _receiver_spelling(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Call):
        return f"{_receiver_spelling(node.func)}(…)"
    return ast.dump(node)[:40]


#: `Session.get(entity, ident, …)`. A keyword call is legal Python and legal
#: SQLAlchemy, and a positional-only matcher reports it as no lookup at all.
_ENTITY_KEYWORDS = ("entity",)
_IDENT_KEYWORDS = ("ident",)


def _argument(node: ast.Call, position: int, keywords: tuple[str, ...]) -> ast.AST | None:
    """The call's `position`-th positional argument, or its keyword spelling."""
    if len(node.args) > position:
        return node.args[position]
    for keyword in node.keywords:
        if keyword.arg in keywords:
            return keyword.value
    return None


def _workspace_id_receiver(node: ast.AST | None) -> ast.Attribute | None:
    """`X.workspace_id`, however the key is dressed up.

    `X.workspace_id or None` and `getattr(X, "workspace_id")` are the same read
    of the same nullable column; a matcher that only knows the bare attribute
    reports them as no lookup, which is a silent miss rather than a pass.
    """
    if isinstance(node, ast.Attribute) and node.attr == "workspace_id":
        return node
    if isinstance(node, ast.BoolOp):
        for value in node.values:
            found = _workspace_id_receiver(value)
            if found is not None:
                return found
        return None
    if isinstance(node, ast.IfExp):
        return _workspace_id_receiver(node.body) or _workspace_id_receiver(node.orelse)
    if (
        isinstance(node, ast.Call)
        and _name_of(node.func) == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == "workspace_id"
        and isinstance(node.args[0], (ast.Name, ast.Attribute))
    ):
        # Synthesised so the caller sees one shape; `lineno` is the call's.
        return ast.copy_location(
            ast.Attribute(value=node.args[0], attr="workspace_id", ctx=ast.Load()), node
        )
    return None


def _is_workspace_lookup(
    node: ast.AST, workspace_names: frozenset[str] = frozenset({"Workspace"})
) -> ast.Attribute | None:
    """The receiver of `<anything>.get(Workspace, <receiver>.workspace_id)`.

    `workspace_names` holds every local spelling of `Workspace` in the module
    under scan, because `from … import Workspace as Ws` is one line and would
    otherwise take a site out of the census's sight entirely.
    """
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if node.func.attr != "get":
        return None
    model = _argument(node, 0, _ENTITY_KEYWORDS)
    if model is None or _name_of(model) not in workspace_names:
        return None
    return _workspace_id_receiver(_argument(node, 1, _IDENT_KEYWORDS))


def _workspace_spellings(tree: ast.Module) -> frozenset[str]:
    """Every name bound to `Workspace` in this module, including `import … as`."""
    names = {"Workspace"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(
                alias.asname
                for alias in node.names
                if alias.asname and alias.name.rsplit(".", 1)[-1] == "Workspace"
            )
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Name):
            if node.value.id in names:
                names.update(target.id for target in node.targets if isinstance(target, ast.Name))
    return frozenset(names)


def _scope_nodes(scope: ast.AST) -> list[ast.AST]:
    """Every node in `scope` that is not inside a nested function scope."""
    nodes: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        nodes.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return nodes


def workspace_lookups(source: str, label: str, index: TypeIndex | None = None) -> list[Lookup]:
    """Every workspace-by-`workspace_id` lookup in `source`, with its receiver's type.

    `resolved` is the receiver's class name, or `""` when this walk could not
    resolve it. `""` is a failure, not a pass — see the fail-closed test.
    """
    tree = ast.parse(source)
    resolved_index = index if index is not None else _index_for(tree)
    workspace_names = _workspace_spellings(tree)
    lookups: list[Lookup] = []
    for scope, class_name in _scopes(tree):
        bindings = Scope(resolved_index, class_name)
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bindings.bind_parameters(scope)
        nodes = _scope_nodes(scope)
        bindings.bind_body(nodes)
        for node in nodes:
            key = _is_workspace_lookup(node, workspace_names)
            if key is None:
                continue
            receiver = bindings.type_of(key.value)
            lookups.append(
                Lookup(
                    site=f"{label}:{node.lineno}",
                    receiver=_receiver_spelling(key.value),
                    resolved=receiver.name if receiver else "",
                )
            )
    return sorted(set(lookups))


def _index_for(tree: ast.Module) -> TypeIndex:
    index = TypeIndex()
    index.add_module(tree)
    return index


def _scopes(tree: ast.Module) -> list[tuple[ast.AST, str]]:
    """Every scope that can hold a lookup, paired with its enclosing class name."""
    scopes: list[tuple[ast.AST, str]] = [(tree, "")]
    stack: list[tuple[ast.AST, str]] = [(tree, "")]
    while stack:
        node, class_name = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                stack.append((child, child.name))
                scopes.append((child, child.name))
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                stack.append((child, class_name))
                scopes.append((child, class_name))
            else:
                stack.append((child, class_name))
    return scopes


def _package_root() -> Path:
    return Path(binding_module.__file__).resolve().parent.parent


def _package_sources() -> list[tuple[Path, str]]:
    root = _package_root()
    return [
        (path.relative_to(root), path.read_text(encoding="utf-8"))
        for path in sorted(root.rglob("*.py"))
    ]


class PackageScan(NamedTuple):
    """The whole-package census: its sites, its denominator, and its index."""

    lookups: list[Lookup]
    scanned: int
    index: TypeIndex
    #: The classes the modules under `MODEL_PACKAGE` declare a nullable
    #: `workspace_id` on, unioned over all of them.
    nullable_roots: frozenset[str]
    #: How many of those modules were read. A derivation that reached one module
    #: derives a narrower set and reads exactly as green, so the count is pinned.
    model_modules: int
    #: Those roots plus every subclass and module-level alias of each, which is
    #: what the ban filters on: a subclass inherits the nullable column and an
    #: alias renames it, and both are the same defect under another spelling.
    nullable_names: frozenset[str]
    #: The same derivation run over every package module *outside*
    #: `MODEL_PACKAGE`. Not part of the ban's scope — that boundary is the
    #: directory, deliberately — but the material for the one assertion that
    #: says whether the boundary still holds. Accumulated in the same loop, so
    #: the package is still read off disk exactly once.
    outside_nullable_roots: frozenset[str] = frozenset()


@pytest.fixture(scope="module")
def package_scan() -> PackageScan:
    """Every workspace-by-`workspace_id` lookup outside the accessor module.

    Built in a fixture, not a test body, so a scan that cannot run is an ERROR
    naming its own failure rather than a zero that reads as a clean tree. The
    nullable receiver set is derived here too, inside the same loop and for the
    same reason, so the package — every `MODEL_PACKAGE` module included — is read
    off disk exactly once and no second walk is added.
    """
    sources = _package_sources()
    index = TypeIndex()
    for _, source in sources:
        index.add_module(ast.parse(source))
    lookups: list[Lookup] = []
    scanned = 0
    model_modules = 0
    roots: set[str] = set()
    outside_roots: set[str] = set()
    for relative, source in sources:
        if MODEL_PACKAGE in relative.parents:
            model_modules += 1
            roots |= nullable_workspace_id_classes(source)
        else:
            outside_roots |= nullable_workspace_id_classes(source)
        if relative == ACCESSOR_MODULE:
            continue
        scanned += 1
        lookups.extend(workspace_lookups(source, str(relative), index))
    if model_modules < MIN_MODEL_MODULES_SCANNED:
        raise AssertionError(
            f"the package scan reached {model_modules} modules under {MODEL_PACKAGE}, "
            f"fewer than the {MIN_MODEL_MODULES_SCANNED} floor, so the nullable receiver "
            "set is derived off part of the package and this census would ban too little "
            "while reading green. The package moved, or `_package_root()` no longer "
            "resolves to it."
        )
    nullable_roots = frozenset(roots)
    nullable_names = nullable_receiver_names(index, nullable_roots)
    return PackageScan(
        sorted(lookups),
        scanned,
        index,
        nullable_roots,
        model_modules,
        nullable_names,
        frozenset(outside_roots),
    )


# --- the positive control ---------------------------------------------------

#: A `Ticket` receiver, bare. The 44 commonest sites.
_BARE = """
def f(session, ticket: Ticket) -> Workspace | None:
    return session.get(Workspace, ticket.workspace_id)
"""
#: The same site carrying the correct guard. Still routed through the accessor,
#: because the allowlist holds the accessor module alone.
_GUARDED = """
def f(session, ticket: Ticket) -> Workspace | None:
    return session.get(Workspace, ticket.workspace_id) if ticket.workspace_id is not None else None
"""
#: The truthiness spelling: `workspace_id == ""` is falsy but a legal key.
_TRUTHINESS = """
def f(session, ticket: Ticket) -> Workspace | None:
    return session.get(Workspace, ticket.workspace_id) if ticket.workspace_id else None
"""
#: A non-`Ticket` receiver. `GithubSyncSettings.workspace_id` is not nullable, so
#: this is not the defect and must NOT be flagged — and must still resolve.
_NON_TICKET = """
def f(session, row: GithubSyncSettings) -> Workspace | None:
    return session.get(Workspace, row.workspace_id)
"""
#: A `self.session` prefix on the session, and a receiver named by its binding.
_SELF_SESSION = """
class Service:
    def __init__(self, session: Session) -> None:
        self.session: Session = session

    def run(self, ticket_id: str) -> Workspace | None:
        row = self.session.get(Ticket, ticket_id)
        return self.session.get(Workspace, row.workspace_id)
"""
#: What ruff-format does to a guarded call at a deep indent. A grep for the
#: one-line spelling reports this as unguarded; the walk does not care.
_WRAPPED = """
def f(session, ticket: Ticket) -> Workspace | None:
    if True:
        if True:
            workspace = (
                session.get(Workspace, ticket.workspace_id)
                if ticket.workspace_id is not None
                else None
            )
            return workspace
    return None
"""
#: A receiver this walk cannot type. It must surface as unresolved, not as clean.
_UNRESOLVABLE = """
def f(session, mystery) -> Workspace | None:
    return session.get(Workspace, mystery.workspace_id)
"""
#: A bare-parameter lookup. Out of scope: there is no `Ticket` row in the
#: expression at all, so the walk must not claim it.
_BARE_PARAMETER = """
def f(session, workspace_id: str | None) -> Workspace | None:
    return session.get(Workspace, workspace_id)
"""


def test_the_detector_sees_the_shapes_it_bans():
    """A zero may be the detector. These eight sources prove it is not."""
    cases = {
        "bare": (_BARE, [TICKET_CLASS]),
        "is-not-None guard": (_GUARDED, [TICKET_CLASS]),
        "truthiness": (_TRUTHINESS, [TICKET_CLASS]),
        "non-Ticket receiver": (_NON_TICKET, ["GithubSyncSettings"]),
        "self.session prefix": (_SELF_SESSION, [TICKET_CLASS]),
        "formatter-wrapped": (_WRAPPED, [TICKET_CLASS]),
        "unresolvable receiver": (_UNRESOLVABLE, [""]),
        "bare parameter": (_BARE_PARAMETER, []),
    }
    for label, (source, expected) in cases.items():
        found = workspace_lookups(source, label)
        assert [lookup.resolved for lookup in found] == expected, (
            f"{label}: expected receivers {expected}, got {found}"
        )


def test_the_detector_does_not_key_on_the_name_ticket():
    """8 of the 64 sites name their receiver something else. Renaming must not help."""
    renamed = _BARE.replace("ticket", "dep")
    found = workspace_lookups(renamed, "renamed")
    assert [lookup.resolved for lookup in found] == [TICKET_CLASS], (
        f"the walk resolved the receiver by its name, not its type: {found}"
    )
    assert workspace_lookups(
        "def f(session, ticket: GithubSyncSettings) -> None:\n"
        "    session.get(Workspace, ticket.workspace_id)\n",
        "misnamed",
    ) == [Lookup("misnamed:2", "ticket", "GithubSyncSettings")], (
        "a non-Ticket row named `ticket` was flagged by its name"
    )


def test_the_detector_scans_the_whole_package(package_scan):
    scanned = package_scan.scanned
    assert scanned > MIN_FILES_SCANNED, (
        f"only {scanned} files scanned: the count below would be the detector, not the code"
    )


def test_the_model_package_declares_exactly_the_known_nullable_workspace_id_classes(
    package_scan,
):
    """The census, and the *input* to the ban rather than a claim beside it.

    It reads the same `nullable_workspace_id_classes` result the ban filters on,
    unioned over every module under `MODEL_PACKAGE`, so a further nullable column
    anywhere in that directory cannot make this test say so while the ban keeps
    covering a narrower set — the two cannot disagree, because there is only one
    derivation.

    `EventView` is in the set deliberately. It is a read DTO rather than a
    `table=True` row class, but `session.get(Workspace, view.workspace_id)` on it
    is the identical NULL-primary-key load, so keeping it fails closed wider at no
    cost and spares the derivation a second predicate.
    """
    assert package_scan.model_modules >= MIN_MODEL_MODULES_SCANNED, (
        f"only {package_scan.model_modules} modules under {MODEL_PACKAGE} were read, "
        "so the set below is derived off part of the package"
    )
    assert package_scan.nullable_roots == {
        "Ticket",
        "DomainEvent",
        "DockerLease",
        "AgentSlot",
        "EventView",
    }, (
        f"the nullable `workspace_id` columns are now {sorted(package_scan.nullable_roots)}, "
        "measured over every module under "
        f"{MODEL_PACKAGE} (at `07bee000e1`: `Ticket` tables.py:166, `DomainEvent` "
        "tables.py:898, `DockerLease` docker_tables.py:135, `AgentSlot` "
        "queue_tables.py:43, `EventView` schemas.py:467). "
        "This set is the ban's scope, so a change here widens or narrows what "
        "`test_no_module_outside_the_accessor_looks_up_a_workspace_by_a_nullable_"
        "workspace_id` covers. A newly nullable receiver needs its own answer — an "
        f"accessor like `{ACCESSOR_NAME}`, or the inline `is not None` guard — not "
        f"`{TICKET_CLASS}`'s.\n"
        f"Boundary: the derivation reads {MODEL_PACKAGE} and nothing outside it. A "
        "nullable `workspace_id` declared elsewhere in the package is out of reach of "
        "this census. When that boundary was drawn the only two were the service-layer "
        "value objects `CapacityRequest` (services/capacity_run.py) and `TrendFilter` "
        "(services/token_usage_trend.py), neither a row type and neither keying a "
        "lookup; a row type declared outside this directory would need the boundary "
        "widened, not a name added to a list.\n"
        "Honest limit: this is read from the *annotation*. A column made nullable only "
        "through `sa_column=Column(..., nullable=True)` is out of reach of any AST read "
        "of the annotation and would be missed. 0 `workspace_id` columns under "
        f"{MODEL_PACKAGE} used `sa_column` at all when this was measured (16 other "
        "columns there did, via `str_enum_column(..., nullable=True)`, none of them a "
        "`workspace_id`), and the directory held 0 `Optional[`, so the annotation read "
        "was complete; if that changes, this derivation needs a second source, not a "
        "wider predicate."
    )


def test_the_derived_scope_is_wired_into_the_scan_the_ban_actually_reads(package_scan):
    """The ban reads `nullable_names`, and only the fixture decides what that holds.

    Every other assertion about the expansion is taken over a *synthetic*
    `PackageScan` built by `_scan_over`, which calls `nullable_receiver_names`
    itself. So they pin the recipe and not the wiring: replacing the fixture's
    line with `nullable_names = frozenset()` disables the ban over the real
    package — 0 nullable-keyed sites becomes 0 out of a scope of nothing — and
    the whole module still passes, because there are no nullable-keyed sites for
    the difference to show up on and the complement pin counts the same 32
    lookups either way. Measured: that one-line mutant left 29 passed, 0 failed.

    This is the assertion that makes the ban's scope non-blank over the real
    package, so lg-milestone-that-1420 is not revertible in the fixture.
    """
    assert package_scan.nullable_roots, (
        "the scan derived no nullable receiver classes at all, so the ban's scope is "
        "empty and it cannot fire on anything. The derivation stopped reading "
        f"{MODEL_PACKAGE}."
    )
    assert package_scan.nullable_names >= package_scan.nullable_roots, (
        f"the ban filters on {sorted(package_scan.nullable_names)}, which does not even "
        f"contain the derived roots {sorted(package_scan.nullable_roots)}. The fixture "
        "is not handing the ban the set this module derives, so every test about the "
        "derivation is about a value the ban never reads."
    )
    assert package_scan.nullable_names == nullable_receiver_names(
        package_scan.index, package_scan.nullable_roots
    ), (
        f"the scan's `nullable_names` is {sorted(package_scan.nullable_names)}, not the "
        "`names_for` expansion of its own roots. The ban's scope is then whatever the "
        "fixture happened to build rather than what the derivation says, and with 0 "
        "nullable-keyed sites in the package nothing else in this module can tell."
    )


def test_no_lookup_is_keyed_on_a_nullable_receiver_declared_outside_the_model_package(
    package_scan,
):
    """The directory boundary, as a detector rather than a sentence in a message.

    The derivation reads `MODEL_PACKAGE` and nothing else, on purpose: a
    whole-package sweep also picks up service-layer value objects that are not
    row types and key no lookup, and the census assertion would then churn with
    unrelated service code. The cost of that boundary is that a nullable
    `workspace_id` declared *outside* the directory is banned nowhere.

    That cost was recorded as prose. It is checked here instead, without widening
    the ban: a class outside `MODEL_PACKAGE` declaring a nullable `workspace_id`
    is fine right up until a lookup is keyed on it, and that is the condition
    asserted. Fail-closed, and it does not move with code that declares such a
    class and never reads a Workspace by it.

    Measured by injecting one such class plus its lookup into
    `services/capacity_run.py`: the suite reds only on
    `test_the_non_nullable_receiver_denominator_is_unchanged`, at 33 lookups
    against the pinned 32, telling the author the package gained a lookup on a
    *non-nullable* receiver and to re-pin the constant. The receiver is nullable
    and carries the exact NULL-primary-key defect — which is this ticket's own
    diagnosis-points-the-wrong-way failure, one directory out.
    """
    stranded = (
        frozenset().union(
            *(package_scan.index.names_for(root) for root in package_scan.outside_nullable_roots)
        )
        if package_scan.outside_nullable_roots
        else frozenset()
    )
    offenders = [lookup for lookup in package_scan.lookups if lookup.resolved in stranded]
    assert not offenders, (
        f"{len(offenders)} sites read a Workspace by a nullable `workspace_id` whose "
        f"class is declared outside {MODEL_PACKAGE}, so the ban "
        "(`test_no_module_outside_the_accessor_looks_up_a_workspace_by_a_nullable_"
        "workspace_id`) does not cover them and the complement pin counts them as "
        "non-nullable receivers — which tells the author to re-pin a count when the "
        "site is the defect:\n"
        + "\n".join(
            f"  {lookup.site}  session.get(Workspace, {lookup.receiver}.workspace_id)"
            f"  — receiver resolves to `{lookup.resolved}`, whose `workspace_id` is "
            "nullable. When it is NULL the key is fully NULL and SQLAlchemy loads "
            "nothing. Guard the site with the inline `is not None` check or an "
            f"accessor beside `{ACCESSOR_NAME}`; if `{lookup.resolved}` is a row type "
            f"that belongs under {MODEL_PACKAGE}, move it there and the ban covers it."
            for lookup in offenders
        )
    )


def test_a_third_nullable_class_widens_the_derived_set_with_no_second_edit():
    """One edit to any `models/domain/` module is the whole change.

    No allowlist to keep in step, and no filename to add. Proven on a synthetic
    source rather than by editing a real model module,
    which is what `nullable_workspace_id_classes` taking source *text* buys.
    The non-nullable declaration in the same source is the control: were the
    predicate routed through `_annotation_type`, it would be picked up too.
    """
    synthetic = """
class Ticket(SQLModel, table=True):
    workspace_id: str | None = Field(default=None, foreign_key="workspaces.id")

class DomainEvent(SQLModel, table=True):
    workspace_id: str | None = Field(default=None, foreign_key="workspaces.id")

class StageFanoutGroup(SQLModel, table=True):
    workspace_id: str = Field(foreign_key="workspaces.id")

class Approval(SQLModel, table=True):
    workspace_id: Optional[str] = Field(default=None, foreign_key="workspaces.id")
"""
    derived = nullable_workspace_id_classes(synthetic)
    assert derived == {"Ticket", "DomainEvent", "Approval"}, (
        f"a third class declaring a nullable `workspace_id` derived as {sorted(derived)}. "
        "The ban's scope must follow one edit to any `models/domain/` module; if this "
        "set does not grow, "
        "the new receiver is banned nowhere and no test says so."
    )
    assert "StageFanoutGroup" not in derived, (
        "a non-nullable `workspace_id` was derived as nullable, which widens the ban to "
        "every receiver in the package. `_declares_nullable` must read whether the "
        "annotation admits `None`, not which class it names."
    )


def test_the_nullability_predicate_does_not_route_through_annotation_type():
    """`_annotation_type` discards `| None`; the ban's scope cannot be built on it."""
    assert _declares_nullable(ast.parse("str | None", mode="eval").body)
    assert _declares_nullable(ast.parse("None | str", mode="eval").body)
    assert _declares_nullable(ast.parse("Optional[str]", mode="eval").body)
    assert not _declares_nullable(ast.parse("str", mode="eval").body)
    assert not _declares_nullable(ast.parse("str | int", mode="eval").body)
    assert not _declares_nullable(ast.parse("list[str]", mode="eval").body)
    #: The reason for a dedicated predicate: `_annotation_type` answers the same
    #: class for both spellings, so it cannot tell them apart.
    assert _annotation_type(ast.parse("str | None", mode="eval").body) == _annotation_type(
        ast.parse("str", mode="eval").body
    ), "`_annotation_type` no longer discards `| None`; re-check why this predicate exists"


#: A nullable receiver that is not a `Ticket`, bound to a name that is neither
#: `event` nor `ticket` nor `domain_event`, so the resolution is provably from the
#: annotation and not from the spelling of the binding. This is the shape AC2
#: injected into a real module by hand, kept here as a permanent oracle: with the
#: ban scoped to `Ticket` alone, every assertion in this module still passed.
_DOMAIN_EVENT_KEYED = """
class DomainEvent:
    workspace_id: str | None

def f(session, row: DomainEvent):
    return session.get(Workspace, row.workspace_id)
"""

#: The control: the same shape on a receiver whose `workspace_id` is not
#: nullable. It must stay out of the ban, or the widening has swallowed the
#: whole package rather than the one property that carries the defect.
_NON_NULLABLE_KEYED = """
class GithubSyncSettings:
    workspace_id: str

def f(session, row: GithubSyncSettings):
    return session.get(Workspace, row.workspace_id)
"""


def _scan_over(source: str, label: str, nullable_roots: frozenset[str]) -> PackageScan:
    """A `PackageScan` over one synthetic module, built the way the fixture is.

    The ban is a function of a `PackageScan`, so handing it a synthetic one is
    how its *own* filter gets exercised. Nothing here reads the package off
    disk: the derivation and the expansion are the real ones, the sources are
    not.
    """
    index = _index_for(ast.parse(source))
    lookups = workspace_lookups(source, label, index)
    return PackageScan(
        sorted(lookups),
        1,
        index,
        nullable_roots,
        MIN_MODEL_MODULES_SCANNED,
        nullable_receiver_names(index, nullable_roots),
    )


def test_the_ban_fires_on_a_nullable_receiver_that_is_not_a_ticket():
    """The widening, as an assertion instead of a measurement taken once by hand.

    Every other test in this module passes with the ban scoped to `Ticket`:
    there are 0 nullable-keyed sites in the package, so the real census cannot
    tell the narrow filter from the wide one, and the complement pin is 26 under
    both. Without this test the whole of lg-milestone-that-1420 is revertible in
    one line, green.
    """
    scan = _scan_over(_DOMAIN_EVENT_KEYED, "injected.py", frozenset({"DomainEvent"}))
    assert [lookup.resolved for lookup in scan.lookups] == ["DomainEvent"], (
        f"the synthetic site did not resolve, so this oracle proves nothing: {scan.lookups}"
    )
    assert "DomainEvent" not in scan.index.names_for(TICKET_CLASS), (
        "the control is void: this receiver already reads as a `Ticket`"
    )
    with pytest.raises(AssertionError) as raised:
        test_no_module_outside_the_accessor_looks_up_a_workspace_by_a_nullable_workspace_id(scan)
    message = str(raised.value)
    assert "injected.py:6" in message, f"the offender's file:line is not named:\n{message}"
    assert "row.workspace_id" in message, f"the receiver expression is not named:\n{message}"
    assert "`DomainEvent`" in message, f"the resolved receiver class is not named:\n{message}"
    assert "NULL primary key" in message, f"the defect is not named:\n{message}"
    assert ACCESSOR_NAME in message, f"no guard is prescribed:\n{message}"
    assert "its own answer" in message, (
        f"`{ACCESSOR_NAME}(session, ticket)` was prescribed for a receiver that is not a "
        f"`{TICKET_CLASS}` and has no accessor:\n{message}"
    )
    lowered = message.lower()
    assert "re-measure" not in lowered and "resolver changed" not in lowered, (
        "the ban told the author the resolver's reach moved, which is the diagnosis "
        f"this ticket exists to stop giving them:\n{message}"
    )


def test_the_ban_is_silent_on_a_receiver_whose_workspace_id_is_not_nullable():
    """R3: a predicate that answers `True` for every column reds 26 sites at once.

    `_annotation_type` discards `| None`, so routed through it the derived set is
    every class with an annotated `workspace_id`. This is what that mistake looks
    like from the ban's side.
    """
    scan = _scan_over(_NON_NULLABLE_KEYED, "control.py", frozenset({"GithubSyncSettings"}))
    assert [lookup.resolved for lookup in scan.lookups] == ["GithubSyncSettings"], (
        f"the control site did not resolve, so it clears nothing: {scan.lookups}"
    )
    clean = _scan_over(_NON_NULLABLE_KEYED, "control.py", frozenset({"DomainEvent"}))
    test_no_module_outside_the_accessor_looks_up_a_workspace_by_a_nullable_workspace_id(clean)
    assert clean.nullable_names == frozenset({"DomainEvent"}), (
        "a receiver with a non-nullable `workspace_id` was pulled into the ban's scope"
    )


def test_a_subclass_or_alias_of_a_nullable_root_is_in_the_bans_scope():
    """`names_for` is the identity on both real roots, so this needs a synthetic one.

    A `class ArchivedEvent(DomainEvent)` inherits the nullable column and an
    `EventRow = DomainEvent` renames it. Dropping the expansion and filtering on
    the roots alone passes every other test in this module.
    """
    source = """
class DomainEvent:
    workspace_id: str | None

class ArchivedEvent(DomainEvent):
    pass

EventRow = DomainEvent

def f(session, row: ArchivedEvent):
    return session.get(Workspace, row.workspace_id)
"""
    roots = nullable_workspace_id_classes(source)
    assert roots == {"DomainEvent"}, f"the synthetic root did not derive: {sorted(roots)}"
    index = _index_for(ast.parse(source))
    names = nullable_receiver_names(index, roots)
    assert {"ArchivedEvent", "EventRow"} <= names, (
        f"the nullable receiver set is {sorted(names)}: a subclass inherits the nullable "
        "column and an alias renames it, and filtering on the roots alone bans neither"
    )
    scan = _scan_over(source, "subclassed.py", roots)
    with pytest.raises(AssertionError) as raised:
        test_no_module_outside_the_accessor_looks_up_a_workspace_by_a_nullable_workspace_id(scan)
    assert "ArchivedEvent" in str(raised.value)
    assert nullable_receiver_names(index, frozenset()) == frozenset(), (
        "an empty root set must expand to an empty name set, not raise: a census that "
        "errors on its own wiring is indistinguishable from a package with no tables"
    )


def test_the_derivation_is_not_keyed_on_the_names_ticket_and_domain_event():
    """The set is derived from the column, not from the two names found first."""
    source = """
class Zebra(SQLModel, table=True):
    workspace_id: str | None = Field(default=None)
"""
    assert nullable_workspace_id_classes(source) == {"Zebra"}, (
        "a nullable `workspace_id` on a class named neither `Ticket` nor `DomainEvent` "
        "was not derived, so the ban's scope is still keyed on the class name"
    )


def test_the_derivation_is_not_keyed_on_a_filename():
    """Two module sources in, one set out: the directory is the only boundary.

    `nullable_workspace_id_classes` takes source *text*, so it cannot tell
    `tables.py` from `docker_tables.py` even in principle — which is exactly why
    `package_scan` can accumulate it over a directory. Scoping the derivation to
    one filename was the gap this closed: `DockerLease`, `AgentSlot` and
    `EventView` declare nullable `workspace_id` columns outside `tables.py` and
    were banned nowhere while the census read green.

    Both sources are read; only the one with a nullable column contributes.
    """
    nullable_module = """
class Zebra(SQLModel, table=True):
    workspace_id: str | None = Field(default=None, foreign_key="workspaces.id")
"""
    plain_module = """
class Quagga(SQLModel, table=True):
    workspace_id: str = Field(foreign_key="workspaces.id")

class Okapi(SQLModel, table=True):
    name: str
"""
    assert nullable_workspace_id_classes(plain_module) == frozenset(), (
        "a module with no nullable `workspace_id` contributed to the derived set, "
        "which would widen the ban to receivers that cannot carry this defect"
    )
    assert nullable_workspace_id_classes(nullable_module) == {"Zebra"}, (
        "a nullable `workspace_id` in a module that is not `tables.py` was not derived"
    )
    accumulated = frozenset().union(
        *(nullable_workspace_id_classes(source) for source in (plain_module, nullable_module))
    )
    assert accumulated == {"Zebra"}, (
        f"accumulating the derivation over two module sources gave {sorted(accumulated)}. "
        "`package_scan` unions this over every module under "
        f"{MODEL_PACKAGE}; if the union does not carry a class declared outside "
        "`tables.py`, the ban is keyed on a filename again and a receiver in a sibling "
        "model module is banned nowhere while this census reads green."
    )


def test_every_workspace_id_annotation_in_the_model_package_is_readable():
    """The honest limit of A1, as a detector rather than a sentence in a message.

    `_declares_nullable` reads three spellings: a bare name, a `BinOp`/`BitOr`
    with a direct `None` operand, and an `Optional[...]` subscript. Every other
    spelling of a nullable column answers `False` — `Union[str, None]`, the
    quoted `"str | None"` this module's own `_annotation_type` *does* resolve,
    `str | None | int` with the `None` in the middle, and a bare `str` made
    nullable through `sa_column=Column(..., nullable=True)`.

    A respelling of an existing nullable column fails closed: the census's set
    shrinks and that test reds. A *further*, newly added column in one of those
    spellings does not: the census stays as it was, the ban stays narrow, and the
    new receiver is banned nowhere while every assertion here reads green. That is
    the one way this module can report a zero because it looked the wrong way, so
    it is checked rather than confessed — over every module under `MODEL_PACKAGE`,
    because that is the whole of the derivation's reach.
    """
    unreadable: list[str] = []
    model_modules = 0
    for path in sorted((_package_root() / MODEL_PACKAGE).rglob("*.py")):
        model_modules += 1
        relative = path.relative_to(_package_root())
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ClassDef):
                continue
            for child in node.body:
                if not isinstance(child, ast.AnnAssign) or not isinstance(child.target, ast.Name):
                    continue
                if child.target.id != "workspace_id":
                    continue
                spelling = ast.unparse(child.annotation)
                where = f"{relative}:{child.lineno}"
                readable = _declares_nullable(child.annotation) or isinstance(
                    child.annotation, (ast.Name, ast.Attribute)
                )
                if not readable:
                    unreadable.append(f"{node.name}.workspace_id: {spelling}  ({where})")
                elif isinstance(child.value, ast.Call) and any(
                    keyword.arg == "sa_column" for keyword in child.value.keywords
                ):
                    unreadable.append(
                        f"{node.name}.workspace_id: {spelling} = ...sa_column=...  ({where})"
                    )
    assert model_modules >= MIN_MODEL_MODULES_SCANNED, (
        f"only {model_modules} modules under {MODEL_PACKAGE} were read, so a clean "
        "result below would mean this check looked nowhere"
    )
    assert not unreadable, (
        "a `workspace_id` column under "
        f"{MODEL_PACKAGE} is spelled in a way `_declares_nullable` cannot read:\n"
        + "\n".join(f"  {entry}" for entry in unreadable)
        + "\nIf it is nullable, the ban does not cover it and nothing else says so: the "
        "census set stays as it was and reads green. Respell the annotation as "
        "`X | None` or `Optional[X]`, which is what the derivation reads, or give "
        "`nullable_workspace_id_classes` a second source — not a wider guess."
    )


def test_every_workspace_lookup_receiver_resolves_to_a_class(package_scan):
    """Fail closed: a receiver this walk cannot type is a failure, not a pass."""
    lookups = package_scan.lookups
    unresolved = [lookup for lookup in lookups if not lookup.resolved]
    assert not unresolved, (
        "the receiver's type could not be resolved at "
        + ", ".join(f"{lookup.site} (`{lookup.receiver}`)" for lookup in unresolved)
        + f". Route it through `{ACCESSOR_NAME}`, or annotate the receiver so this "
        "census can tell whether it is a `Ticket`. A site this walk cannot read is "
        "not a site it has cleared."
    )


def test_every_receiver_resolves_to_a_class_the_package_declares(package_scan):
    """A resolution to a name that is not a class in this package is a guess.

    The other half of failing closed. `test_every_workspace_lookup_receiver_…`
    catches a receiver that resolved to nothing; this catches one that resolved
    to something meaningless — `ticket: Any`, a mapping read typed by its key,
    a row class imported from somewhere this walk never read. Each of those
    reads as "resolved, and not a `Ticket`", so the census clears it in silence.
    """
    declared = package_scan.index.declared_classes()
    invented = [
        lookup
        for lookup in package_scan.lookups
        if lookup.resolved and lookup.resolved not in declared
    ]
    assert not invented, (
        "the receiver resolved to a name the package declares no class for at "
        + ", ".join(
            f"{lookup.site} (`{lookup.receiver}` -> `{lookup.resolved}`)" for lookup in invented
        )
        + ". That is a guess, not an answer: annotate the receiver with the row "
        f"class it holds, or route the site through `{ACCESSOR_NAME}`."
    )


def _guard_for(receiver_class: str, ticket_names: frozenset[str]) -> str:
    """What the author of an offending site is supposed to do about it."""
    if receiver_class in ticket_names:
        return (
            f"call `{ACCESSOR_NAME}(session, ticket)` from `{ACCESSOR_MODULE}`, which "
            "owns this read"
        )
    return (
        f"`{receiver_class}` has no accessor, so this site needs its own answer: add one "
        f"beside `{ACCESSOR_NAME}`, or carry the inline `is not None` guard here"
    )


def test_no_module_outside_the_accessor_looks_up_a_workspace_by_a_nullable_workspace_id(
    package_scan,
):
    """The census. A nullable `workspace_id` is never read as a primary key raw.

    Scoped by the property that makes the defect possible — the receiver's
    `workspace_id` is nullable — and not by the one class it was first found on.
    `Ticket` has an accessor and `DomainEvent` does not, so the message answers
    per receiver rather than prescribing `Ticket`'s guard to every site.
    """
    ticket_names = package_scan.index.names_for(TICKET_CLASS)
    offenders = [
        lookup for lookup in package_scan.lookups if lookup.resolved in package_scan.nullable_names
    ]
    assert not offenders, (
        f"{len(offenders)} sites read a Workspace by a nullable `workspace_id` as a "
        "primary key. When that column is NULL the key is fully NULL, and SQLAlchemy "
        "answers `SAWarning: fully NULL primary key identity cannot load any object` "
        "and loads nothing — a miss that reads as an absent Workspace:\n"
        + "\n".join(
            f"  {lookup.site}  session.get(Workspace, {lookup.receiver}.workspace_id)"
            f"  — receiver resolves to `{lookup.resolved}`; "
            f"{_guard_for(lookup.resolved, ticket_names)}"
            for lookup in offenders
        )
    )


def test_the_accessor_is_the_one_answer_and_keeps_the_guard_on_its_call_line():
    """`workspace_for_ticket` exists, and its body is the pinned one-line ternary.

    Pinned because an early return (`if ticket.workspace_id is None: return None`)
    moves the guard off the call line, and the ticket's own verification grep —
    `grep -vc "is not None"` over the matching lines — then reports 1 on correct
    code. Imported here rather than at module scope so this module still collects,
    and the census above still runs, before the accessor exists.
    """
    from loregarden.services.ticket_workspace_binding import workspace_for_ticket

    source = (_package_root() / ACCESSOR_MODULE).read_text(encoding="utf-8")
    body = ast.parse(source)
    functions = {node.name: node for node in body.body if isinstance(node, ast.FunctionDef)}
    assert ACCESSOR_NAME in functions, f"{ACCESSOR_MODULE} does not define {ACCESSOR_NAME}"
    accessor = functions[ACCESSOR_NAME]
    statements = [node for node in accessor.body if not isinstance(node, ast.Expr)]
    assert len(statements) == 1 and isinstance(statements[0], ast.Return), (
        f"{ACCESSOR_NAME} must be one `return` statement, not {len(statements)}: an early "
        "return moves the guard off the call line and AC1's own grep then reports 1."
    )
    returned = statements[0].value
    assert isinstance(returned, ast.IfExp), (
        f"{ACCESSOR_NAME}'s body must be the single-line ternary, so the lookup and its "
        "`is not None` guard stay on one line."
    )
    assert ast.unparse(returned) == (
        "session.get(Workspace, ticket.workspace_id) if ticket.workspace_id is not None else None"
    ), f"{ACCESSOR_NAME}'s body is not the pinned ternary: {ast.unparse(returned)}"
    assert workspace_for_ticket.__module__ == binding_module.__name__


def test_the_accessor_module_no_longer_claims_to_do_no_session_io():
    """The purity sentence excluded the read side, and 64 readers re-derived it.

    Leaving it unamended makes the file lie about itself, so the claim is scoped
    to `validate_workspace_binding`.
    """
    docstring = ast.get_docstring(
        ast.parse(_package_root().joinpath(ACCESSOR_MODULE).read_text(encoding="utf-8"))
    )
    assert docstring is not None
    if "no Session / Workspace I/O" in docstring:
        assert "validate_workspace_binding" in docstring, (
            "the module docstring still claims the whole module does no Session I/O, "
            f"while {ACCESSOR_NAME} does exactly that. Scope the claim to "
            "`validate_workspace_binding`."
        )


# --- the adversarial controls -----------------------------------------------
#
# A positive control proves the detector sees the shapes it was written for.
# These prove it is not escapable by the shapes it was *not* written for, which
# is the only question that matters for a guard meant to hold against site 65 in
# a commit nobody on this ticket will read. Each case below was measured
# silently missed or silently mis-resolved by the first version of this walk.

#: `from … import Workspace as Ws`, one line, and a positional matcher on the
#: literal name `Workspace` reports the site as not existing at all.
_ALIASED_MODEL = """
from loregarden.models.domain.tables import Workspace as Ws
def f(session, ticket: Ticket) -> Ws | None:
    return session.get(Ws, ticket.workspace_id)
"""
#: `session.get(tables.Workspace, …)`. The walk already read a dotted
#: *annotation*; it must read a dotted model argument the same way.
_DOTTED_MODEL = """
def f(session, ticket: Ticket):
    return session.get(tables.Workspace, ticket.workspace_id)
"""
#: `Session.get(entity, ident, …)` called by keyword. Legal Python, legal
#: SQLAlchemy, and invisible to a matcher that reads `node.args` only.
_KEYWORD_CALL = """
def f(session, ticket: Ticket):
    return session.get(entity=Workspace, ident=ticket.workspace_id)
"""
#: The key wrapped in a defensive `or`. Same NULL-pk read, same warning.
_OR_NONE_KEY = """
def f(session, ticket: Ticket):
    return session.get(Workspace, ticket.workspace_id or None)
"""
#: The key read through `getattr`. Still the nullable column.
_GETATTR_KEY = """
def f(session, ticket: Ticket):
    return session.get(Workspace, getattr(ticket, "workspace_id"))
"""
#: A mapping read bound to the receiver. `cache.get(key)` has the same shape as
#: `session.get(Ticket, pk)`, and typing it by its key gave the receiver the
#: class `key`: not a `Ticket`, so cleared, and resolved, so invisible to the
#: fail-closed test. It must be unresolved instead.
_MAPPING_GET_RECEIVER = """
def f(session, cache, key):
    ticket = cache.get(key)
    return session.get(Workspace, ticket.workspace_id)
"""
#: One annotation that silences the census. `Any` is resolvable and is not
#: `Ticket`, so it must not count as a resolution.
_ANY_ANNOTATION = """
from typing import Any
def f(session, ticket: Any):
    return session.get(Workspace, ticket.workspace_id)
"""
#: `object`, the same move with a builtin.
_OBJECT_ANNOTATION = """
def f(session, ticket: object):
    return session.get(Workspace, ticket.workspace_id)
"""

_SILENT_MISS_CASES = {
    "aliased model import": _ALIASED_MODEL,
    "dotted model argument": _DOTTED_MODEL,
    "keyword-argument call": _KEYWORD_CALL,
    "`or None` on the key": _OR_NONE_KEY,
    "`getattr` on the key": _GETATTR_KEY,
}

_MUST_FAIL_CLOSED_CASES = {
    "mapping read as receiver": _MAPPING_GET_RECEIVER,
    "`Any` annotation": _ANY_ANNOTATION,
    "`object` annotation": _OBJECT_ANNOTATION,
}


@pytest.mark.parametrize(("label", "source"), sorted(_SILENT_MISS_CASES.items()))
def test_the_detector_is_not_escaped_by_respelling_the_call(label, source):
    """Five respellings of the same NULL-pk read, each a `Ticket` site."""
    found = workspace_lookups(source, label)
    assert [lookup.resolved for lookup in found] == [TICKET_CLASS], (
        f"{label}: the site is a Ticket-keyed Workspace lookup and the walk "
        f"reported {found}. A respelling it cannot see is a site it has not cleared."
    )


@pytest.mark.parametrize(("label", "source"), sorted(_MUST_FAIL_CLOSED_CASES.items()))
def test_a_receiver_the_walk_cannot_honestly_type_is_unresolved(label, source):
    """Not `Ticket`, and not some other class either: unresolved, so the suite fails.

    Each of these resolved to a confident wrong answer before — the class `key`,
    the class `Any`, the class `object` — which both the census and the
    fail-closed test accept. An unresolved receiver is the honest reading.
    """
    found = workspace_lookups(source, label)
    assert [lookup.resolved for lookup in found] == [""], (
        f"{label}: the walk claimed to type this receiver, and reported {found}. "
        "A guess that is not `Ticket` clears the site in silence."
    )


def test_a_subclass_of_ticket_is_a_ticket_site():
    """A subclass inherits the nullable column, so it inherits the defect."""
    source = """
class SpecialTicket(Ticket):
    pass

def f(session, ticket: SpecialTicket):
    return session.get(Workspace, ticket.workspace_id)
"""
    index = _index_for(ast.parse(source))
    found = workspace_lookups(source, "subclass", index)
    assert [lookup.resolved for lookup in found] == ["SpecialTicket"]
    assert "SpecialTicket" in index.names_for(TICKET_CLASS), (
        "a subclass of Ticket is not counted as a Ticket, so one `class X(Ticket)` "
        "takes a site out of the census"
    )


def test_an_alias_of_ticket_is_a_ticket_site():
    """`TicketRow = Ticket` is one line, and renames the class out of an exact match."""
    source = """
class Ticket:
    pass

TicketRow = Ticket

def f(session, ticket: TicketRow):
    return session.get(Workspace, ticket.workspace_id)
"""
    index = _index_for(ast.parse(source))
    found = workspace_lookups(source, "alias", index)
    assert [lookup.resolved for lookup in found] == ["TicketRow"]
    assert "TicketRow" in index.names_for(TICKET_CLASS), (
        "a module-level alias of Ticket is not counted as a Ticket"
    )


def test_the_non_nullable_receiver_denominator_is_unchanged(package_scan):
    """Widening the ban must change what it *bans*, not what the walk *sees*.

    A receiver whose `workspace_id` is not nullable cannot carry this defect, so
    scoping the ban by nullability leaves this complement alone — and the proof
    is that the narrow filter and the widened one return the *same* lookups, at
    every HEAD this has been measured at. That is the pin that separates "the ban
    grew" from "the resolver's reach moved".

    The count itself tracks the package: 26 at `4ec80c15` and `8c97ab2b`, 27 at
    `ed2fd03d` after one `SurveySource` site landed, 32 at `73a70f6a`. See
    `NON_NULLABLE_RECEIVER_LOOKUPS`.
    """
    out_of_scope = [
        lookup
        for lookup in package_scan.lookups
        if lookup.resolved not in package_scan.nullable_names
    ]
    assert len(out_of_scope) == NON_NULLABLE_RECEIVER_LOOKUPS, (
        f"{len(out_of_scope)} lookups resolved to a receiver whose `workspace_id` is not "
        f"nullable, not the {NON_NULLABLE_RECEIVER_LOOKUPS} measured at HEAD 73a70f6a "
        "(32 of 32 resolved lookups, 0 nullable-keyed). Widening the ban does not move "
        "this number, so a change here is one of two things, and they need different "
        "answers: the package gained or lost a lookup on a non-nullable receiver (re-pin, "
        "naming the site and its receiver class), or the walk's reach moved and it is now "
        "resolving a different set of sites (diagnose that first — the other assertions in "
        "this module rest on the walk seeing everything it did before). Re-measure before "
        "moving it; never move it to make this red go away."
    )


def test_the_allowlisted_module_holds_exactly_one_raw_lookup():
    """The allowlist is a whole file, and the census does not read inside it.

    So the one module permitted the raw call is the one place a 65th site could
    be added without this census saying a word. The accessor's own body is
    pinned above; this pins the count, so a second raw lookup anywhere else in
    that module is a failure rather than an exemption nobody asked for.
    """
    source = (_package_root() / ACCESSOR_MODULE).read_text(encoding="utf-8")
    found = workspace_lookups(source, str(ACCESSOR_MODULE))
    assert len(found) == 1, (
        f"{ACCESSOR_MODULE} holds {len(found)} raw workspace-by-`workspace_id` lookups, "
        f"not the one the allowlist is for: {found}. Every reader calls "
        f"`{ACCESSOR_NAME}`, including the ones in its own module."
    )
