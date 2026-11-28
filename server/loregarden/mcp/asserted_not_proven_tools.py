"""What the two tools of the asserted-not-proven debt class tell an agent.

`loregarden_record_asserted_not_proven` writes one — record, amend, measure or
close, selected by `operation` — and `loregarden_open_claims` reads the class.
The read tool's response is the ONLY operator-readable surface this class has;
there is no page, and a later stage proposing one goes through ui-design first.

Both are operations (`services/operations/asserted_claims.py`), so neither
declares a schema here: it is generated from the operation's args model, which
also validates the call. A schema written beside the model would be overwritten
unread and drift unseen, and `tools.py` refuses one.
"""

from __future__ import annotations

from typing import Any

from loregarden.mcp.tool_ids import McpTool
from loregarden.services.asserted_not_proven.listing import QUESTION
from loregarden.services.asserted_not_proven.schemas import CODE_SITE_FORM, TEST_REF_FORM

ASSERTED_NOT_PROVEN_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": McpTool.RECORD_ASSERTED_NOT_PROVEN,
        "description": (
            "Record a claim this ticket believes WITHOUT PROOF, or amend, measure or close "
            "one. Recording is the normal path, not a failure to file: a finding or learning "
            "observed once and not proven belongs here rather than being filed as true or "
            "not filed at all. Every claim names the artifact it is OWED as a resolvable "
            f"pointer ({TEST_REF_FORM} for a behaviour, a drafted default or a document "
            f"defect; {CODE_SITE_FORM} for a code site), the fallback if it turns out false "
            "(named, and explicitly NOT BUILT), its upstream identifier and its citation. "
            "Writes are idempotent on (ticket, claim): an identical payload writes nothing. "
            "Amending or closing a claim whose dependent_documents are unanswered is "
            "REFUSED — updating a dependent document is an implementation obligation "
            "tracked as work, not a convention. Read the class with loregarden_open_claims."
        ),
    },
    {
        "name": McpTool.OPEN_CLAIMS,
        "description": (
            f"Answer one question: {QUESTION} Returns the members of the "
            "asserted-not-proven debt class, grouped by kind with a per-group count, "
            "closable first and then the ones whose ticket landed while their proof never "
            "appeared. Merges claims recorded on tickets with `ceiling:` markers scanned out "
            "of the tree, and says how many files it read. An empty class is not a failure."
        ),
    },
]
