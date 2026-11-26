from pathlib import Path
from unittest import mock

from loregarden.services import unified_diff
from loregarden.services.unified_diff import parse_unified_diff, path_from_diff_git
from tests.worktree_helpers import git, make_repo


def _rows(section: dict) -> list[tuple[str, str]]:
    return [(line["type"], line["text"]) for line in section["lines"]]


def _diff_after(repo: Path, change) -> str:
    change(repo)
    git(repo, "add", "-A")
    return git(repo, "diff", "--cached").stdout


def _commit(repo: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        (repo / name).write_text(content)
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "files")


def test_two_files_each_get_their_own_rows_and_counts():
    patch = """diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1,2 +1,3 @@
 context
-old
+new
diff --git a/bar.ts b/bar.ts
--- a/bar.ts
+++ b/bar.ts
@@ -1 +1,2 @@
 keep
+added
"""
    sections = parse_unified_diff(patch)

    assert [(s["path"], s["add"], s["del"], s["truncated"]) for s in sections] == [
        ("foo.py", 1, 1, False),
        ("bar.ts", 1, 0, False),
    ]
    assert _rows(sections[0]) == [
        ("h", "@@ -1,2 +1,3 @@"),
        ("c", "context"),
        ("d", "old"),
        ("a", "new"),
    ]
    assert _rows(sections[1]) == [("h", "@@ -1 +1,2 @@"), ("c", "keep"), ("a", "added")]


def test_lines_that_look_like_file_headers_are_read_as_content(tmp_path):
    repo = make_repo(tmp_path)
    _commit(repo, {"q.sql": "-- note\nselect 1;\n"})

    patch = _diff_after(repo, lambda r: (r / "q.sql").write_text("select 1;\n++ b/fake\n"))
    sections = parse_unified_diff(patch)

    assert [(s["path"], s["add"], s["del"]) for s in sections] == [("q.sql", 1, 1)]
    assert _rows(sections[0])[1:] == [("d", "-- note"), ("c", "select 1;"), ("a", "++ b/fake")]


def test_a_deleted_file_with_a_space_in_its_name_has_only_its_deletions(tmp_path):
    repo = make_repo(tmp_path)
    _commit(repo, {"my file.txt": "a\nb\n"})

    patch = _diff_after(repo, lambda r: (r / "my file.txt").unlink())
    sections = parse_unified_diff(patch)

    assert [(s["path"], s["add"], s["del"]) for s in sections] == [("my file.txt", 0, 2)]
    assert _rows(sections[0]) == [("h", "@@ -1,2 +0,0 @@"), ("d", "a"), ("d", "b")]


def test_an_edited_file_with_a_space_in_its_name_takes_its_stat(tmp_path):
    repo = make_repo(tmp_path)
    _commit(repo, {"my file.txt": "a\n"})

    patch = _diff_after(repo, lambda r: (r / "my file.txt").write_text("b\n"))
    sections = parse_unified_diff(patch, stat_overrides={"my file.txt": (7, 9)})

    assert [(s["path"], s["add"], s["del"]) for s in sections] == [("my file.txt", 7, 9)]


def test_a_missing_newline_marker_is_not_a_row(tmp_path):
    repo = make_repo(tmp_path)
    _commit(repo, {"n.txt": "a"})

    patch = _diff_after(repo, lambda r: (r / "n.txt").write_text("b"))
    sections = parse_unified_diff(patch)

    assert _rows(sections[0]) == [("h", "@@ -1 +1 @@"), ("d", "a"), ("a", "b")]


@mock.patch.object(unified_diff, "MAX_DIFF_LINES", 3)
def test_rows_stop_at_the_limit_and_say_so_once_in_each_cut_file():
    patch = """diff --git a/a.txt b/a.txt
@@ -0,0 +1,3 @@
+1
+2
+3
diff --git a/b.txt b/b.txt
@@ -0,0 +1,2 @@
+4
+5
"""
    sections = parse_unified_diff(patch)

    assert [(s["path"], s["add"], s["truncated"]) for s in sections] == [
        ("a.txt", 2, True),
        ("b.txt", 0, True),
    ]
    assert _rows(sections[0]) == [
        ("h", "@@ -0,0 +1,3 @@"),
        ("a", "1"),
        ("a", "2"),
        ("c", "… diff truncated …"),
    ]
    assert _rows(sections[1]) == [("c", "… diff truncated …")]


def test_a_long_line_is_cut_to_the_line_limit():
    patch = "diff --git a/x b/x\n@@ -0,0 +1 @@\n+" + "y" * 600 + "\n"

    (section,) = parse_unified_diff(patch)

    assert _rows(section)[1] == ("a", "y" * unified_diff.MAX_DIFF_LINE_CHARS)


def test_lines_before_any_file_header_are_ignored():
    assert parse_unified_diff("@@ -1 +1 @@\n-a\n+b\n") == []


def test_a_file_with_no_hunk_is_left_out():
    patch = "diff --git a/bin.png b/bin.png\nBinary files a/bin.png and b/bin.png differ\n"

    assert parse_unified_diff(patch) == []


def test_the_path_of_a_diff_git_line():
    assert path_from_diff_git("diff --git a/x/y.py b/x/y.py") == "x/y.py"
    assert path_from_diff_git("diff --git a/a b c b/a b c") == "a b c"
    assert path_from_diff_git("diff --git a/old.py b/new.py") == "new.py"
    assert path_from_diff_git("diff --git a/only") == "only"
    assert path_from_diff_git("diff --git weird") == "weird"


def test_files_without_diff_git_lines_split_at_their_headers():
    patch = """--- a/x.txt
+++ b/x.txt
@@ -1,2 +1,2 @@
-a
+b
 same
--- a/y.txt
+++ b/y.txt
@@ -1 +1 @@
-c
+d
"""
    sections = parse_unified_diff(patch)

    assert [(s["path"], s["add"], s["del"]) for s in sections] == [("x.txt", 1, 1), ("y.txt", 1, 1)]
    assert _rows(sections[0])[1:] == [("d", "a"), ("a", "b"), ("c", "same")]


def test_a_deleted_one_line_file_keeps_its_line(tmp_path):
    repo = make_repo(tmp_path)
    _commit(repo, {"one.txt": "only\n"})

    patch = _diff_after(repo, lambda r: (r / "one.txt").unlink())
    sections = parse_unified_diff(patch)

    assert [(s["path"], _rows(s)) for s in sections] == [
        ("one.txt", [("h", "@@ -1 +0,0 @@"), ("d", "only")])
    ]


def test_a_renamed_and_edited_file_with_spaces_is_named_by_its_new_path(tmp_path):
    repo = make_repo(tmp_path)
    _commit(repo, {"a b.txt": "1\n2\n3\n4\n5\n"})

    def rename(r: Path) -> None:
        (r / "a b.txt").unlink()
        (r / "c d.txt").write_text("1\n2\n3\n4\nfive\n")

    patch = _diff_after(repo, rename)
    sections = parse_unified_diff(patch)

    assert [(s["path"], s["add"], s["del"]) for s in sections] == [("c d.txt", 1, 1)]


def test_an_empty_line_inside_a_hunk_is_blank_context():
    patch = "diff --git a/x b/x\n@@ -1,3 +1,3 @@\n a\n\n-b\n+c\n"

    (section,) = parse_unified_diff(patch)

    assert _rows(section)[1:] == [("c", "a"), ("c", ""), ("d", "b"), ("a", "c")]
