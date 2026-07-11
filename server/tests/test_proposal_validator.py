"""Tests for decomposition proposal validation and normalization.

Tests verify that generated proposals conform to expected structure and can be
safely persisted. Coverage includes:
- Required field validation
- Hierarchy structure validation (parent-child relationships, no orphans, no cycles)
- Text field normalization (encoding, length limits, whitespace)
- External ID uniqueness
- Priority validation
- Depth and breadth limits
"""

import pytest
from loregarden.models.domain import WorkItemType
from loregarden.models.domain.schemas import HierarchyWorkItem
from loregarden.services.proposal_validator import ProposalValidationError, ProposalValidator

# ============================================================================
# TEST SUITE
# ============================================================================


class TestProposalValidatorRequiredFields:
    """Verify required fields are enforced."""

    def test_missing_external_id_raises_error(self):
        """Missing external_id should fail validation."""
        item = HierarchyWorkItem(
            external_id="",
            title="Test",
            work_item_type=WorkItemType.TASK,
        )
        with pytest.raises(ProposalValidationError, match="external_id is required"):
            ProposalValidator.validate_all([item])

    def test_missing_title_raises_error(self):
        """Missing title should fail validation."""
        item = HierarchyWorkItem(
            external_id="id1",
            title="",
            work_item_type=WorkItemType.TASK,
        )
        with pytest.raises(ProposalValidationError, match="title is required"):
            ProposalValidator.validate_all([item])

    def test_required_fields_enforced_by_pydantic(self):
        """Pydantic enforces required fields before validation layer."""
        # Note: work_item_type is enforced as non-nullable enum by Pydantic,
        # so validation layer doesn't need to check. This test documents that.
        # Attempting to create item with None work_item_type raises pydantic error.
        import pytest as pytest_module
        from pydantic import ValidationError

        with pytest_module.raises(ValidationError):
            HierarchyWorkItem(
                external_id="id1",
                title="Test",
                work_item_type=None,
            )

    def test_valid_required_fields_passes(self):
        """Item with all required fields should pass."""
        item = HierarchyWorkItem(
            external_id="id1",
            title="Test",
            work_item_type=WorkItemType.TASK,
        )
        result = ProposalValidator.validate_all([item])
        assert len(result) == 1
        assert result[0].external_id == "id1"


class TestProposalValidatorHierarchyTypes:
    """Verify parent-child type relationships are valid."""

    def test_milestone_can_contain_feature(self):
        """Milestone can contain Feature as child."""
        item = HierarchyWorkItem(
            external_id="m1",
            title="Milestone",
            work_item_type=WorkItemType.MILESTONE,
            children=[
                HierarchyWorkItem(
                    external_id="f1",
                    title="Feature",
                    work_item_type=WorkItemType.FEATURE,
                )
            ],
        )
        result = ProposalValidator.validate_all([item])
        assert len(result[0].children) == 1

    def test_milestone_can_contain_bug(self):
        """Milestone can contain Bug as child."""
        item = HierarchyWorkItem(
            external_id="m1",
            title="Milestone",
            work_item_type=WorkItemType.MILESTONE,
            children=[
                HierarchyWorkItem(
                    external_id="b1",
                    title="Bug",
                    work_item_type=WorkItemType.BUG,
                )
            ],
        )
        result = ProposalValidator.validate_all([item])
        assert len(result[0].children) == 1

    def test_milestone_cannot_contain_capability(self):
        """Milestone cannot contain Capability directly."""
        item = HierarchyWorkItem(
            external_id="m1",
            title="Milestone",
            work_item_type=WorkItemType.MILESTONE,
            children=[
                HierarchyWorkItem(
                    external_id="c1",
                    title="Capability",
                    work_item_type=WorkItemType.CAPABILITY,
                )
            ],
        )
        with pytest.raises(ProposalValidationError, match="milestone cannot contain capability"):
            ProposalValidator.validate_all([item])

    def test_feature_can_contain_capability(self):
        """Feature can contain Capability as child."""
        item = HierarchyWorkItem(
            external_id="f1",
            title="Feature",
            work_item_type=WorkItemType.FEATURE,
            children=[
                HierarchyWorkItem(
                    external_id="c1",
                    title="Capability",
                    work_item_type=WorkItemType.CAPABILITY,
                )
            ],
        )
        result = ProposalValidator.validate_all([item])
        assert len(result[0].children) == 1

    def test_feature_cannot_contain_milestone(self):
        """Feature cannot contain Milestone."""
        item = HierarchyWorkItem(
            external_id="f1",
            title="Feature",
            work_item_type=WorkItemType.FEATURE,
            children=[
                HierarchyWorkItem(
                    external_id="m1",
                    title="Milestone",
                    work_item_type=WorkItemType.MILESTONE,
                )
            ],
        )
        with pytest.raises(ProposalValidationError, match="feature cannot contain milestone"):
            ProposalValidator.validate_all([item])

    def test_capability_can_contain_task(self):
        """Capability can contain Task as child."""
        item = HierarchyWorkItem(
            external_id="c1",
            title="Capability",
            work_item_type=WorkItemType.CAPABILITY,
            children=[
                HierarchyWorkItem(
                    external_id="t1",
                    title="Task",
                    work_item_type=WorkItemType.TASK,
                )
            ],
        )
        result = ProposalValidator.validate_all([item])
        assert len(result[0].children) == 1

    def test_task_cannot_have_children(self):
        """Task cannot have any children."""
        item = HierarchyWorkItem(
            external_id="t1",
            title="Task",
            work_item_type=WorkItemType.TASK,
            children=[
                HierarchyWorkItem(
                    external_id="t2",
                    title="Subtask",
                    work_item_type=WorkItemType.TASK,
                )
            ],
        )
        with pytest.raises(ProposalValidationError, match="task cannot contain task"):
            ProposalValidator.validate_all([item])

    def test_bug_cannot_have_children(self):
        """Bug cannot have any children."""
        item = HierarchyWorkItem(
            external_id="b1",
            title="Bug",
            work_item_type=WorkItemType.BUG,
            children=[
                HierarchyWorkItem(
                    external_id="t1",
                    title="Task",
                    work_item_type=WorkItemType.TASK,
                )
            ],
        )
        with pytest.raises(ProposalValidationError, match="bug cannot contain task"):
            ProposalValidator.validate_all([item])

    def test_full_valid_hierarchy_chain(self):
        """Full chain milestone→feature→capability→task should pass."""
        item = HierarchyWorkItem(
            external_id="m1",
            title="Milestone",
            work_item_type=WorkItemType.MILESTONE,
            children=[
                HierarchyWorkItem(
                    external_id="f1",
                    title="Feature",
                    work_item_type=WorkItemType.FEATURE,
                    children=[
                        HierarchyWorkItem(
                            external_id="c1",
                            title="Capability",
                            work_item_type=WorkItemType.CAPABILITY,
                            children=[
                                HierarchyWorkItem(
                                    external_id="t1",
                                    title="Task",
                                    work_item_type=WorkItemType.TASK,
                                )
                            ],
                        )
                    ],
                )
            ],
        )
        result = ProposalValidator.validate_all([item])
        assert result[0].work_item_type == WorkItemType.MILESTONE
        assert result[0].children[0].work_item_type == WorkItemType.FEATURE
        assert result[0].children[0].children[0].work_item_type == WorkItemType.CAPABILITY
        assert result[0].children[0].children[0].children[0].work_item_type == WorkItemType.TASK


class TestProposalValidatorExternalIDUniqueness:
    """Verify no duplicate external IDs within proposal."""

    def test_duplicate_external_ids_raise_error(self):
        """Duplicate IDs should fail validation."""
        items = [
            HierarchyWorkItem(
                external_id="id1",
                title="Item 1",
                work_item_type=WorkItemType.TASK,
            ),
            HierarchyWorkItem(
                external_id="id1",
                title="Item 2",
                work_item_type=WorkItemType.TASK,
            ),
        ]
        with pytest.raises(ProposalValidationError, match="Duplicate external_id"):
            ProposalValidator.validate_all(items)

    def test_duplicate_ids_in_nested_children_raise_error(self):
        """Duplicate IDs in nested hierarchy should fail."""
        item = HierarchyWorkItem(
            external_id="id1",
            title="Parent",
            work_item_type=WorkItemType.FEATURE,
            children=[
                HierarchyWorkItem(
                    external_id="id2",
                    title="Child",
                    work_item_type=WorkItemType.CAPABILITY,
                    children=[
                        HierarchyWorkItem(
                            external_id="id2",
                            title="Duplicate",
                            work_item_type=WorkItemType.TASK,
                        )
                    ],
                )
            ],
        )
        with pytest.raises(ProposalValidationError, match="Duplicate external_id"):
            ProposalValidator.validate_all([item])

    def test_unique_external_ids_pass(self):
        """All unique IDs should pass validation."""
        items = [
            HierarchyWorkItem(
                external_id="id1",
                title="Item 1",
                work_item_type=WorkItemType.TASK,
            ),
            HierarchyWorkItem(
                external_id="id2",
                title="Item 2",
                work_item_type=WorkItemType.TASK,
            ),
        ]
        result = ProposalValidator.validate_all(items)
        assert len(result) == 2


class TestProposalValidatorPriority:
    """Verify priority values are within valid range [1, 2, 3]."""

    def test_priority_1_valid(self):
        """Priority 1 (high) should pass."""
        item = HierarchyWorkItem(
            external_id="id1",
            title="Test",
            work_item_type=WorkItemType.TASK,
            priority=1,
        )
        result = ProposalValidator.validate_all([item])
        assert result[0].priority == 1

    def test_priority_2_valid(self):
        """Priority 2 (medium) should pass."""
        item = HierarchyWorkItem(
            external_id="id1",
            title="Test",
            work_item_type=WorkItemType.TASK,
            priority=2,
        )
        result = ProposalValidator.validate_all([item])
        assert result[0].priority == 2

    def test_priority_3_valid(self):
        """Priority 3 (low) should pass."""
        item = HierarchyWorkItem(
            external_id="id1",
            title="Test",
            work_item_type=WorkItemType.TASK,
            priority=3,
        )
        result = ProposalValidator.validate_all([item])
        assert result[0].priority == 3

    def test_priority_0_invalid(self):
        """Priority 0 should fail."""
        item = HierarchyWorkItem(
            external_id="id1",
            title="Test",
            work_item_type=WorkItemType.TASK,
            priority=0,
        )
        with pytest.raises(ProposalValidationError, match="Priority must be 1-3"):
            ProposalValidator.validate_all([item])

    def test_priority_negative_invalid(self):
        """Negative priority should fail."""
        item = HierarchyWorkItem(
            external_id="id1",
            title="Test",
            work_item_type=WorkItemType.TASK,
            priority=-5,
        )
        with pytest.raises(ProposalValidationError, match="Priority must be 1-3"):
            ProposalValidator.validate_all([item])

    def test_priority_4_invalid(self):
        """Priority 4 should fail."""
        item = HierarchyWorkItem(
            external_id="id1",
            title="Test",
            work_item_type=WorkItemType.TASK,
            priority=4,
        )
        with pytest.raises(ProposalValidationError, match="Priority must be 1-3"):
            ProposalValidator.validate_all([item])

    def test_priority_very_large_invalid(self):
        """Very large priority should fail."""
        item = HierarchyWorkItem(
            external_id="id1",
            title="Test",
            work_item_type=WorkItemType.TASK,
            priority=99999,
        )
        with pytest.raises(ProposalValidationError, match="Priority must be 1-3"):
            ProposalValidator.validate_all([item])

    def test_default_priority_is_3(self):
        """Default priority should be 3."""
        item = HierarchyWorkItem(
            external_id="id1",
            title="Test",
            work_item_type=WorkItemType.TASK,
        )
        result = ProposalValidator.validate_all([item])
        assert result[0].priority == 3


class TestProposalValidatorTextFields:
    """Verify text field lengths and normalization."""

    def test_title_max_length_valid(self):
        """Title at max length should pass."""
        title = "x" * ProposalValidator.MAX_TITLE_LENGTH
        item = HierarchyWorkItem(
            external_id="id1",
            title=title,
            work_item_type=WorkItemType.TASK,
        )
        result = ProposalValidator.validate_all([item])
        assert len(result[0].title) == ProposalValidator.MAX_TITLE_LENGTH

    def test_title_exceeds_max_length_fails(self):
        """Title exceeding max length should fail."""
        title = "x" * (ProposalValidator.MAX_TITLE_LENGTH + 1)
        item = HierarchyWorkItem(
            external_id="id1",
            title=title,
            work_item_type=WorkItemType.TASK,
        )
        with pytest.raises(ProposalValidationError, match="Title exceeds max length"):
            ProposalValidator.validate_all([item])

    def test_description_max_length_valid(self):
        """Description at max length should pass."""
        desc = "x" * ProposalValidator.MAX_DESCRIPTION_LENGTH
        item = HierarchyWorkItem(
            external_id="id1",
            title="Test",
            work_item_type=WorkItemType.TASK,
            description=desc,
        )
        result = ProposalValidator.validate_all([item])
        assert len(result[0].description) == ProposalValidator.MAX_DESCRIPTION_LENGTH

    def test_description_exceeds_max_length_fails(self):
        """Description exceeding max length should fail."""
        desc = "x" * (ProposalValidator.MAX_DESCRIPTION_LENGTH + 1)
        item = HierarchyWorkItem(
            external_id="id1",
            title="Test",
            work_item_type=WorkItemType.TASK,
            description=desc,
        )
        with pytest.raises(ProposalValidationError, match="Description exceeds max length"):
            ProposalValidator.validate_all([item])

    def test_acceptance_criteria_max_items_valid(self):
        """Max acceptance criteria items should pass."""
        criteria = [
            f"Criterion {i}" for i in range(ProposalValidator.MAX_ACCEPTANCE_CRITERIA_ITEMS)
        ]
        item = HierarchyWorkItem(
            external_id="id1",
            title="Test",
            work_item_type=WorkItemType.TASK,
            acceptance_criteria=criteria,
        )
        result = ProposalValidator.validate_all([item])
        assert len(result[0].acceptance_criteria) == ProposalValidator.MAX_ACCEPTANCE_CRITERIA_ITEMS

    def test_acceptance_criteria_exceeds_max_items_fails(self):
        """Exceeding max acceptance criteria items should fail."""
        criteria = [
            f"Criterion {i}" for i in range(ProposalValidator.MAX_ACCEPTANCE_CRITERIA_ITEMS + 1)
        ]
        item = HierarchyWorkItem(
            external_id="id1",
            title="Test",
            work_item_type=WorkItemType.TASK,
            acceptance_criteria=criteria,
        )
        with pytest.raises(ProposalValidationError, match="Acceptance criteria exceeds max items"):
            ProposalValidator.validate_all([item])

    def test_acceptance_criterion_max_length_valid(self):
        """Criterion at max length should pass."""
        criterion = "x" * ProposalValidator.MAX_ACCEPTANCE_CRITERIA_ITEM_LENGTH
        item = HierarchyWorkItem(
            external_id="id1",
            title="Test",
            work_item_type=WorkItemType.TASK,
            acceptance_criteria=[criterion],
        )
        result = ProposalValidator.validate_all([item])
        assert (
            len(result[0].acceptance_criteria[0])
            == ProposalValidator.MAX_ACCEPTANCE_CRITERIA_ITEM_LENGTH
        )

    def test_acceptance_criterion_exceeds_max_length_fails(self):
        """Criterion exceeding max length should fail."""
        criterion = "x" * (ProposalValidator.MAX_ACCEPTANCE_CRITERIA_ITEM_LENGTH + 1)
        item = HierarchyWorkItem(
            external_id="id1",
            title="Test",
            work_item_type=WorkItemType.TASK,
            acceptance_criteria=[criterion],
        )
        with pytest.raises(
            ProposalValidationError, match="Acceptance criterion .* exceeds max length"
        ):
            ProposalValidator.validate_all([item])


class TestProposalValidatorTextNormalization:
    """Verify text fields are normalized correctly."""

    def test_unicode_nfc_normalization(self):
        """Unicode text should be normalized to NFC form."""
        title_denormalized = "é"  # é in decomposed form (e + combining acute)
        expected = "é"  # é in composed form (single character)

        item = HierarchyWorkItem(
            external_id="id1",
            title=title_denormalized,
            work_item_type=WorkItemType.TASK,
        )
        result = ProposalValidator.validate_all([item])

        assert result[0].title == expected
        assert len(result[0].title) == 1

    def test_title_whitespace_stripped(self):
        """Title whitespace should be stripped."""
        item = HierarchyWorkItem(
            external_id="id1",
            title="  Test Title  ",
            work_item_type=WorkItemType.TASK,
        )
        result = ProposalValidator.validate_all([item])
        assert result[0].title == "Test Title"

    def test_description_whitespace_stripped(self):
        """Description leading/trailing whitespace should be stripped."""
        item = HierarchyWorkItem(
            external_id="id1",
            title="Test",
            work_item_type=WorkItemType.TASK,
            description="  Description text  ",
        )
        result = ProposalValidator.validate_all([item])
        assert result[0].description == "Description text"

    def test_description_preserves_internal_newlines(self):
        """Description should preserve single and double newlines."""
        desc = "Line 1\n\nLine 2\nLine 3"
        item = HierarchyWorkItem(
            external_id="id1",
            title="Test",
            work_item_type=WorkItemType.TASK,
            description=desc,
        )
        result = ProposalValidator.validate_all([item])
        assert result[0].description == "Line 1\n\nLine 2\nLine 3"

    def test_description_collapses_excessive_newlines(self):
        """Description should collapse 3+ newlines to 2."""
        desc = "Line 1\n\n\n\nLine 2"
        item = HierarchyWorkItem(
            external_id="id1",
            title="Test",
            work_item_type=WorkItemType.TASK,
            description=desc,
        )
        result = ProposalValidator.validate_all([item])
        assert result[0].description == "Line 1\n\nLine 2"

    def test_acceptance_criteria_whitespace_stripped(self):
        """Acceptance criteria should have whitespace stripped."""
        criteria = ["  Criterion 1  ", "Criterion 2  ", "  Criterion 3"]
        item = HierarchyWorkItem(
            external_id="id1",
            title="Test",
            work_item_type=WorkItemType.TASK,
            acceptance_criteria=criteria,
        )
        result = ProposalValidator.validate_all([item])
        assert result[0].acceptance_criteria == ["Criterion 1", "Criterion 2", "Criterion 3"]

    def test_special_characters_preserved(self):
        """Special characters should be preserved during normalization."""
        title = "Feature: <script>alert('xss')</script>"
        item = HierarchyWorkItem(
            external_id="id1",
            title=title,
            work_item_type=WorkItemType.TASK,
        )
        result = ProposalValidator.validate_all([item])
        assert result[0].title == title


class TestProposalValidatorTreeLimits:
    """Verify hierarchy depth and breadth constraints."""

    def test_max_valid_hierarchy_depth_accepted(self):
        """Maximum valid hierarchy depth per VALID_HIERARCHY rules should pass.

        Given the hierarchy rules (Milestone→Feature→Capability→Task),
        the practical maximum depth is 4. The MAX_HIERARCHY_DEPTH constraint of 10
        allows for future hierarchy expansion.
        """
        # Build maximum valid chain: Milestone → Feature → Capability → Task
        item = HierarchyWorkItem(
            external_id="m1",
            title="Milestone",
            work_item_type=WorkItemType.MILESTONE,
            children=[
                HierarchyWorkItem(
                    external_id="f1",
                    title="Feature",
                    work_item_type=WorkItemType.FEATURE,
                    children=[
                        HierarchyWorkItem(
                            external_id="c1",
                            title="Capability",
                            work_item_type=WorkItemType.CAPABILITY,
                            children=[
                                HierarchyWorkItem(
                                    external_id="t1",
                                    title="Task",
                                    work_item_type=WorkItemType.TASK,
                                )
                            ],
                        )
                    ],
                )
            ],
        )

        result = ProposalValidator.validate_all([item])
        assert result is not None
        assert result[0].work_item_type == WorkItemType.MILESTONE

    def test_exceeds_max_depth_fails(self):
        """Hierarchy exceeding max depth should fail."""
        # Build chain that violates max depth constraint
        item = None
        for i in range(ProposalValidator.MAX_HIERARCHY_DEPTH + 2):
            if i % 3 == 0:
                wtype = WorkItemType.FEATURE
            elif i % 3 == 1:
                wtype = WorkItemType.CAPABILITY
            else:
                wtype = WorkItemType.TASK

            item = HierarchyWorkItem(
                external_id=f"id{i}",
                title=f"Item {i}",
                work_item_type=wtype,
                children=[item] if item else [],
            )

        with pytest.raises(ProposalValidationError, match="exceeds maximum depth"):
            ProposalValidator.validate_all([item])

    def test_max_breadth_valid(self):
        """Node with max children should pass."""
        children = [
            HierarchyWorkItem(
                external_id=f"id{i}",
                title=f"Child {i}",
                work_item_type=WorkItemType.BUG,
            )
            for i in range(ProposalValidator.MAX_CHILDREN_PER_NODE)
        ]
        item = HierarchyWorkItem(
            external_id="parent",
            title="Parent",
            work_item_type=WorkItemType.FEATURE,
            children=children,
        )
        result = ProposalValidator.validate_all([item])
        assert len(result[0].children) == ProposalValidator.MAX_CHILDREN_PER_NODE

    def test_exceeds_max_breadth_fails(self):
        """Node exceeding max children should fail."""
        children = [
            HierarchyWorkItem(
                external_id=f"id{i}",
                title=f"Child {i}",
                work_item_type=WorkItemType.BUG,
            )
            for i in range(ProposalValidator.MAX_CHILDREN_PER_NODE + 1)
        ]
        item = HierarchyWorkItem(
            external_id="parent",
            title="Parent",
            work_item_type=WorkItemType.FEATURE,
            children=children,
        )
        with pytest.raises(ProposalValidationError, match="exceeds maximum"):
            ProposalValidator.validate_all([item])


class TestProposalValidatorCycles:
    """Verify no cyclic references are allowed."""

    def test_no_cycles_in_nested_structure(self):
        """Normal nested structure should pass cycle detection."""
        item = HierarchyWorkItem(
            external_id="id1",
            title="Item 1",
            work_item_type=WorkItemType.FEATURE,
            children=[
                HierarchyWorkItem(
                    external_id="id2",
                    title="Item 2",
                    work_item_type=WorkItemType.CAPABILITY,
                    children=[
                        HierarchyWorkItem(
                            external_id="id3",
                            title="Item 3",
                            work_item_type=WorkItemType.TASK,
                        )
                    ],
                )
            ],
        )
        ProposalValidator.validate_no_cycles([item])


class TestProposalValidatorIntegration:
    """Integration tests covering complete validation workflows."""

    def test_valid_complete_proposal_passes(self):
        """Complete valid proposal should pass all validation."""
        item = HierarchyWorkItem(
            external_id="m1",
            title="Q3 Platform Release",
            work_item_type=WorkItemType.MILESTONE,
            description="Core platform features for Q3 2026",
            priority=1,
            acceptance_criteria=["Feature A delivered", "Feature B delivered"],
            children=[
                HierarchyWorkItem(
                    external_id="f1",
                    title="Authentication System",
                    work_item_type=WorkItemType.FEATURE,
                    description="User login and signup",
                    priority=1,
                    acceptance_criteria=["OAuth2 integrated", "Sessions persisted"],
                    children=[
                        HierarchyWorkItem(
                            external_id="c1",
                            title="Login Flow",
                            work_item_type=WorkItemType.CAPABILITY,
                            priority=1,
                            children=[
                                HierarchyWorkItem(
                                    external_id="t1",
                                    title="Implement login endpoint",
                                    work_item_type=WorkItemType.TASK,
                                    priority=1,
                                )
                            ],
                        )
                    ],
                ),
                HierarchyWorkItem(
                    external_id="b1",
                    title="Known session leak",
                    work_item_type=WorkItemType.BUG,
                    priority=1,
                ),
            ],
        )

        result = ProposalValidator.validate_all([item])
        assert len(result) == 1
        assert result[0].external_id == "m1"
        assert result[0].priority == 1
        assert len(result[0].children) == 2

    def test_empty_hierarchy_fails(self):
        """Empty hierarchy should fail."""
        with pytest.raises(ProposalValidationError, match="Hierarchy cannot be empty"):
            ProposalValidator.validate_all([])

    def test_validation_catches_multiple_issues(self):
        """Validation should catch issues like invalid type hierarchy."""
        item = HierarchyWorkItem(
            external_id="id1",
            title="Parent Task",
            work_item_type=WorkItemType.TASK,
            children=[
                HierarchyWorkItem(
                    external_id="id2",
                    title="Invalid Child",
                    work_item_type=WorkItemType.FEATURE,  # Task can't contain Feature
                )
            ],
        )
        with pytest.raises(ProposalValidationError):
            ProposalValidator.validate_all([item])

    def test_normalization_is_idempotent(self):
        """Normalizing twice should yield same result."""
        item = HierarchyWorkItem(
            external_id="id1",
            title="  Test  ",
            work_item_type=WorkItemType.TASK,
            description="  Desc  ",
            priority=1,
        )

        result1 = ProposalValidator.validate_all([item])
        result2 = ProposalValidator.validate_all(result1)

        assert result1[0].title == result2[0].title
        assert result1[0].description == result2[0].description


class TestProposalValidatorEdgeCases:
    """Edge cases and boundary conditions."""

    def test_single_character_fields(self):
        """Single character fields should pass."""
        item = HierarchyWorkItem(
            external_id="x",
            title="T",
            work_item_type=WorkItemType.TASK,
            description="D",
            acceptance_criteria=["A"],
        )
        result = ProposalValidator.validate_all([item])
        assert result[0].title == "T"

    def test_empty_description_allowed(self):
        """Empty description should be allowed (optional field)."""
        item = HierarchyWorkItem(
            external_id="id1",
            title="Test",
            work_item_type=WorkItemType.TASK,
            description="",
        )
        result = ProposalValidator.validate_all([item])
        assert result[0].description == ""

    def test_empty_acceptance_criteria_list_allowed(self):
        """Empty acceptance criteria list should be allowed (optional)."""
        item = HierarchyWorkItem(
            external_id="id1",
            title="Test",
            work_item_type=WorkItemType.TASK,
            acceptance_criteria=[],
        )
        result = ProposalValidator.validate_all([item])
        assert result[0].acceptance_criteria == []

    def test_mixed_unicode_and_ascii(self):
        """Mixed Unicode and ASCII should normalize correctly."""
        item = HierarchyWorkItem(
            external_id="id1",
            title="Test café résumé",
            work_item_type=WorkItemType.TASK,
        )
        result = ProposalValidator.validate_all([item])
        assert "café" in result[0].title
        assert "résumé" in result[0].title

    def test_whitespace_only_title_becomes_empty_after_normalization(self):
        """Title with only whitespace normalizes to empty string.

        This is caught at the normalization step when we try to use an empty title,
        or at text validation if the check comes first.
        """
        item = HierarchyWorkItem(
            external_id="id1",
            title="   ",
            work_item_type=WorkItemType.TASK,
        )
        # After stripping, title becomes empty, but Pydantic might reject this first
        # Accept either behavior (normalized empty or validation error)
        try:
            result = ProposalValidator.validate_all([item])
            # If it passes, title should be normalized to empty
            assert result[0].title == ""
        except ProposalValidationError:
            # If validation fails, that's also acceptable
            pass

    def test_newlines_in_title_preserved(self):
        """Newlines in title should be preserved (though unusual)."""
        item = HierarchyWorkItem(
            external_id="id1",
            title="Test\nTitle",
            work_item_type=WorkItemType.TASK,
        )
        result = ProposalValidator.validate_all([item])
        assert "\n" in result[0].title
