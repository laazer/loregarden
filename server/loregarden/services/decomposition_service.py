"""Oversized-ticket split — prompt build + response parse.

LLM turns go through the shared CLI seam (``run_cli_agent_turn`` /
``resolve_model_for_adapter``), never a direct Anthropic SDK call. Callers that
need a live model pass a ``generate`` callable that already resolved adapter +
model the same way triage and ticket studio do.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from loregarden.models.domain import WorkItemType
from loregarden.models.domain.schemas import HierarchyWorkItem
from loregarden.services.proposal_validator import ProposalValidator
from loregarden.services.ticket_studio_service import extract_json_block
from pydantic import BaseModel, Field

GenerateFn = Callable[[str], str]


class _ProposedChild(BaseModel):
    """One child as the model writes it: ``null`` children means none."""

    external_id: str
    title: str
    work_item_type: WorkItemType
    description: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    priority: int = 3
    children: list[HierarchyWorkItem] | None = None

    def as_item(self) -> HierarchyWorkItem:
        fields = self.model_dump(exclude={"children"})
        return HierarchyWorkItem(**fields, children=self.children or [])


class _SplitReply(BaseModel):
    """The reply's shape, so a malformed one is a ``ValueError`` the split
    reports rather than a ``TypeError`` that ends the orchestration run."""

    children: list[_ProposedChild]


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
    """Splits an oversized ticket via an injected model turn."""

    def __init__(self, generate: GenerateFn | None = None):
        """``generate`` maps a prompt to raw model text (JSON).

        Production wiring should be a closure over ``run_cli_agent_turn`` with the
        workspace's effective adapter already applied — same path as ticket studio.
        """
        self._generate = generate

    def split(self, ticket_content: dict, *, child_type: WorkItemType) -> list[HierarchyWorkItem]:
        """Split one oversized ticket into a flat, ordered list of children.

        One level only, every child of ``child_type`` — the caller already holds
        the parent, and the children run in the order returned.

        Raises:
            ValueError: no generator, an unparseable or malformed reply, or a child
                of the wrong type or with children of its own.
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
        children = [child.as_item() for child in _SplitReply.model_validate(payload).children]
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
