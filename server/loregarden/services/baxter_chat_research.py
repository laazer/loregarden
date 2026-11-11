"""Research threads: a Home Baxter thread that searches, reads source, and records.

A research thread answers capability questions about other projects ("which
repos generate a skeleton for an unrigged mesh?") and puts what it finds in the
reference library. Three things make it differ from a general thread, and all
three are decided here so the snapshot, the prompt and the turn agree:

- **Tools.** It is offered the read tools, web search and fetch, and the
  research MCP tools, and nothing that writes a file or runs a shell. On a
  general Home turn Write, Edit and safe git are auto-approved; a research turn
  is never offered them at all (`tool_grants.effective_allowlist`).
- **No publish.** Its turns run like any acting turn (its own worktree, so it
  never holds the shared checkout), but report ``acting=False``, so no commit or
  push follows.
- **Claude only.** Only Claude enforces a tool allowlist; on any other adapter
  the grant would silently not apply, so research refuses to run there.
"""

from __future__ import annotations

from loregarden.agents.cli_tool_ids import READ_ONLY_CLI_TOOLS
from loregarden.mcp.tool_ids import RESEARCH_MCP_TOOLS, mcp_tool_values
from loregarden.models.domain import BaxterChatSession
from loregarden.models.domain.chat_enums import ChatThreadMode
from loregarden.models.domain.enums import (
    ChatAdvisoryCause,
    CliAdapter,
    CliTool,
    ToolPosture,
)
from loregarden.models.domain.schemas import StudioAgentToolGrants
from loregarden.services.chat_mode import ChatModeView, advisory
from loregarden.services.reference_survey.research import (
    capabilities,
    person_refutations,
    thread_questions,
)
from sqlmodel import Session

#: Offered to a research turn: read, search, fetch, and ask the operator.
RESEARCH_CLI_TOOLS: frozenset[CliTool] = READ_ONLY_CLI_TOOLS | frozenset(
    {CliTool.ASK_USER_QUESTION}
)
#: Named as well as left out, so a later widening of the base set cannot reach them.
_NEVER: tuple[CliTool, ...] = (
    CliTool.WRITE,
    CliTool.EDIT,
    CliTool.BASH,
    CliTool.NOTEBOOK_EDIT,
    CliTool.TASK,
)


def is_research(chat_session: BaxterChatSession | None) -> bool:
    return chat_session is not None and chat_session.mode is ChatThreadMode.RESEARCH


def research_agent(agent: dict) -> dict:
    """Baxter, narrowed to research: read-only CLI tools and the research MCP tools."""
    grants = StudioAgentToolGrants(
        posture=ToolPosture.ALLOWLIST,
        allowed_tools=sorted(RESEARCH_CLI_TOOLS),
        disallowed_tools=list(_NEVER),
        mcp_servers=[],
    )
    return {
        **agent,
        "tool_grants": grants,
        "mcp_tools": mcp_tool_values(RESEARCH_MCP_TOOLS),
        "mcp_enabled": True,
    }


def research_refusal(adapter: str) -> str:
    """Why this adapter cannot run a research turn, or "" when it can."""
    if adapter == CliAdapter.CLAUDE:
        return ""
    return (
        f"Research threads run on Claude Code only: the {adapter} adapter cannot be limited "
        "to read-only tools, so a research turn there could edit files. Switch this "
        "thread's runtime to Claude Code."
    )


def research_mode(adapter: str) -> ChatModeView:
    """The mode a research thread's next turn runs in, for the snapshot's pill."""
    if refusal := research_refusal(adapter):
        return advisory(ChatAdvisoryCause.ADAPTER_CANNOT_RESEARCH, refusal)
    return advisory(
        ChatAdvisoryCause.RESEARCH_THREAD,
        "A research thread searches the web and reads other projects' source, and records "
        "what it finds in the reference library. It does not edit code.",
    )


def research_turn(
    session: Session, chat_session: BaxterChatSession | None, agent: dict, adapter: str
) -> tuple[dict, list[str], bool]:
    """The agent, extra prompt lines and research flag for a turn in this thread.

    A general thread gets the agent back unchanged and no lines. A research
    thread on an adapter that cannot be held read-only is refused (ValueError).
    """
    if chat_session is None or not is_research(chat_session):
        return agent, [], False
    if refusal := research_refusal(adapter):
        raise ValueError(refusal)
    return research_agent(agent), research_prompt_lines(session, chat_session), True


def research_prompt_lines(session: Session, chat_session: BaxterChatSession) -> list[str]:
    """The research posture, this thread's questions, and the refutations people made."""
    lines = [
        "",
        "## Research thread",
        "This is a research thread. Find projects that do what the operator asks, check "
        "each capability in the project's source, and put what you find on the record. "
        "You cannot edit files or run a shell here, and you do not need to.",
        f"This thread's chat_session_id is {chat_session.id}.",
        "Method:",
        "1. For a new question, call loregarden_research_record action=question with the "
        "question and the capabilities a candidate must have (one per item, in the "
        "operator's words). Reuse an open question below when the operator is refining it.",
        "2. Search the web for candidates. For each repository, read its source with "
        "loregarden_research_repo (tree, grep, read): find the code that does each "
        "capability, or establish that it is not there.",
        "3. Record every candidate with loregarden_research_record action=candidates: one "
        "claim per capability. verified needs evidence_path, the evidence_commit "
        "loregarden_research_repo reported, and evidence_start/evidence_end: the lines "
        "(at most 60) that do it; refuted cites the lines that show it does not, the "
        "same way; a README or doc page is docs_only; looked "
        "for and absent is not_found. Never call a capability verified from docs.",
        "4. Read `refused` and `warnings` in the reply. Fix a refused verified claim by "
        "reading the file and citing its commit, or downgrade it; never drop it silently.",
        "5. Answer with one card, then at most a few sentences on what stands out:",
        '   ```loregarden\\n{"primitive":"research_candidates","question_id":"<id>"}\\n```',
        "   The card shows the recorded table; do not repeat the table in prose.",
        '   When the operator asks about one project and one capability ("can X do Y?"), '
        "answer with a claim card instead, after recording it:",
        '   ```loregarden\\n{"primitive":"research_claim","url":"<url>","capability":"<capability>"}\\n```',
        'When the operator corrects you ("X can\'t do that"), record it with '
        "loregarden_research_record action=refute, quoting them in `note`.",
        "When asked to re-run a question, record against its question_id and answer with a "
        "diff card, which shows what the run added, dropped and changed:",
        '   ```loregarden\\n{"primitive":"research_diff","question_id":"<id>"}\\n```',
        "When the operator asks what is known about one project, answer with a project card:",
        '   ```loregarden\\n{"primitive":"research_project","url":"<url>"}\\n```',
        "To queue a pick for review, call loregarden_survey_queue action=enqueue with "
        "research_question_id=<the question>; it goes to the operator's approval like any "
        "enqueue, and its survey pass is briefed with the question and your claims. The "
        "operator can also queue rows from the card directly.",
    ]
    questions = thread_questions(session, chat_session.id)
    if questions:
        lines.append("Open questions in this thread:")
        for question in questions:
            caps = "; ".join(capabilities(question))
            lines.append(
                f"- {question.id}: {question.question} (capabilities: {caps}; "
                f"runs: {question.run_count})"
            )
    refutations = person_refutations(session)
    if refutations:
        lines.append(
            "People have refuted these claims. Do not recommend a project for a capability "
            "it was refuted for unless you cite new source evidence, and say so:"
        )
        lines.extend(
            f'- {claim.url}: not "{claim.capability}" ({claim.note})' for claim in refutations
        )
    return lines
