"""Best-effort mapping of pasted references onto project areas.

A pasted article rarely says "this is for loremaker's operations": SigNoz is
described as a Datadog alternative, and it is the reader who knows loremaker
needs tracing and loregarden's capacity ledger needs metrics. So one agent turn
reads every entry's name and description against every live area's name,
description and keywords, and proposes the areas each one serves, with a reason,
including areas the text never names. A keyword match runs first and is in the
prompt as a draft to improve on; it is also the answer when the operator asks
for keywords only.

The agent only proposes. Its reply is validated: an area it invented or a URL
not pasted is dropped and reported as a warning, never silently. Nothing is
written; the paste form pre-ticks the suggestions and the operator queues them.

It runs under the `triage` agent's definition (a small, fast model) with no
tools: everything it needs is in the prompt.
"""

from __future__ import annotations

import re
from enum import StrEnum

from loregarden.models.domain import Workspace
from loregarden.services.cli_agent_runner import (
    CliAgentProfile,
    run_cli_agent_turn,
    stub_response,
)
from loregarden.services.reference_survey.areas import AreaView, list_areas
from loregarden.services.reference_survey.schemas import MAX_BULK_URLS
from loregarden.services.ticket_studio_service import extract_json_block
from loregarden.services.triage_service import TRIAGE_AGENT_ID
from loregarden.services.workspace_paths import resolve_workspace_root
from pydantic import BaseModel, Field, ValidationError
from sqlmodel import Session, col, select

AREA_MAPPER_PROFILE = CliAgentProfile(
    agent_id=TRIAGE_AGENT_ID,
    assistant_label="Area mapper",
    cli_label="Area mapper",
    stub_env="LOREGARDEN_AREA_MAPPER_STUB_RESPONSE",
    timeout_env="LOREGARDEN_AREA_MAPPER_TIMEOUT",
    tmp_prefix="loregarden-area-mapper-",
    reply_cap=40_000,
)

#: What the description of one entry contributes to the prompt, at most.
_WHY_CHARS = 600


class SuggestMode(StrEnum):
    #: Keyword draft, then one agent turn over it.
    AGENT = "agent"
    #: Keyword draft only: instant and free, misses what the text does not say.
    KEYWORDS = "keywords"


class SuggestEntryIn(BaseModel):
    url: str = Field(min_length=1)
    name: str = ""
    why: str = ""


class SuggestIn(BaseModel):
    entries: list[SuggestEntryIn] = Field(min_length=1, max_length=MAX_BULK_URLS)
    mode: SuggestMode = SuggestMode.AGENT


class AreaPick(BaseModel):
    #: `<workspace>/<slug>`.
    area: str
    reason: str = ""


class EntrySuggestion(BaseModel):
    url: str
    areas: list[AreaPick]


class SuggestResult(BaseModel):
    mode: SuggestMode
    suggestions: list[EntrySuggestion]
    #: What the agent proposed that could not be used, in words.
    warnings: list[str]


class AreaMapperReplyError(RuntimeError):
    """The agent answered, but not with suggestions the form can use."""


class AreaMapperRunError(RuntimeError):
    """The agent turn failed: the CLI exited, timed out, or could not sign in."""


class _Reply(BaseModel):
    entries: list[EntrySuggestion]


def _terms(area: AreaView) -> list[str]:
    return [
        t.lower() for t in [area.slug.replace("-", " "), area.name, *area.keywords] if t.strip()
    ]


def keyword_draft(entries: list[SuggestEntryIn], areas: list[AreaView]) -> list[EntrySuggestion]:
    """Areas whose name, slug or keywords appear as whole words in an entry's text."""
    compiled = {
        a.label: [(t, re.compile(rf"(?<![\w-]){re.escape(t)}(?![\w-])")) for t in _terms(a)]
        for a in areas
    }
    draft = []
    for entry in entries:
        text = f"{entry.name}\n{entry.why}".lower()
        picks = []
        for area in areas:
            hits = [term for term, pattern in compiled[area.label] if pattern.search(text)]
            if hits:
                picks.append(AreaPick(area=area.label, reason="mentions " + ", ".join(hits[:3])))
        draft.append(EntrySuggestion(url=entry.url, areas=picks))
    return draft


def build_prompt(
    entries: list[SuggestEntryIn], areas: list[AreaView], draft: list[EntrySuggestion]
) -> str:
    area_lines = [
        f"- {a.label} — {a.name}: {a.description}"
        + (f" (keywords: {', '.join(a.keywords)})" if a.keywords else "")
        for a in areas
    ]
    entry_lines = []
    drafted = {d.url: d for d in draft}
    for entry in entries:
        why = " ".join(entry.why.split())[:_WHY_CHARS]
        hint = ", ".join(p.area for p in drafted[entry.url].areas) or "nothing"
        entry_lines += [f"- url: {entry.url}", f"  name: {entry.name or '(none)'}",
                        f"  description: {why or '(none)'}", f"  keyword draft: {hint}"]  # fmt: skip
    return "\n".join(
        [
            "You map reference projects onto the parts of a team's own products they could improve.",
            "",
            "## Rules",
            "- For each entry, pick every area below that the project could realistically inform:",
            "  a pattern to adopt, a tool to use, or a component to build in that area. Include areas",
            "  the description never names when the fit is real (an observability platform serves",
            "  any area about operations or monitoring; a CI engine serves areas about builds).",
            "- Shared, product-neutral building blocks (UI kits, harnesses, gates) belong to the",
            "  shared-library areas when they exist, in addition to the product areas that use them.",
            "- Leave an entry with no areas rather than forcing a weak fit. Usually 1-4 areas.",
            "- Each reason is one short sentence naming what in that area it would help.",
            "- Use only area labels and URLs listed below, exactly as written.",
            "",
            "## Areas",
            *area_lines,
            "",
            "## Entries",
            *entry_lines,
            "",
            "## Reply",
            "Reply with one JSON object in a ```json fence and nothing else:",
            '{"entries": [{"url": "...", "areas": [{"area": "<workspace>/<slug>", "reason": "..."}]}]}',
        ]
    )


def validate_reply(
    entries: list[SuggestEntryIn], areas: list[AreaView], reply: str
) -> tuple[list[EntrySuggestion], list[str]]:
    payload = extract_json_block(reply)
    if payload is None:
        raise AreaMapperReplyError("The agent did not reply with a JSON object of suggestions.")
    try:
        parsed = _Reply.model_validate(payload)
    except ValidationError as exc:
        raise AreaMapperReplyError(
            f"The agent's suggestions did not match the format: {exc}"
        ) from exc
    labels = {a.label for a in areas}
    urls = [e.url for e in entries]
    by_url: dict[str, list[AreaPick]] = {}
    warnings: list[str] = []
    for proposed in parsed.entries:
        if proposed.url not in urls:
            warnings.append(f"Left out suggestions for {proposed.url}: it was not pasted.")
            continue
        unknown = [p.area for p in proposed.areas if p.area not in labels]
        if unknown:
            warnings.append(
                f"{proposed.url}: left out {len(unknown)} area(s) that do not exist: "
                + ", ".join(unknown[:5])
            )
        picks = by_url.setdefault(proposed.url, [])
        for pick in proposed.areas:
            if pick.area in labels and pick.area not in {p.area for p in picks}:
                picks.append(AreaPick(area=pick.area, reason=pick.reason.strip()))
    missing = [u for u in urls if u not in by_url]
    if missing:
        warnings.append(
            f"The agent said nothing about {len(missing)} link(s); they have no areas ticked."
        )
    return [EntrySuggestion(url=u, areas=by_url.get(u, [])) for u in urls], warnings


def _runnable_workspace(session: Session) -> Workspace:
    """The CLI needs a directory to run in; any checked-out workspace will do."""
    for workspace in session.exec(
        select(Workspace)
        .where(col(Workspace.archived_at).is_(None))
        .order_by(col(Workspace.created_at))
    ).all():
        if resolve_workspace_root(workspace).is_dir():
            return workspace
    raise AreaMapperRunError("No workspace is checked out on this machine for the agent to run in.")


def suggest_areas(session: Session, body: SuggestIn) -> SuggestResult:
    """Propose areas per entry. Raises on any failure to answer; writes nothing."""
    entries = list(
        {e.url.strip(): e.model_copy(update={"url": e.url.strip()}) for e in body.entries}.values()
    )
    areas = list_areas(session)
    draft = keyword_draft(entries, areas)
    if body.mode is SuggestMode.KEYWORDS or not areas:
        warnings = [] if areas else ["No project areas are set up yet; add some first."]
        return SuggestResult(mode=SuggestMode.KEYWORDS, suggestions=draft, warnings=warnings)
    reply = stub_response(AREA_MAPPER_PROFILE)
    if reply is None:
        workspace = _runnable_workspace(session)
        try:
            reply = run_cli_agent_turn(
                AREA_MAPPER_PROFILE,
                workspace=workspace,
                prompt=build_prompt(entries, areas, draft),
                workspace_slug=workspace.slug,
                granted_tools=[],
                read_only=True,
            )
        except (RuntimeError, TimeoutError) as exc:
            runtime = workspace.cli_adapter or "its default"
            raise AreaMapperRunError(
                f"The agent ran under {workspace.slug}'s runtime ({runtime}) and failed: {exc}. "
                "Use keyword suggestions instead, or fix the triage agent's runtime and ask again."
            ) from exc
    suggestions, warnings = validate_reply(entries, areas, reply)
    return SuggestResult(mode=SuggestMode.AGENT, suggestions=suggestions, warnings=warnings)
