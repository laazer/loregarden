"""Validator for decomposition proposals before persistence.

Ensures all required fields are present, hierarchy structure is valid,
text fields are normalized, and structural constraints are satisfied.
"""

import re
import unicodedata

from loregarden.models.domain import WorkItemType
from loregarden.models.domain.schemas import HierarchyWorkItem


class ProposalValidationError(Exception):
    """Base exception for proposal validation failures."""

    pass


class ProposalValidator:
    """Validator for decomposition proposals before persistence.

    Ensures all required fields are present, hierarchy structure is valid,
    text fields are normalized, and structural constraints are satisfied.
    """

    # Constraints from research specification
    MAX_TITLE_LENGTH = 1024
    MAX_DESCRIPTION_LENGTH = 10000
    MAX_ACCEPTANCE_CRITERIA_ITEMS = 10
    MAX_ACCEPTANCE_CRITERIA_ITEM_LENGTH = 500
    PRIORITY_MIN = 1
    PRIORITY_MAX = 3
    MAX_HIERARCHY_DEPTH = 10
    MAX_CHILDREN_PER_NODE = 100

    @staticmethod
    def validate_all(hierarchy: list[HierarchyWorkItem]) -> list[HierarchyWorkItem]:
        """Master validation orchestrator.

        Validates complete proposal structure and returns normalized hierarchy.

        Args:
            hierarchy: Root-level work items from decomposition proposal

        Returns:
            Normalized hierarchy ready for persistence

        Raises:
            ProposalValidationError: If any validation constraint fails
        """
        if not hierarchy:
            raise ProposalValidationError("Hierarchy cannot be empty")

        ProposalValidator.validate_external_id_uniqueness(hierarchy)
        ProposalValidator.validate_tree_limits(hierarchy)

        normalized = []
        for item in hierarchy:
            normalized.append(ProposalValidator._normalize_and_validate_item(item))

        return normalized

    @staticmethod
    def _normalize_and_validate_item(item: HierarchyWorkItem, depth: int = 0) -> HierarchyWorkItem:
        """Validate and normalize a single hierarchy item and its children.

        Normalization happens BEFORE validation to catch issues like
        whitespace-only strings that would become empty after normalization.

        Args:
            item: Work item to validate and normalize
            depth: Current depth in hierarchy (0 for roots)

        Returns:
            Normalized HierarchyWorkItem

        Raises:
            ProposalValidationError: If any validation fails
        """
        # Normalize all text fields first
        normalized_title = ProposalValidator.normalize_text(item.title).strip()
        normalized_description = ProposalValidator.normalize_text(
            item.description, preserve_breaks=True
        ).strip()
        normalized_external_id = item.external_id.strip() if item.external_id else ""
        normalized_criteria = [
            ProposalValidator.normalize_text(ac).strip() for ac in item.acceptance_criteria
        ]

        # Now validate the normalized values
        ProposalValidator.validate_required_fields_normalized(
            normalized_external_id, normalized_title, item.work_item_type
        )
        ProposalValidator.validate_priority(item.priority)
        ProposalValidator.validate_text_fields_normalized(
            normalized_title,
            normalized_description,
            normalized_criteria,
        )

        if depth > 0:
            ProposalValidator.validate_no_orphans(item)

        normalized_children = []
        for child in item.children:
            ProposalValidator.validate_hierarchy_types(item.work_item_type, child.work_item_type)
            normalized_children.append(
                ProposalValidator._normalize_and_validate_item(child, depth + 1)
            )

        return HierarchyWorkItem(
            external_id=normalized_external_id,
            title=normalized_title,
            work_item_type=item.work_item_type,
            description=normalized_description,
            acceptance_criteria=normalized_criteria,
            priority=item.priority,
            parent_ticket_id=item.parent_ticket_id,
            children=normalized_children,
        )

    @staticmethod
    def validate_required_fields(item: HierarchyWorkItem) -> None:
        """Verify all required fields are present and non-empty.

        This validates raw (non-normalized) values. Prefer validate_required_fields_normalized
        for post-normalization validation.

        Args:
            item: Work item to validate

        Raises:
            ProposalValidationError: If required fields are missing or empty
        """
        if not item.external_id:
            raise ProposalValidationError("external_id is required and cannot be empty")
        if not item.title:
            raise ProposalValidationError("title is required and cannot be empty")
        if item.work_item_type is None:
            raise ProposalValidationError("work_item_type is required")

    @staticmethod
    def validate_required_fields_normalized(
        external_id: str, title: str, work_item_type: WorkItemType
    ) -> None:
        """Verify normalized required fields are present and non-empty.

        Validates after normalization/stripping to catch edge cases like
        whitespace-only strings that become empty.

        Args:
            external_id: Normalized external_id
            title: Normalized title
            work_item_type: Work item type

        Raises:
            ProposalValidationError: If required fields are empty after normalization
        """
        if not external_id:
            raise ProposalValidationError("external_id is required and cannot be empty")
        if not title:
            raise ProposalValidationError("title is required and cannot be empty")
        if work_item_type is None:
            raise ProposalValidationError("work_item_type is required")

    @staticmethod
    def validate_hierarchy_types(parent_type: WorkItemType, child_type: WorkItemType) -> None:
        """Validate parent-child relationship per VALID_HIERARCHY rules.

        Args:
            parent_type: Parent work item type
            child_type: Child work item type

        Raises:
            ProposalValidationError: If relationship is not allowed
        """
        from loregarden.models.domain.enums import VALID_HIERARCHY

        allowed = VALID_HIERARCHY.get(parent_type, [])
        if child_type not in allowed:
            raise ProposalValidationError(
                f"{parent_type.value} cannot contain {child_type.value} children"
            )

    @staticmethod
    def validate_no_orphans(item: HierarchyWorkItem) -> None:
        """Verify item is properly structured (not orphaned in isolation).

        During proposal phase, parent_ticket_id is None, so we verify tree
        structure consistency. Each child must have access to parent context.

        Args:
            item: Item to check

        Raises:
            ProposalValidationError: If structural issues detected
        """
        for child in item.children:
            if not child.external_id:
                raise ProposalValidationError("Child item missing external_id")

    @staticmethod
    def validate_no_cycles(hierarchy: list[HierarchyWorkItem]) -> None:
        """Detect cyclic references in hierarchy tree.

        Note: Current implementation uses nested objects, making cycles
        structurally impossible. This validates against future API changes.

        Args:
            hierarchy: Root items to check

        Raises:
            ProposalValidationError: If cycle detected
        """
        visited = set()
        rec_stack = set()

        def has_cycle(item: HierarchyWorkItem) -> bool:
            if item.external_id in rec_stack:
                return True
            if item.external_id in visited:
                return False

            visited.add(item.external_id)
            rec_stack.add(item.external_id)

            for child in item.children:
                if has_cycle(child):
                    return True

            rec_stack.remove(item.external_id)
            return False

        for root in hierarchy:
            if has_cycle(root):
                raise ProposalValidationError("Cyclic reference detected in hierarchy")

    @staticmethod
    def validate_external_id_uniqueness(hierarchy: list[HierarchyWorkItem]) -> None:
        """Verify no duplicate external IDs within proposal.

        Args:
            hierarchy: All root items in proposal

        Raises:
            ProposalValidationError: If duplicate IDs found
        """
        seen_ids = set()

        def check_ids(item: HierarchyWorkItem) -> None:
            if item.external_id in seen_ids:
                raise ProposalValidationError(f"Duplicate external_id: {item.external_id}")
            seen_ids.add(item.external_id)
            for child in item.children:
                check_ids(child)

        for root in hierarchy:
            check_ids(root)

    @staticmethod
    def validate_priority(priority: int) -> None:
        """Verify priority is in valid range [1, 2, 3].

        Args:
            priority: Priority value to validate

        Raises:
            ProposalValidationError: If priority outside valid range
        """
        if not isinstance(priority, int):
            raise ProposalValidationError(f"Priority must be integer, got {type(priority)}")
        if not (ProposalValidator.PRIORITY_MIN <= priority <= ProposalValidator.PRIORITY_MAX):
            raise ProposalValidationError(f"Priority must be 1-3, got {priority}")

    @staticmethod
    def validate_text_fields(item: HierarchyWorkItem) -> None:
        """Validate text field lengths and encoding.

        This validates raw (non-normalized) values. Prefer validate_text_fields_normalized
        for post-normalization validation.

        Args:
            item: Work item with text fields to validate

        Raises:
            ProposalValidationError: If text constraints violated
        """
        if len(item.title) > ProposalValidator.MAX_TITLE_LENGTH:
            raise ProposalValidationError(
                f"Title exceeds max length {ProposalValidator.MAX_TITLE_LENGTH} "
                f"(got {len(item.title)})"
            )

        if len(item.description) > ProposalValidator.MAX_DESCRIPTION_LENGTH:
            raise ProposalValidationError(
                f"Description exceeds max length {ProposalValidator.MAX_DESCRIPTION_LENGTH} "
                f"(got {len(item.description)})"
            )

        if len(item.acceptance_criteria) > ProposalValidator.MAX_ACCEPTANCE_CRITERIA_ITEMS:
            raise ProposalValidationError(
                f"Acceptance criteria exceeds max items {ProposalValidator.MAX_ACCEPTANCE_CRITERIA_ITEMS} "
                f"(got {len(item.acceptance_criteria)})"
            )

        for i, criterion in enumerate(item.acceptance_criteria):
            if len(criterion) > ProposalValidator.MAX_ACCEPTANCE_CRITERIA_ITEM_LENGTH:
                raise ProposalValidationError(
                    f"Acceptance criterion {i} exceeds max length "
                    f"{ProposalValidator.MAX_ACCEPTANCE_CRITERIA_ITEM_LENGTH} "
                    f"(got {len(criterion)})"
                )

    @staticmethod
    def validate_text_fields_normalized(
        title: str, description: str, acceptance_criteria: list[str]
    ) -> None:
        """Validate normalized text field lengths and encoding.

        Args:
            title: Normalized title
            description: Normalized description
            acceptance_criteria: Normalized acceptance criteria list

        Raises:
            ProposalValidationError: If text constraints violated
        """
        if len(title) > ProposalValidator.MAX_TITLE_LENGTH:
            raise ProposalValidationError(
                f"Title exceeds max length {ProposalValidator.MAX_TITLE_LENGTH} (got {len(title)})"
            )

        if len(description) > ProposalValidator.MAX_DESCRIPTION_LENGTH:
            raise ProposalValidationError(
                f"Description exceeds max length {ProposalValidator.MAX_DESCRIPTION_LENGTH} "
                f"(got {len(description)})"
            )

        if len(acceptance_criteria) > ProposalValidator.MAX_ACCEPTANCE_CRITERIA_ITEMS:
            raise ProposalValidationError(
                f"Acceptance criteria exceeds max items {ProposalValidator.MAX_ACCEPTANCE_CRITERIA_ITEMS} "
                f"(got {len(acceptance_criteria)})"
            )

        for i, criterion in enumerate(acceptance_criteria):
            if len(criterion) > ProposalValidator.MAX_ACCEPTANCE_CRITERIA_ITEM_LENGTH:
                raise ProposalValidationError(
                    f"Acceptance criterion {i} exceeds max length "
                    f"{ProposalValidator.MAX_ACCEPTANCE_CRITERIA_ITEM_LENGTH} "
                    f"(got {len(criterion)})"
                )

    @staticmethod
    def validate_tree_limits(
        hierarchy: list[HierarchyWorkItem],
        depth: int = 0,
        max_depth: int = MAX_HIERARCHY_DEPTH,
        max_breadth: int = MAX_CHILDREN_PER_NODE,
    ) -> None:
        """Check hierarchy doesn't exceed depth and breadth limits.

        Args:
            hierarchy: Items to validate
            depth: Current depth in traversal
            max_depth: Maximum allowed hierarchy depth
            max_breadth: Maximum children per node

        Raises:
            ProposalValidationError: If limits exceeded
        """
        for item in hierarchy:
            if depth > max_depth:
                raise ProposalValidationError(
                    f"Hierarchy exceeds maximum depth {max_depth} (at item {item.external_id})"
                )

            if len(item.children) > max_breadth:
                raise ProposalValidationError(
                    f"Item {item.external_id} has {len(item.children)} children, "
                    f"exceeds maximum {max_breadth}"
                )

            ProposalValidator.validate_tree_limits(item.children, depth + 1, max_depth, max_breadth)

    @staticmethod
    def normalize_text(text: str, preserve_breaks: bool = False) -> str:
        """Normalize text: Unicode NFC, whitespace handling.

        Args:
            text: Text to normalize
            preserve_breaks: If True, collapse 3+ newlines to 2; if False, strip all

        Returns:
            Normalized text
        """
        if not isinstance(text, str):
            text = str(text)

        text = unicodedata.normalize("NFC", text)

        if preserve_breaks:
            text = text.strip()
            text = re.sub(r"\n\n+", "\n\n", text)
        else:
            text = text.strip()

        return text
