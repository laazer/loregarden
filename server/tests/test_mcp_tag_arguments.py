"""Memory tools read `tags`/`aliases` as a list, a JSON-encoded list, or comma text.

The schema has always promised "comma-separated tags or JSON array", but every
string was split on commas — so `'["routing", "rework"]'` was stored as the tags
`'["routing"'` and `'"rework"]'`. Seen on the live graph (lg-workflow-integrity-765).
"""

import pytest
from loregarden.mcp.tools import normalize_tool_arguments
from loregarden.services.memory_store import MemoryGraphStore
from loregarden.services.memory_tag_repair import repair_split_json_tags

LEARNING = {"ticket_id": "t-1", "workspace_slug": "loregarden", "content": "Body."}
MEMORY_TOOLS = [
    ("loregarden_append_learning", LEARNING),
    ("loregarden_upsert_memory", {"title": "T", "workspace_slug": "loregarden"}),
    (
        "loregarden_upsert_blog_post",
        {"ticket_id": "t-1", "workspace_slug": "loregarden", "title": "T", "body": "B"},
    ),
]


@pytest.mark.parametrize(("tool", "base"), MEMORY_TOOLS)
@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ('["routing", "rework", "absorb-adapt"]', ["routing", "rework", "absorb-adapt"]),
        ('  ["a, with comma", "b"] ', ["a, with comma", "b"]),
        ("routing, rework", ["routing", "rework"]),
        (["routing", " rework "], ["routing", "rework"]),
        ("", []),
    ],
)
def test_tags_arrive_as_a_list_whatever_shape_they_were_sent_in(tool, base, tags, expected):
    payload = normalize_tool_arguments(tool, {**base, "tags": tags})

    assert payload["tags"] == expected


def test_aliases_parse_json_the_same_way():
    payload = normalize_tool_arguments(
        "loregarden_append_learning", {**LEARNING, "aliases": '["journal mode", "WAL"]'}
    )

    assert payload["aliases"] == ["journal mode", "WAL"]


@pytest.mark.parametrize("tags", ['["routing", "rework"', '{"a": 1}', '"just-a-string"'])
def test_json_that_is_not_a_list_of_strings_is_rejected_not_split(tags):
    with pytest.raises(ValueError, match="tags"):
        normalize_tool_arguments("loregarden_append_learning", {**LEARNING, "tags": tags})


# ── Repair of rows the comma split already wrote ─────────────────────────────

FRAGMENTS = ["learning", "loregarden", '["routing"', '"rework"', '"absorb-adapt"', '"classify"]']


def test_reopening_the_graph_rejoins_json_fragment_tags(tmp_path):
    path = tmp_path / "lg" / "memory.db"
    written = MemoryGraphStore(path).upsert_node(
        title="Negated AC", body="Body.", tags=FRAGMENTS, workspace_slug="lg"
    )

    reopened = MemoryGraphStore(path).list_nodes(workspace_slug="lg")

    assert [row["tags"] for row in reopened if row["id"] == written["id"]] == [
        ["learning", "loregarden", "routing", "rework", "absorb-adapt", "classify"]
    ]


def test_a_bracketed_run_that_is_not_json_is_left_visible(tmp_path):
    path = tmp_path / "lg" / "memory.db"
    MemoryGraphStore(path).upsert_node(
        title="Odd", body="Body.", tags=["[not json", "still not]"], workspace_slug="lg"
    )

    [row] = MemoryGraphStore(path).list_nodes(workspace_slug="lg")

    assert row["tags"] == ["[not json", "still not]"]


def test_repair_is_a_no_op_on_clean_rows(tmp_path):
    graph = MemoryGraphStore(tmp_path / "lg" / "memory.db")
    graph.upsert_node(title="Clean", body="Body.", tags=["ops", "sqlite"], workspace_slug="lg")

    with graph.connection() as conn:
        assert repair_split_json_tags(conn) == 0
