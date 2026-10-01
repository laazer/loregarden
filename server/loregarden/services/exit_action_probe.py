"""What this control plane can actually observe about a run's runtime.

Kept apart from `services.exit_actions` on purpose: that module decides what a
set of statuses *means*, this one decides what the statuses *are*. The split is
what lets the resolver be tested against fixed snapshots while the probing side
stays free to learn about more of the environment.

The contract with the resolver is a fail-closed one. Anything this module
cannot observe it omits, and an omitted identifier resolves to unknown rather
than to available — so growing the catalog never quietly grants an agent a
capability nobody checked.
"""

from __future__ import annotations

import os

from loregarden.config import settings
from loregarden.models.domain import (
    AuthorityStatus,
    CliAdapter,
    RuntimeAvailability,
)
from loregarden.services.orchestration_profile import OrchestrationProfile

#: Adapters that execute a real CLI in a real checkout. `LOCAL` is the
#: in-process runner and `DEFAULT` is the inherit sentinel, so neither one is
#: evidence that a shell or a writable tree exists for this run.
_EXECUTING_ADAPTERS = frozenset(
    {CliAdapter.CLAUDE, CliAdapter.CURSOR, CliAdapter.CODEX, CliAdapter.OPENCODE}
)


def _present(value: RuntimeAvailability | bool) -> RuntimeAvailability:
    return RuntimeAvailability.AVAILABLE if value else RuntimeAvailability.UNAVAILABLE


def _env_present(*names: str) -> bool:
    return any(os.environ.get(name, "").strip() for name in names)


def probe_capabilities(*, adapter: CliAdapter) -> dict[str, RuntimeAvailability]:
    """Capabilities that follow from the executor this run was given."""
    executes = adapter in _EXECUTING_ADAPTERS
    return {
        "shell_command": _present(executes),
        "workspace_file_write": _present(executes),
    }


def probe_credentials() -> dict[str, RuntimeAvailability]:
    """Presence of credential *sources* — never their values.

    Mirrors `doctor.check_cli_credentials`, which is the same question asked
    for a different consumer.
    """
    return {
        "claude_profile": _present(
            _env_present("CLAUDE_CODE_OAUTH_TOKEN")
            or (settings.repo_root / "data" / ".claude-oauth-token").is_file()
        ),
        "cursor_profile": _present(_env_present("CURSOR_API_KEY")),
        "codex_profile": _present(_env_present("CODEX_API_KEY", "OPENAI_API_KEY")),
        "github_token": _present(_env_present("GITHUB_TOKEN", "GH_TOKEN")),
    }


def probe_authority(*, profile: OrchestrationProfile | None) -> dict[str, AuthorityStatus]:
    """Policy standing for the scopes this control plane governs.

    A switch the operator left off is `GRANTABLE`, not `DENIED`: policy permits
    it and only a person has to say yes, which is the one human-required state
    an approval genuinely resolves.
    """
    if profile is None:
        return {}
    return {
        "repo:push": AuthorityStatus.GRANTED if profile.git.push else AuthorityStatus.GRANTABLE,
        "release:publish": (
            AuthorityStatus.GRANTED if profile.git.auto_merge else AuthorityStatus.GRANTABLE
        ),
    }
