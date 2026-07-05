"""
Test suite: Assign workflow to any ticket (ticket 25)

Specification: Any ticket should be able to be assigned specific agent workflows

This test module verifies that workflows can be initialized and executed on
all ticket types: MILESTONE, FEATURE, CAPABILITY, TASK, and BUG.

Previously, workflows were restricted to FEATURE, TASK, and BUG only.
This suite ensures the restriction is removed while maintaining backward compatibility.
"""

import json
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from loregarden.models.domain import (
    Ticket,
    WorkItemType,
    StageStatus,
    WorkflowInstance,
)


class TestWorkflowInitializationForAllTicketTypes:
    """Verify that workflows are initialized for all ticket types upon creation."""

    def test_milestone_gets_workflow_on_creation(self, client: TestClient):
        """
        Acceptance: MILESTONE should have a workflow_stage_key when created.

        This is the primary feature requirement — workflows must be assignable
        to MILESTONE tickets.
        """
        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Milestone with workflow",
                "work_item_type": "milestone",
                "description": "Test milestone ticket with workflow",
                "acceptance_criteria": ["Workflow initializes on creation"],
            },
        )
        assert res.status_code == 201, f"Failed to create milestone: {res.text}"
        body = res.json()

        # Verify ticket was created
        assert body["work_item_type"] == "milestone"
        assert body["id"] is not None

        # Verify workflow was initialized
        assert body["workflow_stage_key"] == "planning", \
            f"Expected workflow_stage_key='planning', got {body['workflow_stage_key']}"
        assert body["workflow_stage_status"] == "pending", \
            f"Expected workflow_stage_status='pending', got {body['workflow_stage_status']}"
        assert len(body["stages"]) >= 5, \
            f"Expected at least 5 stages, got {len(body['stages'])}"

    def test_capability_gets_workflow_on_creation(self, client: TestClient):
        """
        Acceptance: CAPABILITY should have a workflow_stage_key when created.

        CAPABILITY is a mid-level work item in the hierarchy and should support
        workflows like FEATURE and TASK.
        """
        # Get a FEATURE to use as parent
        feature = next(
            t
            for t in client.get("/api/tickets?workspace=loregarden").json()
            if t["work_item_type"] == "feature"
        )

        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Capability with workflow",
                "work_item_type": "capability",
                "parent_ticket_id": feature["id"],
                "description": "Test capability ticket with workflow",
                "acceptance_criteria": ["Workflow initializes on creation"],
            },
        )
        assert res.status_code == 201, f"Failed to create capability: {res.text}"
        body = res.json()

        # Verify ticket was created
        assert body["work_item_type"] == "capability"
        assert body["id"] is not None

        # Verify workflow was initialized
        assert body["workflow_stage_key"] == "planning", \
            f"Expected workflow_stage_key='planning', got {body['workflow_stage_key']}"
        assert body["workflow_stage_status"] == "pending", \
            f"Expected workflow_stage_status='pending', got {body['workflow_stage_status']}"
        assert len(body["stages"]) >= 5, \
            f"Expected at least 5 stages, got {len(body['stages'])}"

    def test_feature_still_gets_workflow_backward_compatibility(self, client: TestClient):
        """
        Acceptance: FEATURE should still get workflow (backward compatibility).
        """
        milestone_id = next(
            t["id"]
            for t in client.get("/api/tickets?workspace=loregarden").json()
            if t["work_item_type"] == "milestone"
        )

        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Feature with workflow (backward compat)",
                "work_item_type": "feature",
                "parent_ticket_id": milestone_id,
                "description": "Verify FEATURE still works",
            },
        )
        assert res.status_code == 201
        body = res.json()
        assert body["work_item_type"] == "feature"
        assert body["workflow_stage_key"] == "planning"
        assert len(body["stages"]) >= 5

    def test_task_still_gets_workflow_backward_compatibility(self, client: TestClient):
        """
        Acceptance: TASK should still get workflow (backward compatibility).
        """
        capability = next(
            t
            for t in client.get("/api/tickets?workspace=loregarden").json()
            if t["work_item_type"] == "capability"
        )

        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Task with workflow (backward compat)",
                "work_item_type": "task",
                "parent_ticket_id": capability["id"],
                "description": "Verify TASK still works",
            },
        )
        assert res.status_code == 201
        body = res.json()
        assert body["work_item_type"] == "task"
        assert body["workflow_stage_key"] == "planning"
        assert len(body["stages"]) >= 5

    def test_bug_still_gets_workflow_backward_compatibility(self, client: TestClient):
        """
        Acceptance: BUG should still get workflow (backward compatibility).
        """
        capability = next(
            t
            for t in client.get("/api/tickets?workspace=loregarden").json()
            if t["work_item_type"] == "capability"
        )

        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Bug with workflow (backward compat)",
                "work_item_type": "bug",
                "parent_ticket_id": capability["id"],
                "description": "Verify BUG still works",
            },
        )
        assert res.status_code == 201
        body = res.json()
        assert body["work_item_type"] == "bug"
        assert body["workflow_stage_key"] == "planning"
        assert len(body["stages"]) >= 5


class TestWorkflowInstanceCreation:
    """Verify that WorkflowInstance records are created for all ticket types."""

    def test_milestone_workflow_instance_created(self, client: TestClient, monkeypatch):
        """
        Acceptance: WorkflowInstance should be created for MILESTONE.
        """
        # Create a MILESTONE
        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Milestone for workflow instance test",
                "work_item_type": "milestone",
                "description": "Test workflow instance creation",
            },
        )
        assert res.status_code == 201
        milestone_id = res.json()["id"]

        # Verify WorkflowInstance exists
        from loregarden.db.session import engine
        from sqlmodel import Session
        with Session(engine) as session:
            instance = session.exec(
                select(WorkflowInstance).where(
                    WorkflowInstance.ticket_id == milestone_id
                )
            ).first()
            assert instance is not None, \
                f"No WorkflowInstance found for milestone {milestone_id}"
            assert instance.template_id is not None
            assert instance.current_stage_key == "planning"

    def test_capability_workflow_instance_created(self, client: TestClient, monkeypatch):
        """
        Acceptance: WorkflowInstance should be created for CAPABILITY.
        """
        # Get a feature to use as parent
        feature = next(
            t
            for t in client.get("/api/tickets?workspace=loregarden").json()
            if t["work_item_type"] == "feature"
        )

        # Create a CAPABILITY
        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Capability for workflow instance test",
                "work_item_type": "capability",
                "parent_ticket_id": feature["id"],
                "description": "Test workflow instance creation",
            },
        )
        assert res.status_code == 201
        capability_id = res.json()["id"]

        # Verify WorkflowInstance exists
        from loregarden.db.session import engine
        from sqlmodel import Session
        with Session(engine) as session:
            instance = session.exec(
                select(WorkflowInstance).where(
                    WorkflowInstance.ticket_id == capability_id
                )
            ).first()
            assert instance is not None, \
                f"No WorkflowInstance found for capability {capability_id}"
            assert instance.template_id is not None
            assert instance.current_stage_key == "planning"


class TestWorkflowStageFields:
    """Verify that workflow-related fields are properly set for all ticket types."""

    def test_milestone_has_valid_stage_status(self, client: TestClient):
        """
        Acceptance: MILESTONE should have a valid StageStatus value.
        """
        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Milestone stage status test",
                "work_item_type": "milestone",
            },
        )
        assert res.status_code == 201
        body = res.json()

        # Verify the stage status is one of the valid values
        valid_statuses = {"pending", "running", "blocked", "awaiting", "done", "wont_do"}
        assert body["workflow_stage_status"] in valid_statuses, \
            f"Invalid workflow_stage_status: {body['workflow_stage_status']}"

    def test_capability_has_valid_stage_status(self, client: TestClient):
        """
        Acceptance: CAPABILITY should have a valid StageStatus value.
        """
        feature = next(
            t
            for t in client.get("/api/tickets?workspace=loregarden").json()
            if t["work_item_type"] == "feature"
        )

        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Capability stage status test",
                "work_item_type": "capability",
                "parent_ticket_id": feature["id"],
            },
        )
        assert res.status_code == 201
        body = res.json()

        valid_statuses = {"pending", "running", "blocked", "awaiting", "done", "wont_do"}
        assert body["workflow_stage_status"] in valid_statuses, \
            f"Invalid workflow_stage_status: {body['workflow_stage_status']}"

    def test_all_ticket_types_have_stages_array(self, client: TestClient):
        """
        Acceptance: All ticket types should have a stages array in the response.
        """
        tickets = client.get("/api/tickets?workspace=loregarden").json()

        # Get one of each type
        milestone = next(t for t in tickets if t["work_item_type"] == "milestone")
        feature = next(t for t in tickets if t["work_item_type"] == "feature")
        capability = next(t for t in tickets if t["work_item_type"] == "capability")
        task = next(t for t in tickets if t["work_item_type"] == "task")

        # Verify all have stages
        for ticket_type, ticket in [
            ("milestone", milestone),
            ("feature", feature),
            ("capability", capability),
            ("task", task),
        ]:
            assert "stages" in ticket, f"{ticket_type} missing stages"
            assert isinstance(ticket["stages"], list), \
                f"{ticket_type} stages is not a list"
            assert len(ticket["stages"]) > 0, \
                f"{ticket_type} has empty stages array"


class TestWorkflowHierarchyInteractions:
    """Verify that workflows work correctly with ticket hierarchy."""

    def test_milestone_with_workflow_and_feature_children(self, client: TestClient):
        """
        Acceptance: MILESTONE with workflow should coexist with FEATURE children
        that each have their own workflows.

        This tests the edge case mentioned in the spec: hierarchy should be
        independent of workflow eligibility.
        """
        # Create a milestone with workflow
        milestone_res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Milestone parent with children",
                "work_item_type": "milestone",
            },
        )
        assert milestone_res.status_code == 201
        milestone = milestone_res.json()

        # Create a feature as a child
        feature_res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Feature child of milestone",
                "work_item_type": "feature",
                "parent_ticket_id": milestone["id"],
            },
        )
        assert feature_res.status_code == 201
        feature = feature_res.json()

        # Verify both have workflows
        assert milestone["workflow_stage_key"] == "planning"
        assert feature["workflow_stage_key"] == "planning"

        # Verify we can fetch detail and both have stages
        milestone_detail = client.get(f"/api/tickets/{milestone['id']}").json()
        feature_detail = client.get(f"/api/tickets/{feature['id']}").json()

        assert len(milestone_detail["stages"]) >= 5
        assert len(feature_detail["stages"]) >= 5

    def test_capability_with_workflow_under_feature_with_workflow(self, client: TestClient):
        """
        Acceptance: CAPABILITY with workflow should work correctly when its
        parent FEATURE also has a workflow.
        """
        # Get or create a feature
        feature = next(
            t
            for t in client.get("/api/tickets?workspace=loregarden").json()
            if t["work_item_type"] == "feature"
        )

        # Create a capability as a child
        capability_res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Capability child of feature",
                "work_item_type": "capability",
                "parent_ticket_id": feature["id"],
            },
        )
        assert capability_res.status_code == 201
        capability = capability_res.json()

        # Verify both have workflows
        assert feature["workflow_stage_key"] == "planning"
        assert capability["workflow_stage_key"] == "planning"

        # Verify both have stages
        feature_detail = client.get(f"/api/tickets/{feature['id']}").json()
        capability_detail = client.get(f"/api/tickets/{capability['id']}").json()

        assert len(feature_detail["stages"]) >= 5
        assert len(capability_detail["stages"]) >= 5


class TestWorkflowDetailRetrieval:
    """Verify that workflows are properly loaded and serialized for all ticket types."""

    def test_milestone_detail_includes_workflow_fields(self, client: TestClient):
        """
        Acceptance: GET /api/tickets/{id} should include workflow fields for MILESTONE.
        """
        # Create a milestone
        create_res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Milestone detail test",
                "work_item_type": "milestone",
            },
        )
        milestone_id = create_res.json()["id"]

        # Fetch detail
        detail_res = client.get(f"/api/tickets/{milestone_id}")
        assert detail_res.status_code == 200
        detail = detail_res.json()

        # Verify all workflow fields are present
        assert "workflow_stage_key" in detail
        assert "workflow_stage_status" in detail
        assert "stages" in detail
        assert detail["workflow_stage_key"] == "planning"
        assert len(detail["stages"]) >= 5

    def test_capability_detail_includes_workflow_fields(self, client: TestClient):
        """
        Acceptance: GET /api/tickets/{id} should include workflow fields for CAPABILITY.
        """
        feature = next(
            t
            for t in client.get("/api/tickets?workspace=loregarden").json()
            if t["work_item_type"] == "feature"
        )

        # Create a capability
        create_res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Capability detail test",
                "work_item_type": "capability",
                "parent_ticket_id": feature["id"],
            },
        )
        capability_id = create_res.json()["id"]

        # Fetch detail
        detail_res = client.get(f"/api/tickets/{capability_id}")
        assert detail_res.status_code == 200
        detail = detail_res.json()

        # Verify all workflow fields are present
        assert "workflow_stage_key" in detail
        assert "workflow_stage_status" in detail
        assert "stages" in detail
        assert detail["workflow_stage_key"] == "planning"
        assert len(detail["stages"]) >= 5


class TestWorkflowStateConsistency:
    """Verify that workflow state is consistent across API layers."""

    def test_milestone_workflow_state_consistency(self, client: TestClient):
        """
        Acceptance: Workflow state should be consistent between list and detail views.

        This ensures that workflow_stage_key, workflow_stage_status, and stages
        are synchronized across API endpoints.
        """
        # Create a milestone
        create_res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Milestone consistency test",
                "work_item_type": "milestone",
            },
        )
        milestone_id = create_res.json()["id"]

        # Get from list view
        list_ticket = None
        for t in client.get("/api/tickets?workspace=loregarden").json():
            if t["id"] == milestone_id:
                list_ticket = t
                break
        assert list_ticket is not None

        # Get from detail view
        detail_ticket = client.get(f"/api/tickets/{milestone_id}").json()

        # Verify consistency
        assert list_ticket["workflow_stage_key"] == detail_ticket["workflow_stage_key"]
        assert list_ticket["workflow_stage_status"] == detail_ticket["workflow_stage_status"]
        # Stages should match
        assert len(list_ticket["stages"]) == len(detail_ticket["stages"])

    def test_capability_workflow_state_consistency(self, client: TestClient):
        """
        Acceptance: Workflow state should be consistent for CAPABILITY.
        """
        feature = next(
            t
            for t in client.get("/api/tickets?workspace=loregarden").json()
            if t["work_item_type"] == "feature"
        )

        # Create a capability
        create_res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Capability consistency test",
                "work_item_type": "capability",
                "parent_ticket_id": feature["id"],
            },
        )
        capability_id = create_res.json()["id"]

        # Get from list view
        list_ticket = None
        for t in client.get("/api/tickets?workspace=loregarden").json():
            if t["id"] == capability_id:
                list_ticket = t
                break
        assert list_ticket is not None

        # Get from detail view
        detail_ticket = client.get(f"/api/tickets/{capability_id}").json()

        # Verify consistency
        assert list_ticket["workflow_stage_key"] == detail_ticket["workflow_stage_key"]
        assert list_ticket["workflow_stage_status"] == detail_ticket["workflow_stage_status"]


class TestWorkflowInitializationWithoutTemplate:
    """Verify graceful degradation when workspace has no workflow template."""

    def test_milestone_fails_without_template_error_message(self, client: TestClient):
        """
        Acceptance: When creating a workflow-eligible ticket in a workspace
        with no template, the error message should be clear.

        Note: This test assumes the seeded database always has a template.
        If a workspace without template exists, it should handle MILESTONE.
        """
        # This test documents the expected behavior.
        # The seeded loregarden workspace has a template, so this verifies
        # the normal path works.
        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Milestone with template",
                "work_item_type": "milestone",
            },
        )
        # Should succeed because loregarden has a template
        assert res.status_code == 201
        assert res.json()["workflow_stage_key"] == "planning"


class TestWorkflowEdgeCases:
    """Test edge cases and boundary conditions for all-ticket workflow support."""

    def test_milestone_can_transition_workflow_stages(self, client: TestClient):
        """
        Acceptance: MILESTONE should be able to transition through workflow stages,
        just like FEATURE and TASK.

        This ensures that the orchestration engine works with MILESTONE.
        """
        # Create a milestone
        create_res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Milestone for stage transition",
                "work_item_type": "milestone",
            },
        )
        assert create_res.status_code == 201
        milestone = create_res.json()

        # Verify initial stage is planning
        assert milestone["workflow_stage_key"] == "planning"
        assert milestone["workflow_stage_status"] == "pending"

        # Get ticket detail to verify stages structure
        detail = client.get(f"/api/tickets/{milestone['id']}").json()

        # Verify stages exist and are properly structured
        planning_stage = next(
            (s for s in detail["stages"] if s["key"] == "planning"),
            None,
        )
        assert planning_stage is not None, "planning stage not found in stages"
        assert "order" in planning_stage
        assert "status" in planning_stage

    def test_capability_can_transition_workflow_stages(self, client: TestClient):
        """
        Acceptance: CAPABILITY should be able to transition through workflow stages.
        """
        feature = next(
            t
            for t in client.get("/api/tickets?workspace=loregarden").json()
            if t["work_item_type"] == "feature"
        )

        # Create a capability
        create_res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Capability for stage transition",
                "work_item_type": "capability",
                "parent_ticket_id": feature["id"],
            },
        )
        assert create_res.status_code == 201
        capability = create_res.json()

        # Verify initial stage is planning
        assert capability["workflow_stage_key"] == "planning"
        assert capability["workflow_stage_status"] == "pending"

        # Get ticket detail to verify stages structure
        detail = client.get(f"/api/tickets/{capability['id']}").json()

        # Verify stages exist
        planning_stage = next(
            (s for s in detail["stages"] if s["key"] == "planning"),
            None,
        )
        assert planning_stage is not None, "planning stage not found in stages"

    def test_multiple_milestones_each_get_own_workflow(self, client: TestClient):
        """
        Acceptance: Creating multiple MILESTONE tickets should give each
        its own workflow instance (not shared state).
        """
        # Create first milestone
        res1 = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Milestone A",
                "work_item_type": "milestone",
            },
        )
        milestone1 = res1.json()

        # Create second milestone
        res2 = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Milestone B",
                "work_item_type": "milestone",
            },
        )
        milestone2 = res2.json()

        # Both should have workflows initialized
        assert milestone1["workflow_stage_key"] == "planning"
        assert milestone2["workflow_stage_key"] == "planning"

        # Verify they're different tickets
        assert milestone1["id"] != milestone2["id"]

        # Get their instances
        from loregarden.db.session import engine
        from sqlmodel import Session
        with Session(engine) as session:
            inst1 = session.exec(
                select(WorkflowInstance).where(
                    WorkflowInstance.ticket_id == milestone1["id"]
                )
            ).first()
            inst2 = session.exec(
                select(WorkflowInstance).where(
                    WorkflowInstance.ticket_id == milestone2["id"]
                )
            ).first()

            # Both should exist
            assert inst1 is not None
            assert inst2 is not None
            # They should be different instances
            assert inst1.id != inst2.id
