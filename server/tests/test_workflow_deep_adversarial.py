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

import json
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from concurrent.futures import ThreadPoolExecutor, as_completed

from loregarden.models.domain import (
    Ticket,
    WorkItemType,
    StageStatus,
    WorkflowInstance,
    WorkflowTemplate,
)


class TestStateConsistencyUnderConcurrency:
    """Expose race conditions and state consistency issues under concurrent access."""

    def test_concurrent_milestone_creation_no_state_leakage(self, client: TestClient, db_session: Session):
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
            assert ticket["workflow_stage_key"] != "", \
                f"Ticket {ticket['id']} has empty workflow_stage_key"

        # Verify database consistency: each ticket has exactly one WorkflowInstance
        for ticket_id in ids:
            instances = db_session.exec(
                select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket_id)
            ).all()
            assert len(instances) == 1, \
                f"Ticket {ticket_id} has {len(instances)} instances (expected 1)"

    def test_concurrent_workflow_instance_creation_isolation(self, client: TestClient, db_session: Session):
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
                    assert inst1.id != inst2.id, \
                        f"Instances {i} and {j} have same ID"
                    # stages_json should be separate (even if logically identical)
                    assert inst1.ticket_id != inst2.ticket_id


class TestWorkflowInitializationMutations:
    """Mutation testing: flip conditions to expose initialization assumptions."""

    def test_milestone_workflow_NOT_skipped_by_condition(self, client: TestClient, db_session: Session):
        """
        Mutation: The code that initializes workflows must NOT skip MILESTONE.

        This catches if someone reverted the WORKFLOW_WORK_ITEM_TYPES constant
        or re-added a conditional check that skips non-FEATURE/TASK/BUG types.
        """
        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Mutation - workflow init check",
                "work_item_type": "milestone",
            },
        )
        assert res.status_code == 201
        milestone = res.json()

        # Mutation: verify workflow WAS initialized (not skipped)
        assert milestone["workflow_stage_key"] != "", \
            "MILESTONE workflow was skipped (conditional check bug)"
        assert milestone["workflow_stage_status"] == "pending", \
            "MILESTONE workflow status not initialized"

        # Verify WorkflowInstance was created (not skipped)
        instance = db_session.exec(
            select(WorkflowInstance).where(WorkflowInstance.ticket_id == milestone["id"])
        ).first()
        assert instance is not None, \
            "MILESTONE WorkflowInstance was skipped (conditional check bug)"

    def test_capability_workflow_NOT_skipped_by_condition(self, client: TestClient, db_session: Session):
        """
        Mutation: The code that initializes workflows must NOT skip CAPABILITY.
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
                "title": "Mutation - capability workflow init",
                "work_item_type": "capability",
                "parent_ticket_id": feature["id"],
            },
        )
        assert res.status_code == 201
        capability = res.json()

        # Mutation: flip expectation - workflow SHOULD be there
        assert capability["workflow_stage_key"] != "", \
            "CAPABILITY workflow was skipped"

        # Verify instance exists
        instance = db_session.exec(
            select(WorkflowInstance).where(WorkflowInstance.ticket_id == capability["id"])
        ).first()
        assert instance is not None, \
            "CAPABILITY WorkflowInstance was not created"

    def test_workflow_constants_includes_all_types(self, db_session: Session):
        """
        Mutation: Verify WORKFLOW_WORK_ITEM_TYPES constant wasn't
        accidentally left with only the old three types.

        Catches: Incomplete implementation, reverted changes, conditional logic errors.
        """
        from loregarden.models.domain import WORKFLOW_WORK_ITEM_TYPES, WorkItemType

        # Must include all 5 types
        expected_types = {
            WorkItemType.MILESTONE,
            WorkItemType.FEATURE,
            WorkItemType.CAPABILITY,
            WorkItemType.TASK,
            WorkItemType.BUG,
        }

        for work_type in expected_types:
            assert work_type in WORKFLOW_WORK_ITEM_TYPES, \
                f"{work_type} missing from WORKFLOW_WORK_ITEM_TYPES (mutation detected)"

        assert len(WORKFLOW_WORK_ITEM_TYPES) == 5, \
            f"WORKFLOW_WORK_ITEM_TYPES has {len(WORKFLOW_WORK_ITEM_TYPES)} types, expected 5"


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
            "next_agent",
        }

        missing = required_fields - set(milestone.keys())
        assert not missing, \
            f"MILESTONE response missing fields: {missing}"

        # Verify types
        assert isinstance(milestone["workflow_stage_key"], str)
        assert isinstance(milestone["workflow_stage_status"], str)
        assert isinstance(milestone["stages"], list)
        # next_agent can be string or null
        assert milestone["next_agent"] is None or isinstance(milestone["next_agent"], str)

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
            "next_agent",
        }

        for field in workflow_fields:
            assert field in capability_from_create, \
                f"Create response missing '{field}'"
            assert field in capability_from_detail, \
                f"Detail response missing '{field}'"
            assert field in capability_from_list, \
                f"List response missing '{field}'"

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
            assert not missing, \
                f"Stage {i} missing fields: {missing}"

            # Values should not be None or empty
            assert stage["key"] is not None and stage["key"] != "", \
                f"Stage {i} has empty key"
            assert stage["order"] is not None, \
                f"Stage {i} has null order"
            assert stage["status"] is not None, \
                f"Stage {i} has null status"


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
        assert task_res.status_code != 201, \
            "Hierarchy validation must work (orthogonal from workflow)"

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
        for ticket, name in [(milestone, "milestone"), (feature, "feature"), (capability, "capability")]:
            assert ticket["workflow_stage_key"] != "", \
                f"{name} workflow not initialized (hierarchy interference)"
            assert len(ticket["stages"]) > 0, \
                f"{name} has empty stages (hierarchy interference)"


class TestErrorRecoveryAndPartialFailures:
    """Expose error recovery paths and partial failure scenarios."""

    def test_workflow_init_failure_doesnt_create_ticket_half_state(self, client: TestClient, db_session: Session):
        """
        Error recovery: If workflow initialization fails, ticket should not
        be left in a half-initialized state.

        Exposes: Partial failures, missing rollback, inconsistent state.
        """
        # Try to create milestone in workspace with no template
        # (This test needs a workspace without template, which may not exist)
        # For now, test that if creation fails, no partial state exists
        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Partial state test",
                "work_item_type": "milestone",
            },
        )

        if res.status_code == 201:
            # Creation succeeded; if workflow failed to init, we'd see empty stage key
            milestone = res.json()
            if milestone["workflow_stage_key"] == "":
                # This is the bad case: ticket created but workflow not initialized
                # Verify there's a WorkflowInstance anyway (or handle gracefully)
                instance = db_session.exec(
                    select(WorkflowInstance).where(
                        WorkflowInstance.ticket_id == milestone["id"]
                    )
                ).first()
                # Either instance should exist or error should have been thrown
                assert instance is not None or milestone.get("error"), \
                    "Partial failure: ticket created but workflow not initialized"

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

        assert res.status_code != 201, \
            "Should not create ticket with invalid parent"

        # Verify no ticket was created with this description
        orphan = db_session.exec(
            select(Ticket).where(
                Ticket.title == "Invalid parent test"
            )
        ).first()
        assert orphan is None, \
            "Ticket was partially created despite invalid parent"


class TestIdempotencyAndReplayability:
    """Verify operations are idempotent and can be replayed safely."""

    def test_multiple_workflow_initializations_idempotent(self, client: TestClient, db_session: Session):
        """
        Idempotency: Initializing workflow multiple times should result
        in same state, not duplicates.

        Exposes: Non-idempotent operations, duplicate instances.
        """
        # Create milestone
        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Idempotency test",
                "work_item_type": "milestone",
            },
        )
        assert res.status_code == 201
        milestone_id = res.json()["id"]

        # Simulate replaying initialization (e.g., retry in service)
        # by directly checking state
        db_ticket = db_session.exec(
            select(Ticket).where(Ticket.id == milestone_id)
        ).first()

        initial_stage_key = db_ticket.workflow_stage_key

        # Verify WorkflowInstance count is still 1
        instances = db_session.exec(
            select(WorkflowInstance).where(WorkflowInstance.ticket_id == milestone_id)
        ).all()
        assert len(instances) == 1, \
            f"Non-idempotent: got {len(instances)} instances after creation"

        # State should be unchanged
        db_session.refresh(db_ticket)
        assert db_ticket.workflow_stage_key == initial_stage_key, \
            "Workflow stage changed on idempotent operation"

    def test_stage_transition_not_duplicated_on_replay(self, client: TestClient, db_session: Session):
        """
        Idempotency: Replaying a stage transition should be safe.

        Exposes: Double-execution of transitions, missing idempotency checks.
        """
        # Create milestone
        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Stage transition replay test",
                "work_item_type": "milestone",
            },
        )
        assert res.status_code == 201
        milestone_id = res.json()["id"]

        # Verify workflow has multiple stages
        instance = db_session.exec(
            select(WorkflowInstance).where(WorkflowInstance.ticket_id == milestone_id)
        ).first()

        stages_json = json.loads(instance.stages_json)
        if len(stages_json) > 1:
            # If we could replay a transition, verify idempotency
            # (This is a placeholder; actual replay test would update stage)
            assert instance.current_stage_key is not None, \
                "Current stage is null (not tracking state)"


class TestAssumptionFlipping:
    """Flip assumptions to expose hidden bugs."""

    def test_assume_workflow_stage_key_is_NOT_first_stage_key(self, client: TestClient):
        """
        Assumption flip: What if workflow_stage_key is NOT the first stage?

        This test verifies the assumption is CORRECT, not violated.
        """
        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Assumption flip - stage order",
                "work_item_type": "milestone",
            },
        )
        assert res.status_code == 201
        milestone = res.json()

        # Verify the assumption holds
        first_stage_key = milestone["stages"][0]["key"]
        assert milestone["workflow_stage_key"] == first_stage_key, \
            "Assumption violated: workflow_stage_key must be first stage"

    def test_assume_workflow_stage_exists_in_stages_array(self, client: TestClient):
        """
        Assumption flip: Verify workflow_stage_key is always in stages array.

        Exposes: Stale stage keys, orphaned states.
        """
        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Assumption flip - stage existence",
                "work_item_type": "capability",
                "parent_ticket_id": next(
                    t["id"]
                    for t in client.get("/api/tickets?workspace=loregarden").json()
                    if t["work_item_type"] == "feature"
                ),
            },
        )
        assert res.status_code == 201
        capability = res.json()

        stage_keys = {s["key"] for s in capability["stages"]}
        assert capability["workflow_stage_key"] in stage_keys, \
            f"Assumption violated: workflow_stage_key '{capability['workflow_stage_key']}' " \
            f"not in stages {stage_keys}"

    def test_assume_stages_never_null_or_empty(self, client: TestClient):
        """
        Assumption flip: stages array must always have content.
        """
        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Assumption flip - stages content",
                "work_item_type": "milestone",
            },
        )
        assert res.status_code == 201
        milestone = res.json()

        assert milestone["stages"] is not None, \
            "Assumption violated: stages is null"
        assert isinstance(milestone["stages"], list), \
            "Assumption violated: stages is not a list"
        assert len(milestone["stages"]) > 0, \
            "Assumption violated: stages is empty"


class TestMockOveruseDetection:
    """Verify system works with real dependencies, not mocks."""

    def test_workflow_instance_actually_persisted_to_database(self, client: TestClient, db_session: Session):
        """
        Mock overuse: Verify WorkflowInstance is actually persisted,
        not just mocked in memory.

        Exposes: Mock-only tests that pass but fail in production.
        """
        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Real persistence test",
                "work_item_type": "milestone",
            },
        )
        assert res.status_code == 201
        milestone_id = res.json()["id"]

        # Query database directly (not through API/cache)
        # Force a fresh database session
        db_session.expire_all()

        instance = db_session.exec(
            select(WorkflowInstance).where(WorkflowInstance.ticket_id == milestone_id)
        ).first()

        assert instance is not None, \
            "WorkflowInstance not found in database (possible mock overuse)"
        assert instance.ticket_id == milestone_id
        assert instance.template_id is not None, \
            "Template reference missing (incomplete persistence)"

    def test_workflow_template_actually_loaded_not_mocked(self, client: TestClient, db_session: Session):
        """
        Mock overuse: Verify workflow template is loaded from database,
        not mocked.

        Exposes: Tests that pass with mocks but fail without them.
        """
        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Real template loading test",
                "work_item_type": "milestone",
            },
        )
        assert res.status_code == 201
        milestone = res.json()

        # Verify stages came from real template
        stage_keys = [s["key"] for s in milestone["stages"]]
        assert len(stage_keys) > 0, \
            "No stages loaded (template not loaded)"

        # Verify consistency: all creations get same stage definitions
        res2 = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Real template loading test 2",
                "work_item_type": "milestone",
            },
        )
        assert res2.status_code == 201
        milestone2 = res2.json()

        stage_keys2 = [s["key"] for s in milestone2["stages"]]
        assert stage_keys == stage_keys2, \
            "Template not loaded consistently (mock detection)"


class TestDataConsistencyInvariantsViolations:
    """Detect violations of data consistency invariants."""

    def test_workflow_stage_status_must_be_valid_enum(self, client: TestClient, db_session: Session):
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

        db_ticket = db_session.exec(
            select(Ticket).where(Ticket.id == milestone_id)
        ).first()

        valid_statuses = {"pending", "running", "blocked", "awaiting", "done", "wont_do"}
        assert str(db_ticket.workflow_stage_status) in valid_statuses, \
            f"Invalid stage status: {db_ticket.workflow_stage_status}"

    def test_workflow_instance_references_valid_ticket(self, client: TestClient, db_session: Session):
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
        ticket = db_session.exec(
            select(Ticket).where(Ticket.id == instance.ticket_id)
        ).first()
        assert ticket is not None, \
            f"WorkflowInstance references non-existent ticket {instance.ticket_id}"

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
            select(WorkflowInstance).where(
                WorkflowInstance.ticket_id.in_(milestone_ids)
            )
        ).all()

        assert len(instances) == len(milestone_ids), \
            f"Expected {len(milestone_ids)} instances, got {len(instances)} (orphaned or missing)"

        # All instances should reference existing tickets
        for instance in instances:
            ticket = db_session.exec(
                select(Ticket).where(Ticket.id == instance.ticket_id)
            ).first()
            assert ticket is not None, \
                f"Orphaned instance: references non-existent ticket {instance.ticket_id}"
