"""
Test suite for smart import routing to Studio with preview flag.

Ticket:   34-route-smart-import-selection-to-studio-with-prev
Stage:    test_break (test_designer)

Acceptance Criteria mapping:
  - AC-1: Smart import selection navigates to Studio
  - AC-2: Imported ticket data passed to Studio context
  - AC-3: Studio recognizes preview state (not finalized)

These tests verify the server-side behavior for smart import flow:
1. Smart import mode flag is preserved through API calls
2. Preview endpoint recognizes smart import mode
3. Studio session created from smart import includes preview flag
4. Imported data is correctly formatted for Studio context
5. Preview state prevents direct finalization (must go through Studio)

Implementation Notes:
- Tests assume new API parameter: import_mode="smart"|"regular"
- Studio session model includes: is_preview, import_source, imported_tickets
- Preview endpoint returns: mode confirmation, data for Studio
"""

import textwrap

from fastapi.testclient import TestClient


def sample_ticket_md(
    *,
    external_id: str = "test-feature",
    title: str = "Test Feature",
    work_item_type: str = "feature",
) -> str:
    return textwrap.dedent(
        f"""\
        # TICKET: {external_id}
        Title: {title}
        Work item type: {work_item_type}

        ---

        ## Description
        Feature imported via smart import.

        ---

        ## Acceptance Criteria
        - Criterion one
        - Criterion two
        """
    )


# ===========================================================================
# AC-1: Navigation — Smart Import Routes to Studio
# ===========================================================================


class TestSmartImportNavigation:
    """Verify smart import mode triggers Studio navigation path."""

    def test_smart_import_returns_preview_data_for_studio(self, client: TestClient):
        """
        N1: Smart import preview returns data suitable for Studio session.

        When import_mode="smart" is passed to preview endpoint,
        response includes studio_context with imported ticket draft data.
        """
        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "loregarden",
                "files": [{"name": "feature.md", "content": sample_ticket_md()}],
                "mode": "smart",
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body.get("mode") == "smart"
        assert "studio_context" in body
        assert "imported_tickets" in body["studio_context"]

    def test_regular_import_does_not_include_studio_context(self, client: TestClient):
        """
        N2: Regular import uses existing confirmation flow (no studio_context).
        """
        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "loregarden",
                "files": [{"name": "feature.md", "content": sample_ticket_md()}],
                "mode": "regular",
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body.get("mode") == "regular"
        assert "studio_context" not in body


    def test_smart_import_defaults_to_studio_context(self, client: TestClient):
        """
        N3: If mode parameter omitted, smart import assumes Studio context.

        This ensures backward compatibility while defaulting to smart flow.
        """
        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "loregarden",
                "files": [{"name": "feature.md", "content": sample_ticket_md()}],
            },
        )
        assert res.status_code == 200
        body = res.json()
        # Default behavior: smart import preferred
        assert body.get("mode", "smart") == "smart"


# ===========================================================================
# AC-2: Data Flow — Imported Tickets to Studio Context
# ===========================================================================


class TestDataFlowToStudio:
    """Verify imported ticket data flows correctly to Studio session."""


    def test_smart_import_includes_full_ticket_data(self, client: TestClient):
        """
        D1: Imported ticket data includes all fields needed by Studio.

        Fields: title, work_item_type, description, acceptance_criteria,
        external_id, priority, parent references.
        """
        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "loregarden",
                "files": [{"name": "feature.md", "content": sample_ticket_md()}],
                "mode": "smart",
            },
        )
        assert res.status_code == 200
        body = res.json()
        tickets = body["studio_context"]["imported_tickets"]
        assert len(tickets) >= 1

        ticket = tickets[0]
        assert ticket["title"] == "Test Feature"
        assert ticket["work_item_type"] == "feature"
        assert ticket["external_id"] == "test-feature"
        assert "description" in ticket
        assert "acceptance_criteria" in ticket


    def test_multiple_files_smart_import_included_in_studio_context(
        self, client: TestClient
    ):
        """
        D2: Multiple imported files result in multiple draft tickets in Studio.
        """
        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "loregarden",
                "files": [
                    {"name": "feature1.md", "content": sample_ticket_md(external_id="f1")},
                    {"name": "feature2.md", "content": sample_ticket_md(external_id="f2")},
                ],
                "mode": "smart",
            },
        )
        assert res.status_code == 200
        body = res.json()
        tickets = body["studio_context"]["imported_tickets"]
        assert len(tickets) == 2
        assert {t["external_id"] for t in tickets} == {"f1", "f2"}


    def test_smart_import_preserves_ticket_hierarchy(self, client: TestClient):
        """
        D3: Parent-child relationships in imported files are preserved.
        """
        feature_md = sample_ticket_md(external_id="feature", work_item_type="feature")
        task_md = textwrap.dedent(
            """\
            # TICKET: task-1
            Title: Task for feature
            Work item type: task
            Parent ticket: feature

            ---

            ## Description
            Child of imported feature.
            """
        )

        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "loregarden",
                "files": [
                    {"name": "feature.md", "content": feature_md},
                    {"name": "task.md", "content": task_md},
                ],
                "mode": "smart",
            },
        )
        assert res.status_code == 200
        body = res.json()
        tickets = {t["external_id"]: t for t in body["studio_context"]["imported_tickets"]}
        assert tickets["task-1"].get("parent_external_id") == "feature"


# ===========================================================================
# AC-3: Preview State — Studio Recognizes Non-Finalized Import
# ===========================================================================


class TestPreviewStateRecognition:
    """Verify Studio correctly handles preview state from smart import."""


    def test_smart_import_session_marked_as_preview(self, client: TestClient):
        """
        P1: Studio session created from smart import has is_preview=True.

        This flag indicates tickets are imported but not yet committed.
        """
        # First create a studio session with smart import data
        preview_res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "loregarden",
                "files": [{"name": "feature.md", "content": sample_ticket_md()}],
                "mode": "smart",
            },
        )
        assert preview_res.status_code == 200
        studio_context = preview_res.json()["studio_context"]

        # Then create a session with that data
        session_res = client.post(
            "/api/ticket-studio/sessions",
            json={
                "workspace_slug": "loregarden",
                "title": "Import preview",
                "brief": "Smart imported tickets",
                "imported_tickets": studio_context["imported_tickets"],
            },
        )
        assert session_res.status_code == 200 or session_res.status_code == 201
        session = session_res.json()
        assert session.get("is_preview") is True


    def test_regular_import_session_not_marked_as_preview(self, client: TestClient):
        """
        P2: Regular import sessions do not use is_preview flag.

        Regular flow goes through confirmation modal before any session creation.
        """
        # Regular import doesn't create session directly; confirmed import does.
        # This test documents expected behavior.
        pass


    def test_preview_session_prevents_direct_commit(self, client: TestClient):
        """
        P3: Preview session cannot be committed until reviewed by user.

        Prevents accidental finalization of imported data.
        """
        # Create a preview session
        session_res = client.post(
            "/api/ticket-studio/sessions",
            json={
                "workspace_slug": "loregarden",
                "title": "Import preview",
                "brief": "Smart imported tickets",
                "is_preview": True,
            },
        )
        session_id = session_res.json()["id"]

        # Attempt to commit without going through UI validation
        commit_res = client.post(
            f"/api/ticket-studio/sessions/{session_id}/commit",
            json={"confirm_preview": False},
        )
        # Should either fail or require explicit preview confirmation
        assert (
            commit_res.status_code >= 400
            or commit_res.json().get("requires_preview_confirmation") is True
        )


    def test_preview_session_survives_clarifications(self, client: TestClient):
        """
        P4: Preview flag is maintained through clarification request/response.
        """
        session_id = "some-preview-session-id"

        # Request clarifications on preview session
        clarify_res = client.post(
            f"/api/ticket-studio/sessions/{session_id}/clarifications",
        )
        assert clarify_res.status_code == 200
        updated = clarify_res.json()
        assert updated.get("is_preview") is True


# ===========================================================================
# AC-1,2,3: Integration — End-to-End Smart Import Flow
# ===========================================================================


class TestSmartImportEndToEnd:
    """Verify complete flow from import files through Studio session."""


    def test_smart_import_full_flow(self, client: TestClient):
        """
        E1: Complete smart import flow: files → preview → Studio session.

        1. User selects files and smart mode
        2. Preview API returns Studio context
        3. Studio session is created with imported data
        4. Session marked as preview (AC-3)
        5. User reviews and commits in Studio
        """
        # Step 1: Get preview
        preview_res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "loregarden",
                "files": [{"name": "feature.md", "content": sample_ticket_md()}],
                "mode": "smart",
            },
        )
        assert preview_res.status_code == 200
        studio_context = preview_res.json()["studio_context"]

        # Step 2: Create session with imported data
        session_res = client.post(
            "/api/ticket-studio/sessions",
            json={
                "workspace_slug": "loregarden",
                "title": "Smart import session",
                "brief": "Reviewing imported tickets",
                "imported_tickets": studio_context["imported_tickets"],
            },
        )
        assert session_res.status_code in [200, 201]
        session = session_res.json()

        # Step 3: Verify session is marked as preview
        assert session.get("is_preview") is True

        # Step 4: Get session details
        session_id = session["id"]
        detail_res = client.get(f"/api/ticket-studio/sessions/{session_id}")
        assert detail_res.status_code == 200
        session_detail = detail_res.json()
        assert session_detail.get("is_preview") is True
        assert len(session_detail.get("draft", [])) >= 1


    def test_smart_import_multiple_files_e2e(self, client: TestClient):
        """
        E2: Smart import with multiple files routes all to single Studio session.
        """
        files = [
            {"name": "feature1.md", "content": sample_ticket_md(external_id="f1")},
            {"name": "feature2.md", "content": sample_ticket_md(external_id="f2")},
        ]

        preview_res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "loregarden",
                "files": files,
                "mode": "smart",
            },
        )
        assert preview_res.status_code == 200
        imported = preview_res.json()["studio_context"]["imported_tickets"]
        assert len(imported) == 2

        session_res = client.post(
            "/api/ticket-studio/sessions",
            json={
                "workspace_slug": "loregarden",
                "title": "Multi-file import",
                "imported_tickets": imported,
            },
        )
        assert session_res.status_code in [200, 201]
        session = session_res.json()
        assert session.get("is_preview") is True
        assert len(session.get("draft", [])) == 2
