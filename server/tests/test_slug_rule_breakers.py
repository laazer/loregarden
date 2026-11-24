"""One slug rule, and the shapes the other copy of it still accepts (AC7(e)).

`test_memory_search_basis.py` pins six slug shapes against
`memory_scope.graph_path_for_workspace` and reads the leak through
`loregarden_memory_status`. Those are the shapes the criterion names. The
second copy in `config.resolved_memory_sqlite_path` accepts several more, and
three of them fail differently from `../../other`:

- `'/etc'` is not a traversal, it is an absolute path, and `base.parent / slug`
  **discards the base entirely** — the answer is `/etc/memory.db`, nowhere near
  the vault and not reachable by climbing out of it.
- `'.'` resolves to the shared BASE file. That is the whitespace hazard again
  with a different spelling: the base holds every tenant's rows and
  `MemoryGraphStore.search` filters `workspace_slug` only when the slug is
  non-empty, so a non-empty slug quietly answering with the base is a
  cross-tenant read, not a path escape.
- `'./loregarden'` is the one AC7(d) names outright: it is silently REWRITTEN
  onto a live workspace's own stores. The vault half does it too and by a
  different mechanism — `ObsidianMemoryStore._workspace_segment` runs
  `slugify`, so `'../../other'` becomes the directory `other` — which the
  existing pin cannot see, because it only looks for `other/memory.db`.

So the matrix here is wide, and the property is checked on each entry point
**independently** as well as between them. Agreement alone is satisfiable by
deleting the guard from `memory_scope`, which would make both copies wrong
together and this file green.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from loregarden.services.memory_scope import graph_path_for_workspace
from loregarden.services.memory_store import AgentMemoryService
from tests.mcp_helpers import call_mcp

#: Shapes no workspace slug takes and every path rule must refuse. `''` is
#: deliberately absent: it legitimately means the shared base file.
REFUSED_SHAPES = [
    "../other",
    "../../other",
    "..",
    ".",
    "./a",
    "./loregarden",
    "a/b",
    "a/../b",
    "/etc",
    "/",
    "a\\b",
    "   ",
    "\t",
    ".hidden",
    "..a",
    "a b",
    "a\x00b",
    "~",
    "-x",
    "%2e%2e",
    "..%2fother",
]

#: Every workspace slug in the live control plane, read 2026-11-18, plus the
#: shapes the id vocabulary allows around them. The guard must refuse
#: traversal without refusing a real workspace: a rule that rejected
#: `lore-eden` would point a live workspace at nothing and read its memory as
#: empty, which is the failure AC7(d) refuses a slugify for.
LIVE_SHAPES = [
    "blobert",
    "lore-eden",
    "loregarden",
    "loremaker",
    "real929",
    "real929b",
    "surface-ws",
    "tinkercg",
    "a",
    "a_b",
    "ws1",
    "x" * 80,
]


@pytest.fixture(name="base")
def base_fixture(tmp_path):
    """A configured base, because with none both copies answer `None`.

    Without this the agreement below holds vacuously for every shape in the
    matrix, which is the quietest way for this file to prove nothing.
    """
    base = tmp_path / "Loregarden" / "memory.db"
    base.parent.mkdir(parents=True)
    with patch("loregarden.config._memory_sqlite_base_path", return_value=base):
        yield base


def _answer(call):
    """A path, or the refusal — a refusal is an answer, not a failure."""
    try:
        return call()
    except ValueError:
        return ValueError


def _config(slug):
    from loregarden.config import resolved_memory_sqlite_path

    return _answer(lambda: resolved_memory_sqlite_path(slug))


def _scope(base, slug):
    return _answer(lambda: graph_path_for_workspace(base, slug))


# --- the property, on each entry point by itself ---------------------------


@pytest.mark.parametrize("slug", REFUSED_SHAPES, ids=repr)
@pytest.mark.parametrize("entry", ["config", "memory_scope"], ids=str)
def test_no_entry_point_answers_a_path_for_a_slug_that_is_not_a_segment(base, slug, entry):
    """Checked per entry point, so agreement cannot be reached by removing
    the guard from the one that has it."""
    answer = _config(slug) if entry == "config" else _scope(base, slug)
    assert answer is ValueError, f"{entry} answered {answer!r} for {slug!r}"


@pytest.mark.parametrize("slug", REFUSED_SHAPES, ids=repr)
def test_the_two_entry_points_agree_on_every_shape_in_the_wider_matrix(base, slug):
    assert _config(slug) == _scope(base, slug)


@pytest.mark.parametrize("slug", LIVE_SHAPES, ids=repr)
def test_a_slug_a_live_workspace_uses_still_resolves_to_its_own_file(base, slug):
    """The guard must not relocate anything that works today."""
    expected = base.parent / slug / base.name
    assert _config(slug) == _scope(base, slug) == expected


def test_an_absolute_slug_does_not_discard_the_vault_root(base):
    """`base.parent / '/etc'` is `/etc`: the join drops everything to its left.

    A guard written as "the answer must stay under the vault" catches this
    one; a guard written as "reject `..`" does not.
    """
    assert _config("/etc") is ValueError
    assert _config("/etc") != Path("/etc") / base.name


def test_a_non_empty_slug_never_answers_with_the_shared_base_file(base):
    """`'.'` and `'   '` both resolve to the base today.

    The base is the multi-tenant file, and it is filtered by `workspace_slug`
    only when the slug is non-empty — so answering with it for a non-empty
    slug is a cross-tenant read reachable from one MCP argument, and it looks
    nothing like an escape.
    """
    for slug in (".", "   ", "\t"):
        assert _config(slug) is not base, f"{slug!r} resolved to the shared base file"


def test_a_refused_slug_is_never_rewritten_onto_a_live_workspace(base):
    """AC7(d)'s own argument, as a test: reject, do not slugify.

    `'./loregarden'` must not answer with `loregarden`'s file. A rewrite is
    worse than an escape, because the agent is then told, with no error, that
    it is reading a workspace it is not.
    """
    live = base.parent / "loregarden" / base.name
    for slug in ("./loregarden", "../loregarden", "loregarden/", " loregarden"):
        assert _config(slug) != live, f"{slug!r} was rewritten onto {live}"


def test_a_refused_slug_leaves_nothing_behind_on_disk(base):
    """Refusing twice must also create nothing: constructing a store creates
    its file, so a guard that mkdirs before checking leaves a directory that
    reads as a configured workspace forever."""
    before = sorted(p.name for p in base.parent.iterdir())
    for _ in range(2):
        for slug in ("../other", "/etc", "./loregarden"):
            _config(slug)
    assert sorted(p.name for p in base.parent.iterdir()) == before


# --- the same slugs, as an agent and an operator reach them ----------------


@pytest.mark.parametrize("slug", ["../../other", "/etc", ".", "./loregarden"], ids=repr)
def test_status_refuses_rather_than_reporting_any_path_for_these(base, slug):
    """`AgentMemoryService.status` is how both copies are reached at once.

    It must RAISE. Answering `None` instead would pass a test that only looks
    for a path outside the vault, while telling the agent the store is not
    configured — "silently absent" recorded as a fact, which is the failure
    this whole ticket exists to remove.
    """
    service = AgentMemoryService(obsidian=None, graph_sqlite_base=None)
    with pytest.raises(ValueError, match="bare path segment"):
        service.status(workspace_slug=slug)


def test_the_status_tool_refuses_a_rewritten_slug_instead_of_answering_for_another_workspace(
    client,
):
    """The vault half of the rewrite, which the existing pin cannot see.

    `ObsidianMemoryStore._workspace_segment` slugifies, so `'./loregarden'`
    reports `loregarden`'s real Memory, Learnings, BlogPosts and Checkpoints
    directories — all of them inside the vault, so no assertion about paths
    escaping the vault fires. The tell is that the reply is IDENTICAL to the
    one for the slug it was rewritten into.
    """

    def _paths(slug):
        response = call_mcp(client, "loregarden_memory_status", {"workspace_slug": slug})
        if "isError" in response["result"]:
            return None  # refused, which is the answer this test wants
        payload = json.loads(response["result"]["content"][0]["text"])
        # The echoed `workspace_slug` differs by construction, so comparing
        # whole payloads would pass while every store pointed at the same
        # directories. Only the paths are the claim.
        return {key: value for key, value in payload.items() if key.endswith(("_dir", "_path"))}

    assert _paths("./loregarden") != _paths("loregarden"), (
        "a slug every read and write path refuses was answered with another workspace's own stores"
    )


@pytest.mark.parametrize("slug", ["../../other", "/etc", "./loregarden"], ids=repr)
def test_the_status_tool_names_the_refusal_rather_than_reporting_stores(client, slug):
    """What an agent gets back: a refusal it can act on.

    Not a success payload with paths, and not a success payload of nulls —
    AC3 is explicit that a recoverable condition reaches an agent as a
    shaped reply with a remedy, and "it failed" and "there is nothing" must
    not collapse into one answer.
    """
    response = call_mcp(client, "loregarden_memory_status", {"workspace_slug": slug})
    text = json.dumps(response)

    if "isError" in response.get("result", {}):
        return  # an error is an honest answer, if a blunter one
    payload = json.loads(response["result"]["content"][0]["text"])
    assert "memory_sqlite_path" not in payload, text[:400]
    assert payload.get("remedy"), "a refusal an agent cannot act on is half an answer"


@pytest.mark.parametrize("slug", ["../../other", "/etc", "./loregarden"], ids=repr)
def test_the_status_route_refuses_with_a_client_error_not_a_500(client, slug):
    """`api/memory.py` reaches the same `status()`.

    Once config delegates, the refusal travels here too, and
    `run_rest_operation` already turns a `ValueError` into a 400. A 500 would
    mean the operator surface reports a crash for an argument the system
    deliberately refuses; a 200 would mean it still reports the paths.
    """
    response = client.get("/api/memory/status", params={"workspace_slug": slug})
    assert 400 <= response.status_code < 500, (response.status_code, response.text[:300])
