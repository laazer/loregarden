"""What ``load_profile_from_path`` says when the file is not a profile at all.

Every reader of a workspace's orchestration profile funnels through this one
function — ``resolve_orchestration_profile`` (the dispatch paths, the target
branch, the git automation config), ``default_gate_commands`` and
``list_profiles`` (the Gates editor, which is the surface that *writes* these
files). So this is the seam where "the file could not be read" has to become one
named error, rather than six different exceptions from three libraries leaking
into whichever caller happened to ask.

The six shapes below are measured, not imagined (2026-10-17, this venv):

| ``default.yaml``                     | raised today                 | a ``ValueError``? |
|--------------------------------------|------------------------------|-------------------|
| ``monitor: {autofix: [stage_thrash]}`` | pydantic ``ValidationError`` | yes               |
| ``name: [unclosed``                  | ruamel ``ParserError``       | no                |
| a tab-indented mapping               | ruamel ``ScannerError``      | no                |
| a sequence document                  | ``TypeError``                | no                |
| a scalar document                    | ``TypeError``                | no                |
| invalid UTF-8 bytes                  | ``UnicodeDecodeError``       | yes               |

Two of them are a ``ValueError`` and four are not, which is the whole defect:
the guards downstream are spelled ``except ValueError``, so four shapes of a
half-written profile escape as a 500. An empty file and a ``null`` document are
*not* in that table — they parse to ``{}`` through ``_load_yaml``'s ``or {}``
and are legal, and this module pins that they stay legal.

The classification lives here rather than in ``resolve_orchestration_profile``
because this is the only place all three readers pass through, and because it is
testable with a ``tmp_path`` and one file: no Session, no Workspace, no git
repository, no route.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import pytest
from loregarden.services import orchestration_profile as orchestration_profile_module
from loregarden.services.orchestration_profile import (
    OrchestrationProfile,
    ProfileUnreadableError,
    load_profile_from_path,
)

#: The shape, the bytes on disk, and the fragment of the underlying library's
#: own message that has to survive into the named error — because that fragment
#: is the only text naming *which* line of *which* file an operator has to go
#: and fix. The two non-mapping shapes have no underlying exception to carry
#: (their cause is removed rather than caught), so they carry the reason this
#: seam writes itself.
UNREADABLE = {
    "schema-invalid": (
        b"slug: default\nname: Default\nmonitor:\n  autofix: [stage_thrash]\n",
        "Conditions cannot be auto-fixed",
    ),
    "corrupt-syntax": (
        b"slug: default\nname: [unclosed\n",
        "while parsing a flow sequence",
    ),
    "tab-indented": (
        b"slug: default\nname:\n\tnested: 1\n",
        "that cannot start any token",
    ),
    "sequence-document": (b"- one\n- two\n", "the document is a sequence, not a mapping"),
    "scalar-document": (b"just a string\n", "the document is a scalar, not a mapping"),
    "invalid-utf8": (b"slug: default\nname: \xff\xfe not utf-8\n", "codec can't decode byte"),
}

#: The two shapes whose cause is removed rather than wrapped (the mapping check
#: raises directly), so there is no ``__cause__`` to carry through.
NON_MAPPING = ("sequence-document", "scalar-document")


def _write(tmp_path: Path, shape: str) -> Path:
    """``default.yaml`` holding ``shape``'s bytes.

    Written as bytes, not text: one of the six shapes is invalid UTF-8, and it
    cannot be expressed any other way.
    """
    path = tmp_path / "default.yaml"
    path.write_bytes(UNREADABLE[shape][0])
    return path


# --- AC3: one named error, at the load seam -----------------------------------


def test_the_error_is_a_runtimeerror_and_deliberately_not_a_valueerror():
    """AC3's last sentence, and the reason every tuple downstream is edited.

    A ``ValueError`` base would make this change free — every
    ``except ValueError`` already in the tree would catch it — and that is
    exactly why it is refused. ``lg-milestone-that-975`` is an open sibling
    whose purpose is to *narrow* ``describe_handoff_failure``'s ``ValueError``
    arm; an error that rides in on ``ValueError`` and is named in no tuple
    re-opens this 500 the day 975 lands. ``RuntimeError`` matches
    ``TargetBranchError`` (target_branch.py:61), so ``grep -rn
    ProfileUnreadableError`` enumerates every site that has decided what an
    unreadable profile means.
    """
    assert issubclass(ProfileUnreadableError, RuntimeError), (
        "ProfileUnreadableError has to subclass RuntimeError, the way TargetBranchError "
        "does, so that the sites which handle it are the sites that named it"
    )
    assert not issubclass(ProfileUnreadableError, ValueError), (
        "a ValueError base makes the migration free and the guarantee worthless: "
        "every `except ValueError` in the tree would catch it silently, and the day "
        "lg-milestone-that-975 narrows that arm this 500 comes back"
    )


@pytest.mark.parametrize("shape", sorted(UNREADABLE), ids=sorted(UNREADABLE))
def test_every_unreadable_shape_raises_the_one_named_error(tmp_path: Path, shape: str):
    """AC3. Six input shapes, three libraries, one error out.

    Parametrized rather than written six times because the point is that the
    caller cannot tell them apart: a guard written for the schema-invalid file
    has to hold for the truncated one, the tab-indented one, the sequence, the
    scalar and the non-UTF-8 one too. Four of the six are not a ``ValueError``
    today, so the ``isinstance`` check below is the part that fails before the
    fix for four of them, and the wrap is what makes all six one answer.
    """
    path = _write(tmp_path, shape)

    with pytest.raises(ProfileUnreadableError) as excinfo:
        load_profile_from_path(path)

    assert type(excinfo.value) is ProfileUnreadableError, (
        f"{shape} raised {type(excinfo.value).__name__}: the point of the seam is that "
        "every unreadable shape leaves it as the same named error"
    )


@pytest.mark.parametrize("shape", sorted(UNREADABLE), ids=sorted(UNREADABLE))
def test_the_error_names_the_file_by_its_absolute_path(tmp_path: Path, shape: str):
    """AC3 / C7. Both the message and the attribute, and absolute in both.

    The attribute is what a caller uses to name the file without parsing prose
    (``stage_retry_budget``'s WARNING does exactly that). The message is what
    reaches the operator, through the 400 ``detail`` and the failed run's
    ``stderr``. ``_repo_relative``'s repo-relative form is the wrong answer for
    both: the operator is in a worktree, and `agent_context/orchestration/
    default.yaml` is not a path they can open from where they are standing.
    """
    path = _write(tmp_path, shape)
    absolute = path.resolve()

    with pytest.raises(ProfileUnreadableError) as excinfo:
        load_profile_from_path(path)

    assert excinfo.value.path == absolute, (
        f"exc.path is {excinfo.value.path}: callers name the file from this attribute, "
        "so it has to be the absolute path and not whatever relative form was passed in"
    )
    assert str(absolute) in str(excinfo.value), (
        f"the message does not carry the file an operator has to open: {excinfo.value}"
    )


@pytest.mark.parametrize("shape", sorted(UNREADABLE), ids=sorted(UNREADABLE))
def test_the_underlying_failure_is_carried_verbatim(tmp_path: Path, shape: str):
    """AC3. The cause's own words survive the wrap.

    ``ruamel``'s message is the only text that names the line and column that
    will not parse, and pydantic's is the only text that names the invalid
    field. A named error that replaced them with "the profile could not be read"
    would be a worse answer than the 500 it replaces: the operator would know a
    file is broken and have no way to find out where.
    """
    path = _write(tmp_path, shape)
    _, fragment = UNREADABLE[shape]

    with pytest.raises(ProfileUnreadableError) as excinfo:
        load_profile_from_path(path)

    message = str(excinfo.value)
    assert fragment in message, (
        f"the text naming what is actually wrong with the file was dropped: {message}"
    )
    assert message.startswith(
        f"{excinfo.value.path} could not be read as an orchestration profile: "
    ), f"the message is not the one shape every caller renders: {message!r}"

    if shape in NON_MAPPING:
        assert excinfo.value.__cause__ is None, (
            "the non-mapping shape has its cause removed rather than caught (C4), so "
            f"there is no exception under it: {excinfo.value.__cause__!r}"
        )
        return
    cause = excinfo.value.__cause__
    assert cause is not None, (
        "the wrap dropped `from exc`, so the traceback no longer shows which library "
        "refused the file"
    )
    assert str(cause) in message, (
        f"the caught exception's own text is not carried verbatim: {message!r} does not "
        f"contain {str(cause)!r}"
    )


@pytest.mark.parametrize("shape", sorted(NON_MAPPING), ids=sorted(NON_MAPPING))
def test_a_document_that_is_not_a_mapping_says_what_it_is_instead(tmp_path: Path, shape: str):
    """C4 / R3. The two shapes whose cause is removed, not caught.

    A sequence and a scalar both raise ``TypeError`` today — the sequence from
    ``"slug" not in raw`` (ruamel's ``CommentedSeq`` compares a str to an int),
    the scalar from ``raw["slug"] = path.stem``. Catching ``TypeError`` here
    would relabel a *programming* error inside ``OrchestrationProfile``'s own
    validators as "the profile could not be read" and hand it to the operator as
    a fallback branch, which is what the guard one layer up refuses. So the
    check goes in front of the indexing instead, and it says which of the two
    the file actually is — because "not a mapping" without that is a riddle.
    """
    path = _write(tmp_path, shape)
    expected = UNREADABLE[shape][1]

    with pytest.raises(ProfileUnreadableError) as excinfo:
        load_profile_from_path(path)

    assert str(excinfo.value).endswith(expected), (
        f"a {shape} has to say what the document is instead of a mapping: {excinfo.value}"
    )


@pytest.mark.parametrize("shape", sorted(UNREADABLE), ids=sorted(UNREADABLE))
def test_the_same_file_raises_the_same_named_error_on_every_call(tmp_path: Path, shape: str):
    """Risk 7. ``_parse_profile`` is ``lru_cache``d, and a cache is a liar here.

    ``functools.lru_cache`` memoises *returns*, never raises, so a failing parse
    is re-attempted on every call. Two operators hitting the same broken profile
    must each get the named error — not one of them getting it and the second
    getting a cached success, and not the second getting the raw library
    exception because the wrap only ran on the cache miss.
    """
    path = _write(tmp_path, shape)

    with pytest.raises(ProfileUnreadableError) as first:
        load_profile_from_path(path)
    with pytest.raises(ProfileUnreadableError) as second:
        load_profile_from_path(path)

    assert str(first.value) == str(second.value), (
        f"the same broken file described itself two ways: {str(first.value)!r} then "
        f"{str(second.value)!r}"
    )


# --- AC4: what is still legal, and what is still a programming error ----------


@pytest.mark.parametrize(
    ("label", "content"),
    [("empty-file", b""), ("null-document", b"null\n")],
    ids=["empty-file", "null-document"],
)
def test_an_empty_file_and_a_null_document_are_still_a_profile(
    tmp_path: Path, label: str, content: bytes
):
    """AC4. The two shapes that look unreadable and are not.

    ``_load_yaml``'s ``or {}`` turns both into an empty mapping, which
    ``_parse_profile`` then stamps with the file's stem and validates into a
    profile of pure defaults. That is a real behaviour the tree relies on — a
    workspace whose YAML has been emptied runs on the built-in defaults rather
    than refusing to dispatch — and a mapping check written as "the document is
    truthy" would break both while passing every test above.
    """
    path = tmp_path / "default.yaml"
    path.write_bytes(content)

    profile = load_profile_from_path(path)

    assert type(profile) is OrchestrationProfile, (
        f"a {label} is legal and must load to a profile, not {type(profile).__name__}"
    )
    assert profile.slug == path.stem, (
        f"the slug is stamped from the file name when the document does not carry one: "
        f"{profile.slug!r}"
    )


def _handlers(func) -> list[ast.ExceptHandler]:
    source = textwrap.dedent(inspect.getsource(func))
    return [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ExceptHandler)  # py-org: allow-isinstance - AST node walk
    ]


def _caught_names(handler: ast.ExceptHandler) -> set[str]:
    if handler.type is None:
        return {"BaseException"}
    return {
        node.id
        for node in ast.walk(handler.type)
        if isinstance(node, ast.Name)  # py-org: allow-isinstance - AST node walk
    }


def test_no_wrap_tuple_at_this_seam_catches_a_programming_error():
    """AC4's second half, read off the AST rather than the text.

    ``TypeError`` is the shape this seam is most tempted to catch, because it is
    what two of the six inputs raise today and catching it is one word. It must
    not be caught: a ``TypeError`` from inside a validator we wrote, or from
    ``model_validate`` itself, would then be reported to an operator as "the
    profile could not be read" and acted on as a fallback — a programming error
    laundered into a configuration error. ``Exception`` and ``BaseException``
    are the same mistake, wider.

    Asserted on the AST so that re-ordering, re-wrapping or aliasing the names
    cannot change the answer, and over both functions because the wrap could be
    put in either one.
    """
    refused = {"TypeError", "Exception", "BaseException"}
    for func in (
        orchestration_profile_module.load_profile_from_path,
        orchestration_profile_module._parse_profile.__wrapped__,
    ):
        for handler in _handlers(func):
            caught = _caught_names(handler)
            assert not (caught & refused), (
                f"{func.__name__} catches {sorted(caught & refused)}: at this seam that "
                "relabels a programming error as an unreadable file and hands it to the "
                "operator as a configuration problem"
            )


def test_the_wrap_names_the_three_libraries_that_refuse_a_file_and_nothing_else():
    """R3, and the reason it is three names and not one.

    ``ParserError`` alone misses the tab-indented file, which is a
    ``ScannerError``; ``YAMLError`` is their common base and covers both.
    ``UnicodeDecodeError`` is a ``ValueError`` today, so leaving it out would
    keep it working at the handoff guard (whose tuple still names
    ``ValueError``) while turning the three Gate Studio GETs into new 500s.
    ``ValidationError`` is the shape that already worked and must not regress.

    Pinned as a closed set, in the same breath as the test above: "catches
    nothing too broad" is only half an answer if the tuple can also be narrowed
    back to one name and stay green.
    """
    handlers = _handlers(orchestration_profile_module.load_profile_from_path)
    assert len(handlers) == 1, (
        f"load_profile_from_path has {len(handlers)} except handlers: the wrap goes "
        "around the _parse_profile call only, so that path.stat()'s OSError — a missing "
        "file, a different question — is not relabelled as an unreadable one"
    )
    assert _caught_names(handlers[0]) == {"YAMLError", "UnicodeDecodeError", "ValidationError"}, (
        f"the wrap catches {sorted(_caught_names(handlers[0]))}: a tab-indented file is a "
        "ScannerError and a truncated one is a ParserError, so the name has to be their "
        "YAMLError base; invalid UTF-8 and a schema-invalid document are the other two"
    )


def test_a_missing_file_is_not_reported_as_an_unreadable_one(tmp_path: Path):
    """R3's exclusion. ``path.stat()``'s ``OSError`` stays an ``OSError``.

    "There is no profile here" and "the profile here will not parse" are two
    different answers, and ``resolve_orchestration_profile`` already asks the
    first one with ``is_file()``. A wrap placed around the whole function body
    instead of around the ``_parse_profile`` call would collapse them, and the
    Gates editor would tell an operator to go and fix a file that does not
    exist.
    """
    with pytest.raises(OSError) as excinfo:
        load_profile_from_path(tmp_path / "nothing-here.yaml")

    assert not isinstance(  # py-org: allow-isinstance - the type is the claim
        excinfo.value, ProfileUnreadableError
    ), "a file that is not there was reported as a file that cannot be parsed"


# --- adversarial: what the six shapes above do not pin ------------------------
#
# Everything above this line is the suite the criteria asked for, and it is
# satisfied by implementations that are wrong in four ways. Each block below is
# a mutant that survives every test above, measured by building it (2026-10-17,
# this venv) and watching the module stay green:
#
# | mutant                                               | survives above | killed by |
# |------------------------------------------------------|----------------|-----------|
# | `self.path = Path(path)` — no `.resolve()`           | yes            | the two path tests |
# | message with anything appended to the reason         | yes            | the exact-message test |
# | `except (ParserError, ScannerError, ...)` not `YAMLError` | yes       | the unlisted shapes |
# | the mapping check written as `if not raw`            | yes            | the legal shapes |
# | `except TypeError` added to the wrap                 | AST only       | the propagation tests |
#
# The first one survives for a reason worth writing down: `tmp_path` is already
# resolved (pytest hands out `/private/var/...` on this platform, not the
# `/var/...` symlink), so `path.resolve() == path` for every file the tests
# above write, and an error that stores the path it was handed is
# indistinguishable from one that resolves it.


def test_the_path_is_resolved_rather_than_the_one_it_was_handed(tmp_path: Path):
    """R2's ``Path(path).resolve()``, which nothing above can see.

    Every test above writes `tmp_path / "default.yaml"` and asserts
    `exc.path == path.resolve()` — but `tmp_path` is already a resolved
    absolute path, so that assertion holds just as well for an error that
    stores its argument untouched. The resolution only shows up when the path
    handed in is not already normal, and that is the realistic case:
    `resolve_orchestration_profile` builds its candidates as
    `orchestration_dir() / "default.yaml"`, and `orchestration_dir()` is
    `settings.repo_root / ...`, which is whatever the process was started with.

    It matters because C7's whole point is that the operator can open the file
    from where they are standing. A detail reading
    `.../worktrees/t-1215/server/../agent_context/orchestration/default.yaml`
    is a path they have to mentally evaluate, and the 400 and the run's
    `stderr` render `exc.path` verbatim.
    """
    (tmp_path / "sub").mkdir()
    path = tmp_path / "default.yaml"
    path.write_bytes(UNREADABLE["corrupt-syntax"][0])

    with pytest.raises(ProfileUnreadableError) as excinfo:
        load_profile_from_path(tmp_path / "sub" / ".." / "default.yaml")

    assert excinfo.value.path == path, (
        f"exc.path is the un-normalized path it was handed, {excinfo.value.path}: "
        "C7 asks for a path the operator can open, and this one has to be evaluated first"
    )
    assert ".." not in str(excinfo.value.path), (
        f"exc.path carries a `..` segment: {excinfo.value.path}"
    )
    # Only the lead. `ruamel`'s own message echoes the path it was handed, so
    # the un-normalized form survives in the carried reason — which is fine and
    # is not what any caller renders as "the file to open".
    assert str(excinfo.value).startswith(f"{path} could not be read"), (
        f"the sentence does not lead with the resolved path: {excinfo.value}"
    )


def test_a_relative_path_is_still_named_absolutely(tmp_path: Path, monkeypatch):
    """R2 / C7 again, from the other direction: the caller's cwd is not a fact.

    `load_profile_from_path` is reachable with a relative path — `path.stat()`
    accepts one — and the error then names a file relative to whatever
    directory the server process happens to be in. The operator reading the
    toast is not in that directory, and neither is the run whose `stderr`
    carries the same string. `_repo_relative` is the form this seam
    deliberately does *not* use for the message (C7), and a path resolved
    against an unstated cwd is the same mistake with less warning.
    """
    path = tmp_path / "default.yaml"
    path.write_bytes(UNREADABLE["corrupt-syntax"][0])
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ProfileUnreadableError) as excinfo:
        load_profile_from_path(Path("default.yaml"))

    assert excinfo.value.path.is_absolute(), (
        f"exc.path is relative to the server's cwd, which no reader of it shares: "
        f"{excinfo.value.path}"
    )
    assert excinfo.value.path == path.resolve()
    assert str(path.resolve()) in str(excinfo.value)


@pytest.mark.parametrize("shape", sorted(UNREADABLE), ids=sorted(UNREADABLE))
def test_the_message_is_exactly_the_sentence_and_carries_nothing_else(tmp_path: Path, shape: str):
    """R2's word "exactly", which `startswith` plus `in` does not test.

    The tests above assert the message *begins* with the sentence and
    *contains* the cause's text. Both still pass when the reason is wrapped,
    duplicated, prefixed with the exception's class name, or has a remedy
    sentence stapled on — and this string is rendered verbatim into the 400
    `detail`, which Risk 5 measured against a four-line toast clamp at ~188
    characters while the lead sentence alone is already 191. Every character
    added here is a character of the `ruamel` message, naming the line that
    will not parse, that falls off the end.

    Asserted as an equality so the rendering is pinned once, for the five
    places that re-render it (the three Gate Studio GETs, the handoff WARNING
    and the budget WARNING), rather than approximately in each of them.
    """
    path = _write(tmp_path, shape)

    with pytest.raises(ProfileUnreadableError) as excinfo:
        load_profile_from_path(path)

    reason = UNREADABLE[shape][1] if shape in NON_MAPPING else str(excinfo.value.__cause__)
    assert str(excinfo.value) == (
        f"{path.resolve()} could not be read as an orchestration profile: {reason}"
    ), (
        "the message is not exactly the one sentence R2 pins — anything extra here is "
        f"text clipped off the end of the operator's toast: {str(excinfo.value)!r}"
    )


#: Three more shapes of a file nobody meant to write, measured the same way and
#: on none of the ticket's lists. Each is a *different* ``ruamel`` exception
#: class, and that is the point: the first is the literal shape the ticket names
#: as the cause ("an interrupted write"), and a tuple spelled with leaf names
#: rather than their ``YAMLError`` base catches none of them.
UNLISTED_UNREADABLE = {
    # An interrupted write leaves the tail of the file unwritten, and on a
    # sparse or pre-allocated file that tail reads back as NUL bytes. Not a
    # ParserError and not a ScannerError: ruamel refuses it at the reader.
    "nul-byte-tail": (b"slug: default\nname: Def\x00ault\n", "unacceptable character"),
    # The hand edit that adds a key without noticing the file already has it.
    # ruamel's round-trip loader refuses rather than taking the last one.
    "duplicate-keys": (b"slug: default\nslug: other\n", "found duplicate key"),
    # An editor (or a save that appended instead of replacing) leaving two
    # documents in one file. The round-trip `load` reads a single document.
    "two-documents": (b"---\nslug: a\n---\nslug: b\n", "expected a single document"),
}


@pytest.mark.parametrize("shape", sorted(UNLISTED_UNREADABLE), ids=sorted(UNLISTED_UNREADABLE))
def test_the_yamlerror_base_covers_shapes_nobody_enumerated(tmp_path: Path, shape: str):
    """R3's reason for naming ``YAMLError`` rather than the classes it has seen.

    The ticket lists four shapes and the spec stage found two more, which is
    six measured inputs and a wrap tuple that happens to cover them. That is
    not the same as a tuple that covers *unreadable files*, and the difference
    is testable: these three are `ReaderError`, `DuplicateKeyError` and
    `ComposerError`, none of which is a `ParserError`, a `ScannerError` or a
    `ValidationError`, and all of which a half-written or hand-edited profile
    produces in practice. A tuple enumerated from the six measured shapes
    passes every test above and leaves all three a 500.

    The first one is the shape the ticket's own description names as the cause.
    "The Gates editor writes these files, so an interrupted write … is what
    produces the shapes above" — an interrupted write does not usually produce
    a syntax error, it produces a truncated file, and a truncated file whose
    tail was pre-allocated reads back with NUL bytes in it. That is a
    `ReaderError`, and it is on no list in this ticket.
    """
    content, fragment = UNLISTED_UNREADABLE[shape]
    path = tmp_path / "default.yaml"
    path.write_bytes(content)

    with pytest.raises(ProfileUnreadableError) as excinfo:
        load_profile_from_path(path)

    assert type(excinfo.value) is ProfileUnreadableError, (
        f"{shape} escaped as {type(excinfo.value).__name__}: a tuple enumerated from the "
        "shapes someone happened to measure is not a tuple that covers unreadable files"
    )
    assert fragment in str(excinfo.value), (
        f"the text naming what is wrong with the file was dropped: {excinfo.value}"
    )
    assert str(path.resolve()) in str(excinfo.value)


#: Six files that look broken to a reader and are not, beyond AC4's two. All
#: six reach ``_parse_profile`` as an empty mapping through ``_load_yaml``'s
#: ``or {}`` and validate into a profile of pure defaults.
LEGAL_BUT_LOOKS_BROKEN = {
    "comments-only": b"# the gate list, deliberately empty for now\n",
    "whitespace-only": b"   \n\n\t\n",
    "tilde-null": b"~\n",
    "explicit-empty-mapping": b"{}\n",
    "document-marker-only": b"---\n",
    # A BOM is what a Windows editor adds on save. ruamel strips it, so this
    # file parses — and an implementation that read the bytes itself to decide
    # whether they "look like" a mapping would refuse it.
    "bom-prefixed": b"\xef\xbb\xbfname: Default\n",
}


@pytest.mark.parametrize(
    "shape", sorted(LEGAL_BUT_LOOKS_BROKEN), ids=sorted(LEGAL_BUT_LOOKS_BROKEN)
)
def test_a_file_with_nothing_in_it_is_still_a_profile_however_it_says_nothing(
    tmp_path: Path, shape: str
):
    """AC4's class rather than AC4's two examples.

    AC4 pins a zero-byte file and a `null` document. Both of those are falsy
    *before* `_load_yaml`'s `or {}` and empty mappings after it, so a mapping
    check written as `if not raw: raise` — the shortest thing that passes every
    unreadable test in this module — fails exactly those two and nothing else,
    and is therefore caught. A check written as `if raw is None or raw == {}`
    passes AC4's two and fails all six of these.

    These six matter because they are what a profile looks like when an
    operator has deliberately emptied it: the file still exists, carries the
    comment explaining why, and the workspace runs on the built-in defaults.
    Refusing to dispatch on a file whose author meant "nothing special here"
    would be a new outage with this ticket's name on it, caused by the guard
    that was supposed to prevent one.
    """
    path = tmp_path / f"{shape}.yaml"
    path.write_bytes(LEGAL_BUT_LOOKS_BROKEN[shape])

    profile = load_profile_from_path(path)

    assert type(profile) is OrchestrationProfile, (
        f"{shape} is a legal profile of pure defaults, not an unreadable file"
    )
    assert profile.slug == path.stem, (
        f"the slug was not stamped from the file name: {profile.slug!r}"
    )


#: Non-mapping documents beyond the two the criteria name, with the word each
#: has to use for itself. The sequence and the scalar are *classes* of document,
#: and the implementation picks between them by duck-typing (C6 refuses
#: ``isinstance``) — so a ``date``, an ``int`` and a ``bool`` have to land on the
#: same side as a ``str``, and a flow sequence on the same side as a block one.
NON_MAPPING_SHAPES = {
    "int-scalar": (b"42\n", "scalar"),
    "bool-scalar": (b"true\n", "scalar"),
    # A YAML timestamp loads as a `datetime.date` — not a str, not a number,
    # and the shape a profile reduced to a single stray line can take.
    "date-scalar": (b"2026-10-17\n", "scalar"),
    # A flow sequence rather than a block one: the same class of document, but
    # it reaches `"slug" not in raw` by a different route and raises its
    # TypeError from a different frame today.
    "flow-sequence": (b"[alpha, beta]\n", "sequence"),
}


@pytest.mark.parametrize("shape", sorted(NON_MAPPING_SHAPES), ids=sorted(NON_MAPPING_SHAPES))
def test_the_mapping_check_is_duck_typed_over_every_kind_of_document(tmp_path: Path, shape: str):
    """C6, which the two criteria shapes do not exercise.

    AC3's non-mapping shapes are a block sequence and a string, which a check
    written `isinstance(raw, list)` / `isinstance(raw, str)` classifies
    correctly — and that check is refused by the `py-organization` gate and
    wrong besides. A `date`, an `int` and a `bool` are all scalars and none of
    them is a `str`; a `CommentedSeq` from flow syntax is a sequence and is not
    reached through the same frame as a block one.

    Also pins that the word is right. "The document is not a mapping" is a
    riddle to an operator looking at a file they can see has lines in it;
    "the document is a sequence" tells them they are one `-` away from the
    answer. The implementation only has the two words to choose between, so
    choosing by `hasattr(raw, "append")` has to hold for everything, not just
    for the two examples.
    """
    content, kind = NON_MAPPING_SHAPES[shape]
    path = tmp_path / "default.yaml"
    path.write_bytes(content)

    with pytest.raises(ProfileUnreadableError) as excinfo:
        load_profile_from_path(path)

    assert str(excinfo.value).endswith(f"the document is a {kind}, not a mapping"), (
        f"a {shape} is a {kind} and has to say so: {excinfo.value}"
    )
    assert excinfo.value.__cause__ is None, (
        f"the mapping check caught something instead of preventing it (C4): "
        f"{excinfo.value.__cause__!r}"
    )


def test_a_repaired_profile_is_read_again_rather_than_refused_from_the_cache(tmp_path: Path):
    """A3, in the direction that matters to the operator who fixes the file.

    The test above pins that two reads of one broken file both raise, which is
    `lru_cache` not memoising exceptions. The consequence nobody asserts is the
    other half of the same fact: the operator opens the file the error named,
    fixes it, and the very next dispatch has to get the repaired profile — not
    a refusal replayed from a cache, and not the profile from before the break.

    Driven as the whole sequence (broken, repaired, broken again) rather than
    as three tests, because the cache is the state under test and it is the
    *transitions* that can be wrong. A cache keyed on the path alone would pass
    a repaired-file test run on its own and fail here on the third write.
    """
    path = tmp_path / "default.yaml"

    path.write_bytes(b"slug: default\nname: [unclosed\n")
    with pytest.raises(ProfileUnreadableError):
        load_profile_from_path(path)

    path.write_bytes(b"slug: default\nname: Repaired\n")
    assert load_profile_from_path(path).name == "Repaired", (
        "the file was fixed and the loader replayed the failure from before it: the "
        "operator has no way to tell their fix worked, and the dispatch stays degraded"
    )

    path.write_bytes(b"slug: default\nname: [broken again, and a different length\n")
    with pytest.raises(ProfileUnreadableError) as excinfo:
        load_profile_from_path(path)
    assert str(path.resolve()) in str(excinfo.value), (
        "a second break after a repair was answered out of the cache: a profile that "
        "parsed once would then keep dispatching on stale gates forever"
    )


def test_the_error_is_constructible_by_the_callers_that_name_the_file(tmp_path: Path):
    """R2's constructor as a contract, not as an implementation detail.

    `resolve_stage_retry_budget`'s WARNING (R5) reads `exc.path` rather than
    parsing the message, and the three Gate Studio GETs render `str(exc)`. Both
    are promises about this class, and both are only ever exercised in this
    suite through a file on disk — so an implementation that computed `path`
    somewhere in `load_profile_from_path` and handed the class a formatted
    string would pass every other test here and break the moment a second
    raise site is added. R3 already has two raise sites.
    """
    exc = ProfileUnreadableError(tmp_path / "sub" / ".." / "default.yaml", "because of a reason")

    assert exc.path == (tmp_path / "default.yaml").resolve(), (
        f"the class does not resolve the path its callers hand it: {exc.path}"
    )
    assert str(exc) == (
        f"{exc.path} could not be read as an orchestration profile: because of a reason"
    ), f"the class does not render R2's sentence from its own two arguments: {str(exc)!r}"


@pytest.mark.parametrize(
    ("label", "raised"),
    [
        # The exact mistake C4 refuses, as behaviour rather than as an AST
        # shape: a TypeError from inside a validator is a bug in our code.
        ("a-programming-error", TypeError("NoneType is not subscriptable")),
        # And the one no tuple should ever swallow: an operator pressing
        # Ctrl-C, or the orchestrator cancelling a stage, during a parse.
        ("an-interrupt", KeyboardInterrupt()),
    ],
    ids=["a-programming-error", "an-interrupt"],
)
def test_a_failure_that_is_not_the_file_is_not_relabelled_as_the_file(
    tmp_path: Path, monkeypatch, label: str, raised: BaseException
):
    """AC4's second half and C4, measured instead of read off the AST.

    The only thing pinning "TypeError is in no wrap tuple" today is an AST walk
    over two functions. That holds for the names written in this module and
    says nothing about the behaviour: a wrap built from a variable, a tuple
    assembled at import, an `except` in a helper this seam calls, or a second
    `try` added inside `_parse_profile` all defeat it while being exactly the
    mistake it was written to catch.

    So this raises from inside the parse and asserts what comes out. A
    `TypeError` from our own validator has to stay a `TypeError` — relabelled,
    it reaches the operator as "go and fix your profile" for a file that is
    fine, and `describe_handoff_failure` reports it as a fallback branch, which
    is precisely what the guard one layer up refuses. A `KeyboardInterrupt` is
    a `BaseException` and would only be caught by a bare `except:`.
    """
    path = tmp_path / "default.yaml"
    path.write_bytes(b"slug: default\nname: Default\n")

    def _explode(_path: Path) -> dict:
        raise raised

    monkeypatch.setattr(orchestration_profile_module, "_load_yaml", _explode)

    with pytest.raises(type(raised)) as excinfo:
        load_profile_from_path(path)

    assert type(excinfo.value) is type(raised), (
        f"{label} came out of the load seam as {type(excinfo.value).__name__}: this seam "
        "classifies files that cannot be read, and this was not one"
    )
