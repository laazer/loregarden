"""Is a branch's work on a ref? One answer, for every shape of history.

Publishing a tree squashes it onto the base (#621), so afterwards no commit on
the base is a commit on the branch, and every surface that asked "is this
branch's work there?" had to learn squash on its own. Six fixes taught six
surfaces (#609, #627, #631, #654, #655, #659), with four different checks
between them, and they disagreed: `adds_nothing` compared against the tip
only, so a squash the base later edited read as unlanded while
`superseding_commit` read it as landed.

So the question has one owner, `work_landed.landing_commit`, and this module
holds both halves of keeping it that way: every history shape built for real,
with the answer it must get, and a boundary test that no surface asks the
question any other way.
"""

from __future__ import annotations

import ast
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from loregarden.services.work_landed import landing_commit

SERVICES = Path(__file__).resolve().parents[1] / "loregarden"


class _Repo:
    def __init__(self, root: Path) -> None:
        self.root = root

    def git(self, *args: str) -> str:
        done = subprocess.run(
            ["git", *args], cwd=self.root, check=True, capture_output=True, text=True
        )
        return done.stdout.strip()

    def commit(self, message: str, **files: str | None) -> str:
        for name, body in files.items():
            path = self.root / name
            if body is None:
                path.unlink()
            else:
                path.write_text(body)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def squash_onto_main(self, branch: str) -> str:
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--squash", branch)
        return self.commit(f"squash {branch}")

    def tip(self, ref: str) -> str:
        return self.git("rev-parse", f"refs/heads/{ref}")


@pytest.fixture(name="repo")
def repo_fixture(tmp_path: Path) -> _Repo:
    repo = _Repo(tmp_path / "repo")
    repo.root.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "t@example.com")
    repo.git("config", "user.name", "Test")
    repo.git("config", "commit.gpgsign", "false")
    repo.commit("seed", **{"seed.txt": "seed\n", "shared.txt": "one\ntwo\nthree\n"})
    repo.git("checkout", "-q", "-b", "feature")
    return repo


# ---- the shapes ------------------------------------------------------------


def _unlanded(repo: _Repo) -> None:
    repo.commit("work", **{"a.txt": "a\n"})


def _merged(repo: _Repo) -> None:
    repo.commit("work", **{"a.txt": "a\n"})
    repo.git("checkout", "-q", "main")
    repo.git("merge", "-q", "--no-ff", "-m", "merge feature", "feature")


def _fast_forwarded(repo: _Repo) -> None:
    repo.commit("work", **{"a.txt": "a\n"})
    repo.git("checkout", "-q", "main")
    repo.git("merge", "-q", "--ff-only", "feature")


def _squashed(repo: _Repo) -> None:
    repo.commit("work", **{"a.txt": "a\n"})
    repo.commit("more", **{"shared.txt": "one\nTWO\nthree\n"})
    repo.squash_onto_main("feature")


def _squashed_then_edited(repo: _Repo) -> None:
    """#570's shape: the base rewrote the very line the squash brought in, so
    merging the branch into the tip conflicts, though its work did land."""
    _squashed(repo)
    repo.commit("later edit", **{"shared.txt": "one\nTWO, revised\nthree\n"})


def _squashed_then_unrelated(repo: _Repo) -> None:
    _squashed(repo)
    repo.commit("unrelated", **{"seed.txt": "seed\nmore\n"})


def _squashed_then_reverted(repo: _Repo) -> None:
    """Landing is a fact about history: the revert is the base's own later change."""
    _squashed(repo)
    repo.git("revert", "--no-edit", "HEAD")


def _partly_squashed(repo: _Repo) -> None:
    """Only the first of two commits reached the base."""
    repo.commit("first", **{"a.txt": "a\n"})
    repo.git("checkout", "-q", "-b", "first-only")
    repo.squash_onto_main("first-only")
    repo.git("checkout", "-q", "feature")
    repo.commit("second", **{"b.txt": "b\n"})


def _moved_on_after_squash(repo: _Repo) -> None:
    _squashed(repo)
    repo.git("checkout", "-q", "feature")
    repo.commit("after", **{"c.txt": "c\n"})


def _empty_branch(repo: _Repo) -> None:
    """Nothing to deliver, so nothing is missing: the same answer `land_ticket`
    has always given a branch the target already contains."""


def _nets_to_nothing(repo: _Repo) -> None:
    """Commits that cancel out add nothing, though no commit is shared."""
    repo.commit("add", **{"a.txt": "a\n"})
    repo.commit("remove", **{"a.txt": None})


def _base_has_the_same_change(repo: _Repo) -> None:
    """The same edit made independently on the base, as a cherry-pick is."""
    work = repo.commit("work", **{"a.txt": "a\n"})
    repo.git("checkout", "-q", "main")
    repo.commit("unrelated first", **{"seed.txt": "seed\nmore\n"})
    repo.git("cherry-pick", work)


@dataclass(frozen=True)
class Shape:
    build: Callable[[_Repo], None]
    landed: bool


SHAPES: dict[str, Shape] = {
    "unlanded": Shape(_unlanded, landed=False),
    "merged": Shape(_merged, landed=True),
    "fast-forwarded": Shape(_fast_forwarded, landed=True),
    "squashed": Shape(_squashed, landed=True),
    "squashed, then the base edited the same file": Shape(_squashed_then_edited, landed=True),
    "squashed, then the base moved on": Shape(_squashed_then_unrelated, landed=True),
    "squashed, then reverted on the base": Shape(_squashed_then_reverted, landed=True),
    "only the first commit squashed": Shape(_partly_squashed, landed=False),
    "squashed, then the branch moved on": Shape(_moved_on_after_squash, landed=False),
    "an empty branch": Shape(_empty_branch, landed=True),
    "commits that net to nothing": Shape(_nets_to_nothing, landed=True),
    "the same change cherry-picked": Shape(_base_has_the_same_change, landed=True),
}


@pytest.mark.parametrize("shape", SHAPES.values(), ids=SHAPES.keys())
def test_every_shape_of_history_gets_its_answer(repo: _Repo, shape: Shape):
    shape.build(repo)
    holder = landing_commit(repo.root, repo.tip("feature"), repo.tip("main"))

    assert bool(holder) is shape.landed
    if holder:
        # The holder is on the base, and holds everything the branch adds.
        repo.git("merge-base", "--is-ancestor", holder, repo.tip("main"))


# ---- one owner -------------------------------------------------------------

#: The building blocks the question used to be answered with, ad hoc.
_BUILDING_BLOCKS = frozenset({"superseding_commit", "adds_nothing"})

#: Where they may still be used: their definition, and the one owner.
_OWNERS = frozenset({"services/git_merge_noco.py", "services/work_landed.py"})


def test_no_surface_asks_the_question_any_other_way():
    """Every surface goes through `landing_commit`, so none can learn squash on
    its own again. A new surface that reaches for a building block instead
    fails here, not as the seventh fix."""
    offenders: list[str] = []
    for path in sorted(SERVICES.rglob("*.py")):
        relative = path.relative_to(SERVICES).as_posix()
        if relative in _OWNERS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom):  # py-org: allow-isinstance
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.Attribute):  # py-org: allow-isinstance
                names = [node.attr]
            offenders += [f"{relative}: {name}" for name in names if name in _BUILDING_BLOCKS]
    assert not offenders, (
        "Ask whether a branch's work is on a ref with work_landed.landing_commit: "
        f"{sorted(set(offenders))}"
    )
