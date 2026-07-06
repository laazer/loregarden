import json

from loregarden.services.artifact_service import capture_git_diff, parse_test_output


def test_parse_pytest_output():
    text = """
============================= test session starts ==============================
tests/test_foo.py::test_ok PASSED
tests/test_foo.py::test_bad FAILED - AssertionError: boom
========================= 1 passed, 1 failed in 0.42s =========================
"""
    result = parse_test_output(text, cmd="uv run pytest -q")
    assert result is not None
    assert "1 passed" in result["summary"]
    assert "1 failed" in result["summary"]
    assert len(result["rows"]) == 2
    assert result["rows"][0]["status"] == "pass"
    assert result["rows"][1]["status"] == "fail"
    assert "boom" in result["rows"][1]["msg"]


def test_parse_test_output_empty():
    assert parse_test_output("") is None


def test_capture_git_diff_from_repo():
    from loregarden.models.domain import Workspace

    ws = Workspace(slug="loregarden", name="Loregarden", repo_path=".")
    diff = capture_git_diff(ws)
    assert diff is None or "sections" in diff


def test_parse_unified_diff_lines():
    from loregarden.services.artifact_service import _parse_unified_diff

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
    sections = _parse_unified_diff(patch)
    assert len(sections) == 2
    assert sections[0]["path"] == "foo.py"
    assert sections[1]["path"] == "bar.ts"
    assert sections[0]["add"] == 1
    assert sections[0]["del"] == 1
    assert sections[1]["add"] == 1
    types = [line["type"] for line in sections[0]["lines"]]
    assert "h" in types
    assert "a" in types
    assert "d" in types


def test_extract_pytest_from_stream_json():
    from loregarden.models.domain import AgentRun
    from loregarden.services.artifact_service import (
        extract_pytest_sections_from_stream_json,
        extract_test_source_from_run,
    )

    assistant = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_bash1",
                        "name": "Bash",
                        "input": {"command": "python -m pytest -x 2>&1 | tail -50"},
                    }
                ]
            },
        }
    )
    result = json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_bash1",
                        "content": (
                            "tests/test_foo.py::test_ok PASSED\n"
                            "======================= 122 passed, 1 warning in 21.71s ========================"
                        ),
                    }
                ]
            },
        }
    )
    sections = extract_pytest_sections_from_stream_json(f"{assistant}\n{result}")
    assert len(sections) == 1
    assert "pytest" in sections[0][0]
    run = AgentRun(
        run_code="run_x",
        ticket_id="t1",
        workspace_id="w1",
        agent_id="static_qa",
        stage_key="testing",
        stdout=f"{assistant}\n{result}",
    )
    text, cmd = extract_test_source_from_run(run)
    parsed = __import__(
        "loregarden.services.artifact_service", fromlist=["parse_test_output"]
    ).parse_test_output(text, cmd=cmd)
    assert parsed is not None
    assert parsed["summary"] == "122 passed · 0 failed · 0 skipped"
    assert parsed["cmd"] == "python -m pytest -x 2>&1 | tail -50"
    assert parsed["rows"][0]["name"] == "tests/test_foo.py::test_ok"


def test_rejects_json_garbage_rows():
    garbage = '{"type":"user","message":{"content":"npm FAILED"}}\n'
    assert parse_test_output(garbage, cmd="claude --output-format stream-json") is None
