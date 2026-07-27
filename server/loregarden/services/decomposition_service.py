"""Hierarchy decomposition — prompt build + response parse.

LLM turns go through the shared CLI seam (``run_cli_agent_turn`` /
``resolve_model_for_adapter``), never a direct Anthropic SDK call. Callers that
need a live model pass a ``generate`` callable that already resolved adapter +
model the same way triage and ticket studio do.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from loregarden.models.domain import WorkItemType
from loregarden.models.domain.enums import VALID_HIERARCHY
from loregarden.models.domain.schemas import HierarchyWorkItem
from loregarden.services.proposal_validator import ProposalValidationError, ProposalValidator

logger = logging.getLogger(__name__)

GenerateFn = Callable[[str], str]


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
        except (json.JSONDecodeError, ValueError) as e:
            logger.error("Parsing error: %s", e)
            raise
        except ProposalValidationError as e:
            logger.error("Proposal validation error: %s", e)
            raise

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
        except json.JSONDecodeError as e:
            logger.error("Failed to parse JSON response: %s", e)
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
