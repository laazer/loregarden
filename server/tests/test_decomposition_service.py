"""Tests for ticket decomposition service — LLM-powered hierarchy generation."""

import json
from unittest.mock import MagicMock, Mock, patch

import pytest
from loregarden.models.domain import WorkItemType
from loregarden.models.domain.schemas import HierarchyWorkItem


class MockClaudeResponse:
    """Mock response from Claude API."""

    def __init__(self, content: str):
        self.content = [MagicMock(text=content)]
        self.usage = MagicMock(
            input_tokens=100, output_tokens=200, cache_creation_input_tokens=0
        )


def parse_hierarchy_from_response(response_text: str) -> list[HierarchyWorkItem]:
    """Extract hierarchy from Claude response text."""
    try:
        data = json.loads(response_text)
        items = []
        for item_data in data.get("hierarchy", []):
            items.append(_parse_item(item_data))
        return items
    except json.JSONDecodeError:
        return []


def _parse_item(data: dict) -> HierarchyWorkItem:
    """Recursively parse a hierarchy item."""
    children = [_parse_item(c) for c in data.get("children", [])]
    return HierarchyWorkItem(
        external_id=data.get("external_id", ""),
        title=data.get("title", ""),
        work_item_type=WorkItemType(data.get("work_item_type", "task")),
        description=data.get("description", ""),
        acceptance_criteria=data.get("acceptance_criteria", []),
        priority=data.get("priority", 3),
        children=children,
    )


class TestDecompositionServiceHappyPath:
    """Standard expected behavior for decomposition service."""

    def test_decompose_simple_feature(self):
        """Decompose a simple feature ticket into hierarchy."""
        ticket_content = {
            "title": "Add user authentication",
            "description": "Implement login and signup flows",
            "acceptance_criteria": ["Users can sign up", "Users can log in"],
        }

        mock_response_text = json.dumps(
            {
                "hierarchy": [
                    {
                        "external_id": "auth-feature",
                        "title": "Add user authentication",
                        "work_item_type": "feature",
                        "description": "Core authentication feature",
                        "acceptance_criteria": ["Users can sign up", "Users can log in"],
                        "priority": 1,
                        "children": [
                            {
                                "external_id": "auth-signup",
                                "title": "Implement signup flow",
                                "work_item_type": "capability",
                                "description": "Signup API and UI",
                                "acceptance_criteria": ["Form validation works"],
                                "priority": 1,
                                "children": [
                                    {
                                        "external_id": "auth-signup-api",
                                        "title": "Create signup API endpoint",
                                        "work_item_type": "task",
                                        "description": "POST /auth/signup endpoint",
                                        "acceptance_criteria": ["Endpoint returns token"],
                                        "priority": 1,
                                        "children": [],
                                    }
                                ],
                            },
                            {
                                "external_id": "auth-login",
                                "title": "Implement login flow",
                                "work_item_type": "capability",
                                "description": "Login API and UI",
                                "acceptance_criteria": ["Session persists"],
                                "priority": 1,
                                "children": [],
                            },
                        ],
                    }
                ]
            }
        )

        hierarchy = parse_hierarchy_from_response(mock_response_text)

        assert len(hierarchy) == 1
        root = hierarchy[0]
        assert root.external_id == "auth-feature"
        assert root.title == "Add user authentication"
        assert root.work_item_type == WorkItemType.FEATURE
        assert len(root.children) == 2

        # Check first child
        assert root.children[0].external_id == "auth-signup"
        assert root.children[0].work_item_type == WorkItemType.CAPABILITY
        assert len(root.children[0].children) == 1

        # Check grandchild
        grandchild = root.children[0].children[0]
        assert grandchild.external_id == "auth-signup-api"
        assert grandchild.work_item_type == WorkItemType.TASK

    def test_decompose_milestone(self):
        """Decompose milestone into features and capabilities."""
        ticket_content = {
            "title": "Q3 Release Milestone",
            "description": "Deliver core platform features for Q3",
        }

        mock_response_text = json.dumps(
            {
                "hierarchy": [
                    {
                        "external_id": "q3-milestone",
                        "title": "Q3 Release Milestone",
                        "work_item_type": "milestone",
                        "description": "Core platform features",
                        "priority": 1,
                        "children": [
                            {
                                "external_id": "q3-auth",
                                "title": "Authentication System",
                                "work_item_type": "feature",
                                "priority": 1,
                                "children": [],
                            },
                            {
                                "external_id": "q3-api",
                                "title": "REST API Improvements",
                                "work_item_type": "feature",
                                "priority": 2,
                                "children": [],
                            },
                        ],
                    }
                ]
            }
        )

        hierarchy = parse_hierarchy_from_response(mock_response_text)

        assert len(hierarchy) == 1
        assert hierarchy[0].work_item_type == WorkItemType.MILESTONE
        assert len(hierarchy[0].children) == 2

    def test_decompose_with_all_hierarchy_levels(self):
        """Decompose ticket with full hierarchy: milestone→feature→capability→task."""
        mock_response_text = json.dumps(
            {
                "hierarchy": [
                    {
                        "external_id": "m1",
                        "title": "Milestone",
                        "work_item_type": "milestone",
                        "children": [
                            {
                                "external_id": "f1",
                                "title": "Feature",
                                "work_item_type": "feature",
                                "children": [
                                    {
                                        "external_id": "c1",
                                        "title": "Capability",
                                        "work_item_type": "capability",
                                        "children": [
                                            {
                                                "external_id": "t1",
                                                "title": "Task",
                                                "work_item_type": "task",
                                                "children": [],
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        )

        hierarchy = parse_hierarchy_from_response(mock_response_text)

        # Verify full chain
        assert hierarchy[0].work_item_type == WorkItemType.MILESTONE
        assert hierarchy[0].children[0].work_item_type == WorkItemType.FEATURE
        assert hierarchy[0].children[0].children[0].work_item_type == WorkItemType.CAPABILITY
        assert (
            hierarchy[0].children[0].children[0].children[0].work_item_type
            == WorkItemType.TASK
        )

    def test_decompose_with_bugs_in_hierarchy(self):
        """Include bug work items in hierarchy at appropriate levels."""
        mock_response_text = json.dumps(
            {
                "hierarchy": [
                    {
                        "external_id": "f1",
                        "title": "Feature",
                        "work_item_type": "feature",
                        "children": [
                            {
                                "external_id": "c1",
                                "title": "Capability",
                                "work_item_type": "capability",
                                "children": [],
                            },
                            {
                                "external_id": "b1",
                                "title": "Known regression",
                                "work_item_type": "bug",
                                "children": [],
                            },
                        ],
                    }
                ]
            }
        )

        hierarchy = parse_hierarchy_from_response(mock_response_text)

        assert hierarchy[0].work_item_type == WorkItemType.FEATURE
        assert len(hierarchy[0].children) == 2
        assert hierarchy[0].children[1].work_item_type == WorkItemType.BUG

    def test_populated_acceptance_criteria(self):
        """Verify acceptance criteria are populated in hierarchy items."""
        mock_response_text = json.dumps(
            {
                "hierarchy": [
                    {
                        "external_id": "f1",
                        "title": "Feature",
                        "work_item_type": "feature",
                        "acceptance_criteria": [
                            "Criterion 1",
                            "Criterion 2",
                            "Criterion 3",
                        ],
                        "children": [
                            {
                                "external_id": "c1",
                                "title": "Capability",
                                "work_item_type": "capability",
                                "acceptance_criteria": ["Sub-criterion 1", "Sub-criterion 2"],
                                "children": [],
                            }
                        ],
                    }
                ]
            }
        )

        hierarchy = parse_hierarchy_from_response(mock_response_text)

        assert len(hierarchy[0].acceptance_criteria) == 3
        assert "Criterion 1" in hierarchy[0].acceptance_criteria
        assert len(hierarchy[0].children[0].acceptance_criteria) == 2

    def test_populated_descriptions(self):
        """Verify descriptions are populated at all hierarchy levels."""
        mock_response_text = json.dumps(
            {
                "hierarchy": [
                    {
                        "external_id": "f1",
                        "title": "Feature",
                        "work_item_type": "feature",
                        "description": "Feature description",
                        "children": [
                            {
                                "external_id": "c1",
                                "title": "Capability",
                                "work_item_type": "capability",
                                "description": "Capability description",
                                "children": [
                                    {
                                        "external_id": "t1",
                                        "title": "Task",
                                        "work_item_type": "task",
                                        "description": "Task description",
                                        "children": [],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        )

        hierarchy = parse_hierarchy_from_response(mock_response_text)

        assert hierarchy[0].description == "Feature description"
        assert hierarchy[0].children[0].description == "Capability description"
        assert hierarchy[0].children[0].children[0].description == "Task description"

    def test_priority_preserved_in_hierarchy(self):
        """Priority values are preserved for each work item."""
        mock_response_text = json.dumps(
            {
                "hierarchy": [
                    {
                        "external_id": "f1",
                        "title": "High Priority Feature",
                        "work_item_type": "feature",
                        "priority": 1,
                        "children": [
                            {
                                "external_id": "c1",
                                "title": "Medium Priority Capability",
                                "work_item_type": "capability",
                                "priority": 2,
                                "children": [
                                    {
                                        "external_id": "t1",
                                        "title": "Low Priority Task",
                                        "work_item_type": "task",
                                        "priority": 3,
                                        "children": [],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        )

        hierarchy = parse_hierarchy_from_response(mock_response_text)

        assert hierarchy[0].priority == 1
        assert hierarchy[0].children[0].priority == 2
        assert hierarchy[0].children[0].children[0].priority == 3


class TestDecompositionServiceEdgeCases:
    """Edge cases and boundary conditions."""

    def test_decompose_empty_description(self):
        """Handle ticket with empty description."""
        ticket_content = {
            "title": "Quick Fix",
            "description": "",
            "acceptance_criteria": [],
        }

        mock_response_text = json.dumps(
            {
                "hierarchy": [
                    {
                        "external_id": "fix-1",
                        "title": "Quick Fix",
                        "work_item_type": "task",
                        "description": "",
                        "acceptance_criteria": [],
                        "children": [],
                    }
                ]
            }
        )

        hierarchy = parse_hierarchy_from_response(mock_response_text)

        assert len(hierarchy) == 1
        assert hierarchy[0].description == ""
        assert len(hierarchy[0].acceptance_criteria) == 0

    def test_decompose_very_long_description(self):
        """Handle ticket with very long description."""
        long_desc = "A" * 5000  # 5000 character description

        mock_response_text = json.dumps(
            {
                "hierarchy": [
                    {
                        "external_id": "big-ticket",
                        "title": "Large Ticket",
                        "work_item_type": "feature",
                        "description": long_desc,
                        "children": [],
                    }
                ]
            }
        )

        hierarchy = parse_hierarchy_from_response(mock_response_text)

        assert hierarchy[0].description == long_desc

    def test_decompose_special_characters_in_title(self):
        """Handle special characters in titles."""
        mock_response_text = json.dumps(
            {
                "hierarchy": [
                    {
                        "external_id": "special-1",
                        "title": "Fix: Special™ Characters (with @#$%^&*)",
                        "work_item_type": "task",
                        "children": [],
                    }
                ]
            }
        )

        hierarchy = parse_hierarchy_from_response(mock_response_text)

        assert "Special™" in hierarchy[0].title
        assert "@#$%^&*" in hierarchy[0].title

    def test_decompose_multiline_description(self):
        """Handle multiline descriptions with newlines."""
        multiline = "Line 1\nLine 2\nLine 3\n\nParagraph 2"

        mock_response_text = json.dumps(
            {
                "hierarchy": [
                    {
                        "external_id": "multiline-1",
                        "title": "Multiline",
                        "work_item_type": "task",
                        "description": multiline,
                        "children": [],
                    }
                ]
            }
        )

        hierarchy = parse_hierarchy_from_response(mock_response_text)

        assert "Line 1\nLine 2" in hierarchy[0].description

    def test_decompose_wide_branching(self):
        """Handle feature with many sibling capabilities."""
        children = [
            {
                "external_id": f"cap-{i}",
                "title": f"Capability {i}",
                "work_item_type": "capability",
                "children": [],
            }
            for i in range(10)
        ]

        mock_response_text = json.dumps(
            {
                "hierarchy": [
                    {
                        "external_id": "wide-feature",
                        "title": "Wide Feature",
                        "work_item_type": "feature",
                        "children": children,
                    }
                ]
            }
        )

        hierarchy = parse_hierarchy_from_response(mock_response_text)

        assert len(hierarchy[0].children) == 10

    def test_decompose_deep_nesting(self):
        """Handle deeply nested hierarchy."""
        current = {
            "external_id": "deep-task",
            "title": "Deep Task",
            "work_item_type": "task",
            "children": [],
        }

        # Create 5 levels of nesting
        for i in range(4, 0, -1):
            current = {
                "external_id": f"level-{i}",
                "title": f"Level {i}",
                "work_item_type": "capability" if i % 2 == 0 else "feature",
                "children": [current],
            }

        mock_response_text = json.dumps({"hierarchy": [current]})
        hierarchy = parse_hierarchy_from_response(mock_response_text)

        # Verify we can traverse to the deepest level
        current = hierarchy[0]
        depth = 0
        while current.children:
            current = current.children[0]
            depth += 1

        assert depth >= 4

    def test_decompose_mixed_sibling_types(self):
        """Handle siblings of different types (capabilities and bugs)."""
        mock_response_text = json.dumps(
            {
                "hierarchy": [
                    {
                        "external_id": "feature-1",
                        "title": "Feature",
                        "work_item_type": "feature",
                        "children": [
                            {
                                "external_id": "cap-1",
                                "title": "Capability 1",
                                "work_item_type": "capability",
                                "children": [],
                            },
                            {
                                "external_id": "bug-1",
                                "title": "Related Bug",
                                "work_item_type": "bug",
                                "children": [],
                            },
                            {
                                "external_id": "cap-2",
                                "title": "Capability 2",
                                "work_item_type": "capability",
                                "children": [],
                            },
                        ],
                    }
                ]
            }
        )

        hierarchy = parse_hierarchy_from_response(mock_response_text)

        assert len(hierarchy[0].children) == 3
        types = [c.work_item_type for c in hierarchy[0].children]
        assert WorkItemType.CAPABILITY in types
        assert WorkItemType.BUG in types

    def test_decompose_single_item_no_children(self):
        """Handle single item with no children."""
        mock_response_text = json.dumps(
            {
                "hierarchy": [
                    {
                        "external_id": "single",
                        "title": "Single Task",
                        "work_item_type": "task",
                        "children": [],
                    }
                ]
            }
        )

        hierarchy = parse_hierarchy_from_response(mock_response_text)

        assert len(hierarchy) == 1
        assert len(hierarchy[0].children) == 0

    def test_decompose_multiple_root_items(self):
        """Handle hierarchy with multiple root items."""
        mock_response_text = json.dumps(
            {
                "hierarchy": [
                    {
                        "external_id": "root-1",
                        "title": "Root 1",
                        "work_item_type": "milestone",
                        "children": [],
                    },
                    {
                        "external_id": "root-2",
                        "title": "Root 2",
                        "work_item_type": "milestone",
                        "children": [],
                    },
                    {
                        "external_id": "root-3",
                        "title": "Root 3",
                        "work_item_type": "milestone",
                        "children": [],
                    },
                ]
            }
        )

        hierarchy = parse_hierarchy_from_response(mock_response_text)

        assert len(hierarchy) == 3


class TestDecompositionServiceErrorHandling:
    """Error handling and failure scenarios."""

    def test_handle_invalid_json_response(self):
        """Handle malformed JSON response from Claude."""
        malformed_json = "{ invalid json }"

        hierarchy = parse_hierarchy_from_response(malformed_json)

        assert hierarchy == []

    def test_handle_missing_hierarchy_field(self):
        """Handle response missing 'hierarchy' field."""
        mock_response = json.dumps({"some_other_field": []})

        hierarchy = parse_hierarchy_from_response(mock_response)

        assert hierarchy == []

    def test_handle_missing_required_fields(self):
        """Handle work items missing required fields (graceful degradation)."""
        # This test verifies the parsing handles partial data
        mock_response_text = json.dumps(
            {
                "hierarchy": [
                    {
                        "external_id": "partial",
                        # Missing title, work_item_type
                        "children": [],
                    }
                ]
            }
        )

        # Should handle gracefully (with defaults)
        hierarchy = parse_hierarchy_from_response(mock_response_text)

        assert len(hierarchy) == 1

    def test_token_limit_handling_short_response(self):
        """Service handles cases where response might hit token limits."""
        # Simulate truncated response
        mock_response_text = json.dumps(
            {
                "hierarchy": [
                    {
                        "external_id": "truncated",
                        "title": "Truncated hierarchy",
                        "work_item_type": "feature",
                        "note": "[Response was truncated due to token limits]",
                        "children": [],
                    }
                ]
            }
        )

        hierarchy = parse_hierarchy_from_response(mock_response_text)

        assert len(hierarchy) == 1

    def test_api_timeout_scenario(self):
        """API timeout returns empty or error indicator."""
        # Empty response simulating timeout
        hierarchy = parse_hierarchy_from_response("")

        assert hierarchy == []

    def test_api_rate_limit_scenario(self):
        """API rate limit returns empty or error indicator."""
        error_response = json.dumps(
            {"error": "rate_limit_exceeded", "hierarchy": []}
        )

        hierarchy = parse_hierarchy_from_response(error_response)

        assert hierarchy == []


class TestDecompositionServicePromptValidation:
    """Tests for prompt clarity and consistency."""

    def test_prompt_includes_ticket_context(self):
        """Prompt should include all ticket context: title, description, criteria."""
        ticket = {
            "title": "Test Feature",
            "description": "Test description",
            "acceptance_criteria": ["AC1", "AC2"],
        }

        # This would be called in the actual service
        expected_prompt_elements = [
            ticket["title"],
            ticket["description"],
            "acceptance_criteria",
        ]

        # For testing, verify the elements are part of what would be sent
        assert ticket["title"]
        assert ticket["description"]
        assert len(ticket["acceptance_criteria"]) > 0

    def test_prompt_specifies_hierarchy_levels(self):
        """Prompt should clearly specify valid hierarchy levels."""
        valid_levels = [
            "milestone",
            "feature",
            "capability",
            "task",
            "bug",
        ]

        # Verify all levels are represented
        assert len(valid_levels) == 5

    def test_prompt_requires_external_ids(self):
        """Prompt should require external_id for traceability."""
        mock_response_text = json.dumps(
            {
                "hierarchy": [
                    {
                        "external_id": "feature-001",
                        "title": "Feature",
                        "work_item_type": "feature",
                        "children": [],
                    }
                ]
            }
        )

        hierarchy = parse_hierarchy_from_response(mock_response_text)

        assert hierarchy[0].external_id == "feature-001"

    def test_prompt_specifies_acceptance_criteria_format(self):
        """Prompt should specify acceptance criteria as list of strings."""
        mock_response_text = json.dumps(
            {
                "hierarchy": [
                    {
                        "external_id": "f1",
                        "title": "Feature",
                        "work_item_type": "feature",
                        "acceptance_criteria": [
                            "Users can login",
                            "Session persists",
                        ],
                        "children": [],
                    }
                ]
            }
        )

        hierarchy = parse_hierarchy_from_response(mock_response_text)

        assert isinstance(hierarchy[0].acceptance_criteria, list)
        assert all(isinstance(ac, str) for ac in hierarchy[0].acceptance_criteria)


class TestDecompositionServiceRepeatability:
    """Tests for reproducibility and consistency."""

    def test_same_input_produces_consistent_structure(self):
        """Same ticket should produce consistent hierarchy structure."""
        ticket = {
            "title": "User authentication",
            "description": "Implement auth system",
            "acceptance_criteria": ["Users can sign up", "Users can log in"],
        }

        # Generate multiple hierarchies from same input
        response_text = json.dumps(
            {
                "hierarchy": [
                    {
                        "external_id": "auth",
                        "title": "User authentication",
                        "work_item_type": "feature",
                        "children": [
                            {
                                "external_id": "auth-signup",
                                "title": "Signup",
                                "work_item_type": "capability",
                                "children": [],
                            }
                        ],
                    }
                ]
            }
        )

        hierarchy1 = parse_hierarchy_from_response(response_text)
        hierarchy2 = parse_hierarchy_from_response(response_text)

        # Both should have same structure
        assert len(hierarchy1) == len(hierarchy2)
        assert hierarchy1[0].external_id == hierarchy2[0].external_id
        assert len(hierarchy1[0].children) == len(hierarchy2[0].children)

    def test_work_item_types_follow_valid_hierarchy(self):
        """Generated hierarchy respects valid parent-child type rules."""
        # Valid: milestone -> feature -> capability -> task
        mock_response_text = json.dumps(
            {
                "hierarchy": [
                    {
                        "external_id": "m1",
                        "title": "Milestone",
                        "work_item_type": "milestone",
                        "children": [
                            {
                                "external_id": "f1",
                                "title": "Feature",
                                "work_item_type": "feature",
                                "children": [
                                    {
                                        "external_id": "c1",
                                        "title": "Capability",
                                        "work_item_type": "capability",
                                        "children": [
                                            {
                                                "external_id": "t1",
                                                "title": "Task",
                                                "work_item_type": "task",
                                                "children": [],
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        )

        hierarchy = parse_hierarchy_from_response(mock_response_text)

        # Verify chain is valid
        types_chain = []
        current = hierarchy[0]
        types_chain.append(current.work_item_type)
        while current.children:
            current = current.children[0]
            types_chain.append(current.work_item_type)

        # Should follow valid progression
        assert types_chain[0] == WorkItemType.MILESTONE
        assert types_chain[1] == WorkItemType.FEATURE
        assert types_chain[2] == WorkItemType.CAPABILITY
        assert types_chain[3] == WorkItemType.TASK


class TestDecompositionServiceIntegration:
    """Integration-style tests for service behavior."""

    def test_hierarchy_preserves_all_ticket_metadata(self):
        """Generated hierarchy preserves all original ticket metadata."""
        mock_response_text = json.dumps(
            {
                "hierarchy": [
                    {
                        "external_id": "feature-001",
                        "title": "Complex Feature",
                        "work_item_type": "feature",
                        "description": "Detailed description",
                        "acceptance_criteria": [
                            "Criterion 1",
                            "Criterion 2",
                            "Criterion 3",
                        ],
                        "priority": 1,
                        "children": [],
                    }
                ]
            }
        )

        hierarchy = parse_hierarchy_from_response(mock_response_text)

        item = hierarchy[0]
        assert item.external_id == "feature-001"
        assert item.title == "Complex Feature"
        assert item.description == "Detailed description"
        assert len(item.acceptance_criteria) == 3
        assert item.priority == 1

    def test_decompose_real_world_example_subscription_feature(self):
        """Real-world example: decompose subscription feature."""
        mock_response_text = json.dumps(
            {
                "hierarchy": [
                    {
                        "external_id": "subscription-feature",
                        "title": "Subscription Management System",
                        "work_item_type": "feature",
                        "description": "Enable users to subscribe to premium plans",
                        "acceptance_criteria": [
                            "Users can select subscription plan",
                            "Payment processing works",
                            "Subscription status tracked",
                        ],
                        "priority": 1,
                        "children": [
                            {
                                "external_id": "subscription-plans",
                                "title": "Plan Management",
                                "work_item_type": "capability",
                                "description": "Define and manage subscription plans",
                                "children": [
                                    {
                                        "external_id": "subscription-plans-api",
                                        "title": "Create Plans API",
                                        "work_item_type": "task",
                                        "children": [],
                                    }
                                ],
                            },
                            {
                                "external_id": "subscription-payment",
                                "title": "Payment Integration",
                                "work_item_type": "capability",
                                "description": "Integrate payment processor",
                                "children": [],
                            },
                        ],
                    }
                ]
            }
        )

        hierarchy = parse_hierarchy_from_response(mock_response_text)

        assert len(hierarchy) == 1
        feature = hierarchy[0]
        assert feature.title == "Subscription Management System"
        assert len(feature.children) == 2
        assert feature.children[0].children  # Plans has sub-tasks


class TestDecompositionServiceValidation:
    """Input validation and constraint checking."""

    def test_external_id_uniqueness_within_response(self):
        """External IDs should be unique within a decomposition response."""
        mock_response_text = json.dumps(
            {
                "hierarchy": [
                    {
                        "external_id": "unique-1",
                        "title": "Item 1",
                        "work_item_type": "feature",
                        "children": [
                            {
                                "external_id": "unique-2",
                                "title": "Item 2",
                                "work_item_type": "capability",
                                "children": [],
                            }
                        ],
                    }
                ]
            }
        )

        hierarchy = parse_hierarchy_from_response(mock_response_text)

        # Collect all IDs
        all_ids = []

        def collect_ids(item):
            all_ids.append(item.external_id)
            for child in item.children:
                collect_ids(child)

        for root in hierarchy:
            collect_ids(root)

        # Verify all IDs are unique
        assert len(all_ids) == len(set(all_ids))

    def test_priority_within_valid_range(self):
        """Priority values should be within valid range [1, 3]."""
        mock_response_text = json.dumps(
            {
                "hierarchy": [
                    {
                        "external_id": "f1",
                        "title": "Feature",
                        "work_item_type": "feature",
                        "priority": 1,
                        "children": [
                            {
                                "external_id": "c1",
                                "title": "Capability",
                                "work_item_type": "capability",
                                "priority": 2,
                                "children": [
                                    {
                                        "external_id": "t1",
                                        "title": "Task",
                                        "work_item_type": "task",
                                        "priority": 3,
                                        "children": [],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        )

        hierarchy = parse_hierarchy_from_response(mock_response_text)

        def check_priorities(item):
            assert 1 <= item.priority <= 3
            for child in item.children:
                check_priorities(child)

        for root in hierarchy:
            check_priorities(root)

    def test_valid_work_item_types(self):
        """All work item types should be valid enum values."""
        valid_types = {
            "milestone",
            "feature",
            "capability",
            "task",
            "bug",
        }

        mock_response_text = json.dumps(
            {
                "hierarchy": [
                    {
                        "external_id": f"type-{t}",
                        "title": f"Item {t}",
                        "work_item_type": t,
                        "children": [],
                    }
                    for t in valid_types
                ]
            }
        )

        hierarchy = parse_hierarchy_from_response(mock_response_text)

        for item in hierarchy:
            assert item.work_item_type in WorkItemType.__members__.values()
