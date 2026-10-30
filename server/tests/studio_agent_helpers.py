"""Shared scaffolding for the Studio agent write suites.

`test_studio_agent_writes.py` pins the contract and
`test_studio_agent_writes_adversarial.py` attacks it; both drive the same two
HTTP surfaces and seed the same four reference sources, so the setup lives here
rather than twice. The assertions stay in the suites — this module builds rows
and reads responses and asserts nothing about behaviour.

`add_raw_template` / `add_raw_draft` take the `stages_json` blob verbatim on
purpose: the column has no schema behind it, and what an older build or a
hand-edit left in it is exactly what the adversarial suite feeds the scan.
"""

import json

from fastapi.testclient import TestClient
from loregarden.models.domain import (
    StudioAgent,
    StudioAgentVersion,
    StudioWorkflow,
    WorkflowTemplate,
)
from loregarden.testing.factories import make_ticket
from sqlmodel import Session, select

AGENT_SLUG = "delete-probe"
AGENTS_PATH = "/api/studio/agents"


def create_agent(client: TestClient, slug: str = AGENT_SLUG, **overrides) -> dict:
    """An agent through the real POST, so it has the version row every row has."""
    body = {
        "slug": slug,
        "name": slug.replace("-", " ").title(),
        "description": "Probes the agent write surfaces.",
        "role_body": "You probe the Studio agent writes.",
        "adapter": "claude",
        "mcp_enabled": True,
    }
    body.update(overrides)
    res = client.post(AGENTS_PATH, json=body)
    assert res.status_code == 200, res.text
    return res.json()


def stage(**overrides) -> dict:
    """One stage dict in the shape `stages_json` carries."""
    payload = {
        "key": "work",
        "name": "Work",
        "stage_type": "agent",
        "agent_id": "",
        "skill_name": "",
        "order": 1,
        "classify_routes": [],
        "parallel_agents": [],
    }
    payload.update(overrides)
    return payload


def add_template(session: Session, slug: str, stages: list[dict]) -> None:
    add_raw_template(session, slug, json.dumps(stages))


def add_draft(session: Session, slug: str, stages: list[dict]) -> None:
    add_raw_draft(session, slug, json.dumps(stages))


def add_raw_template(session: Session, slug: str, stages_json: str) -> None:
    session.add(WorkflowTemplate(slug=slug, name=slug, stages_json=stages_json))
    session.commit()


def add_raw_draft(session: Session, slug: str, stages_json: str) -> None:
    session.add(StudioWorkflow(slug=slug, name=slug, stages_json=stages_json))
    session.commit()


def pin_ticket(session: Session, workspace, external_id: str, *, column: str, agent: str, state):
    """A ticket whose `next_agent` or `scope_reroute_agent` names `agent`."""
    ticket = make_ticket(session, workspace_id=workspace.id, external_id=external_id, state=state)
    setattr(ticket, column, agent)
    session.add(ticket)
    session.commit()
    return ticket


def classes(payload: dict, half: str) -> dict[str, dict]:
    """The refusal payload's reference classes for one half, keyed by kind."""
    return {entry["kind"]: entry for entry in payload["references"][half]}


def versions_status(client: TestClient, slug: str) -> int:
    return client.get(f"{AGENTS_PATH}/{slug}/versions").status_code


def writable_slugs(client: TestClient) -> set[str]:
    """Slugs backed by a real row — the registry fallbacks are `read_only`."""
    return {item["slug"] for item in client.get(AGENTS_PATH).json() if not item["read_only"]}


def version_rows(session: Session, slug: str) -> list[StudioAgentVersion]:
    agent = session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).first()
    if not agent:
        return []
    return list(
        session.exec(
            select(StudioAgentVersion).where(StudioAgentVersion.agent_id == agent.id)
        ).all()
    )


def orphan_version_rows(session: Session) -> list[StudioAgentVersion]:
    """Version rows whose agent is gone — what a delete must never leave."""
    session.expire_all()
    live = {agent.id for agent in session.exec(select(StudioAgent)).all()}
    return [
        row for row in session.exec(select(StudioAgentVersion)).all() if row.agent_id not in live
    ]
