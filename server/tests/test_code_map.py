"""The repo map reaches agents, and stops claiming things that stopped being true."""

from pathlib import Path

from loregarden.config import settings
from loregarden.services.code_map import (
    lookup_entries,
    render_code_map,
    structure_tree,
    verify_code_map,
)

MAP = """
## STRUCTURE

```
repo/
├── server/   # the backend
└── client/   # the frontend
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Routing | `services/router.py` | `resolve_route` does the work |
| Config | `settings.py` | `MAX_SIZE` and `"role_file"` |

## NEXT SECTION

not part of the map
"""


def _repo(tmp_path: Path, *, router: str = "", settings_py: str = "") -> Path:
    (tmp_path / "AGENTS.md").write_text(MAP)
    services = tmp_path / "server" / "loregarden" / "services"
    services.mkdir(parents=True)
    (services / "router.py").write_text(router or "def resolve_route():\n    pass\n")
    (tmp_path / "server" / "loregarden" / "settings.py").write_text(
        settings_py or 'MAX_SIZE = 10\nCONFIG = {"role_file": "x"}\n'
    )
    return tmp_path


def test_a_true_map_reports_no_drift(tmp_path):
    assert verify_code_map(_repo(tmp_path)) == []


def test_a_moved_function_is_caught(tmp_path):
    """The drift that actually costs time: the file still resolves, so nothing
    looks wrong until the agent reads the wrong one."""
    repo = _repo(tmp_path, router="def something_else():\n    pass\n")
    drift = verify_code_map(repo)
    assert len(drift) == 1
    assert "resolve_route" in drift[0].detail


def test_a_deleted_file_is_caught(tmp_path):
    repo = _repo(tmp_path)
    (repo / "server" / "loregarden" / "services" / "router.py").unlink()
    assert any("does not exist" in d.detail for d in verify_code_map(repo))


def test_constants_and_config_keys_count_as_definitions(tmp_path):
    """The map legitimately points at more than functions; flagging a constant
    as missing would train everyone to ignore the check."""
    assert verify_code_map(_repo(tmp_path)) == []


def test_a_mention_is_not_a_definition(tmp_path):
    """A symbol keeps appearing as a caller in the file it moved out of."""
    repo = _repo(tmp_path, router="from elsewhere import resolve_route\nresolve_route()\n")
    assert any("resolve_route" in d.detail for d in verify_code_map(repo))


def test_the_rendered_map_carries_structure_and_index(tmp_path):
    rendered = render_code_map(_repo(tmp_path))
    assert "Repository structure" in rendered
    assert "Where to look" in rendered
    assert "server/" in rendered
    # The section after the table is not part of the map.
    assert "not part of the map" not in rendered


def test_parsing_stops_at_the_next_section(tmp_path):
    assert "not part of the map" not in structure_tree(MAP)
    assert [e.task for e in lookup_entries(MAP)] == ["Routing", "Config"]


def test_a_repo_without_a_map_contributes_nothing(tmp_path):
    assert render_code_map(tmp_path) == ""


def test_this_repository_map_is_still_true():
    """The gate. AGENTS.md points agents at files; when it rots it points them
    at the wrong one, confidently."""
    drift = verify_code_map(settings.repo_root)
    assert not drift, "AGENTS.md has drifted:\n" + "\n".join(
        f"  [{d.entry}] {d.detail}" for d in drift
    )
