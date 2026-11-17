"""The code_signals package's error contract, over metadata git emits with exit 0.

Ticket lg-code-knowledge-299, AC12. Split out of `test_code_signals.py` when
that module reached the 2500-line organization cap; the ticket's "all in
test_code_signals.py" is a placement note from the spec and the cap is a hard
gate, so the error-contract tests — which are one cohesive subject — moved
rather than being trimmed to fit.

Every repository here is built by a fixture from git PLUMBING. None of these
objects can be produced by `git commit`: it refuses the timestamp, normalises
the author email (measured: `bad\\udcff@x.com` came back re-encoded as
`bad\\xc3\\xbf@x.com`), and the non-UTF-8 path cannot be checked out on macOS at
all. A broken plumbing step inside a test body would fail in the call phase
and read as the parser raising, which is precisely the assertion under test.

Scaffolding and the shared fixtures are imported from `test_code_signals`
following this suite's existing pattern for fixtures used by name.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest
from loregarden.services.code_signals import (
    GitSignalsParseError,
    GitSignalsReadError,
    SignalConfig,
    compute_git_signals,
    git_history,
    refresh_workspace_signals,
)
from loregarden.services.code_signals.git_history import parse_log_stream
from loregarden.services.git_subprocess import run_git
from tests.test_code_signals import (  # noqa: F401 - fixtures are used by name
    ALICE,
    _commit,
    _git,
    _init,
    _lines,
    _pair_rows,
    _signal_rows,
    _when,
    _write,
    authored_repo_fixture,
    captured_stream_fixture,
    config_fixture,
    not_a_repo_fixture,
    session_fixture,
    workspace_fixture,
)

# ==========================================================================
# AC12: the error contract is closed over commit metadata git emits with
# exit 0
#
# Both objects below are unreachable through `git commit`, so both repos are
# built with plumbing in a fixture (R11). A broken plumbing step inside a test
# body would fail in the call phase and read as the parser raising, which is
# precisely the assertion under test.
# ==========================================================================


#: 31 digits: larger than any `time_t`, and larger than `datetime`'s own range.
#: Which of OverflowError / OSError / ValueError the conversion raises is
#: platform-dependent, which is the whole reason the guard cannot name only one.
OVERFLOWING_TIMESTAMP = "9" * 31

#: A lone 0xFF byte: legal in a git path, not valid UTF-8, and not encodable by
#: SQLite's driver once `surrogateescape` has turned it into a lone surrogate.
NON_UTF8_PATH_BYTES = b"bad\xff.py"


def _git_bytes(root: Path, *args: str, stdin: bytes = b"") -> bytes:
    """One git plumbing command in *root*, stdin and stdout both raw bytes.

    `_git` is text-mode, and neither object here survives a decode: the commit
    carries a timestamp git itself will not re-emit through `%ct` unless it is
    written `--literally`, and the tree carries a path that is not UTF-8 at
    all.
    """
    result = run_git(args, cwd=root, input=stdin, check=True, capture_output=True)
    return result.stdout


@pytest.fixture(name="overflow_timestamp_repo")
def overflow_timestamp_repo_fixture(tmp_path: Path) -> Path:
    """A repo whose tip carries a committer timestamp no platform clock holds.

    `git commit` refuses the date, so the commit object is hand-written and
    hashed with `--literally`. git re-emits the 31 digits verbatim through
    `%ct` and exits 0, which is what makes this the package's problem: the read
    succeeded, so `GitSignalsReadError` never fires, and the conversion in
    `_parse_record` is the only thing standing between the stream and the
    caller.
    """
    root = _init(tmp_path / "overflow")
    _write(root, "src/base.py", _lines(3, "base"))
    _commit(root, "feat: base", author=ALICE, days_ago=30)

    tree = _git(root, "rev-parse", "HEAD^{tree}").stdout.strip()
    parent = _git(root, "rev-parse", "HEAD").stdout.strip()
    obj = (
        f"tree {tree}\n"
        f"parent {parent}\n"
        f"author {ALICE} <{ALICE}> 1700000000 +0000\n"
        f"committer {ALICE} <{ALICE}> {OVERFLOWING_TIMESTAMP} +0000\n"
        "\n"
        "fix: a timestamp beyond the end of time\n"
    ).encode()
    sha = _git_bytes(root, "hash-object", "-t", "commit", "-w", "--stdin", "--literally", stdin=obj)
    _git(root, "update-ref", "refs/heads/main", sha.decode().strip())
    return root


@pytest.fixture(name="non_utf8_path_repo")
def non_utf8_path_repo_fixture(tmp_path: Path) -> Path:
    """A repo holding a path whose bytes are not valid UTF-8.

    Written with `git mktree`, because the path cannot be checked out on macOS
    at all and so cannot be produced by `git add`. Under `-z` git emits the raw
    bytes with no quoting, so the parser meets `b"bad\\xff.py"` directly.
    """
    root = _init(tmp_path / "non_utf8")
    _write(root, "src/base.py", _lines(3, "base"))
    _commit(root, "feat: base", author=ALICE, days_ago=30)

    blob = _git_bytes(root, "hash-object", "-w", "--stdin", stdin=b"x\n").strip()
    entry = b"100644 blob " + blob + b"\t" + NON_UTF8_PATH_BYTES + b"\n"
    tree = _git_bytes(root, "mktree", stdin=entry).decode().strip()
    commit = _git(
        root,
        "commit-tree",
        tree,
        "-p",
        _git(root, "rev-parse", "HEAD").stdout.strip(),
        "-m",
        "fix: a path no decoder accepts",
        when=_when(20),
        author=ALICE,
    ).stdout.strip()
    _git(root, "update-ref", "refs/heads/main", commit)
    return root


def test_an_unconvertible_committer_timestamp_raises_parse_error_naming_it(
    overflow_timestamp_repo, config
):
    """AC12(a). The conversion raises OverflowError on this platform and OSError
    on others; `except ValueError` catches neither, so the package leaks a
    third exception class out of a documented two-class contract."""
    with pytest.raises(GitSignalsParseError) as caught:
        compute_git_signals(overflow_timestamp_repo, config)

    assert OVERFLOWING_TIMESTAMP in str(caught.value)


@pytest.mark.parametrize("error", [OverflowError, OSError, ValueError])
def test_every_way_the_clock_can_refuse_a_timestamp_is_the_same_failure(error, captured_stream):
    """The guard must be written against the family, not against the one
    exception this machine happens to raise. Patching the conversion is the
    only way to reach the other two deterministically: which one fires depends
    on the platform's `time_t`, so a repo fixture can only ever prove one."""
    clock = mock.Mock()
    clock.fromtimestamp.side_effect = error("clock refused")

    with (
        mock.patch.object(git_history, "datetime", clock),
        pytest.raises(GitSignalsParseError),
    ):
        parse_log_stream(captured_stream)


def test_a_path_that_is_not_utf8_raises_parse_error_naming_the_row(non_utf8_path_repo, config):
    """AC12(b). Not a lone-surrogate string: SQLite's driver cannot encode one,
    so storing it moves the failure into `session.commit()` inside the writer,
    where AC9's 'a failed read deletes no rows' no longer holds."""
    with pytest.raises(GitSignalsParseError) as caught:
        compute_git_signals(non_utf8_path_repo, config)

    message = str(caught.value)
    assert "bad" in message
    assert r"\xff" in message


def test_a_path_that_is_not_utf8_is_not_silently_dropped(non_utf8_path_repo, config):
    """The other wrong answer. Dropping the row would let the computation
    succeed and report the path's bugfix count as zero rather than unknown,
    which is the failure mode this whole milestone exists to prevent."""
    with pytest.raises(GitSignalsParseError):
        compute_git_signals(non_utf8_path_repo, config)


def test_a_non_utf8_path_raises_at_parse_with_no_git_process(captured_stream):
    """The same contract at the unit boundary: the row shape is rejected by
    `_parse_file_row`, not by something downstream that happens to choke on the
    surrogate later."""
    poisoned = captured_stream.replace(b"plain.py", NON_UTF8_PATH_BYTES)
    assert poisoned != captured_stream

    with pytest.raises(GitSignalsParseError):
        parse_log_stream(poisoned)


def test_a_non_utf8_path_leaves_the_workspaces_existing_rows_intact(
    session, workspace, authored_repo, non_utf8_path_repo, config
):
    """A failed read deletes no rows (AC9). That property only survives if the
    failure happens at parse: a lone surrogate reaching the writer fails at
    flush, after the delete."""
    refresh_workspace_signals(session, workspace.id, authored_repo, config)
    before = {row.file_path: row.churn_commits for row in _signal_rows(session, workspace.id)}
    assert before

    with pytest.raises(GitSignalsParseError):
        refresh_workspace_signals(session, workspace.id, non_utf8_path_repo, config)

    session.rollback()
    after = {row.file_path: row.churn_commits for row in _signal_rows(session, workspace.id)}
    assert after == before


@pytest.mark.parametrize("repo", ["overflow_timestamp_repo", "non_utf8_path_repo"])
def test_neither_hostile_object_escapes_the_two_error_contract(repo, config, request):
    """AC12(c). The contract is that a consumer catching the two named errors
    catches everything the package can throw. Asserting the specific class per
    fixture above does not check that, because a third class would simply make
    those tests fail for the wrong reason; this one states the closure."""
    root = request.getfixturevalue(repo)

    try:
        compute_git_signals(root, config)
    except (GitSignalsReadError, GitSignalsParseError):
        pass
    except Exception as error:  # noqa: BLE001 - the class of what escapes IS the assertion
        pytest.fail(f"{type(error).__name__} escapes the package's two-error contract: {error}")
    else:
        # Without this the closure would be satisfied by the package quietly
        # succeeding, which is the other wrong answer (a) and (b) rule out.
        pytest.fail(f"{repo} was accepted rather than rejected")


# ==========================================================================
# AC12, adversarial: the clauses the criterion names are two instances of one
# defect, and the closure sentence is about the family
#
# AC12's headline is that a consumer catching the two named errors catches
# EVERYTHING the package can throw over commit metadata git emits with exit 0.
# Clauses (a) and (b) name the committer timestamp and the path. They are not
# the only two fields the parser decodes, and the reason (b) must raise —
# `surrogateescape` produces a lone surrogate that SQLite's driver cannot
# encode, so the failure lands inside the writer after the delete — applies
# verbatim to the AUTHOR EMAIL, which is persisted as `top_author`.
#
# Measured on a real repository before these tests were written: a commit
# object carrying `bad\xff@x.com` is emitted through `%aE` with exit 0 and no
# quoting, `compute_git_signals` returns `top_author='bad\udcff@x.com'`, and
# `sqlite3` then refuses it with UnicodeEncodeError.
# ==========================================================================


#: A lone 0xFF inside an email address. Legal in a commit object, re-emitted
#: raw under `-z`, and not encodable once `surrogateescape` has had it.
NON_UTF8_AUTHOR_BYTES = b"bad\xff@example.com"

#: A latin-1 commit subject. Unlike the two above this one is HARMLESS: the
#: subject is read by `is_fix_subject` and never persisted, so nothing
#: downstream has to encode it. It is here to pin that the fix for (b) rejects
#: what cannot be stored and not merely what cannot be decoded.
LATIN1_SUBJECT_BYTES = b"fix: caf\xe9 in the decoder"


def _raw_commit(root: Path, *, blob_body: bytes, header: bytes, message: bytes) -> str:
    """Write a commit object by hand, under `src/base.py`, on top of HEAD.

    `git commit` normalises both fields these fixtures are about: it re-encodes
    an invalid-UTF-8 author email into valid UTF-8 (measured: `bad\\udcff@x.com`
    came back as `bad\\xc3\\xbf@x.com`), and it warns about but keeps a
    non-UTF-8 message. Only `hash-object --literally` puts the exact bytes into
    the object, so the tree is built with `mktree` and the commit by hand.

    *header* carries the author and committer lines verbatim, because they are
    the bytes under test.
    """
    blob = _git_bytes(root, "hash-object", "-w", "--stdin", stdin=blob_body).decode().strip()
    inner = _git_bytes(root, "mktree", stdin=b"100644 blob " + blob.encode() + b"\tbase.py\n")
    tree = _git_bytes(root, "mktree", stdin=b"040000 tree " + inner.strip() + b"\tsrc\n")
    parent = _git(root, "rev-parse", "HEAD").stdout.strip()
    obj = (
        b"tree "
        + tree.strip()
        + b"\nparent "
        + parent.encode()
        + b"\n"
        + header
        + b"\n\n"
        + message
        + b"\n"
    )
    sha = _git_bytes(root, "hash-object", "-t", "commit", "-w", "--stdin", "--literally", stdin=obj)
    _git(root, "update-ref", "refs/heads/main", sha.decode().strip())
    return sha.decode().strip()


def _unix(days_ago: float) -> int:
    return int((datetime.now(UTC) - timedelta(days=days_ago)).timestamp())


@pytest.fixture(name="hostile_author_repo")
def hostile_author_repo_fixture(tmp_path: Path) -> Path:
    """A repo whose newest commit has an author email that is not UTF-8.

    The commit ADDS lines to a SOURCE path, so the hostile email is not merely
    present in the stream: it wins `top_author` and is written to
    `file_git_signals.top_author` by the only writer in the package.
    """
    root = _init(tmp_path / "hostile_author")
    _write(root, "src/base.py", _lines(2, "base"))
    _commit(root, "feat: base", author=ALICE, days_ago=30)

    stamp = str(_unix(10)).encode()
    header = (
        b"author Bad <" + NON_UTF8_AUTHOR_BYTES + b"> " + stamp + b" +0000\n"
        b"committer Bad <" + NON_UTF8_AUTHOR_BYTES + b"> " + stamp + b" +0000"
    )
    _raw_commit(
        root,
        blob_body=_lines(6, "base").encode(),
        header=header,
        message=b"fix: an author no decoder accepts",
    )
    return root


@pytest.fixture(name="latin1_subject_repo")
def latin1_subject_repo_fixture(tmp_path: Path) -> Path:
    """A repo with a latin-1 commit subject and nothing else wrong with it.

    Commit messages in a legacy encoding are ordinary in real history — git
    itself only warns — so the package must keep reading this repository.
    """
    root = _init(tmp_path / "latin1_subject")
    _write(root, "src/base.py", _lines(2, "base"))
    _commit(root, "feat: base", author=ALICE, days_ago=30)

    stamp = str(_unix(5)).encode()
    header = (
        b"author Alice <" + ALICE.encode() + b"> " + stamp + b" +0000\n"
        b"committer Alice <" + ALICE.encode() + b"> " + stamp + b" +0000"
    )
    _raw_commit(
        root,
        blob_body=_lines(5, "base").encode(),
        header=header,
        message=LATIN1_SUBJECT_BYTES,
    )
    return root


def test_the_author_email_is_a_field_the_package_persists_and_must_decode(hostile_author_repo):
    """The premise, measured rather than assumed: git hands this stream out
    with exit 0 and no quoting, so `GitSignalsReadError` never fires and the
    bytes are the parser's problem exactly as the path's are."""
    result = run_git(
        git_history.log_args(SignalConfig()),
        cwd=hostile_author_repo,
        check=False,
        capture_output=True,
    )

    assert result.returncode == 0
    assert NON_UTF8_AUTHOR_BYTES in result.stdout


def test_a_non_utf8_author_email_raises_parse_error_naming_the_record(hostile_author_repo, config):
    """AC12(b) is a clause about one field; its reason is about the family.

    `surrogateescape` turns `bad\\xff@…` into `bad\\udcff@…`, the computation
    returns it as `top_author`, and SQLite's driver refuses to encode a lone
    surrogate — measured directly against `sqlite3`. That is the same three
    steps the criterion spells out for the path, so it has the same answer:
    reject at parse, where AC9's 'a failed read deletes no rows' still holds.
    Storing it fails inside the writer after the delete; dropping the record
    silently reassigns the file's owner to somebody who did not write it.
    """
    with pytest.raises(GitSignalsParseError) as caught:
        compute_git_signals(hostile_author_repo, config)

    assert r"\xff" in str(caught.value)


def test_a_non_utf8_author_email_never_reaches_a_stored_row(
    session, workspace, authored_repo, hostile_author_repo, config
):
    """The closure AC12(c) states, at the boundary where it actually matters.

    Nothing here asserts a parse error: it asserts that the refresh raises
    only what the contract names, and that the workspace's good rows are
    exactly where they were. A lone surrogate reaching `session.commit()`
    fails this on both counts, because the delete has already run.
    """
    refresh_workspace_signals(session, workspace.id, authored_repo, config)
    before = {row.file_path: row.churn_commits for row in _signal_rows(session, workspace.id)}
    assert before

    try:
        refresh_workspace_signals(session, workspace.id, hostile_author_repo, config)
    except (GitSignalsReadError, GitSignalsParseError):
        pass
    except Exception as error:  # noqa: BLE001 - the class of what escapes IS the assertion
        pytest.fail(f"{type(error).__name__} escapes the package's two-error contract: {error}")
    else:
        pytest.fail("a lone-surrogate author email was accepted rather than rejected")

    session.rollback()
    after = {row.file_path: row.churn_commits for row in _signal_rows(session, workspace.id)}
    assert after == before


def test_a_latin1_commit_subject_is_still_read_rather_than_rejected(latin1_subject_repo, config):
    """The over-correction this pair of tests exists to prevent.

    The cheapest way to satisfy AC12(b) and the test above is to decode every
    field strictly, which would make the package refuse any repository holding
    a commit message in a legacy encoding — common, harmless, and nowhere near
    a column. A subject is never persisted, so it must stay readable, and the
    fix it declares must still be attributed to the source path it touched.
    """
    signals = compute_git_signals(latin1_subject_repo, config)

    assert signals.commits_read == 2
    assert signals.files["src/base.py"].bugfix_commits == 1
    assert "caf" in signals.records[0].subject


# ==========================================================================
# AC4 / AC9, adversarial: a knob whose value git silently cannot honour
#
# `window_days` goes straight into `--since=<n> days ago`. Measured on a
# fixture repository: 20000 reads every commit, 25000 reads NONE, because the
# date lands before the Unix epoch and git's parser gives up. A WIDER window
# therefore returns FEWER commits, and the package reports that as
# `EmptyBasis.NO_COMMITS_IN_WINDOW` — a confident wrong answer with no error
# anywhere, which is exactly the collapse AC9 forbids. Non-positive values are
# the same shape: `--since=0 days ago` and `--since=-1 days ago` read a window
# that has not happened yet.
# ==========================================================================


@pytest.mark.parametrize("window_days", [0, -1, -180, 25_000, 100_000])
def test_a_window_the_config_cannot_honour_is_refused_rather_than_read_as_empty(window_days):
    """A knob that accepts a value it will silently mis-answer is not a knob.

    The refusal belongs at construction, where the caller can see it, rather
    than at `empty_basis`, where it is indistinguishable from a quiet repo.
    """
    with pytest.raises(ValueError):
        SignalConfig(window_days=window_days)


@pytest.mark.parametrize("window_days", [11, 180, 3_650])
def test_a_legitimate_window_is_still_accepted_end_to_end(authored_repo, window_days):
    """The other half of the bound: a ten-year window is an ordinary request
    for 'effectively all of this history', and must not be collateral.

    The narrowest value here is 11 days rather than 1 only because
    `authored_repo`'s newest commit is 10 days old; the floor itself is pinned
    by the test below, which needs no repository.
    """
    signals = compute_git_signals(authored_repo, SignalConfig(window_days=window_days))

    assert signals.empty_basis is None
    assert signals.commits_read > 0


@pytest.mark.parametrize("window_days", [1, 7, 20_000])
def test_the_bound_does_not_reach_into_windows_git_can_express(window_days):
    """A one-day window is a legitimate 'what moved today', and 20000 days was
    measured reading every commit of a fixture repository. Whatever shape the
    refusal above takes, it must not swallow either end."""
    assert SignalConfig(window_days=window_days).window_days == window_days


# ==========================================================================
# AC13: the error contract is closed over a bad `repo_root` as well, not only
# over git's output
#
# `repo_root` is caller-supplied — a workspace whose checkout moved, was
# deleted, or was never cloned. Measured at HEAD before these tests were
# written:
#
#     compute_git_signals(Path("/tmp/does-not-exist-xyz"), SignalConfig())
#         -> FileNotFoundError: [Errno 2] ... '/tmp/does-not-exist-xyz'
#     compute_git_signals(Path("/etc/hosts"), SignalConfig())
#         -> NotADirectoryError: [Errno 20] ... '/etc/hosts'
#
# Both come from the subprocess layer resolving `cwd` BEFORE git runs, so
# there is no exit code and no stderr for `read_history`'s stderr branch to
# classify: the guard has to be an explicit check (or an `OSError`
# conversion) in `read_history`, not a third stderr marker. A directory that
# exists and holds no repository is NOT this case and keeps its current
# behaviour of git exit 128 -> GitSignalsReadError; the closure test at the
# end of this section pins both beside each other.
#
# The five backlog consumers (300/301/302/303/305) catch against the
# package's documented two-error contract, and AC9's "a failed read deletes
# no rows" holds here today only by the accident that the raise is early.
# ==========================================================================


@pytest.fixture(name="missing_repo_root")
def missing_repo_root_fixture(tmp_path: Path) -> Path:
    """A path that is not there at all.

    Built in a fixture like every other repository in this suite (R11), even
    though the construction is one `/` — so that a future change to how these
    roots are made has one place to change, and so a setup failure is an
    ERROR rather than something the assertion could absorb.
    """
    root = tmp_path / "checkout-that-moved"
    assert not root.exists()
    return root


@pytest.fixture(name="file_repo_root")
def file_repo_root_fixture(tmp_path: Path) -> Path:
    """A path that exists and is not a directory.

    The sibling failure, and a different `OSError` subclass
    (`NotADirectoryError`, errno 20, against `FileNotFoundError`'s errno 2),
    so a guard written against one class alone does not satisfy both.
    """
    root = tmp_path / "checkout.tar"
    root.write_bytes(b"not a directory\n")
    assert root.is_file()
    return root


def test_a_repo_root_that_does_not_exist_raises_read_error_naming_the_path(
    missing_repo_root, config
):
    """AC13. `FileNotFoundError` is outside the two-error contract this
    package is sold on, so a consumer catching
    `(GitSignalsReadError, GitSignalsParseError)` around a workspace whose
    checkout has moved crashes instead of handling it."""
    with pytest.raises(GitSignalsReadError) as caught:
        compute_git_signals(missing_repo_root, config)

    assert str(missing_repo_root) in str(caught.value)


def test_a_repo_root_that_is_not_a_directory_raises_read_error_naming_the_path(
    file_repo_root, config
):
    """The same criterion's second half. `NotADirectoryError` is a different
    `OSError` subclass with a different errno, so the guard must cover the
    family rather than the one class the missing-path case happens to
    produce."""
    with pytest.raises(GitSignalsReadError) as caught:
        compute_git_signals(file_repo_root, config)

    assert str(file_repo_root) in str(caught.value)


@pytest.mark.parametrize("root_fixture", ["missing_repo_root", "file_repo_root"])
def test_the_bad_root_guard_lives_at_the_git_seam(root_fixture, config, request):
    """AC13 places the guard in `git_history.read_history`, which is the only
    module in the package that reaches a filesystem path at all.

    Asserted by calling `read_history` directly rather than by reading the
    source: a guard that lived in `compute` or in `store` would leave the git
    seam itself still raising the raw `OSError` at every other caller.
    """
    root = request.getfixturevalue(root_fixture)

    with pytest.raises(GitSignalsReadError) as caught:
        git_history.read_history(root, config)

    assert str(root) in str(caught.value)


def test_a_bad_root_raises_before_any_delete_so_both_tables_survive(
    session, workspace, authored_repo, missing_repo_root, config
):
    """AC13's reason for existing beyond the exception class.

    Asserted with NO rollback between the failure and the read: the property
    is that the writer never reached its delete, not that a rollback undid
    one. Both tables are checked, because `refresh_workspace_signals` clears
    the pair table as well and a guard placed after the first delete would
    leave the signal rows standing while the pairs were gone.
    """
    refresh_workspace_signals(session, workspace.id, authored_repo, config)
    signals_before = {
        row.file_path: row.churn_commits for row in _signal_rows(session, workspace.id)
    }
    pairs_before = {
        (p.file_path, p.partner_path): p.support for p in _pair_rows(session, workspace.id)
    }
    assert signals_before
    assert pairs_before

    with pytest.raises(GitSignalsReadError):
        refresh_workspace_signals(session, workspace.id, missing_repo_root, config)

    assert {
        row.file_path: row.churn_commits for row in _signal_rows(session, workspace.id)
    } == signals_before
    assert {
        (p.file_path, p.partner_path): p.support for p in _pair_rows(session, workspace.id)
    } == pairs_before


@pytest.mark.parametrize("root_fixture", ["missing_repo_root", "file_repo_root", "not_a_repo"])
def test_no_bad_repo_root_escapes_the_two_error_contract(root_fixture, config, request):
    """AC13 extends AC12(c)'s closure to the other input the package takes.

    `not_a_repo` sits in the same parametrization deliberately: it is the one
    bad root that already behaves (git runs, exits 128, stderr classifies),
    and AC13 says it must keep doing so. A guard that short-circuits every
    non-repository path before git runs would pass the two cases above and
    quietly strip this one of the exit code and stderr
    `GitSignalsReadError` carries.
    """
    root = request.getfixturevalue(root_fixture)

    try:
        compute_git_signals(root, config)
    except (GitSignalsReadError, GitSignalsParseError):
        pass
    except Exception as error:  # noqa: BLE001 - the class of what escapes IS the assertion
        pytest.fail(f"{type(error).__name__} escapes the package's two-error contract: {error}")
    else:
        pytest.fail(f"{root_fixture} was accepted rather than rejected")


def test_a_directory_that_is_not_a_repository_still_carries_gits_own_exit(not_a_repo, config):
    """The regression AC13's guard could cause, stated on its own.

    An over-eager `is_dir()` check that raised for anything without a `.git`
    would satisfy every assertion above while destroying the information the
    existing contract carries: git's real exit code and its real stderr.
    """
    with pytest.raises(GitSignalsReadError) as caught:
        compute_git_signals(not_a_repo, config)

    assert caught.value.returncode != 0
    assert caught.value.stderr


# ==========================================================================
# AC13, the rest of the family: a bad `repo_root` is not two `OSError`
# subclasses, it is the whole `OSError` family the `cwd` resolution can raise
#
# AC13's lettered examples are `FileNotFoundError` (errno 2) and
# `NotADirectoryError` (errno 20); its headline sentence is the contract —
# `(GitSignalsReadError, GitSignalsParseError)` "demonstrably catches
# everything `compute_git_signals` can raise for a bad `repo_root`". Measured
# at HEAD 95f80daf3f, two more subclasses escape it:
#
#     a directory with mode 000  -> PermissionError: [Errno 13] ...
#     a symlink pointing at itself -> OSError: [Errno 62] Too many levels ...
#
# The first one is the finding that matters, and it is a finding about the
# FIX rather than only about HEAD. The cheapest guard that satisfies AC13's
# two named roots is `if not repo_root.is_dir(): raise GitSignalsReadError`,
# and `Path.is_dir()` is TRUE for a mode-000 directory: stat only needs the
# parent's execute bit, while `chdir` needs the directory's own. So that
# guard closes the missing path, closes the regular file, closes the symlink
# loop (`is_dir()` swallows ELOOP and answers False) — and leaves a raw
# `PermissionError` crossing the seam for the one root a real deployment
# actually produces: a workspace checkout on a volume whose permissions
# changed under a shared machine.
#
# An `OSError` conversion around the `run_git` call — the other
# implementation AC13 already names — closes all four at once. These tests
# are written so that only that one passes.
# ==========================================================================


@pytest.fixture(name="unenterable_repo_root")
def unenterable_repo_root_fixture(tmp_path: Path):
    """A directory that exists, IS a directory, and cannot be entered.

    Mode 000: `stat` still answers (that needs the parent's execute bit), so
    every `exists()`/`is_dir()` check passes, and only the `chdir` the
    subprocess layer performs while resolving `cwd` fails. The mode is
    restored on teardown so pytest's own `tmp_path` cleanup can remove it;
    leaving it at 000 would turn a later housekeeping failure into noise that
    looks like this test.

    Skipped under euid 0, which ignores the permission bits entirely and
    would make the fixture produce an ordinary, enterable directory — a
    vacuously green test rather than an honest skip.
    """
    if os.geteuid() == 0:
        pytest.skip("euid 0 ignores the permission bits, so this root would be enterable")
    root = tmp_path / "checkout-on-a-locked-volume"
    root.mkdir()
    os.chmod(root, 0o000)
    try:
        yield root
    finally:
        os.chmod(root, 0o755)


@pytest.fixture(name="looping_repo_root")
def looping_repo_root_fixture(tmp_path: Path) -> Path:
    """A symlink pointing at itself: ELOOP, a plain `OSError` with no subclass.

    Included because it is the one member of this family that carries no
    dedicated exception class at all, so a guard written as a tuple of named
    subclasses (`except (FileNotFoundError, NotADirectoryError)`) lets it
    through while reading as exhaustive.
    """
    root = tmp_path / "checkout-pointing-at-itself"
    root.symlink_to(root)
    return root


def test_a_repo_root_that_cannot_be_entered_raises_read_error_naming_the_path(
    unenterable_repo_root, config
):
    """AC13's headline sentence, against the root its two examples miss.

    The `is_dir()` assertion is the point of the test, not scaffolding: it
    states the premise that separates this root from the other three, so a
    guard that only asks `is_dir()` fails here and says why.
    """
    assert unenterable_repo_root.is_dir(), (
        "premise: this root passes every is_dir() check and still cannot be entered"
    )

    with pytest.raises(GitSignalsReadError) as caught:
        compute_git_signals(unenterable_repo_root, config)

    assert str(unenterable_repo_root) in str(caught.value)


def test_a_repo_root_that_is_a_symlink_loop_raises_read_error_naming_the_path(
    looping_repo_root, config
):
    """The same criterion against an `OSError` with no subclass of its own."""
    with pytest.raises(GitSignalsReadError) as caught:
        compute_git_signals(looping_repo_root, config)

    assert str(looping_repo_root) in str(caught.value)


@pytest.mark.parametrize("root_fixture", ["unenterable_repo_root", "looping_repo_root"])
def test_every_bad_root_is_refused_at_the_git_seam_not_only_the_two_named_ones(
    root_fixture, config, request
):
    """The seam placement AC13 asks for, widened to the rest of the family.

    Separate from the existing seam test rather than folded into it so the
    two criteria-named roots and the two this round adds fail independently:
    a guard that covers one pair and not the other then reports exactly which
    half is missing instead of one collapsed failure.
    """
    root = request.getfixturevalue(root_fixture)

    with pytest.raises(GitSignalsReadError) as caught:
        git_history.read_history(root, config)

    assert str(root) in str(caught.value)


@pytest.mark.parametrize(
    "root_fixture",
    ["missing_repo_root", "file_repo_root", "unenterable_repo_root", "looping_repo_root"],
)
def test_no_bad_root_in_the_whole_oserror_family_escapes_the_two_error_contract(
    root_fixture, config, request
):
    """AC13's closure, over every way the `cwd` resolution can fail.

    `else: pytest.fail` is deliberate: a guard that quietly returned an empty
    `GitSignals` for an unreadable checkout would satisfy a bare
    `pytest.raises`-free closure test while committing the exact collapse
    AC9 forbids — a failed read reported as "there is nothing here", which
    `refresh_workspace_signals` would then write by deleting every good row.
    """
    root = request.getfixturevalue(root_fixture)

    try:
        compute_git_signals(root, config)
    except (GitSignalsReadError, GitSignalsParseError):
        pass
    except Exception as error:  # noqa: BLE001 - the class of what escapes IS the assertion
        pytest.fail(f"{type(error).__name__} escapes the package's two-error contract: {error}")
    else:
        pytest.fail(f"{root_fixture} was accepted rather than rejected")


@pytest.mark.parametrize(
    "root_fixture", ["file_repo_root", "unenterable_repo_root", "looping_repo_root"]
)
def test_no_bad_root_reaches_the_writers_delete(
    root_fixture, session, workspace, authored_repo, config, request
):
    """AC9's no-rows-deleted property, over the rest of the family.

    The existing version of this test covers the missing path only. Read with
    no `session.rollback()` between the failure and the assertion, because
    the property under test is that the writer never reached its delete —
    not that a rollback undid one.
    """
    bad_root = request.getfixturevalue(root_fixture)
    refresh_workspace_signals(session, workspace.id, authored_repo, config)
    signals_before = {
        row.file_path: row.churn_commits for row in _signal_rows(session, workspace.id)
    }
    pairs_before = {
        (p.file_path, p.partner_path): p.support for p in _pair_rows(session, workspace.id)
    }
    assert signals_before
    assert pairs_before

    with pytest.raises(GitSignalsReadError):
        refresh_workspace_signals(session, workspace.id, bad_root, config)

    assert {
        row.file_path: row.churn_commits for row in _signal_rows(session, workspace.id)
    } == signals_before
    assert {
        (p.file_path, p.partner_path): p.support for p in _pair_rows(session, workspace.id)
    } == pairs_before
