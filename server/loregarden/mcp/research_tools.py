"""`loregarden_research_repo` and `loregarden_research_record`: what a research thread works with.

`research_repo` reads another project's source in the reference cache's clone
and always reports the commit it read. `research_record` puts a research run on
the record: the question, its candidates, a claim per capability, and a
person's corrections; it is an operation shared with the research page's
refute route (`services/operations/research_record.py`), and only its schema is
here. The rules (a verified claim names a real file at that
commit; a person's refutation stands) are enforced in
`services.reference_survey.research`, not here.
"""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from loregarden.mcp.tool_ids import McpTool
from loregarden.mcp.tool_schemas import (
    enum_string_prop,
    integer_prop,
    string_prop,
    tool_schema,
)
from loregarden.models.domain.survey_enums import (
    ResearchRepoAction,
)
from loregarden.services.reference_survey import research_repo
from loregarden.services.reference_survey.errors import SurveyError

RESEARCH_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": McpTool.RESEARCH_REPO,
        "description": (
            "Read a repository's source for research, in loregarden's clone of it. Every "
            "reply names the commit it read: cite that commit and the path when you "
            "record a verified claim. head refreshes the clone to the latest commit; "
            "tree lists files (optional glob), grep searches (extended regex, "
            "case-insensitive, optional path glob), read returns numbered lines. A read "
            "that cannot happen returns `refused` with the reason."
        ),
        "inputSchema": tool_schema(
            properties={
                "action": enum_string_prop("What to read.", [a.value for a in ResearchRepoAction]),
                "url": string_prop("The repository URL."),
                "pattern": string_prop("tree: a glob such as '*.py'. grep: the regex."),
                "path": string_prop("read: the file. grep: an optional path glob."),
                "start": integer_prop("read: first line (default 1)."),
                "end": integer_prop("read: last line (default: up to 400 lines)."),
            },
            required=["action", "url"],
        ),
    },
    {
        "name": McpTool.RESEARCH_RECORD,
        "description": (
            "Put a research thread's work on the record. question: open a question in "
            "this thread with the capabilities a candidate needs; returns question_id. "
            "candidates: record candidates for a question, each with a claim per "
            "capability; a verified claim without a real file at the commit you read is "
            "refused and named in `refused`. refute: record the operator's correction "
            "that a project does not do something; it stands over later agent claims."
        ),
    },
]


def _read_repo(action: ResearchRepoAction, url: str, args: dict[str, Any]) -> dict:
    if action is ResearchRepoAction.HEAD:
        return research_repo.head(url)
    if action is ResearchRepoAction.TREE:
        return research_repo.tree(url, args.get("pattern") or "")
    if action is ResearchRepoAction.GREP:
        return research_repo.grep(url, args.get("pattern") or "", args.get("path") or "")
    return research_repo.read(
        url, args.get("path") or "", int(args.get("start") or 1), int(args.get("end") or 0)
    )


def _repo(session: Session, args: dict[str, Any]) -> str:
    """A read of the clone, or ``refused`` with the reason, as `fetch_reference` reports a
    blocked fetch: a repo that cannot be read is an answer the agent acts on, not a fault."""
    del session  # reads the clone, not the database
    url = args["url"]
    try:
        payload = _read_repo(ResearchRepoAction(args["action"]), url, args)
    except SurveyError as exc:
        payload = {"url": url, "refused": str(exc)}
    return json.dumps(payload, indent=2)


#: Registered in `tool_registry.EXTENDED_TOOLS`, not `execute_tool`'s chain.
HANDLERS = {
    McpTool.RESEARCH_REPO.value: _repo,
}
