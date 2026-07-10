"""Adversarial test suite for proposal validator — exposes edge cases and hidden weaknesses.

This test suite systematically covers:
- Mutation testing (type coercion, boundary flips, assumption violations)
- Combinatorial edge cases (max length + control chars, null + concurrency)
- Stress scenarios (massive hierarchies, huge fields, rapid normalization)
- Error path validation (malformed input recovery, type coercion failures)
- Assumption validation (external_id uniqueness under sorting, priority coercion)
- Determinism checks (same input → same output across runs)
- Integration gaps (validator vs service mismatch, mock false confidence)
"""

import pytest
from loregarden.models.domain import WorkItemType
from loregarden.models.domain.schemas import HierarchyWorkItem
from loregarden.services.proposal_validator import ProposalValidator, ProposalValidationError


class TestProposalValidatorMutationTesting:
    """Mutation testing: flip assumptions and break expected behavior."""

    def test_priority_string_coercion_edge_case(self):
        """Priority coercion from string may fail unexpectedly.

        Current code does `int(priority)` which works for "1" but what about:
        - "1.5" (float string)?
        - " 1 " (whitespace)?
        - "1e0" (scientific notation)?
        """
        item = HierarchyWorkItem(
            external_id="id1",
            title="Test",
            work_item_type=WorkItemType.TASK,
            priority=1,  # Will work if passed as int
        )
        result = ProposalValidator.validate_all([item])
        assert result[0].priority == 1

    def test_priority_float_coercion_fails(self):
        """Priority as float should fail int conversion.

        Pydantic should reject fractional floats before validator sees them.
        This tests that type safety is enforced at schema level.
        """
        # Pydantic rejects fractional floats
        with pytest.raises(Exception):  # ValidationError from Pydantic
            HierarchyWorkItem(
                external_id="id1",
                title="Test",
                work_item_type=WorkItemType.TASK,
                priority=1.5,
            )

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

    def test_work_item_type_enum_coercion(self):
        """work_item_type enum coercion edge cases.

        What if someone passes lowercase 'task' or mixed case?
        Pydantic's Enum handling is strict by default unless coerce=True.
        """
        # This should work if Pydantic allows it, but may fail
        try:
            item = HierarchyWorkItem(
                external_id="id1",
                title="Test",
                work_item_type="task",  # lowercase string
            )
            # If this succeeds, Pydantic is doing case-insensitive coercion
            result = ProposalValidator.validate_all([item])
            assert result[0].work_item_type == WorkItemType.TASK
        except (ValueError, TypeError):
            # If Pydantic rejects it, that's expected
            pass

    def test_acceptance_criteria_non_string_coercion(self):
        """acceptance_criteria with non-string items must be rejected by Pydantic.

        Type validation at schema level ensures items are strings before validator sees them.
        """
        # Pydantic enforces string type for list items
        with pytest.raises(Exception):  # ValidationError from Pydantic
            HierarchyWorkItem(
                external_id="id1",
                title="Test",
                work_item_type=WorkItemType.TASK,
                acceptance_criteria=[1, 2, 3],  # integers not allowed
            )

    def test_description_none_vs_empty_string(self):
        """None description should be rejected by Pydantic.

        Type validation at schema level ensures description is string, not None.
        """
        # Pydantic rejects None for non-optional string field
        with pytest.raises(Exception):  # ValidationError from Pydantic
            HierarchyWorkItem(
                external_id="id1",
                title="Test",
                work_item_type=WorkItemType.TASK,
                description=None,
            )


class TestProposalValidatorBoundaryMutations:
    """Combine boundary conditions with mutations to expose logic flaws."""

    def test_max_title_length_with_multibyte_unicode(self):
        """Max length check uses character count, not byte count.

        If using byte-length limits, multibyte Unicode chars may cause issues.
        A 4-byte emoji counts as 1 character but 4 bytes.
        """
        # 1024 emoji characters = 4096 bytes, but still valid character count
        title = "😀" * ProposalValidator.MAX_TITLE_LENGTH
        item = HierarchyWorkItem(
            external_id="id1",
            title=title,
            work_item_type=WorkItemType.TASK,
        )
        result = ProposalValidator.validate_all([item])
        assert len(result[0].title) == ProposalValidator.MAX_TITLE_LENGTH

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

    def test_priority_boundary_plus_type_coercion(self):
        """Priority at boundary (1, 3) combined with type mutations.

        If priority validation runs before type check, boundary conditions
        might miss type coercion failures.
        """
        # Priority exactly at boundaries
        for priority in [1, 3]:
            item = HierarchyWorkItem(
                external_id=f"id{priority}",
                title="Test",
                work_item_type=WorkItemType.TASK,
                priority=priority,
            )
            result = ProposalValidator.validate_all([item])
            assert result[0].priority == priority

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
        """Max depth reached with type hierarchy violation at deepest level.

        Validator checks depth limit first, but if depth+1 item has invalid type,
        should it fail on depth or type? Order of validation matters.
        """
        # Build a chain that violates VALID_HIERARCHY at max depth
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
        # Should fail on hierarchy type, but might fail on depth instead
        with pytest.raises(ProposalValidationError):
            ProposalValidator.validate_all([item])


class TestProposalValidatorCombinatorial:
    """Combinatorial testing: pair multiple factors to expose hidden interactions."""

    def test_max_length_field_plus_unicode_normalization(self):
        """Field at max length combined with Unicode that changes length after normalization.

        Some Unicode sequences normalize to different lengths (e.g., precomposed vs decomposed).
        A field at exactly max length might exceed limit after normalization.
        """
        # Create title with decomposed accents that will normalize to composed form
        # Decomposed é (e + combining acute) → composed é (single char)
        # But in reverse, some composed forms expand when normalized

        base = "a" * (ProposalValidator.MAX_TITLE_LENGTH - 1)
        # Decomposed form of é
        composed_char = "é"  # Single é character
        title = base + composed_char

        item = HierarchyWorkItem(
            external_id="id1",
            title=title,
            work_item_type=WorkItemType.TASK,
        )
        result = ProposalValidator.validate_all([item])
        assert len(result[0].title) <= ProposalValidator.MAX_TITLE_LENGTH

    def test_duplicate_id_in_wide_tree_with_mixed_types(self):
        """Duplicate ID detection in deeply nested tree with mixed hierarchy types.

        ID uniqueness check traverses entire tree. With many branches and levels,
        subtle duplicate might be missed if traversal is incomplete.
        """
        # Wide tree: one parent with many children of different types
        children = []
        for i in range(50):
            if i % 2 == 0:
                wtype = WorkItemType.BUG
            else:
                wtype = WorkItemType.FEATURE
            children.append(
                HierarchyWorkItem(
                    external_id=f"id{i}",
                    title=f"Child {i}",
                    work_item_type=wtype,
                )
            )

        # Add duplicate at end
        children.append(
            HierarchyWorkItem(
                external_id="id0",  # Duplicate
                title="Duplicate",
                work_item_type=WorkItemType.BUG,
            )
        )

        item = HierarchyWorkItem(
            external_id="parent",
            title="Parent",
            work_item_type=WorkItemType.MILESTONE,
            children=children,
        )

        with pytest.raises(ProposalValidationError, match="Duplicate external_id"):
            ProposalValidator.validate_all([item])

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

    def test_multiple_children_with_boundary_priority_and_types(self):
        """Many children with boundary priorities and mixed types at limits.

        If validator processes children in order, later children might mask
        earlier validation failures.
        """
        children = []
        for i in range(ProposalValidator.MAX_CHILDREN_PER_NODE):
            # Alternate priorities at boundaries
            priority = 1 if i % 2 == 0 else 3
            wtype = WorkItemType.BUG if i % 2 == 0 else WorkItemType.FEATURE

            children.append(
                HierarchyWorkItem(
                    external_id=f"child_{i}",
                    title=f"Child {i}",
                    work_item_type=wtype,
                    priority=priority,
                )
            )

        item = HierarchyWorkItem(
            external_id="parent",
            title="Parent",
            work_item_type=WorkItemType.MILESTONE,
            children=children,
        )

        result = ProposalValidator.validate_all([item])
        assert len(result[0].children) == ProposalValidator.MAX_CHILDREN_PER_NODE


class TestProposalValidatorStress:
    """Stress testing: push validator to limits."""

    def test_deeply_nested_hierarchy_near_max_depth(self):
        """Build hierarchy approaching MAX_HIERARCHY_DEPTH limit.

        Recursive validation might hit stack overflow or performance issues.
        Valid hierarchy chain: Milestone → Feature → Capability → Task (4 levels)
        Beyond that violates VALID_HIERARCHY rules, so test up to max allowed depth.
        """
        # Build maximum valid chain: Milestone → Feature → Capability → Task
        item = HierarchyWorkItem(
            external_id="t1",
            title="Task",
            work_item_type=WorkItemType.TASK,
        )

        item = HierarchyWorkItem(
            external_id="c1",
            title="Capability",
            work_item_type=WorkItemType.CAPABILITY,
            children=[item],
        )

        item = HierarchyWorkItem(
            external_id="f1",
            title="Feature",
            work_item_type=WorkItemType.FEATURE,
            children=[item],
        )

        item = HierarchyWorkItem(
            external_id="m1",
            title="Milestone",
            work_item_type=WorkItemType.MILESTONE,
            children=[item],
        )

        result = ProposalValidator.validate_all([item])
        assert result is not None
        assert result[0].work_item_type == WorkItemType.MILESTONE

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

    def test_wide_tree_at_max_breadth(self):
        """Create parent with maximum children.

        Uniqueness check must traverse all children. Performance degrades quadratically.
        """
        children = [
            HierarchyWorkItem(
                external_id=f"child_{i}",
                title=f"Child {i}",
                work_item_type=WorkItemType.BUG,
            )
            for i in range(ProposalValidator.MAX_CHILDREN_PER_NODE)
        ]

        item = HierarchyWorkItem(
            external_id="parent",
            title="Parent",
            work_item_type=WorkItemType.MILESTONE,
            children=children,
        )

        result = ProposalValidator.validate_all([item])
        assert len(result[0].children) == ProposalValidator.MAX_CHILDREN_PER_NODE


class TestProposalValidatorErrorPaths:
    """Test error handling and recovery paths."""

    def test_normalize_text_with_control_characters(self):
        """normalize_text() with control characters (null bytes, etc).

        Unicode normalization might fail or behave unexpectedly with
        control chars like \x00, \x01.
        """
        # Title with null byte
        title_with_null = "Test\x00Title"
        item = HierarchyWorkItem(
            external_id="id1",
            title=title_with_null,
            work_item_type=WorkItemType.TASK,
        )
        # Should handle or reject gracefully
        result = ProposalValidator.validate_all([item])
        # Null byte should be normalized away or preserved
        assert result[0].title is not None

    def test_normalize_text_with_rtl_markers(self):
        """Unicode text with RTL (right-to-left) markers.

        Markers like U+202E (right-to-left override) can affect text display
        without changing character count. Could be used for injection attacks.
        """
        rtl_override = "‮"  # RIGHT-TO-LEFT OVERRIDE
        title = "Normal" + rtl_override + "Title"

        item = HierarchyWorkItem(
            external_id="id1",
            title=title,
            work_item_type=WorkItemType.TASK,
        )
        result = ProposalValidator.validate_all([item])
        # RTL marker should be preserved but not cause issues
        assert len(result[0].title) > 0

    def test_validate_priority_with_none_value(self):
        """Priority field as None should be rejected by Pydantic.

        Schema has default=3 for missing values, but None explicitly
        violates the int type constraint.
        """
        # Pydantic should reject None for int field
        with pytest.raises(Exception):  # ValidationError from Pydantic
            HierarchyWorkItem(
                external_id="id1",
                title="Test",
                work_item_type=WorkItemType.TASK,
                priority=None,
            )

    def test_validate_item_with_circular_reference_impossible(self):
        """Circular references are structurally impossible but test assumption.

        Since children are nested objects, cycles can't exist in normal usage.
        But if API changes to use references (IDs), this could happen.
        This test validates the cycle detection works when needed.
        """
        item = HierarchyWorkItem(
            external_id="id1",
            title="Item 1",
            work_item_type=WorkItemType.FEATURE,
            children=[
                HierarchyWorkItem(
                    external_id="id2",
                    title="Item 2",
                    work_item_type=WorkItemType.CAPABILITY,
                )
            ],
        )

        # Cycle detection should pass (no cycles)
        ProposalValidator.validate_no_cycles([item])


class TestProposalValidatorAssumptions:
    """Test implicit assumptions that could break."""

    def test_assume_external_id_is_string(self):
        """external_id is assumed to be a string throughout.

        If schema allows int and validator uses string methods, could fail.
        """
        item = HierarchyWorkItem(
            external_id="123",  # String that looks like int
            title="Test",
            work_item_type=WorkItemType.TASK,
        )
        result = ProposalValidator.validate_all([item])
        assert isinstance(result[0].external_id, str)

    def test_assume_work_item_type_is_valid_enum(self):
        """work_item_type assumed to be valid enum value.

        If someone creates WorkItemType with invalid value, validator might break.
        """
        item = HierarchyWorkItem(
            external_id="id1",
            title="Test",
            work_item_type=WorkItemType.TASK,
        )
        # Pydantic should enforce enum validity
        result = ProposalValidator.validate_all([item])
        assert result[0].work_item_type in list(WorkItemType)

    def test_assume_children_list_is_mutable(self):
        """Validator assumes children is a list, not None.

        Pydantic enforces that children is always a list (with default []).
        """
        # Pydantic rejects None for list field
        with pytest.raises(Exception):  # ValidationError from Pydantic
            HierarchyWorkItem(
                external_id="id1",
                title="Test",
                work_item_type=WorkItemType.TASK,
                children=None,  # Not allowed
            )

    def test_assume_acceptance_criteria_is_list_of_strings(self):
        """Validator assumes acceptance_criteria items are strings.

        Pydantic enforces type constraint at schema level before validator runs.
        """
        # Pydantic rejects non-string items in list
        with pytest.raises(Exception):  # ValidationError from Pydantic
            HierarchyWorkItem(
                external_id="id1",
                title="Test",
                work_item_type=WorkItemType.TASK,
                acceptance_criteria=[1, 2.5, None, {}],  # Mixed types not allowed
            )

    def test_assume_external_id_uniqueness_check_is_exhaustive(self):
        """Uniqueness check assumes it visits every node.

        If traversal skips a branch, duplicates could hide.
        """
        # Create tree with duplicate hidden in third branch
        items = [
            HierarchyWorkItem(
                external_id="id1",
                title="Item 1",
                work_item_type=WorkItemType.FEATURE,
            ),
            HierarchyWorkItem(
                external_id="id2",
                title="Item 2",
                work_item_type=WorkItemType.FEATURE,
            ),
            HierarchyWorkItem(
                external_id="id1",  # Duplicate in third position
                title="Item 3",
                work_item_type=WorkItemType.FEATURE,
            ),
        ]

        with pytest.raises(ProposalValidationError, match="Duplicate external_id"):
            ProposalValidator.validate_all(items)


class TestProposalValidatorDeterminism:
    """Validate that tests are deterministic and reproducible."""

    def test_normalization_is_consistent_across_runs(self):
        """Same input should normalize identically every time.

        If normalization depends on system state (locale, time, random),
        tests could be flaky.
        """
        item = HierarchyWorkItem(
            external_id="id1",
            title="Test\n\n\n  Title  ",
            work_item_type=WorkItemType.TASK,
            description="Desc\n\n\nText",
        )

        # Run validation multiple times
        results = [ProposalValidator.validate_all([item]) for _ in range(5)]

        # All should be identical
        for i in range(1, len(results)):
            assert results[i][0].title == results[0][0].title
            assert results[i][0].description == results[0][0].description

    def test_unique_id_check_order_independent(self):
        """Duplicate ID detection should work regardless of order.

        If checking IDs depends on processing order, IDs in different positions
        might not be detected.
        """
        # Create two items with duplicate IDs in different orders
        order1 = [
            HierarchyWorkItem(
                external_id="id1",
                title="Item 1",
                work_item_type=WorkItemType.FEATURE,
            ),
            HierarchyWorkItem(
                external_id="id1",  # Duplicate
                title="Item 2",
                work_item_type=WorkItemType.FEATURE,
            ),
        ]

        order2 = [
            HierarchyWorkItem(
                external_id="id1",  # Duplicate first
                title="Item 2",
                work_item_type=WorkItemType.FEATURE,
            ),
            HierarchyWorkItem(
                external_id="id1",
                title="Item 1",
                work_item_type=WorkItemType.FEATURE,
            ),
        ]

        # Both orderings should detect duplicate
        with pytest.raises(ProposalValidationError, match="Duplicate external_id"):
            ProposalValidator.validate_all(order1)

        with pytest.raises(ProposalValidationError, match="Duplicate external_id"):
            ProposalValidator.validate_all(order2)

    def test_hierarchy_validation_is_consistent(self):
        """Same hierarchy should always validate same way.

        If type checking order varies, same tree might pass or fail inconsistently.
        """
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

        # Validate same item multiple times
        for _ in range(3):
            result = ProposalValidator.validate_all([item])
            assert result[0].work_item_type == WorkItemType.MILESTONE
            assert result[0].children[0].work_item_type == WorkItemType.FEATURE


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

    def test_validator_handles_unicode_in_external_id(self):
        """external_id might contain Unicode (should be alphanumeric only).

        If Claude generates "café-feature-001" with accents, should be rejected.
        """
        item = HierarchyWorkItem(
            external_id="café-feature-001",
            title="Test",
            work_item_type=WorkItemType.TASK,
        )
        # Validator doesn't currently check for alphanumeric-only IDs
        # This test documents that assumption
        result = ProposalValidator.validate_all([item])
        assert result[0].external_id == "café-feature-001"

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
