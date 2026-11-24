"""Edge cases for the proposal validator: normalization order, limits, deep trees."""

import pytest
from loregarden.models.domain import WorkItemType
from loregarden.models.domain.schemas import HierarchyWorkItem
from loregarden.services.proposal_validator import ProposalValidationError, ProposalValidator
from pydantic import ValidationError


@pytest.mark.parametrize(
    "field, value",
    [
        ("work_item_type", None),
        ("work_item_type", "TASK"),
        ("priority", 1.5),
        ("priority", None),
        ("description", None),
        ("children", None),
        ("acceptance_criteria", [1, 2, 3]),
        ("acceptance_criteria", [None]),
    ],
)
def test_the_schema_rejects_the_wrong_type_before_the_validator_sees_it(field, value):
    fields = {"external_id": "id1", "title": "Test", "work_item_type": WorkItemType.TASK}
    with pytest.raises(ValidationError):
        HierarchyWorkItem.model_validate({**fields, field: value})


def test_length_is_measured_in_characters_after_normalization():
    """A decomposed é is two characters raw and one once normalized."""
    title = "a" * (ProposalValidator.MAX_TITLE_LENGTH - 1) + "e\u0301"
    item = HierarchyWorkItem(external_id="id1", title=title, work_item_type=WorkItemType.TASK)
    (validated,) = ProposalValidator.validate_all([item])
    assert validated.title == "a" * (ProposalValidator.MAX_TITLE_LENGTH - 1) + "\u00e9"


class TestProposalValidatorMutationTesting:
    """Mutation testing: flip assumptions and break expected behavior."""

    def test_external_id_whitespace_only(self):
        """external_id with only whitespace passes Pydantic but fails validator.

        Current code checks `not external_id` but whitespace-only strings are truthy.
        Normalization doesn't strip external_id, so "   " passes creation but is invalid.
        """
        item = HierarchyWorkItem(
            external_id="   ",
            title="Test",
            work_item_type=WorkItemType.TASK,
        )
        # After normalization, this should fail or be caught
        with pytest.raises(ProposalValidationError):
            ProposalValidator.validate_all([item])

    def test_title_only_whitespace_after_normalization(self):
        """Title that becomes empty after normalization should fail.

        normalize_text() strips leading/trailing whitespace. A title of "   "
        becomes "" after normalization, which violates the "title is required" rule.
        """
        item = HierarchyWorkItem(
            external_id="id1",
            title="   \n  \t  ",
            work_item_type=WorkItemType.TASK,
        )
        # Should fail because normalized title is empty
        with pytest.raises(ProposalValidationError, match="title is required"):
            ProposalValidator.validate_all([item])


class TestProposalValidatorBoundaryMutations:
    """Combine boundary conditions with mutations to expose logic flaws."""

    def test_description_max_with_zero_width_characters(self):
        """Max length check doesn't account for zero-width characters.

        Zero-width joiner (U+200D), zero-width space (U+200B) still count as
        characters but are invisible. Could pad description with these.
        """
        # Create description at limit, then add zero-width chars
        base_desc = "x" * ProposalValidator.MAX_DESCRIPTION_LENGTH
        zwj = "‍"  # zero-width joiner
        desc_with_zwc = base_desc + zwj * 100

        item = HierarchyWorkItem(
            external_id="id1",
            title="Test",
            work_item_type=WorkItemType.TASK,
            description=desc_with_zwc,
        )
        # This should fail but might pass if length check happens before normalization
        with pytest.raises(ProposalValidationError):
            ProposalValidator.validate_all([item])

    def test_acceptance_criteria_boundary_with_empty_strings(self):
        """Max criteria items with some being empty strings.

        Current validator checks list length and item length independently.
        Empty strings pass length check (0 chars) but violate semantic requirement.
        """
        criteria = ["Valid criterion"] * 9 + [""]
        item = HierarchyWorkItem(
            external_id="id1",
            title="Test",
            work_item_type=WorkItemType.TASK,
            acceptance_criteria=criteria,
        )
        result = ProposalValidator.validate_all([item])
        # Empty string is normalized but not rejected
        assert "" in result[0].acceptance_criteria

    def test_hierarchy_depth_boundary_plus_invalid_types(self):
        """A type violation at the deepest level is found, not only at the top."""
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
                                    children=[
                                        HierarchyWorkItem(
                                            external_id="invalid",
                                            title="Invalid",
                                            work_item_type=WorkItemType.MILESTONE,  # Invalid: Task can't contain Milestone
                                        )
                                    ],
                                )
                            ],
                        )
                    ],
                )
            ],
        )
        with pytest.raises(ProposalValidationError, match="task cannot contain milestone"):
            ProposalValidator.validate_all([item])


class TestProposalValidatorCombinatorial:
    """Combinatorial testing: pair multiple factors to expose hidden interactions."""

    def test_whitespace_normalization_plus_length_check_order(self):
        """Normalization happens BEFORE validation (fixed vulnerability).

        With corrected validation order, whitespace-padded content is properly handled:

        1. Create title = "  " + "x"*1024 + "  " (1228 chars raw)
        2. NORMALIZE first: strips to "x"*1024 (1024 chars)
        3. VALIDATE normalized value: 1024 <= 1024 ✓ (valid)

        This test verifies the fix: proposals with whitespace padding should pass.
        """
        # Title that exceeds limit with whitespace but fits after normalization
        raw_title = " " * 100 + "x" * ProposalValidator.MAX_TITLE_LENGTH + " " * 100

        item = HierarchyWorkItem(
            external_id="id1",
            title=raw_title,
            work_item_type=WorkItemType.TASK,
        )

        # Should pass because normalization happens before validation
        result = ProposalValidator.validate_all([item])
        assert len(result) == 1
        assert result[0].title == "x" * ProposalValidator.MAX_TITLE_LENGTH


class TestProposalValidatorStress:
    """Stress testing: push validator to limits."""

    def test_very_large_acceptance_criteria_near_max(self):
        """Create item with maximum acceptance criteria items.

        Each criterion at or near max individual length.
        """
        criteria = [
            "x" * ProposalValidator.MAX_ACCEPTANCE_CRITERIA_ITEM_LENGTH
            for _ in range(ProposalValidator.MAX_ACCEPTANCE_CRITERIA_ITEMS)
        ]

        item = HierarchyWorkItem(
            external_id="id1",
            title="Test",
            work_item_type=WorkItemType.TASK,
            acceptance_criteria=criteria,
        )

        result = ProposalValidator.validate_all([item])
        assert len(result[0].acceptance_criteria) == ProposalValidator.MAX_ACCEPTANCE_CRITERIA_ITEMS


class TestProposalValidatorMockVsReality:
    """Integration tests exposing gaps between mocked and real scenarios."""

    def test_validator_rejects_claude_malformed_response(self):
        """Validator should handle partially-valid Claude responses.

        In real usage, Claude might return hierarchies with subtle type errors,
        missing fields in deeply nested items, etc.
        """
        # Simulate Claude returning a deep hierarchy with one bad item in the middle
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
                            external_id="",  # Missing ID
                            title="Bad Capability",
                            work_item_type=WorkItemType.CAPABILITY,
                        )
                    ],
                )
            ],
        )

        with pytest.raises(ProposalValidationError):
            ProposalValidator.validate_all([item])

    def test_roundtrip_validation_preserves_meaning(self):
        """Validate once, then validate the result again.

        Normalized output should re-validate to identical result (idempotent).
        """
        item = HierarchyWorkItem(
            external_id="id1",
            title="  Test  ",
            work_item_type=WorkItemType.TASK,
            description="  Desc  ",
            acceptance_criteria=["  Criterion  "],
        )

        result1 = ProposalValidator.validate_all([item])
        result2 = ProposalValidator.validate_all(result1)

        assert result1[0].title == result2[0].title
        assert result1[0].description == result2[0].description
        assert result1[0].acceptance_criteria == result2[0].acceptance_criteria
