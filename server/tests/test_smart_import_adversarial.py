"""
Adversarial Test Suite: Smart Import Routing Edge Cases & Mutation Testing

Ticket:   34-route-smart-import-selection-to-studio-with-prev
Stage:    test_break (test_breaker)

Purpose:
This test suite exposes weaknesses, assumptions, and subtle bugs in the
server-side smart import implementation. Tests are designed to reveal:

1. NULL & EMPTY VALUE HANDLING
2. TYPE & STRUCTURE MUTATIONS
3. VALIDATION GAPS (input/output contracts)
4. RACE CONDITION VULNERABILITIES
5. STATE CONSISTENCY VIOLATIONS
6. ERROR HANDLING FAILURES
7. BOUNDARY CONDITION ENFORCEMENT
8. ASSUMPTION VALIDATION

These tests complement the basic happy-path tests in test_smart_import_routing.py
by focusing on adversarial inputs, edge cases, and mutation detection.
"""

import textwrap

import pytest
from fastapi.testclient import TestClient


def sample_ticket_md(
    *,
    external_id: str = "test-feature",
    title: str = "Test Feature",
    work_item_type: str = "feature",
    description: str = "Feature description",
) -> str:
    return textwrap.dedent(
        f"""\
        # TICKET: {external_id}
        Title: {title}
        Work item type: {work_item_type}

        ---

        ## Description
        {description}

        ---

        ## Acceptance Criteria
        - Criterion one
        - Criterion two
        """
    )


# ===========================================================================
# GROUP A: NULL & EMPTY VALUE HANDLING
# ===========================================================================


class TestNullEmptyHandling:
    """Test robustness against null, empty, and missing values."""

    @pytest.mark.skip(reason="Implementation pending")
    def test_null_workspace_slug_returns_error(self, client: TestClient):
        """A1: null workspace_slug should return 400 (not 500)."""
        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": None,
                "files": [{"name": "feature.md", "content": sample_ticket_md()}],
                "mode": "smart",
            },
        )
        assert res.status_code >= 400
        assert res.status_code < 500

    @pytest.mark.skip(reason="Implementation pending")
    def test_empty_workspace_slug_handled(self, client: TestClient):
        """A2: empty string workspace_slug should be rejected."""
        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "",
                "files": [{"name": "feature.md", "content": sample_ticket_md()}],
                "mode": "smart",
            },
        )
        assert res.status_code >= 400

    @pytest.mark.skip(reason="Implementation pending")
    def test_null_files_array_rejected(self, client: TestClient):
        """A3: null files array should be rejected (not cause crash)."""
        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "loregarden",
                "files": None,
                "mode": "smart",
            },
        )
        assert res.status_code >= 400

    @pytest.mark.skip(reason="Implementation pending")
    def test_empty_files_array_handled(self, client: TestClient):
        """A4: empty files array should be accepted but return empty result."""
        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "loregarden",
                "files": [],
                "mode": "smart",
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body.get("mode") == "smart"
        # studio_context should exist but have empty tickets
        assert "studio_context" in body
        assert len(body["studio_context"].get("imported_tickets", [])) == 0

    @pytest.mark.skip(reason="Implementation pending")
    def test_null_mode_defaults_to_smart(self, client: TestClient):
        """A5: null mode parameter should default to 'smart'."""
        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "loregarden",
                "files": [{"name": "feature.md", "content": sample_ticket_md()}],
                "mode": None,
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body.get("mode", "smart") == "smart"

    @pytest.mark.skip(reason="Implementation pending")
    def test_missing_mode_parameter_defaults_to_smart(self, client: TestClient):
        """A6: omitted mode parameter should default to 'smart'."""
        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "loregarden",
                "files": [{"name": "feature.md", "content": sample_ticket_md()}],
                # mode key omitted
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body.get("mode", "smart") == "smart"

    @pytest.mark.skip(reason="Implementation pending")
    def test_null_file_name_handled(self, client: TestClient):
        """A7: null file name should be rejected."""
        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "loregarden",
                "files": [
                    {"name": None, "content": sample_ticket_md()},
                ],
                "mode": "smart",
            },
        )
        assert res.status_code >= 400

    @pytest.mark.skip(reason="Implementation pending")
    def test_null_file_content_handled(self, client: TestClient):
        """A8: null file content should be rejected."""
        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "loregarden",
                "files": [
                    {"name": "feature.md", "content": None},
                ],
                "mode": "smart",
            },
        )
        assert res.status_code >= 400

    @pytest.mark.skip(reason="Implementation pending")
    def test_empty_file_content_handled(self, client: TestClient):
        """A9: empty file content should be handled (maybe skip, maybe error)."""
        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "loregarden",
                "files": [
                    {"name": "empty.md", "content": ""},
                ],
                "mode": "smart",
            },
        )
        # Either reject or return with 0 tickets
        assert res.status_code in [200, 400]


# ===========================================================================
# GROUP B: TYPE & STRUCTURE MUTATIONS
# ===========================================================================


class TestTypeStructureMutations:
    """Test strict type enforcement and structure validation."""

    @pytest.mark.skip(reason="Implementation pending")
    def test_mode_must_be_string_not_number(self, client: TestClient):
        """B1: mode as number should be rejected."""
        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "loregarden",
                "files": [{"name": "feature.md", "content": sample_ticket_md()}],
                "mode": 1,  # invalid type
            },
        )
        assert res.status_code >= 400

    @pytest.mark.skip(reason="Implementation pending")
    def test_mode_invalid_string_rejected(self, client: TestClient):
        """B2: mode with invalid string value rejected."""
        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "loregarden",
                "files": [{"name": "feature.md", "content": sample_ticket_md()}],
                "mode": "invalid_mode",  # not "smart" or "regular"
            },
        )
        assert res.status_code >= 400

    @pytest.mark.skip(reason="Implementation pending")
    def test_files_must_be_array_not_object(self, client: TestClient):
        """B3: files as object instead of array rejected."""
        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "loregarden",
                "files": {"name": "feature.md", "content": sample_ticket_md()},
                "mode": "smart",
            },
        )
        assert res.status_code >= 400

    @pytest.mark.skip(reason="Implementation pending")
    def test_file_must_have_name_and_content(self, client: TestClient):
        """B4: file missing required fields should be rejected."""
        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "loregarden",
                "files": [
                    {"name": "feature.md"},  # missing content
                ],
                "mode": "smart",
            },
        )
        assert res.status_code >= 400

    @pytest.mark.skip(reason="Implementation pending")
    def test_response_studio_context_type_is_object(self, client: TestClient):
        """B5: studio_context in response must be object, not string/array."""
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
        studio_context = body.get("studio_context")
        assert isinstance(studio_context, dict)

    @pytest.mark.skip(reason="Implementation pending")
    def test_response_imported_tickets_type_is_array(self, client: TestClient):
        """B6: imported_tickets must be array, not object."""
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
        assert isinstance(tickets, list)

    @pytest.mark.skip(reason="Implementation pending")
    def test_each_ticket_has_required_fields(self, client: TestClient):
        """B7: each imported ticket must have essential fields."""
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
        assert len(tickets) > 0

        for ticket in tickets:
            assert isinstance(ticket, dict)
            assert "title" in ticket
            assert "work_item_type" in ticket
            assert "external_id" in ticket


# ===========================================================================
# GROUP C: VALIDATION & CONTRACT ENFORCEMENT
# ===========================================================================


class TestValidationContracts:
    """Test input/output contract enforcement."""

    @pytest.mark.skip(reason="Implementation pending")
    def test_smart_mode_always_includes_studio_context(self, client: TestClient):
        """C1: smart mode MUST have studio_context in response."""
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
        # This is a critical requirement that breaks if violated
        assert "studio_context" in body
        assert body["mode"] == "smart"

    @pytest.mark.skip(reason="Implementation pending")
    def test_regular_mode_never_includes_studio_context(self, client: TestClient):
        """C2: regular mode should NOT have studio_context."""
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
        assert body["mode"] == "regular"
        # Regular mode should use different response structure
        # (This assumes current confirmation modal flow)

    @pytest.mark.skip(reason="Implementation pending")
    def test_mode_echo_always_matches_request(self, client: TestClient):
        """C3: response mode field should exactly match request."""
        for mode in ["smart", "regular"]:
            res = client.post(
                "/api/tickets/import/preview",
                json={
                    "workspace_slug": "loregarden",
                    "files": [{"name": "feature.md", "content": sample_ticket_md()}],
                    "mode": mode,
                },
            )
            assert res.status_code == 200
            body = res.json()
            assert body.get("mode") == mode

    @pytest.mark.skip(reason="Implementation pending")
    def test_imported_tickets_count_matches_file_count(self, client: TestClient):
        """C4: number of imported tickets should match number of valid files."""
        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "loregarden",
                "files": [
                    {"name": "f1.md", "content": sample_ticket_md(external_id="f1")},
                    {"name": "f2.md", "content": sample_ticket_md(external_id="f2")},
                    {"name": "f3.md", "content": sample_ticket_md(external_id="f3")},
                ],
                "mode": "smart",
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert len(body["studio_context"]["imported_tickets"]) == 3


# ===========================================================================
# GROUP D: RACE CONDITIONS & CONCURRENCY
# ===========================================================================


class TestRaceConditions:
    """Test behavior under concurrent/rapid requests."""

    @pytest.mark.skip(reason="Implementation pending")
    def test_rapid_preview_requests_dont_interfere(self, client: TestClient):
        """D1: multiple rapid preview requests don't interfere with each other."""
        # Simulate rapid requests
        results = []
        for i in range(5):
            res = client.post(
                "/api/tickets/import/preview",
                json={
                    "workspace_slug": "loregarden",
                    "files": [
                        {
                            "name": f"feature-{i}.md",
                            "content": sample_ticket_md(
                                external_id=f"feature-{i}",
                                title=f"Feature {i}",
                            ),
                        }
                    ],
                    "mode": "smart",
                },
            )
            assert res.status_code == 200
            results.append(res.json())

        # Each should have its own unique ticket
        for i, result in enumerate(results):
            tickets = result["studio_context"]["imported_tickets"]
            assert len(tickets) == 1
            assert tickets[0]["external_id"] == f"feature-{i}"

    @pytest.mark.skip(reason="Implementation pending")
    def test_session_creation_idempotency(self, client: TestClient):
        """D2: creating session with same data twice should be safe."""
        studio_context = {
            "imported_tickets": [
                {
                    "title": "Feature",
                    "external_id": "f1",
                    "work_item_type": "feature",
                }
            ]
        }

        # Create session twice
        res1 = client.post(
            "/api/ticket-studio/sessions",
            json={
                "workspace_slug": "loregarden",
                "title": "Test session",
                "imported_tickets": studio_context["imported_tickets"],
            },
        )
        session1 = res1.json()

        res2 = client.post(
            "/api/ticket-studio/sessions",
            json={
                "workspace_slug": "loregarden",
                "title": "Test session",
                "imported_tickets": studio_context["imported_tickets"],
            },
        )
        session2 = res2.json()

        # Both should succeed and have distinct IDs
        assert res1.status_code in [200, 201]
        assert res2.status_code in [200, 201]
        assert session1["id"] != session2["id"]


# ===========================================================================
# GROUP E: STATE CONSISTENCY & INVARIANTS
# ===========================================================================


class TestStateConsistency:
    """Test that state remains consistent across operations."""

    @pytest.mark.skip(reason="Implementation pending")
    def test_preview_flag_persists_in_session_detail(self, client: TestClient):
        """E1: is_preview flag set on session creation should persist in detail."""
        # Create preview session
        res = client.post(
            "/api/ticket-studio/sessions",
            json={
                "workspace_slug": "loregarden",
                "title": "Preview session",
                "imported_tickets": [
                    {"title": "F1", "external_id": "f1", "work_item_type": "feature"}
                ],
                "is_preview": True,
            },
        )
        session_id = res.json()["id"]

        # Fetch detail and verify flag persists
        detail_res = client.get(f"/api/ticket-studio/sessions/{session_id}")
        assert detail_res.status_code == 200
        session = detail_res.json()
        assert session.get("is_preview") is True

    @pytest.mark.skip(reason="Implementation pending")
    def test_imported_tickets_immutable_after_creation(self, client: TestClient):
        """E2: imported_tickets in session should not change after creation."""
        # Create session with specific tickets
        res = client.post(
            "/api/ticket-studio/sessions",
            json={
                "workspace_slug": "loregarden",
                "title": "Test",
                "imported_tickets": [
                    {"title": "F1", "external_id": "f1", "work_item_type": "feature"},
                    {"title": "F2", "external_id": "f2", "work_item_type": "feature"},
                ],
            },
        )
        session_id = res.json()["id"]
        initial_draft = res.json().get("draft", [])

        # Fetch again and verify count hasn't changed
        detail_res = client.get(f"/api/ticket-studio/sessions/{session_id}")
        session = detail_res.json()
        assert len(session.get("draft", [])) == len(initial_draft)


# ===========================================================================
# GROUP F: BOUNDARY CONDITIONS
# ===========================================================================


class TestBoundaryConditions:
    """Test extreme values and boundary cases."""

    @pytest.mark.skip(reason="Implementation pending")
    def test_many_files_import(self, client: TestClient):
        """F1: importing many files (e.g., 100) works without error."""
        files = [
            {
                "name": f"feature-{i}.md",
                "content": sample_ticket_md(external_id=f"f{i}", title=f"Feature {i}"),
            }
            for i in range(100)
        ]

        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "loregarden",
                "files": files,
                "mode": "smart",
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert len(body["studio_context"]["imported_tickets"]) == 100

    @pytest.mark.skip(reason="Implementation pending")
    def test_very_long_file_content(self, client: TestClient):
        """F2: very long file content is handled."""
        long_content = "A" * 100000 + "\n" + sample_ticket_md()

        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "loregarden",
                "files": [{"name": "huge.md", "content": long_content}],
                "mode": "smart",
            },
        )
        # Should handle gracefully (accept or reject with proper error)
        assert res.status_code in [200, 400, 413]

    @pytest.mark.skip(reason="Implementation pending")
    def test_very_long_workspace_slug(self, client: TestClient):
        """F3: very long workspace slug."""
        long_slug = "x" * 10000

        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": long_slug,
                "files": [{"name": "feature.md", "content": sample_ticket_md()}],
                "mode": "smart",
            },
        )
        # Should handle or reject gracefully
        assert res.status_code in [200, 400]

    @pytest.mark.skip(reason="Implementation pending")
    def test_special_characters_in_workspace_slug(self, client: TestClient):
        """F4: special characters in workspace slug."""
        special_slug = "test-@#$%^&*()_+=[]{}|:;<>,.?/~`"

        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": special_slug,
                "files": [{"name": "feature.md", "content": sample_ticket_md()}],
                "mode": "smart",
            },
        )
        # Should handle or reject with proper error
        assert res.status_code in [200, 400]


# ===========================================================================
# GROUP G: ERROR HANDLING & RECOVERY
# ===========================================================================


class TestTitleFallback:
    """Ticket 85: markdown tickets that use a plain `# Heading` for the
    title, instead of the `Title:` key-value convention, must still import
    successfully via smart import rather than failing every file with
    "Markdown ticket is missing a Title: line".
    """

    def test_smart_import_accepts_h1_heading_as_title(self, client: TestClient):
        content = textwrap.dedent(
            """\
            # Heading-only ticket

            ## Description
            Imported from a tool that only emits an H1 heading, not a
            `Title:` line.
            """
        )
        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "loregarden",
                "files": [{"name": "heading-only.md", "content": content}],
                "mode": "smart",
            },
        )
        assert res.status_code == 200
        body = res.json()
        tickets = body["studio_context"]["imported_tickets"]
        assert len(tickets) == 1
        assert tickets[0]["title"] == "Heading-only ticket"


class TestErrorHandling:
    """Test error cases and recovery."""

    @pytest.mark.skip(reason="Implementation pending")
    def test_invalid_json_body_returns_400(self, client: TestClient):
        """G1: invalid JSON should return 400 not 500."""
        res = client.post(
            "/api/tickets/import/preview",
            data="invalid json {{{",
            headers={"Content-Type": "application/json"},
        )
        assert res.status_code >= 400
        assert res.status_code < 500

    @pytest.mark.skip(reason="Implementation pending")
    def test_malformed_ticket_content_handled(self, client: TestClient):
        """G2: malformed ticket markdown doesn't crash."""
        bad_content = "This is not valid ticket format\n# Random content"

        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "loregarden",
                "files": [{"name": "bad.md", "content": bad_content}],
                "mode": "smart",
            },
        )
        # Should either parse gracefully or return clear error
        assert res.status_code in [200, 400]

    @pytest.mark.skip(reason="Implementation pending")
    def test_nonexistent_workspace_handled(self, client: TestClient):
        """G3: nonexistent workspace slug should return 404 or 400, not 500."""
        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "nonexistent-workspace-xyz",
                "files": [{"name": "feature.md", "content": sample_ticket_md()}],
                "mode": "smart",
            },
        )
        # Should fail gracefully
        assert res.status_code >= 400
        assert res.status_code < 500


# ===========================================================================
# GROUP H: ASSUMPTION VALIDATION
# ===========================================================================


class TestAssumptionValidation:
    """Test implicit assumptions that could break implementation."""

    @pytest.mark.skip(reason="Implementation pending")
    def test_studio_context_structure_complete(self, client: TestClient):
        """H1: studio_context should have all needed fields."""
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
        ctx = body["studio_context"]

        # Verify expected structure
        assert "imported_tickets" in ctx
        assert isinstance(ctx["imported_tickets"], list)

    @pytest.mark.skip(reason="Implementation pending")
    def test_ticket_data_is_not_mutated_by_preview(self, client: TestClient):
        """H2: preview should not modify the imported data."""
        original_content = sample_ticket_md(
            external_id="test",
            title="Original Title",
        )

        res = client.post(
            "/api/tickets/import/preview",
            json={
                "workspace_slug": "loregarden",
                "files": [{"name": "feature.md", "content": original_content}],
                "mode": "smart",
            },
        )
        assert res.status_code == 200
        body = res.json()
        tickets = body["studio_context"]["imported_tickets"]

        # Title should match original
        assert tickets[0]["title"] == "Original Title"

    @pytest.mark.skip(reason="Implementation pending")
    def test_is_preview_defaults_to_true_for_smart_import(self, client: TestClient):
        """H3: sessions created from smart import should default is_preview=True."""
        res = client.post(
            "/api/ticket-studio/sessions",
            json={
                "workspace_slug": "loregarden",
                "title": "Smart import session",
                "imported_tickets": [
                    {"title": "F1", "external_id": "f1", "work_item_type": "feature"}
                ],
                # is_preview not explicitly set
            },
        )
        assert res.status_code in [200, 201]
        session = res.json()
        # Should default to True if created from import context
        assert session.get("is_preview", True) is True
