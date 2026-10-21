"""The card correction turn, run on the CLI agent that wrote the cards."""

from __future__ import annotations

from dataclasses import replace

from loregarden.models.domain import Workspace
from loregarden.services.chat_primitives.repair import CardCorrector
from loregarden.services.cli_agent_runner import (
    CliAgentProfile,
    run_cli_agent_turn,
    stub_response,
)

#: Its own stub, apart from the surface's: a stubbed turn's canned reply is not
#: an answer to a correction prompt, and a test that stubs the turn must not
#: spawn a real CLI to correct it.
CARD_CORRECTION_STUB_ENV = "LOREGARDEN_CARD_CORRECTION_STUB_RESPONSE"

_USER_PROMPT = "Return only the corrected loregarden card fences."


def cli_card_corrector(profile: CliAgentProfile, workspace: Workspace) -> CardCorrector:
    """A corrector that asks *profile*'s agent, read-only and with no tools."""
    correction = replace(
        profile, stub_env=CARD_CORRECTION_STUB_ENV, tmp_prefix="loregarden-card-correction-"
    )

    def correct(prompt: str) -> str:
        stub = stub_response(correction)
        if stub is not None:
            return stub
        return run_cli_agent_turn(
            correction,
            workspace=workspace,
            prompt=prompt,
            user_prompt=_USER_PROMPT,
            granted_tools=[],
            read_only=True,
        )

    return correct
