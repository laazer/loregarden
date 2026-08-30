"""Version snapshots must cover every agent column, and restore must not lie.

Forgetting a column in ``_AGENT_SNAPSHOT_FIELDS`` loses it on every restore with
no error at all, which is why the first test here is structural rather than
behavioural: it fails the build when a column is added, instead of waiting for
someone to notice the data is gone.
"""

from __future__ import annotations

import json

from loregarden.models.domain import (
    StudioAgent,
    StudioAgentCreate,
    StudioAgentToolGrants,
    StudioAgentUpdate,
    StudioAgentVersion,
)
from loregarden.models.domain.enums import CliTool, ToolPosture
from loregarden.services.studio_service import (
    _AGENT_SNAPSHOT_FIELDS,
    _SNAPSHOT_EXCLUDED_FIELDS,
    StudioService,
)
from sqlmodel import select


class TestSnapshotFieldPartition:
    def test_every_column_is_accounted_for(self):
        covered = set(_AGENT_SNAPSHOT_FIELDS) | set(_SNAPSHOT_EXCLUDED_FIELDS)
        assert covered == set(StudioAgent.model_fields), (
            "A StudioAgent column belongs in exactly one of _AGENT_SNAPSHOT_FIELDS "
            "(restored by version history) or _SNAPSHOT_EXCLUDED_FIELDS (identity / "
            "bookkeeping). Add the new column to one of them."
        )

    def test_the_two_lists_do_not_overlap(self):
        assert not set(_AGENT_SNAPSHOT_FIELDS) & set(_SNAPSHOT_EXCLUDED_FIELDS)

    def test_tool_grants_are_snapshotted(self):
        assert "tool_grants_json" in _AGENT_SNAPSHOT_FIELDS


def _create(service: StudioService, slug: str = "grant-test") -> str:
    service.create_agent(
        StudioAgentCreate(slug=slug, name="Grant Test", role_body="Body", adapter="claude")
    )
    return slug


def _strip_grants_from_snapshot(db_session, slug: str, version: int) -> None:
    """Rewrite one agent's snapshot as if it predated ``tool_grants_json``.

    Scoped by agent id, not version alone — the seeded built-ins all carry a
    version 1 row, so an unscoped lookup edits somebody else's history.
    """
    agent = db_session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).one()
    row = db_session.exec(
        select(StudioAgentVersion).where(
            StudioAgentVersion.agent_id == agent.id, StudioAgentVersion.version == version
        )
    ).one()
    snap = json.loads(row.snapshot_json)
    del snap["tool_grants_json"]
    row.snapshot_json = json.dumps(snap)
    db_session.add(row)
    db_session.commit()


class TestToolGrantsRoundTrip:
    def test_grants_survive_a_version_restore(self, db_session):
        service = StudioService(db_session)
        slug = _create(service)
        narrowed = StudioAgentToolGrants(
            posture=ToolPosture.ALLOWLIST, allowed_tools=[CliTool.READ], mcp_servers=["github"]
        )
        saved = service.update_agent(slug, StudioAgentUpdate(tool_grants=narrowed))
        narrowed_version = saved.version

        service.update_agent(slug, StudioAgentUpdate(tool_grants=StudioAgentToolGrants()))
        assert service.get_agent(slug).tool_grants.posture is ToolPosture.INHERIT

        restored = service.restore_agent_version(slug, narrowed_version)
        assert restored.tool_grants == narrowed

    def test_create_persists_grants(self, db_session):
        service = StudioService(db_session)
        grants = StudioAgentToolGrants(posture=ToolPosture.UNRESTRICTED)
        service.create_agent(
            StudioAgentCreate(slug="unrestricted-agent", name="U", tool_grants=grants)
        )
        assert service.get_agent("unrestricted-agent").tool_grants.posture is (
            ToolPosture.UNRESTRICTED
        )


class TestRestoringASnapshotOlderThanAColumn:
    def test_absent_field_resets_to_default_rather_than_carrying_forward(self, db_session):
        """A restore must not leave a policy the target version never had.

        Silently keeping the current value would make a restore look like it
        undid a change while the tool grants stayed narrowed — a policy
        surviving the operation meant to remove it.
        """
        service = StudioService(db_session)
        slug = _create(service, "legacy-snapshot")
        first_version = service.get_agent(slug).version
        _strip_grants_from_snapshot(db_session, slug, first_version)

        service.update_agent(
            slug,
            StudioAgentUpdate(
                tool_grants=StudioAgentToolGrants(
                    posture=ToolPosture.ALLOWLIST, allowed_tools=[CliTool.READ]
                )
            ),
        )
        restored = service.restore_agent_version(slug, first_version)
        assert restored.tool_grants.posture is ToolPosture.INHERIT
        assert restored.tool_grants.allowed_tools == []

    def test_the_reset_is_recorded_in_the_version_history(self, db_session):
        service = StudioService(db_session)
        slug = _create(service, "legacy-note")
        first_version = service.get_agent(slug).version
        _strip_grants_from_snapshot(db_session, slug, first_version)

        service.update_agent(
            slug,
            StudioAgentUpdate(tool_grants=StudioAgentToolGrants(posture=ToolPosture.ALLOWLIST)),
        )
        service.restore_agent_version(slug, first_version)

        notes = [entry.change_note for entry in service.list_agent_versions(slug)]
        assert any("tool_grants_json" in note for note in notes), notes
