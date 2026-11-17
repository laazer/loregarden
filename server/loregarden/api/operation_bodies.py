"""A REST route's request body, generated from its operation's args model.

`run_operation` validates every call against the operation's args model, so a
body written beside it can only drift: a field it accepts and then drops, a
default that differs, a description written twice. A route's body is the args
model less what the route supplies itself (its path parameters, an action it
fixes) and what the page has never sent:

    class CompleteStageRequest(
        OperationBody, operation=OperationId.STAGE_COMPLETE, supplied={"run_id"}
    ):
        \"\"\"A stage's result.\"\"\"

The class is the OpenAPI component, under its own name. A route dumps it
(`body.model_dump()`) beside what it supplies; it declares no attributes of its
own for code to read.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Any, ClassVar

from loregarden.services.operations import OperationId
from loregarden.services.operations.call import OPERATIONS
from pydantic import AfterValidator, BaseModel
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined


@dataclass(frozen=True)
class BodySpec:
    """How a body was cut from its operation's args model, for the tests to read back."""

    operation_id: OperationId
    #: Fields the route fills itself: path parameters, an action it fixes.
    supplied: frozenset[str]
    #: Fields the args model takes that this route does not offer.
    hidden: frozenset[str]


class OperationBody(BaseModel):
    """A request body whose fields are its operation's args model's.

    Class keywords say how it differs from the model. `supplied` names the
    fields the route fills from its path; `only` picks the fields of an
    operation that multiplexes several routes on an action, and `hidden` drops
    fields otherwise. `required` makes a field the route always demanded
    required here though the model defaults it; `renamed` maps a field to the
    key the page sends; `nullable` also takes `null`, read as the model's
    default; `typed` holds a field the model keeps as text (so its parser can
    word an agent's refusal) to the enum the route always validated it as. Unknown keys are ignored, as REST bodies always have; a body that
    must refuse them says `extra="forbid"`.
    """

    #: Every generated body: the tests check each against its route.
    generated: ClassVar[dict[type[OperationBody], BodySpec]] = {}

    def __init_subclass__(
        cls,
        *,
        operation: OperationId,
        supplied: Iterable[str] = (),
        hidden: Iterable[str] = (),
        only: Iterable[str] | None = None,
        required: Iterable[str] = (),
        renamed: Mapping[str, str] | None = None,
        nullable: Iterable[str] = (),
        typed: Mapping[str, type[Enum]] | None = None,
        **config: Any,
    ) -> None:
        # Pydantic collects a model's fields after this hook returns, from the
        # annotations set here; each carries its field (default, description) in
        # `Annotated`.
        super().__init_subclass__(**config)
        if declared := cls.__dict__.get("__annotations__"):
            raise TypeError(f"{cls.__name__} declares {sorted(declared)}; its args model does")
        args = OPERATIONS[operation].args
        fields = args.model_fields
        cut = _Cut(
            supplied=frozenset(supplied),
            hidden=frozenset(hidden),
            required=frozenset(required),
            renamed=dict(renamed or {}),
            nullable=frozenset(nullable),
            typed=dict(typed or {}),
        )
        if only is not None:
            cut = cut.keeping(fields.keys(), frozenset(only))
        cut.check(cls.__name__, args.__name__, fields.keys())
        annotations: dict[str, Any] = {}
        for name, info in fields.items():
            if name in cut.supplied or name in cut.hidden:
                continue
            annotations[name] = cut.offered(name, info)
        cls.__annotations__ = annotations
        OperationBody.generated[cls] = BodySpec(operation, cut.supplied, cut.hidden)


@dataclass(frozen=True)
class _Cut:
    supplied: frozenset[str]
    hidden: frozenset[str]
    required: frozenset[str]
    renamed: Mapping[str, str]
    nullable: frozenset[str]
    typed: Mapping[str, type[Enum]]

    def keeping(self, fields: Iterable[str], only: frozenset[str]) -> _Cut:
        hidden = self.hidden | (frozenset(fields) - only - self.supplied)
        return _Cut(self.supplied, hidden, self.required, self.renamed, self.nullable, self.typed)

    def check(self, body: str, args: str, fields: Iterable[str]) -> None:
        offered_as = self.required | self.nullable | self.renamed.keys() | self.typed.keys()
        if unknown := (self.supplied | self.hidden | offered_as) - frozenset(fields):
            raise TypeError(f"{body}: {args} has no field {sorted(unknown)}")
        if clash := offered_as & (self.supplied | self.hidden):
            raise TypeError(f"{body}: {sorted(clash)} is both offered and held back")

    def offered(self, name: str, info: FieldInfo) -> Any:
        """`name`'s annotation, carrying its field, as this body offers it."""
        field = FieldInfo.merge_field_infos(info)
        annotation = info.annotation
        if name in self.typed:
            choices = self.typed[name]
            annotation = choices | None if info.default is None else choices
        if name in self.required:
            field.default = PydanticUndefined
            field.default_factory = None
        if name in self.renamed:
            field.alias = field.validation_alias = self.renamed[name]
        if name in self.nullable:
            annotation = Annotated[annotation | None, AfterValidator(_none_as(info.get_default()))]
        return Annotated[annotation, field]


def _none_as(default: Any) -> Any:
    return lambda value: default if value is None else value
