"""`loregarden_research_repo` and `loregarden_research_record`: what a research thread works with.

`research_repo` reads another project's source in the reference cache's clone
and always reports the commit it read. `research_record` puts a research run on
the record: the question, its candidates, a claim per capability, and a
person's corrections. The rules (a verified claim names a real file at that
commit; a person's refutation stands) are enforced in
`services.reference_survey.research`, not here.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import TypeAdapter
from sqlmodel import Session

from loregarden.mcp.tool_ids import McpTool
from loregarden.mcp.tool_schemas import (
    enum_string_prop,
    integer_prop,
    string_list_prop,
    string_prop,
    tool_schema,
)
from loregarden.models.domain.survey_enums import (
    ClaimStatus,
    ResearchRecordAction,
    ResearchRepoAction,
)
from loregarden.services.reference_survey import research, research_repo
from loregarden.services.reference_survey.errors import SurveyError

_CANDIDATES = TypeAdapter(list[research.CandidateIn])

_CLAIM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "capability": string_prop("One of the question's capabilities, exactly as opened."),
        "status": enum_string_prop(
            "verified: lines in a file at the commit you read do it (needs evidence_path, "
            "evidence_commit, evidence_start and evidence_end). docs_only: only the docs say so. not_found: you looked in "
            "the source and it is not there. refuted: the source shows it does not; cite those "
            "lines the same way and they are checked like a verified claim's.",
            [status.value for status in ClaimStatus],
        ),
        "evidence_path": string_prop("Path inside the repository, for verified."),
        "evidence_commit": string_prop("The commit loregarden_research_repo reported."),
        "evidence_start": integer_prop("verified: first cited line, as read numbered it."),
        "evidence_end": integer_prop("verified: last cited line; at most 60 lines in all."),
        "evidence_url": string_prop(
            "docs_only: the doc page. For verified the server links the cited lines itself."
        ),
        "note": string_prop("What the evidence shows, in a sentence."),
    },
    "required": ["capability", "status"],
}

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
        "inputSchema": tool_schema(
            properties={
                "action": enum_string_prop(
                    "What to record.", [a.value for a in ResearchRecordAction]
                ),
                "thread_id": string_prop("question: this thread's chat_session_id."),
                "question": string_prop("question: the capability question, in a sentence."),
                "capabilities": string_list_prop(
                    "question: what a candidate must do, one per item (at most 8)."
                ),
                "question_id": string_prop("candidates/refute: the question it answers."),
                "candidates": {
                    "type": "array",
                    "description": "candidates: the projects put forward.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "url": string_prop("Project URL."),
                            "name": string_prop("Its name."),
                            "summary": string_prop("Why it is a candidate, in a sentence."),
                            "claims": {"type": "array", "items": _CLAIM_SCHEMA},
                        },
                        "required": ["url", "name"],
                    },
                },
                "url": string_prop("refute: the project URL."),
                "capability": string_prop("refute: what it does not do."),
                "note": string_prop("refute: the operator's words, or why."),
            },
            required=["action"],
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


def _record(session: Session, args: dict[str, Any]) -> str:
    action = ResearchRecordAction(args["action"])
    if action is ResearchRecordAction.QUESTION:
        question = research.open_question(
            session,
            research.QuestionIn(
                thread_id=args.get("thread_id") or "",
                question=args.get("question") or "",
                capabilities=list(args.get("capabilities") or []),
            ),
        )
        payload: dict[str, Any] = {
            "question_id": question.id,
            "capabilities": research.capabilities(question),
        }
    elif action is ResearchRecordAction.CANDIDATES:
        result = research.record_candidates(
            session,
            args.get("question_id") or "",
            _CANDIDATES.validate_python(args.get("candidates") or []),
        )
        payload = result.model_dump(mode="json")
    else:
        claim = research.refute(
            session,
            research.RefuteIn(
                url=args.get("url") or "",
                capability=args.get("capability") or "",
                note=args.get("note") or "",
                question_id=args.get("question_id") or None,
            ),
        )
        payload = {"claim_id": claim.id, "url": claim.url, "status": claim.status.value}
    return json.dumps(payload, indent=2)


#: Registered in `tool_registry.EXTENDED_TOOLS`, not `execute_tool`'s chain.
HANDLERS = {
    McpTool.RESEARCH_REPO.value: _repo,
    McpTool.RESEARCH_RECORD.value: _record,
}
