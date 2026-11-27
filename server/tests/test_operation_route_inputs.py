"""A REST route's body and query are its operation's args model, less what it supplies.

An input written beside the args model drifted: `/orchestration/tickets/{id}/start`
took `ignore_prerequisites` and dropped it, descriptions were written twice, and
defaults disagreed. A query parameter's default reaches the operation as if the
caller sent it, so `?include_settled` answered `True` to the page and `False` to
agents with nothing saying so, and `/handoffs` dropped the limits its model set.
These pin each operation route's inputs to the model, and the generator's
narrowing to what it says.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Iterator
from typing import Any, get_args
from unittest import mock

import pytest
from fastapi.routing import APIRoute
from loregarden.api.operation_inputs import InputSpec, OperationBody, OperationQuery
from loregarden.main import app
from loregarden.models.domain import TicketState, WorkItemType
from loregarden.services.operations import OperationId
from loregarden.services.operations.call import OPERATIONS
from loregarden.services.operations.spec import Effect
from loregarden.services.reference_survey.research import RefuteIn
from loregarden.services.reference_survey.schedule import ReviewNowIn
from loregarden.services.reference_survey.schemas import SourceIn
from pydantic import BaseModel, ValidationError

# A registry check: runs on pre-commit (.lefthook/scripts/registry-checks.sh).
pytestmark = pytest.mark.registry

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


def _query_routes() -> list[tuple[APIRoute, OperationId]]:
    found = []
    for route in _routes(app.routes):
        source = inspect.getsource(route.endpoint)
        if "run_rest_operation(" in source and route.dependant.query_params:
            (operation,) = set(re.findall(r"OperationId\.(\w+)", source))
            found.append((route, OperationId[operation]))
    return found


_ROUTES = _operation_routes()
_QUERY_ROUTES = _query_routes()
#: The app's inputs, before this module adds its own below.
_BODIES = list(OperationBody.generated)
_QUERIES = list(OperationQuery.generated)
_INPUTS = [*_BODIES, *_QUERIES]


def _spec(model: type[BaseModel]) -> InputSpec:
    return {**OperationBody.generated, **OperationQuery.generated}[model]  # type: ignore[index]


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


def test_the_query_routes_were_found():
    assert len(_QUERY_ROUTES) >= 10, "the route walk reached too few query routes to mean anything"
    taken = {
        param.field_info.annotation
        for route, _ in _QUERY_ROUTES
        for param in route.dependant.query_params
    }
    assert taken >= set(_QUERIES), "a generated query no route takes"


@pytest.mark.parametrize(
    ("route", "operation"), _QUERY_ROUTES, ids=[f"{r.methods} {r.path}" for r, _ in _QUERY_ROUTES]
)
def test_an_operation_routes_query_is_cut_from_its_args_model(
    route: APIRoute, operation: OperationId
):
    """One query model, generated: a parameter in the signature is a second list of fields."""
    params = route.dependant.query_params
    model = params[0].field_info.annotation
    assert len(params) == 1 and model in OperationQuery.generated, (
        f"{route.path}: take `query: Annotated[<OperationQuery for {operation.name}>, Query()]`, "
        f"not {[param.name for param in params]} in the signature"
    )
    assert OperationQuery.generated[model].operation_id is operation, route.path


@pytest.mark.parametrize("model", _INPUTS, ids=lambda m: m.__name__)
def test_an_input_offers_each_field_as_its_args_model_describes_it(model: type[BaseModel]):
    spec = _spec(model)
    fields = OPERATIONS[spec.operation_id].args.model_fields
    assert model.model_fields.keys() == fields.keys() - spec.supplied - spec.hidden
    for name, field in model.model_fields.items():
        assert field.description == fields[name].description, f"{model.__name__}.{name}"


@pytest.mark.parametrize("model", _INPUTS, ids=lambda m: m.__name__)
def test_a_default_differs_from_the_args_model_only_where_the_input_says(model: type[BaseModel]):
    spec = _spec(model)
    fields = OPERATIONS[spec.operation_id].args.model_fields
    for name, field in model.model_fields.items():
        if field.is_required():
            continue
        if name in spec.defaults:
            assert field.default == spec.defaults[name] != fields[name].get_default(), name
        else:
            assert field.get_default() == fields[name].get_default(), f"{model.__name__}.{name}"


def test_the_openapi_components_keep_their_names():
    components = app.openapi()["components"]["schemas"]
    for body in _BODIES:
        assert body.__name__ in components, f"{body.__name__} is not an OpenAPI component"
        listed = set(components[body.__name__].get("properties", {}))
        sent = {field.alias or name for name, field in body.model_fields.items()}
        assert listed == sent, body.__name__


def test_a_get_route_runs_only_read_operations():
    """A GET that writes moves rows with no event behind it, and the strict read
    guard cannot see an operation declared WRITE. The ticket page and the docker
    board were two; each now runs a READ operation."""
    writes = [
        (route.path, name)
        for route in _routes(app.routes)
        if "GET" in route.methods
        for name in set(re.findall(r"OperationId\.(\w+)", inspect.getsource(route.endpoint)))
        if OPERATIONS[OperationId[name]].effect is not Effect.READ
    ]
    assert writes == []


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
    assert _Created(title="t").model_dump()["work_item_type"] is WorkItemType.TASK


class _Defaulted(
    OperationQuery,
    operation=OperationId.OPERATOR_QUESTION_LIST,
    defaults={"include_settled": True},
):
    pass


def test_defaults_gives_a_field_the_routes_own_default():
    assert _Defaulted().model_dump()["include_settled"] is True
    assert _Defaulted(include_settled=False).model_dump()["include_settled"] is False


def test_a_field_both_required_and_defaulted_is_refused():
    with pytest.raises(TypeError, match="both required and defaulted"):

        class _Both(
            OperationQuery,
            operation=OperationId.OPERATOR_QUESTION_LIST,
            required={"include_settled"},
            defaults={"include_settled": True},
        ):
            pass


def test_the_operator_questions_route_lists_settled_ones_unless_asked_not_to(client):
    """The page's default, declared on its query; an agent's stays the model's."""
    with mock.patch("loregarden.api.tickets.run_rest_operation", return_value={}) as run:
        client.get("/api/tickets/t-1/operator-questions")
        client.get("/api/tickets/t-1/operator-questions?include_settled=false")
    sent = [call.args[2]["include_settled"] for call in run.call_args_list]
    assert sent == [True, False]


def test_the_ticket_list_reads_repeated_state_and_type_keys(client):
    """The page sends `?state=` and `?work_item_type=` once per value; the model's
    fields are the plural lists."""
    with mock.patch("loregarden.api.tickets.run_rest_operation", return_value=[]) as run:
        client.get("/api/tickets?state=done&state=backlog&work_item_type=bug")
    sent = run.call_args.args[2]
    assert sent["states"] == [TicketState.DONE, TicketState.BACKLOG]
    assert sent["work_item_types"] == [WorkItemType.BUG]
