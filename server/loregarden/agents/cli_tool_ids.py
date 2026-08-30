"""Policy groupings over the CLI tool vocabulary.

The names themselves are ``loregarden.models.domain.enums.CliTool`` — API
schemas reference them, and models must not import the agents layer. What lives
here is the agent-layer judgement about those names: which set a rail starts
from, and which of them only read.
"""

from __future__ import annotations

from loregarden.models.domain.enums import CliTool

#: Tools that only read. Safe to offer a rail that must not change the repo.
READ_ONLY_CLI_TOOLS: frozenset[CliTool] = frozenset(
    {
        CliTool.READ,
        CliTool.GLOB,
        CliTool.GREP,
        CliTool.WEB_FETCH,
        CliTool.WEB_SEARCH,
    }
)

#: What a chat rail with real tool access starts from. An operator narrowing an
#: agent subtracts from this; they never add to it, so the derived allowlist
#: stays a superset of anything the permission bridge could approve.
CHAT_INTERACTIVE_CLI_TOOLS: frozenset[CliTool] = READ_ONLY_CLI_TOOLS | frozenset(
    {
        CliTool.WRITE,
        CliTool.EDIT,
        CliTool.BASH,
        CliTool.TASK,
        CliTool.TODO_WRITE,
        CliTool.ASK_USER_QUESTION,
        CliTool.NOTEBOOK_EDIT,
    }
)

#: An advisory turn answers from what it already has. It keeps the read tools so
#: it can ground an answer, and AskUserQuestion so it can decline by asking.
CHAT_ADVISORY_CLI_TOOLS: frozenset[CliTool] = READ_ONLY_CLI_TOOLS | frozenset(
    {CliTool.ASK_USER_QUESTION}
)
