"""Hierarchy decomposition — prompt build + response parse.

LLM turns go through the shared CLI seam (``run_cli_agent_turn`` /
``resolve_model_for_adapter``), never a direct Anthropic SDK call. Callers that
need a live model pass a ``generate`` callable that already resolved adapter +
model the same way triage and ticket studio do.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Callable

from loregarden.models.domain import VALID_HIERARCHY, WorkItemType
from loregarden.models.domain.schemas import HierarchyWorkItem
from loregarden.services.proposal_validator import ProposalValidationError, ProposalValidator
from loregarden.services.ticket_studio_service import extract_json_block

logger = logging.getLogger(__name__)

GenerateFn = Callable[[str], str]


def _require_criteria_partition(parent: list[str], children: list[HierarchyWorkItem]) -> None:
    """Refuse a split that drops, invents or duplicates a criterion.

    A criterion lost here is scope nobody finds out was cut: the parent closes
    once its children do.
    """
    expected = Counter(ProposalValidator.normalize_text(ac) for ac in parent)
    proposed = Counter(ac for child in children for ac in child.acceptance_criteria)
    if proposed != expected:
        missing = sum((expected - proposed).values())
        extra = sum((proposed - expected).values())
        raise ValueError(
            f"Split must carry every criterion exactly once: {missing} missing, "
            f"{extra} added or duplicated"
        )


class DecompositionService:
    """Generates hierarchical work item breakdowns via an injected model turn."""

    def __init__(self, generate: GenerateFn | None = None):
        """``generate`` maps a prompt to raw model text (JSON hierarchy).

        Production wiring should be a closure over ``run_cli_agent_turn`` with the
        workspace's effective adapter already applied — same path as ticket studio.
        """
        self._generate = generate

    def decompose(self, ticket_content: dict) -> list[HierarchyWorkItem]:
        """Generate hierarchy proposal for a ticket.

        Args:
            ticket_content: Dict with keys: title, description, acceptance_criteria

        Returns:
            List of HierarchyWorkItem objects representing the proposed hierarchy.
            Empty list if decomposition fails.

        Raises:
            ValueError: If hierarchy validation or normalization fails, or no
                generator was configured.
            ProposalValidationError: If proposal doesn't conform to structure constraints.
        """
        if not ticket_content:
            return []

        if self._generate is None:
            raise ValueError(
                "DecompositionService requires a generate callable wired through the "
                "CLI agent seam (run_cli_agent_turn); direct SDK calls are not supported"
            )

        prompt = self._build_prompt(ticket_content)

        try:
            response_text = self._generate(prompt)
            hierarchy = self._parse_response(response_text)
            return ProposalValidator.validate_all(hierarchy)
        except (json.JSONDecodeError, ValueError):
            logger.exception("Parsing error")
            raise
        except ProposalValidationError:
            logger.exception("Proposal validation error")
            raise

    def split(self, ticket_content: dict, *, child_type: WorkItemType) -> list[HierarchyWorkItem]:
        """Split one oversized ticket into a flat, ordered list of children.

        Unlike ``decompose`` this proposes one level only, every child of
        ``child_type`` — the caller already holds the parent, and the children
        run in the order returned.

        Raises:
            ValueError: no generator, unparseable reply, or a child of the wrong
                type or with children of its own.
            ProposalValidationError: a child breaks the proposal constraints.
        """
        if self._generate is None:
            raise ValueError(
                "DecompositionService requires a generate callable wired through the "
                "CLI agent seam (run_cli_agent_turn); direct SDK calls are not supported"
            )
        reply = self._generate(self._build_split_prompt(ticket_content, child_type))
        payload = extract_json_block(reply)
        if payload is None:
            raise ValueError("Split reply carried no JSON object")
        children = [self._parse_item(item) for item in payload.get("children", [])]
        if len(children) < 2:
            raise ValueError(f"Split proposed {len(children)} child(ren); need at least 2")
        for child in children:
            if child.work_item_type != child_type or child.children:
                raise ValueError(
                    f"Split child '{child.title}' must be a {child_type.value} with no children"
                )
        validated = ProposalValidator.validate_all(children)
        _require_criteria_partition(ticket_content.get("acceptance_criteria", []), validated)
        return validated

    @staticmethod
    def _build_split_prompt(ticket_content: dict, child_type: WorkItemType) -> str:
        criteria = "\n".join(f"- {ac}" for ac in ticket_content.get("acceptance_criteria", []))
        max_acs = ProposalValidator.MAX_ACCEPTANCE_CRITERIA_ITEMS
        max_desc = ProposalValidator.MAX_DESCRIPTION_LENGTH
        return f"""This ticket is too large for one agent run. Split it into smaller {child_type.value} tickets that each ship independently.

TICKET
Title: {ticket_content.get("title", "")}
Description:
{ticket_content.get("description", "")}

Acceptance criteria:
{criteria or "(none)"}

RULES
1. Every acceptance criterion above belongs to exactly one child, carried over verbatim. Add none, drop none.
2. Each child has at most {max_acs} acceptance criteria and a description under {max_desc} characters, written so the child stands alone without the parent.
3. List children in the order they must be built: a child may rely on any child before it, never one after it.
4. Use between 2 and 8 children. Split along seams in the code (a module, a transport, a surface), not by step (design / build / test).

Reply with ONLY this JSON object:
{{
  "children": [
    {{
      "external_id": "short-slug",
      "title": "string",
      "work_item_type": "{child_type.value}",
      "description": "string",
      "acceptance_criteria": ["string", ...],
      "priority": 1,
      "children": []
    }}
  ]
}}"""

    def _build_prompt(self, ticket_content: dict) -> str:
        """Build the prompt for the model to generate hierarchy."""
        title = ticket_content.get("title", "")
        description = ticket_content.get("description", "")
        acceptance_criteria = ticket_content.get("acceptance_criteria", [])

        criteria_text = "\n".join(f"- {ac}" for ac in acceptance_criteria)

        return f"""You are a work breakdown structure expert. Analyze the following ticket and propose a hierarchical breakdown into work items.

TICKET DETAILS:
Title: {title}
Description: {description}

Acceptance Criteria:
{criteria_text if criteria_text else "(none provided)"}

HIERARCHY RULES:
- Valid hierarchy levels are: milestone, feature, capability, task, bug
- Valid parent-child relationships:
  - milestone can contain: feature, bug
  - feature can contain: capability, bug
  - capability can contain: task, bug
  - task cannot contain children
  - bug cannot contain children
- Each item must have:
  - external_id (unique string identifier, e.g., "auth-feature-001")
  - title (clear, concise name)
  - work_item_type (one of: milestone, feature, capability, task, bug)
  - description (detailed explanation)
  - acceptance_criteria (list of strings, specific testable criteria)
  - priority (1=high, 2=medium, 3=low)
  - children (list of child work items, empty list if none)

REQUIREMENTS:
1. Generate a complete, hierarchical breakdown of the ticket
2. All hierarchy levels should be populated where appropriate
3. Each item must have all required fields
4. External IDs must be unique within the response
5. Respect the valid hierarchy rules strictly
6. Include acceptance criteria for all items
7. Return ONLY valid JSON, no markdown or extra text

OUTPUT FORMAT:
Return a JSON object with this exact structure:
{{
  "hierarchy": [
    {{
      "external_id": "string",
      "title": "string",
      "work_item_type": "milestone|feature|capability|task|bug",
      "description": "string",
      "acceptance_criteria": ["string", ...],
      "priority": 1|2|3,
      "children": [...]
    }}
  ]
}}"""

    def _parse_response(self, response_text: str) -> list[HierarchyWorkItem]:
        """Parse model JSON response into HierarchyWorkItem objects."""
        try:
            data = json.loads(response_text)
        except json.JSONDecodeError:
            logger.exception("Failed to parse JSON response")
            raise

        hierarchy_data = data.get("hierarchy", [])
        if not hierarchy_data:
            return []

        return [self._parse_item(item_data) for item_data in hierarchy_data]

    def _parse_item(self, data: dict) -> HierarchyWorkItem:
        """Recursively parse a hierarchy item from dict data."""
        external_id = data.get("external_id")
        if not external_id:
            raise ValueError("external_id is required")

        title = data.get("title")
        if not title:
            raise ValueError("title is required")

        work_item_type_str = data.get("work_item_type")
        if not work_item_type_str:
            raise ValueError("work_item_type is required")

        try:
            work_item_type = WorkItemType(work_item_type_str)
        except ValueError as e:
            raise ValueError(f"Invalid work_item_type '{work_item_type_str}': {e}") from e

        description = data.get("description", "")
        acceptance_criteria = data.get("acceptance_criteria", [])

        if not isinstance(acceptance_criteria, list):
            raise ValueError("acceptance_criteria must be a list")

        priority = data.get("priority", 3)
        if not isinstance(priority, int):
            priority = int(priority)

        children_data = data.get("children", [])
        children = [self._parse_item(child_data) for child_data in children_data]

        return HierarchyWorkItem(
            external_id=external_id,
            title=title,
            work_item_type=work_item_type,
            description=description,
            acceptance_criteria=acceptance_criteria,
            priority=priority,
            children=children,
        )

    def _validate_item(self, item: HierarchyWorkItem) -> None:
        """Validate a work item against hierarchy rules."""
        valid_child_types = VALID_HIERARCHY.get(item.work_item_type, [])

        for child in item.children:
            if child.work_item_type not in valid_child_types:
                raise ValueError(
                    f"Invalid hierarchy: {item.work_item_type.value} cannot contain "
                    f"{child.work_item_type.value}"
                )
            self._validate_item(child)
