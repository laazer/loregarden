"""Service for decomposing tickets into hierarchies using Claude API."""

import json
import logging
from typing import Optional

import anthropic
from loregarden.models.domain import WorkItemType
from loregarden.models.domain.enums import VALID_HIERARCHY
from loregarden.models.domain.schemas import HierarchyWorkItem
from loregarden.services.proposal_validator import ProposalValidator, ProposalValidationError

logger = logging.getLogger(__name__)


class DecompositionService:
    """Generates hierarchical work item breakdowns using Claude."""

    def __init__(self, api_key: Optional[str] = None):
        """Initialize Claude API client.

        Args:
            api_key: Anthropic API key. If not provided, uses ANTHROPIC_API_KEY env var.
        """
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = "claude-3-5-sonnet-20241022"

    def decompose(self, ticket_content: dict) -> list[HierarchyWorkItem]:
        """Generate hierarchy proposal for a ticket.

        Args:
            ticket_content: Dict with keys: title, description, acceptance_criteria

        Returns:
            List of HierarchyWorkItem objects representing the proposed hierarchy.
            Empty list if decomposition fails.

        Raises:
            ValueError: If hierarchy validation or normalization fails.
            ProposalValidationError: If proposal doesn't conform to structure constraints.
        """
        if not ticket_content:
            return []

        prompt = self._build_prompt(ticket_content)

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4000,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
            )

            response_text = response.content[0].text
            hierarchy = self._parse_response(response_text)

            # Validate and normalize proposal using comprehensive validator
            validated_hierarchy = ProposalValidator.validate_all(hierarchy)

            return validated_hierarchy

        except anthropic.APIError as e:
            logger.error(f"Claude API error: {e}")
            raise
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Parsing error: {e}")
            raise
        except ProposalValidationError as e:
            logger.error(f"Proposal validation error: {e}")
            raise

    def _build_prompt(self, ticket_content: dict) -> str:
        """Build the prompt for Claude to generate hierarchy.

        Args:
            ticket_content: Ticket data with title, description, acceptance_criteria

        Returns:
            Formatted prompt string.
        """
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
        """Parse Claude's JSON response into HierarchyWorkItem objects.

        Args:
            response_text: Raw text response from Claude

        Returns:
            List of parsed HierarchyWorkItem objects.

        Raises:
            json.JSONDecodeError: If response is invalid JSON.
            ValueError: If required fields are missing.
        """
        try:
            data = json.loads(response_text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            raise

        hierarchy_data = data.get("hierarchy", [])
        if not hierarchy_data:
            return []

        items = []
        for item_data in hierarchy_data:
            item = self._parse_item(item_data)
            items.append(item)

        return items

    def _parse_item(self, data: dict) -> HierarchyWorkItem:
        """Recursively parse a hierarchy item from dict data.

        Args:
            data: Dictionary containing work item data

        Returns:
            Parsed HierarchyWorkItem object.

        Raises:
            ValueError: If required fields are missing or invalid.
        """
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
            raise ValueError(f"Invalid work_item_type '{work_item_type_str}': {e}")

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
        """Validate a work item against hierarchy rules.

        Args:
            item: HierarchyWorkItem to validate

        Raises:
            ValueError: If hierarchy violates VALID_HIERARCHY rules.
        """
        valid_child_types = VALID_HIERARCHY.get(item.work_item_type, [])

        for child in item.children:
            if child.work_item_type not in valid_child_types:
                raise ValueError(
                    f"Invalid hierarchy: {item.work_item_type.value} cannot contain "
                    f"{child.work_item_type.value}"
                )
            self._validate_item(child)
