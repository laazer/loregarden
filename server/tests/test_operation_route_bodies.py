"""A REST route's body is its operation's args model, less what the route supplies.

A body written beside the args model drifted: `/orchestration/tickets/{id}/start`
took `ignore_prerequisites` and dropped it, descriptions were written twice, and
defaults disagreed. These pin each operation route's body to the model, and the
generator's narrowing to what it says.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Iterator
from typing import Any, get_args

import pytest
from fastapi.routing import APIRoute
from loregarden.api.operation_bodies import OperationBody
from loregarden.main import app
from loregarden.models.domain import WorkItemType
from loregarden.services.operations import OperationId
from loregarden.services.operations.call import OPERATIONS
from loregarden.services.reference_survey.research import RefuteIn
from loregarden.services.reference_survey.schedule import ReviewNowIn
from loregarden.services.reference_survey.schemas import SourceIn
from pydantic import BaseModel, ValidationError

#: Bodies that are not generated, and why each cannot drift from what validates it.
_NOT_GENERATED: dict[type[BaseModel], str] = {
    # The survey and research operations multiplex several routes on `action`;
    # their handlers validate these per-action models themselves.
    SourceIn: "survey_queue validates it (`_source_in`)",
    ReviewNowIn: "survey_queue validates it (`review_now`)",
    RefuteIn: "research_record validates it",
}


def _routes(routes: list[Any]) -> Iterator[APIRoute]:
    for route in routes:
        if inner := getattr(route, "original_router", None):
            yield from _routes(inner.routes)
        elif type(route) is APIRoute:
            yield route


def _operation_routes() -> list[tuple[APIRoute, OperationId, type[BaseModel]]]:
    found = []
    for route in _routes(app.routes):
        source = inspect.getsource(route.endpoint)
        if "run_rest_operation(" not in source or route.body_field is None:
            continue
        (operation,) = set(re.findall(r"OperationId\.(\w+)", source))
        body = route.body_field.field_info.annotation
        body = next((arg for arg in get_args(body) if arg is not type(None)), body)
        found.append((route, OperationId[operation], body))
    return found


_ROUTES = _operation_routes()
#: The app's bodies, before this module adds its own below.
_BODIES = list(OperationBody.generated)


def test_the_routes_were_found():
    assert len(_ROUTES) >= 25, "the route walk reached too few operation routes to mean anything"
    assert {body for _, _, body in _ROUTES} >= set(_BODIES), "a generated body no route takes"


@pytest.mark.parametrize(
    ("route", "operation", "body"), _ROUTES, ids=[r.path for r, _, _ in _ROUTES]
)
def test_an_operation_routes_body_is_cut_from_its_args_model(
    route: APIRoute, operation: OperationId, body: type[BaseModel]
):
    args = OPERATIONS[operation].args
    if body in _NOT_GENERATED:
        return
    if issubclass(args, body):
        return  # the args model extends the body: one source already
    spec = OperationBody.generated.get(body)  # type: ignore[call-overload]
    assert spec is not None, f"{route.path}: generate {body.__name__} from {args.__name__}"
    assert spec.operation_id is operation, f"{route.path}: {body.__name__} is cut from another op"


@pytest.mark.parametrize("body", _BODIES, ids=lambda b: b.__name__)
def test_a_body_offers_each_field_as_its_args_model_describes_it(body: type[OperationBody]):
    spec = OperationBody.generated[body]
    fields = OPERATIONS[spec.operation_id].args.model_fields
    assert body.model_fields.keys() == fields.keys() - spec.supplied - spec.hidden
    for name, field in body.model_fields.items():
        assert field.description == fields[name].description, f"{body.__name__}.{name}"


def test_the_openapi_components_keep_their_names():
    components = app.openapi()["components"]["schemas"]
    for body in _BODIES:
        assert body.__name__ in components, f"{body.__name__} is not an OpenAPI component"
        listed = set(components[body.__name__].get("properties", {}))
        sent = {field.alias or name for name, field in body.model_fields.items()}
        assert listed == sent, body.__name__


# --- the generator --------------------------------------------------------------


class _Created(
    OperationBody,
    operation=OperationId.TICKET_CREATE,
    required={"title"},
    renamed={"parent": "parent_ticket_id"},
    nullable={"parent"},
    typed={"work_item_type": WorkItemType},
):
    pass


class _Strict(
    OperationBody, operation=OperationId.TICKET_UPDATE, supplied={"ticket_id"}, extra="forbid"
):
    pass


class _Picked(OperationBody, operation=OperationId.SURVEY_QUEUE, only={"excluded"}):
    pass


def test_required_makes_a_defaulted_field_required():
    with pytest.raises(ValidationError, match="title"):
        _Created()


@pytest.mark.parametrize(("sent", "dumped"), [(None, ""), ("lg-x-1", "lg-x-1")])
def test_a_renamed_nullable_field_takes_the_pages_key_and_null(sent: str | None, dumped: str):
    assert _Created(title="t", parent_ticket_id=sent).model_dump()["parent"] == dumped
    assert "parent_ticket_id" in _Created.model_json_schema()["properties"]


def test_unknown_keys_are_ignored_unless_the_body_forbids_them():
    assert "nonsense" not in _Created(title="t", nonsense=1).model_dump()  # type: ignore[call-arg]
    with pytest.raises(ValidationError, match="nonsense"):
        _Strict(nonsense=1)  # type: ignore[call-arg]


def test_only_picks_the_fields_of_a_multiplexed_operation():
    assert set(_Picked.model_fields) == {"excluded"}


@pytest.mark.parametrize(
    ("keywords", "refusal"),
    [
        ({"supplied": {"no_such_field"}}, "has no field"),
        ({"required": {"title"}, "hidden": {"title"}}, "both offered and held back"),
    ],
)
def test_a_cut_naming_no_field_or_both_ways_is_refused(keywords: dict[str, Any], refusal: str):
    with pytest.raises(TypeError, match=refusal):

        class _Bad(OperationBody, operation=OperationId.TICKET_CREATE, **keywords):
            pass


def test_a_body_declaring_its_own_field_is_refused():
    with pytest.raises(TypeError, match="declares"):

        class _Declares(OperationBody, operation=OperationId.TICKET_CREATE):
            title: str


@pytest.mark.parametrize("sent", ["Initiative", ""])
def test_a_typed_field_refuses_what_its_enum_does_not_name(sent: str):
    with pytest.raises(ValidationError, match="work_item_type"):
        _Created(title="t", work_item_type=sent)
    assert _Created(title="t").model_dump()["work_item_type"] == "task"
