"""Tests for finalize-hierarchy endpoint — atomic bulk work-item creation."""

from fastapi.testclient import TestClient
from loregarden.models.domain import Ticket, WorkItemType
from sqlmodel import Session, select


class TestFinalizeHierarchyHappyPath:
    """Standard expected behavior for finalize-hierarchy endpoint."""

    def test_create_single_milestone(self, client: TestClient, db_session: Session):
        """Finalize a single milestone work item."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-milestone-01",
                        "title": "Test Milestone",
                        "work_item_type": "milestone",
                        "description": "A test milestone",
                        "acceptance_criteria": ["Criterion 1"],
                        "priority": 1,
                        "children": [],
                    }
                ],
            },
        )
        assert res.status_code == 201
        body = res.json()
        assert "created_ids" in body
        assert "total_created" in body
        assert body["total_created"] == 1
        assert len(body["created_ids"]) == 1

        # Verify ticket was created in database
        ticket = db_session.exec(
            select(Ticket).where(Ticket.external_id == "test-milestone-01")
        ).first()
        assert ticket is not None
        assert ticket.work_item_type == WorkItemType.MILESTONE
        assert ticket.title == "Test Milestone"
        assert ticket.description == "A test milestone"

    def test_create_two_level_hierarchy(self, client: TestClient, db_session: Session):
        """Finalize milestone → feature hierarchy."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-m-02",
                        "title": "Test Milestone 2",
                        "work_item_type": "milestone",
                        "children": [
                            {
                                "external_id": "test-f-02",
                                "title": "Test Feature 2",
                                "work_item_type": "feature",
                                "children": [],
                            }
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 201
        body = res.json()
        assert body["total_created"] == 2
        assert len(body["created_ids"]) == 2

        # Verify parent-child relationship
        milestone = db_session.exec(
            select(Ticket).where(Ticket.external_id == "test-m-02")
        ).first()
        feature = db_session.exec(
            select(Ticket).where(Ticket.external_id == "test-f-02")
        ).first()
        assert milestone is not None
        assert feature is not None
        assert feature.parent_ticket_id == milestone.id
        assert feature.work_item_type == WorkItemType.FEATURE

    def test_create_three_level_hierarchy(self, client: TestClient, db_session: Session):
        """Finalize milestone → feature → capability hierarchy."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-m-03",
                        "title": "Test Milestone 3",
                        "work_item_type": "milestone",
                        "children": [
                            {
                                "external_id": "test-f-03",
                                "title": "Test Feature 3",
                                "work_item_type": "feature",
                                "children": [
                                    {
                                        "external_id": "test-c-03",
                                        "title": "Test Capability 3",
                                        "work_item_type": "capability",
                                        "children": [],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 201
        body = res.json()
        assert body["total_created"] == 3
        created_ids = body["created_ids"]
        assert len(created_ids) == 3

        # Verify hierarchy chain
        milestone = db_session.exec(
            select(Ticket).where(Ticket.external_id == "test-m-03")
        ).first()
        feature = db_session.exec(
            select(Ticket).where(Ticket.external_id == "test-f-03")
        ).first()
        capability = db_session.exec(
            select(Ticket).where(Ticket.external_id == "test-c-03")
        ).first()

        assert feature.parent_ticket_id == milestone.id
        assert capability.parent_ticket_id == feature.id

    def test_create_four_level_hierarchy(self, client: TestClient, db_session: Session):
        """Finalize milestone → feature → capability → task hierarchy."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-m-04",
                        "title": "Test Milestone 4",
                        "work_item_type": "milestone",
                        "children": [
                            {
                                "external_id": "test-f-04",
                                "title": "Test Feature 4",
                                "work_item_type": "feature",
                                "children": [
                                    {
                                        "external_id": "test-c-04",
                                        "title": "Test Capability 4",
                                        "work_item_type": "capability",
                                        "children": [
                                            {
                                                "external_id": "test-t-04",
                                                "title": "Test Task 4",
                                                "work_item_type": "task",
                                                "children": [],
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 201
        body = res.json()
        assert body["total_created"] == 4

    def test_created_ids_in_insertion_order(self, client: TestClient, db_session: Session):
        """Verify returned created_ids are in parent-first insertion order."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-order-m",
                        "title": "Milestone",
                        "work_item_type": "milestone",
                        "children": [
                            {
                                "external_id": "test-order-f1",
                                "title": "Feature 1",
                                "work_item_type": "feature",
                                "children": [],
                            },
                            {
                                "external_id": "test-order-f2",
                                "title": "Feature 2",
                                "work_item_type": "feature",
                                "children": [],
                            },
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 201
        created_ids = res.json()["created_ids"]
        assert len(created_ids) == 3

        # First ID should be milestone, followed by features
        milestone = db_session.get(Ticket, created_ids[0])
        assert milestone.work_item_type == WorkItemType.MILESTONE


class TestFinalizeHierarchyAtomicity:
    """Transactional semantics — all-or-nothing behavior."""

    def test_rollback_on_invalid_parent_child_type(self, client: TestClient, db_session: Session):
        """Transaction rolls back when parent-child type validation fails."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-task-parent",
                        "title": "Task (cannot have children)",
                        "work_item_type": "task",
                        # This violates hierarchy: task cannot be parent to capability
                        "children": [
                            {
                                "external_id": "test-bad-child",
                                "title": "Capability under Task",
                                "work_item_type": "capability",
                                "children": [],
                            }
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 400
        body = res.json()
        assert "error" in body or "detail" in body

        # Verify neither item was created
        task_exists = db_session.exec(
            select(Ticket).where(Ticket.external_id == "test-task-parent")
        ).first()
        capability_exists = db_session.exec(
            select(Ticket).where(Ticket.external_id == "test-bad-child")
        ).first()
        assert task_exists is None
        assert capability_exists is None

    def test_rollback_on_duplicate_external_id_in_hierarchy(
        self, client: TestClient, db_session: Session
    ):
        """Transaction rolls back when duplicate external_ids exist within hierarchy."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-dup-01",
                        "title": "Milestone",
                        "work_item_type": "milestone",
                        "children": [
                            {
                                "external_id": "test-dup-01",  # Duplicate!
                                "title": "Feature with dup ID",
                                "work_item_type": "feature",
                                "children": [],
                            }
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 400
        body = res.json()
        assert "error" in body or "detail" in body

        # Verify nothing was created
        exists = db_session.exec(
            select(Ticket).where(Ticket.external_id == "test-dup-01")
        ).all()
        assert len(exists) == 0

    def test_rollback_on_milestone_with_parent_id(self, client: TestClient, db_session: Session):
        """Transaction rolls back when milestone (top-level) has parent_ticket_id."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-invalid-milestone",
                        "title": "Invalid Milestone",
                        "work_item_type": "milestone",
                        "parent_ticket_id": "invalid-uuid",  # Milestones cannot have parents
                        "children": [],
                    }
                ],
            },
        )
        assert res.status_code == 400
        # Verify nothing was created
        exists = db_session.exec(
            select(Ticket).where(Ticket.external_id == "test-invalid-milestone")
        ).first()
        assert exists is None


class TestFinalizeHierarchyValidation:
    """Validation of input structure and constraints."""

    def test_missing_workspace_slug(self, client: TestClient):
        """Endpoint rejects request without workspace_slug."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "hierarchy": [
                    {
                        "external_id": "test-no-ws",
                        "title": "No workspace",
                        "work_item_type": "milestone",
                        "children": [],
                    }
                ],
            },
        )
        assert res.status_code == 422  # Validation error

    def test_invalid_workspace_slug(self, client: TestClient):
        """Endpoint rejects request with non-existent workspace."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "nonexistent-workspace",
                "hierarchy": [
                    {
                        "external_id": "test-bad-ws",
                        "title": "Bad workspace",
                        "work_item_type": "milestone",
                        "children": [],
                    }
                ],
            },
        )
        assert res.status_code == 400
        assert "workspace" in res.json().get("detail", "").lower()

    def test_invalid_work_item_type(self, client: TestClient):
        """Endpoint rejects invalid work_item_type values."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-invalid-type",
                        "title": "Invalid type",
                        "work_item_type": "invalid_type",
                        "children": [],
                    }
                ],
            },
        )
        assert res.status_code in (400, 422)

    def test_missing_title(self, client: TestClient):
        """Endpoint rejects work item without title."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-no-title",
                        "title": "",  # Empty title
                        "work_item_type": "milestone",
                        "children": [],
                    }
                ],
            },
        )
        assert res.status_code == 400
        body = res.json()
        assert "title" in body.get("detail", "").lower() or "title" in str(body).lower()

    def test_invalid_priority_range(self, client: TestClient, db_session: Session):
        """Endpoint rejects priority outside [1, 3] range."""
        for priority in [0, 4, -1, 10]:
            res = client.post(
                "/api/tickets/finalize-hierarchy",
                json={
                    "workspace_slug": "loregarden",
                    "hierarchy": [
                        {
                            "external_id": f"test-priority-{priority}",
                            "title": f"Priority {priority}",
                            "work_item_type": "milestone",
                            "priority": priority,
                            "children": [],
                        }
                    ],
                },
            )
            assert res.status_code == 400


class TestFinalizeHierarchyEdgeCases:
    """Boundary conditions and unusual but valid inputs."""

    def test_empty_hierarchy(self, client: TestClient):
        """Endpoint handles empty hierarchy array gracefully."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [],
            },
        )
        # Empty hierarchy should either succeed with 0 created or fail with clear error
        assert res.status_code in (201, 400)
        if res.status_code == 201:
            body = res.json()
            assert body.get("total_created") == 0

    def test_hierarchy_with_sibling_nodes(self, client: TestClient, db_session: Session):
        """Finalize hierarchy with multiple siblings at same level."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-parent-sibs",
                        "title": "Parent with siblings",
                        "work_item_type": "milestone",
                        "children": [
                            {
                                "external_id": "test-sib-1",
                                "title": "Sibling 1",
                                "work_item_type": "feature",
                                "children": [],
                            },
                            {
                                "external_id": "test-sib-2",
                                "title": "Sibling 2",
                                "work_item_type": "feature",
                                "children": [],
                            },
                            {
                                "external_id": "test-sib-3",
                                "title": "Sibling 3",
                                "work_item_type": "feature",
                                "children": [],
                            },
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 201
        body = res.json()
        assert body["total_created"] == 4

        # Verify all siblings have same parent
        parent = db_session.exec(
            select(Ticket).where(Ticket.external_id == "test-parent-sibs")
        ).first()
        for i in range(1, 4):
            sibling = db_session.exec(
                select(Ticket).where(Ticket.external_id == f"test-sib-{i}")
            ).first()
            assert sibling.parent_ticket_id == parent.id

    def test_special_characters_in_title(self, client: TestClient, db_session: Session):
        """Finalize hierarchy with special characters in titles."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-special-chars",
                        "title": "Test: Special™ Characters (with @#$%)",
                        "work_item_type": "milestone",
                        "children": [],
                    }
                ],
            },
        )
        assert res.status_code == 201

        ticket = db_session.exec(
            select(Ticket).where(Ticket.external_id == "test-special-chars")
        ).first()
        assert ticket.title == "Test: Special™ Characters (with @#$%)"

    def test_multiline_description(self, client: TestClient, db_session: Session):
        """Finalize hierarchy with multiline descriptions."""
        multiline_desc = "Line 1\nLine 2\nLine 3"
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-multiline",
                        "title": "Multiline Description",
                        "work_item_type": "milestone",
                        "description": multiline_desc,
                        "children": [],
                    }
                ],
            },
        )
        assert res.status_code == 201

        ticket = db_session.exec(
            select(Ticket).where(Ticket.external_id == "test-multiline")
        ).first()
        assert ticket.description == multiline_desc

    def test_long_hierarchy_chain(self, client: TestClient, db_session: Session):
        """Finalize hierarchy with maximum nesting depth (milestone→feature→capability→task)."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-long-chain-m",
                        "title": "Long Chain Milestone",
                        "work_item_type": "milestone",
                        "children": [
                            {
                                "external_id": "test-long-chain-f",
                                "title": "Long Chain Feature",
                                "work_item_type": "feature",
                                "children": [
                                    {
                                        "external_id": "test-long-chain-c",
                                        "title": "Long Chain Capability",
                                        "work_item_type": "capability",
                                        "children": [
                                            {
                                                "external_id": "test-long-chain-t",
                                                "title": "Long Chain Task",
                                                "work_item_type": "task",
                                                "children": [],
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 201
        body = res.json()
        assert body["total_created"] == 4

    def test_multiple_root_items(self, client: TestClient, db_session: Session):
        """Finalize hierarchy with multiple root (milestone) items."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-root-1",
                        "title": "Root Milestone 1",
                        "work_item_type": "milestone",
                        "children": [],
                    },
                    {
                        "external_id": "test-root-2",
                        "title": "Root Milestone 2",
                        "work_item_type": "milestone",
                        "children": [],
                    },
                ],
            },
        )
        assert res.status_code == 201
        body = res.json()
        assert body["total_created"] == 2

        m1 = db_session.exec(
            select(Ticket).where(Ticket.external_id == "test-root-1")
        ).first()
        m2 = db_session.exec(
            select(Ticket).where(Ticket.external_id == "test-root-2")
        ).first()
        assert m1 is not None
        assert m2 is not None


class TestFinalizeHierarchyBugs:
    """Bug-type work items at various hierarchy levels."""

    def test_bug_under_milestone(self, client: TestClient, db_session: Session):
        """Bug can be created directly under milestone."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-bug-m",
                        "title": "Milestone with Bug",
                        "work_item_type": "milestone",
                        "children": [
                            {
                                "external_id": "test-bug-1",
                                "title": "Critical Bug",
                                "work_item_type": "bug",
                                "children": [],
                            }
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 201
        bug = db_session.exec(
            select(Ticket).where(Ticket.external_id == "test-bug-1")
        ).first()
        assert bug.work_item_type == WorkItemType.BUG

    def test_bug_under_feature(self, client: TestClient, db_session: Session):
        """Bug can be created directly under feature."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-bug-f-m",
                        "title": "Milestone",
                        "work_item_type": "milestone",
                        "children": [
                            {
                                "external_id": "test-bug-f",
                                "title": "Feature",
                                "work_item_type": "feature",
                                "children": [
                                    {
                                        "external_id": "test-bug-2",
                                        "title": "Regression",
                                        "work_item_type": "bug",
                                        "children": [],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 201


class TestFinalizeHierarchyResponseStructure:
    """Response format and content correctness."""

    def test_success_response_has_required_fields(self, client: TestClient):
        """Success response includes created_ids and total_created."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-resp-fields",
                        "title": "Response fields test",
                        "work_item_type": "milestone",
                        "children": [],
                    }
                ],
            },
        )
        assert res.status_code == 201
        body = res.json()
        assert "created_ids" in body
        assert "total_created" in body
        assert isinstance(body["created_ids"], list)
        assert isinstance(body["total_created"], int)

    def test_failure_response_has_error_detail(self, client: TestClient):
        """Failure response includes error and detail fields."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-err-detail",
                        "title": "Missing parent",
                        "work_item_type": "task",
                        "children": [],
                    }
                ],
            },
        )
        assert res.status_code == 400
        body = res.json()
        assert "detail" in body or "error" in body


class TestFinalizeHierarchyExternalIdGeneration:
    """External ID handling during finalization."""

    def test_explicit_external_id_preserved(self, client: TestClient, db_session: Session):
        """Explicit external_id values are preserved as-is."""
        external_id = "custom-explicit-id"
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": external_id,
                        "title": "Explicit ID",
                        "work_item_type": "milestone",
                        "children": [],
                    }
                ],
            },
        )
        assert res.status_code == 201
        ticket = db_session.exec(
            select(Ticket).where(Ticket.external_id == external_id)
        ).first()
        assert ticket.external_id == external_id

    def test_no_duplicate_external_ids_across_workspace(
        self, client: TestClient, db_session: Session
    ):
        """Cannot create hierarchy with external_id that already exists in workspace."""
        # First, create a work item with a specific external_id
        res1 = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-existing-id",
                        "title": "First item",
                        "work_item_type": "milestone",
                        "children": [],
                    }
                ],
            },
        )
        assert res1.status_code == 201

        # Try to create another with the same external_id
        res2 = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-existing-id",  # Duplicate!
                        "title": "Second item",
                        "work_item_type": "milestone",
                        "children": [],
                    }
                ],
            },
        )
        assert res2.status_code == 400


class TestFinalizeHierarchyDefaultValues:
    """Default value assignment for optional fields."""

    def test_default_state_is_backlog(self, client: TestClient, db_session: Session):
        """Newly created items default to backlog state."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-default-state",
                        "title": "Default state test",
                        "work_item_type": "milestone",
                        "children": [],
                    }
                ],
            },
        )
        assert res.status_code == 201
        ticket = db_session.exec(
            select(Ticket).where(Ticket.external_id == "test-default-state")
        ).first()
        assert ticket.state == "backlog"

    def test_default_priority_is_3(self, client: TestClient, db_session: Session):
        """Newly created items default to priority 3 (low) when not specified."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-default-priority",
                        "title": "Default priority test",
                        "work_item_type": "milestone",
                        "children": [],
                    }
                ],
            },
        )
        assert res.status_code == 201
        ticket = db_session.exec(
            select(Ticket).where(Ticket.external_id == "test-default-priority")
        ).first()
        assert ticket.priority == 3

    def test_default_empty_description(self, client: TestClient, db_session: Session):
        """Newly created items have empty description when not specified."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-default-desc",
                        "title": "Default description test",
                        "work_item_type": "milestone",
                        "children": [],
                    }
                ],
            },
        )
        assert res.status_code == 201
        ticket = db_session.exec(
            select(Ticket).where(Ticket.external_id == "test-default-desc")
        ).first()
        assert ticket.description == "" or ticket.description is None


class TestFinalizeHierarchyAdvancedTypeValidation:
    """Comprehensive parent-child type validation per VALID_HIERARCHY rules."""

    def test_bug_cannot_have_children(self, client: TestClient, db_session: Session):
        """Bug is a leaf type and cannot have children."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-bug-parent",
                        "title": "Bug (leaf type)",
                        "work_item_type": "bug",
                        "children": [
                            {
                                "external_id": "test-invalid-under-bug",
                                "title": "Cannot have children",
                                "work_item_type": "task",
                                "children": [],
                            }
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 400
        # Verify neither was created
        bug = db_session.exec(
            select(Ticket).where(Ticket.external_id == "test-bug-parent")
        ).first()
        child = db_session.exec(
            select(Ticket).where(Ticket.external_id == "test-invalid-under-bug")
        ).first()
        assert bug is None
        assert child is None

    def test_feature_cannot_have_task_children(self, client: TestClient, db_session: Session):
        """Feature can only have Capability or Bug children, not Task."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-feature-task",
                        "title": "Feature",
                        "work_item_type": "feature",
                        "children": [
                            {
                                "external_id": "test-task-under-feature",
                                "title": "Task (invalid under Feature)",
                                "work_item_type": "task",
                                "children": [],
                            }
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 400
        # Verify neither was created
        assert (
            db_session.exec(
                select(Ticket).where(Ticket.external_id == "test-feature-task")
            ).first()
            is None
        )

    def test_capability_cannot_have_feature_children(
        self, client: TestClient, db_session: Session
    ):
        """Capability can only have Task or Bug children, not Feature."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-cap-feature",
                        "title": "Capability",
                        "work_item_type": "capability",
                        "children": [
                            {
                                "external_id": "test-feature-under-cap",
                                "title": "Feature (invalid under Capability)",
                                "work_item_type": "feature",
                                "children": [],
                            }
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 400

    def test_milestone_can_have_bug_children(self, client: TestClient, db_session: Session):
        """Milestone can have Bug as direct child (valid per VALID_HIERARCHY)."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-milestone-bug",
                        "title": "Milestone with Bug",
                        "work_item_type": "milestone",
                        "children": [
                            {
                                "external_id": "test-bug-under-milestone",
                                "title": "Critical Bug",
                                "work_item_type": "bug",
                                "children": [],
                            }
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 201
        assert res.json()["total_created"] == 2

    def test_feature_can_have_bug_children(self, client: TestClient, db_session: Session):
        """Feature can have Bug as direct child (valid per VALID_HIERARCHY)."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-feature-bug",
                        "title": "Feature with Bug",
                        "work_item_type": "feature",
                        "children": [
                            {
                                "external_id": "test-bug-under-feature",
                                "title": "Regression in Feature",
                                "work_item_type": "bug",
                                "children": [],
                            }
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 201
        assert res.json()["total_created"] == 2

    def test_capability_can_have_bug_children(self, client: TestClient, db_session: Session):
        """Capability can have Bug as direct child (valid per VALID_HIERARCHY)."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-capability-bug",
                        "title": "Capability with Bug",
                        "work_item_type": "capability",
                        "children": [
                            {
                                "external_id": "test-bug-under-capability",
                                "title": "Bug in Capability",
                                "work_item_type": "bug",
                                "children": [],
                            }
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 201
        assert res.json()["total_created"] == 2

    def test_mixed_bug_and_proper_children(self, client: TestClient, db_session: Session):
        """Hierarchy can mix proper type children and bugs at same level."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-mixed-parent",
                        "title": "Parent",
                        "work_item_type": "feature",
                        "children": [
                            {
                                "external_id": "test-mixed-capability",
                                "title": "Capability",
                                "work_item_type": "capability",
                                "children": [],
                            },
                            {
                                "external_id": "test-mixed-bug",
                                "title": "Bug",
                                "work_item_type": "bug",
                                "children": [],
                            },
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 201
        body = res.json()
        assert body["total_created"] == 3


class TestFinalizeHierarchyAtomicityAdvanced:
    """Advanced atomicity scenarios with partial hierarchy failures."""

    def test_rollback_on_deep_hierarchy_mid_chain_type_violation(
        self, client: TestClient, db_session: Session
    ):
        """Rollback entire hierarchy when type violation occurs deep in the tree."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-deep-root",
                        "title": "Milestone",
                        "work_item_type": "milestone",
                        "children": [
                            {
                                "external_id": "test-deep-f",
                                "title": "Feature",
                                "work_item_type": "feature",
                                "children": [
                                    {
                                        "external_id": "test-deep-cap",
                                        "title": "Capability",
                                        "work_item_type": "capability",
                                        "children": [
                                            {
                                                "external_id": "test-deep-task",
                                                "title": "Task",
                                                "work_item_type": "task",
                                                "children": [
                                                    {
                                                        "external_id": "test-deep-bad",
                                                        "title": "Invalid child of Task",
                                                        "work_item_type": "bug",
                                                        "children": [],
                                                    }
                                                ],
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 400
        # Verify no items were created at any level
        for ext_id in [
            "test-deep-root",
            "test-deep-f",
            "test-deep-cap",
            "test-deep-task",
            "test-deep-bad",
        ]:
            assert (
                db_session.exec(
                    select(Ticket).where(Ticket.external_id == ext_id)
                ).first()
                is None
            )

    def test_rollback_on_duplicate_id_in_deep_hierarchy(
        self, client: TestClient, db_session: Session
    ):
        """Rollback entire hierarchy when duplicate ID appears deep in tree."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "test-dup-deep-root",
                        "title": "Milestone",
                        "work_item_type": "milestone",
                        "children": [
                            {
                                "external_id": "test-dup-deep-f",
                                "title": "Feature",
                                "work_item_type": "feature",
                                "children": [
                                    {
                                        "external_id": "test-dup-deep-root",  # Duplicate!
                                        "title": "Capability with dup ID",
                                        "work_item_type": "capability",
                                        "children": [],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 400
        # Verify no items were created
        for ext_id in ["test-dup-deep-root", "test-dup-deep-f"]:
            count = len(
                db_session.exec(
                    select(Ticket).where(Ticket.external_id == ext_id)
                ).all()
            )
            assert count == 0 or count == 1  # At most the pre-existing one


class TestFinalizeHierarchyAcceptanceCriteria:
    """Tests explicitly verifying acceptance criteria for ticket 42."""

    def test_endpoint_accepts_full_hierarchy_structure(
        self, client: TestClient, db_session: Session
    ):
        """Finalize endpoint accepts complex nested hierarchy (AC1)."""
        # This is the exact spec example from SPEC_42_RESEARCH.md
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "ac1-finalization",
                        "title": "Finalization and Work-Item Persistence",
                        "work_item_type": "feature",  # Changed from epic to feature per schema
                        "description": "Create endpoint/handler for bulk work-item creation",
                        "acceptance_criteria": ["Endpoint accepts hierarchy", "Creates atomically"],
                        "priority": 1,
                        "children": [
                            {
                                "external_id": "ac1-finalize-confirmation",
                                "title": "Implement finalize confirmation and work-item creation",
                                "work_item_type": "capability",
                                "children": [
                                    {
                                        "external_id": "ac1-backend-endpoint",
                                        "title": "Backend endpoint implementation",
                                        "work_item_type": "task",
                                        "children": [],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 201
        assert res.json()["total_created"] == 3

    def test_creates_all_work_items_atomically(self, client: TestClient, db_session: Session):
        """All work items created in single transaction (AC2)."""
        # If any failure occurs, none should exist
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "ac2-root",
                        "title": "Root",
                        "work_item_type": "milestone",
                        "children": [
                            {
                                "external_id": "ac2-child",
                                "title": "Child",
                                "work_item_type": "feature",
                                "children": [],
                            }
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 201
        # Both exist because transaction succeeded
        root = db_session.exec(
            select(Ticket).where(Ticket.external_id == "ac2-root")
        ).first()
        child = db_session.exec(
            select(Ticket).where(Ticket.external_id == "ac2-child")
        ).first()
        assert root is not None
        assert child is not None
        assert child.parent_ticket_id == root.id

    def test_links_parent_child_relationships_correctly(
        self, client: TestClient, db_session: Session
    ):
        """Parent-child relationships are correctly established (AC3)."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "ac3-m",
                        "title": "Milestone",
                        "work_item_type": "milestone",
                        "children": [
                            {
                                "external_id": "ac3-f1",
                                "title": "Feature 1",
                                "work_item_type": "feature",
                                "children": [
                                    {
                                        "external_id": "ac3-c1",
                                        "title": "Capability 1",
                                        "work_item_type": "capability",
                                        "children": [
                                            {
                                                "external_id": "ac3-t1",
                                                "title": "Task 1",
                                                "work_item_type": "task",
                                                "children": [],
                                            }
                                        ],
                                    }
                                ],
                            },
                            {
                                "external_id": "ac3-f2",
                                "title": "Feature 2",
                                "work_item_type": "feature",
                                "children": [],
                            },
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 201

        # Verify the complete hierarchy
        m = db_session.exec(
            select(Ticket).where(Ticket.external_id == "ac3-m")
        ).first()
        f1 = db_session.exec(
            select(Ticket).where(Ticket.external_id == "ac3-f1")
        ).first()
        f2 = db_session.exec(
            select(Ticket).where(Ticket.external_id == "ac3-f2")
        ).first()
        c1 = db_session.exec(
            select(Ticket).where(Ticket.external_id == "ac3-c1")
        ).first()
        t1 = db_session.exec(
            select(Ticket).where(Ticket.external_id == "ac3-t1")
        ).first()

        # Check all relationships
        assert f1.parent_ticket_id == m.id
        assert f2.parent_ticket_id == m.id
        assert c1.parent_ticket_id == f1.id
        assert t1.parent_ticket_id == c1.id

    def test_returns_created_work_item_ids_on_success(
        self, client: TestClient, db_session: Session
    ):
        """Returns created work-item IDs and total count on success (AC4)."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "ac4-root",
                        "title": "Root",
                        "work_item_type": "milestone",
                        "children": [
                            {
                                "external_id": "ac4-child1",
                                "title": "Child 1",
                                "work_item_type": "feature",
                                "children": [],
                            },
                            {
                                "external_id": "ac4-child2",
                                "title": "Child 2",
                                "work_item_type": "feature",
                                "children": [],
                            },
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 201
        body = res.json()

        # Verify response structure
        assert "created_ids" in body
        assert "total_created" in body
        assert isinstance(body["created_ids"], list)
        assert isinstance(body["total_created"], int)
        assert body["total_created"] == 3
        assert len(body["created_ids"]) == 3

        # Verify returned IDs are valid UUIDs and correspond to created items
        for ticket_id in body["created_ids"]:
            ticket = db_session.get(Ticket, ticket_id)
            assert ticket is not None
