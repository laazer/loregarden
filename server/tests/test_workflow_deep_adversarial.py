"""
Deep Adversarial Test Suite: Ticket 25 - Assign Workflow to Any Ticket

This suite applies ADVANCED adversarial testing to expose subtle weaknesses,
hidden assumptions, and edge cases that the primary test suite may miss.

Focus areas:
- Subtle state mutations and condition flips
- Concurrency and race condition edge cases
- Data consistency and isolation violations
- Serialization and deserialization edge cases
- Orthogonality between hierarchy and workflow systems
- Error recovery paths and partial failures
- Idempotency and replayability
- Mock overuse and false confidence
- Assumption validation via mutation
- Timing-sensitive state changes
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi.testclient import TestClient
from loregarden.models.domain import (
    Ticket,
    WorkflowInstance,
    WorkItemType,
)
from sqlmodel import Session, select


class TestStateConsistencyUnderConcurrency:
    """Expose race conditions and state consistency issues under concurrent access."""

    def test_concurrent_milestone_creation_no_state_leakage(
        self, client: TestClient, db_session: Session
    ):
        """
        Concurrency mutation: Creating many MILESTONEs simultaneously.

        Exposes: Race conditions in WorkflowInstance creation, duplicate instances,
        or shared state between concurrent tickets.
        """

        def create_milestone(i: int):
            return client.post(
                "/api/tickets",
                json={
                    "workspace_slug": "loregarden",
                    "title": f"Concurrent Race Test {i}",
                    "work_item_type": "milestone",
                    "description": f"Created in concurrent batch {i}",
                },
            )

        # Create 10 milestones concurrently
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(create_milestone, i) for i in range(10)]
            results = []
            for future in as_completed(futures):
                result = future.result()
                assert result.status_code == 201, f"Creation failed: {result.text}"
                results.append(result.json())

        # All should have unique IDs
        ids = [r["id"] for r in results]
        assert len(set(ids)) == len(ids), "Concurrent creations produced duplicate IDs"

        # All should have workflow initialized
        for ticket in results:
            assert ticket["workflow_stage_key"] != "", (
                f"Ticket {ticket['id']} has empty workflow_stage_key"
            )

        # Verify database consistency: each ticket has exactly one WorkflowInstance
        for ticket_id in ids:
            instances = db_session.exec(
                select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket_id)
            ).all()
            assert len(instances) == 1, (
                f"Ticket {ticket_id} has {len(instances)} instances (expected 1)"
            )

    def test_concurrent_workflow_instance_creation_isolation(
        self, client: TestClient, db_session: Session
    ):
        """
        Concurrency mutation: Verify WorkflowInstance records are isolated,
        not shared, even when created rapidly.

        Exposes: Shared reference bugs, transaction isolation issues.
        """
        # Create 5 capabilities with parent feature
        feature = next(
            t
            for t in client.get("/api/tickets?workspace=loregarden").json()
            if t["work_item_type"] == "feature"
        )

        def create_capability(i: int):
            return client.post(
                "/api/tickets",
                json={
                    "workspace_slug": "loregarden",
                    "title": f"Concurrent Capability {i}",
                    "work_item_type": "capability",
                    "parent_ticket_id": feature["id"],
                },
            )

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(create_capability, i) for i in range(5)]
            capabilities = []
            for future in as_completed(futures):
                result = future.result()
                assert result.status_code == 201
                capabilities.append(result.json())

        # Verify each has unique WorkflowInstance with unique stages_json
        instances = []
        stages_jsons = []
        for cap in capabilities:
            instance = db_session.exec(
                select(WorkflowInstance).where(WorkflowInstance.ticket_id == cap["id"])
            ).first()
            assert instance is not None
            instances.append(instance)
            stages_jsons.append(instance.stages_json)

        # No two instances should be the same object
        for i, inst1 in enumerate(instances):
            for j, inst2 in enumerate(instances):
                if i != j:
                    assert inst1.id != inst2.id, f"Instances {i} and {j} have same ID"
                    # stages_json should be separate (even if logically identical)
                    assert inst1.ticket_id != inst2.ticket_id


class TestWorkflowInitializationMutations:
    """Mutation testing: flip conditions to expose initialization assumptions."""

    def test_workflow_constants_includes_all_types(self, db_session: Session):
        """
        Mutation: Verify WORKFLOW_WORK_ITEM_TYPES constant wasn't
        accidentally left with only the old three types.

        Catches: Incomplete implementation, reverted changes, conditional logic errors.
        """
        from loregarden.models.domain import WORKFLOW_WORK_ITEM_TYPES

        # Must include all 5 types
        expected_types = {
            WorkItemType.MILESTONE,
            WorkItemType.FEATURE,
            WorkItemType.CAPABILITY,
            WorkItemType.TASK,
            WorkItemType.BUG,
        }

        for work_type in expected_types:
            assert work_type in WORKFLOW_WORK_ITEM_TYPES, (
                f"{work_type} missing from WORKFLOW_WORK_ITEM_TYPES (mutation detected)"
            )

        assert len(WORKFLOW_WORK_ITEM_TYPES) == 5, (
            f"WORKFLOW_WORK_ITEM_TYPES has {len(WORKFLOW_WORK_ITEM_TYPES)} types, expected 5"
        )
        # INITIATIVE (lg-initiatives-cross-731) must never auto-join this set.
        initiative = getattr(WorkItemType, "INITIATIVE", None)
        assert initiative is not None, "WorkItemType.INITIATIVE missing — R1 / AC1"
        assert initiative not in WORKFLOW_WORK_ITEM_TYPES


class TestSerializationEdgeCases:
    """Expose serialization and deserialization edge cases."""

    def test_milestone_api_response_has_all_workflow_fields(self, client: TestClient):
        """
        Serialization: Verify ALL workflow fields are in response, not partially.

        Exposes: Missing fields in API response, conditional serialization bugs.
        """
        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Serialization test",
                "work_item_type": "milestone",
            },
        )
        assert res.status_code == 201
        milestone = res.json()

        # Required workflow fields
        required_fields = {
            "workflow_stage_key",
            "workflow_stage_status",
            "stages",
            "current_stage_agent",
        }

        missing = required_fields - set(milestone.keys())
        assert not missing, f"MILESTONE response missing fields: {missing}"

        # Verify types
        assert isinstance(milestone["workflow_stage_key"], str)
        assert isinstance(milestone["workflow_stage_status"], str)
        assert isinstance(milestone["stages"], list)
        # derived, so always a string — "" means "no agent resolves for this stage"
        assert isinstance(milestone["current_stage_agent"], str)

    def test_capability_detail_serialization_consistency(self, client: TestClient):
        """
        Serialization: Verify list and detail endpoints have consistent serialization.

        Exposes: Inconsistent serialization between endpoints, missing fields in one path.
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
                "title": "Serialization consistency test",
                "work_item_type": "capability",
                "parent_ticket_id": feature["id"],
            },
        )
        assert res.status_code == 201
        capability_from_create = res.json()

        # Fetch from detail endpoint
        detail_res = client.get(f"/api/tickets/{capability_from_create['id']}")
        assert detail_res.status_code == 200
        capability_from_detail = detail_res.json()

        # Fetch from list endpoint
        all_tickets = client.get("/api/tickets?workspace=loregarden").json()
        capability_from_list = next(
            t for t in all_tickets if t["id"] == capability_from_create["id"]
        )

        # All three should have same workflow fields
        workflow_fields = {
            "workflow_stage_key",
            "workflow_stage_status",
            "stages",
            "current_stage_agent",
        }

        for field in workflow_fields:
            assert field in capability_from_create, f"Create response missing '{field}'"
            assert field in capability_from_detail, f"Detail response missing '{field}'"
            assert field in capability_from_list, f"List response missing '{field}'"

    def test_stages_array_structure_is_consistent(self, client: TestClient):
        """
        Serialization: Verify stages array has consistent structure across all tickets.

        Exposes: Missing stage fields, inconsistent structure, null values.
        """
        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Stage structure test",
                "work_item_type": "milestone",
            },
        )
        assert res.status_code == 201
        milestone = res.json()

        # Required stage fields
        required_stage_fields = {"key", "order", "status"}

        for i, stage in enumerate(milestone["stages"]):
            missing = required_stage_fields - set(stage.keys())
            assert not missing, f"Stage {i} missing fields: {missing}"

            # Values should not be None or empty
            assert stage["key"] is not None and stage["key"] != "", f"Stage {i} has empty key"
            assert stage["order"] is not None, f"Stage {i} has null order"
            assert stage["status"] is not None, f"Stage {i} has null status"


class TestHierarchyWorkflowOrthogonality:
    """Verify hierarchy and workflow systems are truly orthogonal."""

    def test_hierarchy_validation_independent_of_workflow(self, client: TestClient):
        """
        Orthogonality: Invalid hierarchy should fail regardless of workflow eligibility.

        Exposes: Hierarchy checks disabled when adding workflow support.
        """
        # Create milestone
        milestone_res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Test milestone",
                "work_item_type": "milestone",
            },
        )
        assert milestone_res.status_code == 201
        milestone = milestone_res.json()

        # Try to create TASK under MILESTONE (invalid hierarchy)
        task_res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Invalid task parent",
                "work_item_type": "task",
                "parent_ticket_id": milestone["id"],
            },
        )

        # Should fail because TASK cannot be child of MILESTONE
        # This must work regardless of workflow eligibility
        assert task_res.status_code == 400, (
            "Hierarchy validation must work (orthogonal from workflow)"
        )

    def test_workflow_initialization_independent_of_hierarchy(self, client: TestClient):
        """
        Orthogonality: Workflow initialization should work for any valid hierarchy.

        Exposes: Workflow checks interfering with hierarchy logic.
        """
        # Create full hierarchy: MILESTONE -> FEATURE -> CAPABILITY -> TASK
        milestone_res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Hierarchy milestone",
                "work_item_type": "milestone",
            },
        )
        assert milestone_res.status_code == 201
        milestone = milestone_res.json()

        # Each child should initialize workflow independent of hierarchy level
        feature_res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Hierarchy feature",
                "work_item_type": "feature",
                "parent_ticket_id": milestone["id"],
            },
        )
        assert feature_res.status_code == 201
        feature = feature_res.json()

        capability_res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Hierarchy capability",
                "work_item_type": "capability",
                "parent_ticket_id": feature["id"],
            },
        )
        assert capability_res.status_code == 201
        capability = capability_res.json()

        # All must have workflows regardless of depth
        for ticket, name in [
            (milestone, "milestone"),
            (feature, "feature"),
            (capability, "capability"),
        ]:
            assert ticket["workflow_stage_key"] != "", (
                f"{name} workflow not initialized (hierarchy interference)"
            )
            assert len(ticket["stages"]) > 0, f"{name} has empty stages (hierarchy interference)"


class TestErrorRecoveryAndPartialFailures:
    """Expose error recovery paths and partial failure scenarios."""

    def test_invalid_parent_doesnt_create_ticket(self, client: TestClient, db_session: Session):
        """
        Error recovery: If parent validation fails, ticket should not be created.

        Exposes: Partial creation, missing transaction rollback.
        """
        invalid_parent_id = "00000000-0000-0000-0000-000000000001"

        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Invalid parent test",
                "work_item_type": "capability",
                "parent_ticket_id": invalid_parent_id,
            },
        )

        assert res.status_code == 400, "Should not create ticket with invalid parent"

        # Verify no ticket was created with this description
        orphan = db_session.exec(
            select(Ticket).where(Ticket.title == "Invalid parent test")
        ).first()
        assert orphan is None, "Ticket was partially created despite invalid parent"


class TestDataConsistencyInvariantsViolations:
    """Detect violations of data consistency invariants."""

    def test_workflow_stage_status_must_be_valid_enum(
        self, client: TestClient, db_session: Session
    ):
        """
        Invariant: workflow_stage_status must always be a valid StageStatus enum value.

        Exposes: Corrupted state, enum validation bypasses.
        """
        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Enum invariant test",
                "work_item_type": "milestone",
            },
        )
        assert res.status_code == 201
        milestone_id = res.json()["id"]

        db_ticket = db_session.exec(select(Ticket).where(Ticket.id == milestone_id)).first()

        valid_statuses = {"pending", "running", "blocked", "awaiting", "done", "wont_do"}
        assert str(db_ticket.workflow_stage_status) in valid_statuses, (
            f"Invalid stage status: {db_ticket.workflow_stage_status}"
        )

    def test_workflow_instance_references_valid_ticket(
        self, client: TestClient, db_session: Session
    ):
        """
        Invariant: WorkflowInstance.ticket_id must reference an existing Ticket.

        Exposes: Orphaned instances, missing foreign key constraints.
        """
        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Foreign key invariant test",
                "work_item_type": "milestone",
            },
        )
        assert res.status_code == 201
        milestone_id = res.json()["id"]

        instance = db_session.exec(
            select(WorkflowInstance).where(WorkflowInstance.ticket_id == milestone_id)
        ).first()

        # Verify ticket exists
        ticket = db_session.exec(select(Ticket).where(Ticket.id == instance.ticket_id)).first()
        assert ticket is not None, (
            f"WorkflowInstance references non-existent ticket {instance.ticket_id}"
        )

    def test_multiple_instances_no_orphaned_records(self, client: TestClient, db_session: Session):
        """
        Invariant: Creating N tickets should create exactly N WorkflowInstances,
        no more, no fewer.

        Exposes: Orphaned or duplicated instances.
        """
        # Create 3 milestones
        milestone_ids = []
        for i in range(3):
            res = client.post(
                "/api/tickets",
                json={
                    "workspace_slug": "loregarden",
                    "title": f"Invariant check {i}",
                    "work_item_type": "milestone",
                },
            )
            assert res.status_code == 201
            milestone_ids.append(res.json()["id"])

        # Count instances for these tickets
        instances = db_session.exec(
            select(WorkflowInstance).where(WorkflowInstance.ticket_id.in_(milestone_ids))
        ).all()

        assert len(instances) == len(milestone_ids), (
            f"Expected {len(milestone_ids)} instances, got {len(instances)} (orphaned or missing)"
        )

        # All instances should reference existing tickets
        for instance in instances:
            ticket = db_session.exec(select(Ticket).where(Ticket.id == instance.ticket_id)).first()
            assert ticket is not None, (
                f"Orphaned instance: references non-existent ticket {instance.ticket_id}"
            )
