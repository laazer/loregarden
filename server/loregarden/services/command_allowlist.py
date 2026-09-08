"""Which shell commands a stage run may execute without asking a human.

`lg-workflow-integrity-107`: tool permissions are a rubber stamp and gates are
where judgement lives. Measured on the live inbox — 279 `cli_permission` requests
with ONE rejection (0.4%), against 98 `workflow_gate` requests with 13 (13.3%).
Bash alone is 202 of the 279. Every one of those is a person reading a pytest
invocation and clicking yes, and the attention it costs is attention the gates do
not get.

WHY THIS IS NOT A LIST OF VERBS. The obvious implementation — allowlist the
common leading commands — is unsafe here, and the live data says so plainly. The
second most common leading token in those 202 approvals is `cd` (39 of them),
because the shape is `cd <somewhere> && <the real command>`. Allowlisting `cd`
allowlists everything after it. The same data holds 26 heredocs, 22 file writes,
8 `curl` calls (one POSTing to the control plane's own MCP endpoint), 5 inline
`-c` programs and 2 shell function definitions.

So this reads the whole command and refuses anything it cannot fully account for.
A command is allowed only when EVERY part of it is recognised: an optional single
`cd` into the workspace, one allowlisted verb, an optional `2>&1`, and pipes only
into filters that cannot write. Anything else — including anything merely
unfamiliar — falls through to a human, which is the same answer the system gives
today.
"""

from __future__ import annotations

import re

#: Constructs that change what a command means, and cannot be reasoned about by
#: pattern. Each one appears in the live approval history, so none is theoretical.
_FORBIDDEN = (
    re.compile(r"\$\("),  # command substitution
    re.compile(r"`"),  # the older spelling of the same
    re.compile(r"<<"),  # heredoc: writes a file, whatever the leading verb says
    re.compile(r";"),  # statement separator
    re.compile(r"\|\|"),  # conditional chaining
    re.compile(r"(?<!&)&(?!&)"),  # backgrounding; `&&` is handled separately
    re.compile(r">{1,2}"),  # any redirect; the two harmless stderr forms are stripped first
    re.compile(r"^\s*\w+\s*\(\)\s*\{"),  # shell function definition
)

#: Verbs that are never allowed, even inside an otherwise-recognised command.
#: Listed separately from the verb allowlist because the point is that adding a
#: new safe verb later cannot accidentally re-admit one of these.
_NEVER = re.compile(
    r"\b(curl|wget|nc|ssh|scp|rm|rmdir|mv|sudo|chmod|chown|kill|pkill|"
    r"shutdown|reboot|dd|mkfs|pip|npm\s+(install|publish|link)|"
    r"git\s+(push|reset|clean|rebase|merge|checkout|commit|add))\b"
)

#: Inline-program flags. `python -c "..."` is arbitrary code wearing a safe verb.
_INLINE_CODE = re.compile(r"\b(python3?|node|sh|bash|zsh|perl|ruby)\s+(-c|-e)\b")

#: The verbs a stage run may execute unattended: run the tests, run the linters,
#: read the repo's own state. All of them report; none of them mutate.
_SAFE_VERBS = (
    re.compile(r"^python3? -m pytest\b"),
    re.compile(r"^python3? -m py_compile\b"),
    re.compile(r"^pytest\b"),
    re.compile(r"^npm (test|run [a-zA-Z0-9:_-]+)\b"),
    re.compile(r"^npx (tsc|jest|oxlint)\b"),
    re.compile(r"^(ruff|mypy|oxlint|tsc)\b"),
    re.compile(r"^git (status|diff|log|show|branch)\b"),
    re.compile(r"^(ls|cat|head|tail|wc|grep|rg|find)\b"),
)

#: Commands a pipe may feed into. They read their input and write only stdout.
_SAFE_FILTERS = re.compile(r"^(head|tail|wc|cat|sort|uniq|grep|rg|jq|sed -n)\b")

#: A `cd` prefix is allowed exactly once, into a path with no shell characters.
_CD_PREFIX = re.compile(r"^cd\s+([\w./~-]+)\s*&&\s*(.+)$", re.DOTALL)

_TRAILING_STDERR = re.compile(r"\s*2>&1\s*$")


#: The only redirects that write nowhere: merge stderr into stdout, or discard
#: it. Stripped before the redirect check, so `2>errors.txt` — which DOES write a
#: file, and which an exemption keyed on the leading `2` would have allowed —
#: still falls through to a human.
_HARMLESS_STDERR = re.compile(r"2>\s*(&1|/dev/null)")


def _has_forbidden_construct(command: str) -> bool:
    return any(pattern.search(_HARMLESS_STDERR.sub("", command)) for pattern in _FORBIDDEN)


def is_safe_command(command: str) -> bool:
    """Whether this command may run unattended.

    False for anything unrecognised. That asymmetry is the whole design: a new
    tool, a new flag or a shape nobody anticipated costs one approval click,
    while a wrong `True` runs it.
    """
    command = command.strip()
    if not command or "\n" in command:
        # Multi-line input is a script, not a command. Two of the live approvals
        # are exactly this, wrapped around a heredoc.
        return False
    if _has_forbidden_construct(command) or _NEVER.search(command):
        return False
    if _INLINE_CODE.search(command):
        return False

    body = command
    prefix = _CD_PREFIX.match(body)
    if prefix:
        body = prefix.group(2)
    if "&&" in body:
        # One `cd` prefix is a convenience; a chain is a second command, and the
        # second command is the one nobody reads.
        return False

    segments = [segment.strip() for segment in body.split("|")]
    head = _TRAILING_STDERR.sub("", segments[0]).strip()
    if not any(pattern.match(head) for pattern in _SAFE_VERBS):
        return False
    return all(_SAFE_FILTERS.match(_TRAILING_STDERR.sub("", tail).strip()) for tail in segments[1:])
