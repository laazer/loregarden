"""The lead sentence each input class of `describe_handoff_failure` earns.

Three test modules assert these four strings — the service's own suite, the
terminal-handoff route's, and the external harness's — so they are built once,
here, from the identifiers rather than written out three times.

**Built, never parsed** (lg-milestone-that-1237 AC13). Two of the four leads are
*two sentences*, so every extractor the three modules grew while there was only
one — `_lead()`, `_first_sentence()`, and the inline
`head, _, _ = detail.partition(". ")` idiom — returns the lead with its
actionable half cut off:

    lg-…-1237: no target branch — the workspace's orchestration profile will not parse.

That is a string the operator never sees, and a cap or a path assertion made
against it passes while the real lead is wrong. So a guard-arm assertion builds
its expected lead with one of the four composers below and checks
`detail.startswith(expected)`. The single-`". "` split is correct for exactly
one input class — D, whose lead is one sentence — and is left to that class's
tests.
"""

from __future__ import annotations

from pathlib import Path

#: lg-milestone-that-1237 AC11. The live maximum `external_id`: 1625 tickets,
#: 12-29 characters, mean 21.6. A cap proved against a 6-character fixture id
#: proves nothing about the surface, so every test that pins a lead measures it
#: here.
LONGEST_EXTERNAL_ID = "lg-milestone-that-12345678901"

#: AC11's other two maxima, read from the live tree: 4 orchestration profiles at
#: 80-83 characters, 44 worktrees at 88. `tmp_path` alone is shorter than either
#: on this machine, which is why `padded_dir` exists and says so.
LONGEST_PROFILE_PATH = 83
LONGEST_WORKTREE_PATH = 88

#: AC10. Prose is `len(lead)` minus the single absolute path that lead names,
#: and the two arms have different budgets because they carry different amounts
#: of actionable text. Measured at the wordings below: 102/145/127 at a
#: 22-character external id, 109/152/134 at the 29-character live maximum.
PROFILE_PROSE_LIMIT = 115
NO_TARGET_PROSE_LIMIT = 160


def _assert_measurements() -> None:
    """The constants above are measurements, so a typo in one is a changed claim."""
    assert len(LONGEST_EXTERNAL_ID) == 29, LONGEST_EXTERNAL_ID


_assert_measurements()


def profile_lead(external_id: str, profile_path: Path | str) -> str:
    """AC2. Input class A: `ProfileUnreadableError` from `target_branch_name`.

    Names exactly one absolute path — the profile's, read off
    `ProfileUnreadableError.path` — and deliberately **not** `repo_root`:
    nothing in a worktree fixes malformed YAML, and a second path pushes the
    actionable one past the toast clamp.
    """
    return (
        f"{external_id}: no target branch — the workspace's orchestration profile "
        f"will not parse. Fix {profile_path}."
    )


def no_target_lead(external_id: str, repo_root: Path | str | None) -> str:
    """AC3 and AC4. Input class B, and class C which omits the worktree clause.

    `repo_root=None` is class C — `workspace is None`, which never enters the
    `try`. The omission is keyed on that and not on `work_item_type`, so it
    covers both inputs that reach it: a real initiative (`workspace_id` is NULL
    iff INITIATIVE) and a ticket whose workspace row is gone. For both,
    `repo_root` would be the bare `Path(".")` — the server's working directory,
    not a tree the operator can open, which is a path-shaped non-answer.

    `so nothing was merged` is load-bearing: it is the fact the old sentence got
    wrong by implying a merge had been attempted and conflicted.
    """
    lead = (
        f"{external_id}: this ticket has no target branch to be brought up to, so "
        f"nothing was merged — run the stage supervised."
    )
    if repo_root is None:
        return lead
    return f"{lead} The worktree is {repo_root}."


def conflict_lead(external_id: str, target_branch: str, repo_root: Path | str) -> str:
    """AC5. Input class D, the read succeeded: today's lead, byte for byte.

    The real conflicting-refresh case is the one this whole composer was written
    for (lg-milestone-that-917 AC12), and it reads exactly as it does today.
    """
    return (
        f"{external_id}: resolve the conflict with {target_branch}, or run the "
        f"stage supervised — the worktree is {repo_root}."
    )


def prose(lead: str, *, exempt: Path | str | None = None) -> str:
    """`lead` with the single absolute path it names removed (AC10, AC12).

    The measurement rule this restates exempted only `repo_root`, under which a
    profile-arm lead measures over 200 — it names a different path. So the
    exemption is "the one absolute path *that lead* names": the profile's on the
    profile arm, `repo_root` on the no-target arm, and nothing at all on class
    C, whose lead names no path.

    AC12 makes its two absence assertions on the same subtraction, because on
    the raw lead neither is decidable: a workspace repo path may contain a
    branch name as a substring.
    """
    return lead if exempt is None else lead.replace(str(exempt), "")


def padded_dir(base: Path, minimum: int, *, stem: str) -> Path:
    """A directory under `base` named `stem`, padded to at least `minimum` long.

    AC11 requires the leads be measured against the largest realistic volume
    read from the live database — a profile path of at least 83 characters and a
    worktree of at least 88 — and `tmp_path` reaches neither on its own on a
    short `TMPDIR`. So the padding is deliberate and this is where it is said
    so: one long path component rather than a deep tree, because what is being
    bought is length.

    `stem` is required rather than defaulted. Two fixtures in the same test used
    to collide on one padded name — `tmp_path` is already long on this machine,
    so both landed on the minimum `ppp` — and the second one to run died with
    `FileExistsError` inside a fixture, which reads as a harness failure rather
    than as the name clash it was.
    """
    path = base / (stem + "p" * max(0, minimum - len(str(base)) - len(stem) - 1))
    assert len(str(path)) >= minimum, (
        f"padded_dir produced {len(str(path))} characters, short of the {minimum} "
        f"AC11 measures at: {path}"
    )
    return path
