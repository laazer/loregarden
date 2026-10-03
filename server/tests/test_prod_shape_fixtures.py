"""Record what the UI fetches, from the real API over the production-shaped scenario.

Frontend tests that render a page with three hand-written rows cannot see the
page fail at production volume. These fixtures are the API's own responses over
`loregarden.testing.prod_shape`, so a jest test can render the Monitor with its
94 findings or the Memory map with its 34 unlinked records.

    LOREGARDEN_RECORD_FIXTURES=1 pytest tests/test_prod_shape_fixtures.py   # write
    pytest tests/test_prod_shape_fixtures.py                                # check

Checking fails when the committed files no longer match what the API returns —
a response shape changed, or the scenario did — so a jest test can never keep
passing against a picture of the API that stopped being true.
"""

from __future__ import annotations

import json
import os
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from loregarden.db.session import get_session
from loregarden.main import app
from loregarden.models.domain import Artifact
from loregarden.services import initiative_suggestions
from loregarden.services.memory_store import AgentMemoryService
from loregarden.testing.prod_shape import build_prod_shape, prod_shape_id
from sqlmodel import Session

FIXTURES = (
    Path(__file__).resolve().parents[2] / "client" / "src" / "test" / "fixtures" / "prod-shape"
)
RECORD = os.environ.get("LOREGARDEN_RECORD_FIXTURES") == "1"

#: Written at record time and therefore different on every run; pinned so the
#: comparison is about shape and content, not the clock.
_VOLATILE = {"checked_at", "created_at", "updated_at"}
_PINNED = "2026-09-28T00:00:00Z"

#: fixture file -> the request a page makes.
ENDPOINTS = {
    "monitor-findings.json": ("/api/monitor/findings", {}),
    "initiatives.json": ("/api/initiatives", {}),
    "attachable-milestones.json": ("/api/initiatives/attachable-milestones", {}),
    "memory-graph-loregarden.json": ("/api/memory/graph", {"workspace_slug": "loregarden"}),
    # The ticket carrying the scenario's first plan document; resolved per run.
    "ticket-artifacts.json": ("/api/tickets/{plan_ticket}/artifacts", {}),
    "initiative-suggestions.json": ("/api/initiatives/suggestions", {}),
}

#: Fixtures whose response is computed from "now" (the sprint's dates), recorded
#: with the clock pinned. The scenario's own timestamps are stamped at build
#: time, after the pin, so pace still measures every completion in the window.
_CLOCKED = {"initiative-suggestions.json"}


def _pin(value):
    if isinstance(value, dict):  # py-org: allow-isinstance
        return {k: (_PINNED if k in _VOLATILE and v else _pin(v)) for k, v in value.items()}
    if isinstance(value, list):  # py-org: allow-isinstance
        return [_pin(v) for v in value]
    return value


@pytest.fixture(name="api")
def api_fixture(isolated_db):
    """The real app over the scenario alone — no bootstrap seed, whose ids are random."""

    def session_override():
        with Session(isolated_db) as session:
            yield session

    with Session(isolated_db) as session:
        build_prod_shape(session, graph_path=AgentMemoryService.from_settings().graph_path)
    app.dependency_overrides[get_session] = session_override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("name", sorted(ENDPOINTS))
def test_fixture_matches_the_api(api, isolated_db, name):
    path, params = ENDPOINTS[name]
    with Session(isolated_db) as session:
        plan_ticket = session.get(Artifact, prod_shape_id("plan", 0)).ticket_id
    with ExitStack() as stack:
        if name in _CLOCKED:
            pinned = datetime.fromisoformat(_PINNED.replace("Z", "+00:00")).astimezone(timezone.utc)
            stack.enter_context(
                mock.patch.object(initiative_suggestions, "clock", return_value=pinned)
            )
        response = api.get(path.format(plan_ticket=plan_ticket), params=params)
    assert response.status_code == 200, response.text
    body = json.dumps(_pin(response.json()), indent=1, sort_keys=True) + "\n"

    target = FIXTURES / name
    if RECORD:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        return
    assert target.is_file(), f"{target} is missing — run with LOREGARDEN_RECORD_FIXTURES=1"
    assert target.read_text(encoding="utf-8") == body, (
        f"{name} no longer matches the API over the prod-shape scenario. If the change is "
        "intended, re-record with LOREGARDEN_RECORD_FIXTURES=1 and review the jest tests "
        "that read it."
    )
