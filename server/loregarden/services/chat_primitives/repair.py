"""One chance for the model to correct the cards it wrote that did not parse.

A rejected fence still reaches the operator, folded as raw text, so nothing is
lost without it. But the prompt is the only thing teaching a model the card
schema, and a prompt is a request: a model that writes ``"options"`` where the
schema says ``"items"`` keeps doing it until someone notices. Handing the
validation error back turns that into a self-correcting miss rather than a
standing one.

The correction is a separate, tool-less, read-only call that sees only the
broken cards, the error for each and the card contract — never the turn it came
from. It cannot act, so an acting turn's work is never repeated, and the reply
it amends is the one already produced.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from loregarden.agents.chat_role_prompt import chat_ui_primitives_blocks
from loregarden.services.chat_primitives.parser import RejectedFence, parse_reply

#: Takes the correction prompt, returns the model's reply to it.
CardCorrector = Callable[[str], str]

_RETURNED_FENCE_RE = re.compile(r"```loregarden\s*\n.*?\n```", re.DOTALL | re.IGNORECASE)


def correction_prompt(rejected: Sequence[RejectedFence]) -> str:
    """The request for corrected cards, one numbered entry per rejected fence."""
    entries = "\n\n".join(
        f"Card {index}: {fence.reason}\n{fence.fence}"
        for index, fence in enumerate(rejected, start=1)
    )
    contract = "\n".join(chat_ui_primitives_blocks())
    return (
        "You wrote the chat cards below in your last reply, and they failed "
        "validation, so the operator saw raw JSON instead of a card. Each is listed "
        "with the error it failed on.\n\n"
        f"{entries}\n\n"
        "Reply with exactly one corrected ```loregarden fence per card, in the same "
        "order, and nothing else. Keep what each card says; change only its shape "
        "to fit the contract below. If a card cannot be expressed in the contract, "
        "repeat it unchanged.\n"
        f"{contract}\n"
    )


def correct_rejected_cards(
    reply: str, rejected: Sequence[RejectedFence], corrector: CardCorrector
) -> tuple[str, list[RejectedFence]]:
    """*reply* with every card the corrector fixed put in place, and those it did not.

    A correction is taken only when it parses. The answer must hold one fence per
    rejected card; any other count is a reply that cannot be lined up with the
    cards it answers, so nothing is taken from it.
    """
    if not rejected:
        return reply, []
    returned = _RETURNED_FENCE_RE.findall(corrector(correction_prompt(rejected)))
    if len(returned) != len(rejected):
        return reply, list(rejected)

    corrected = reply
    still_rejected: list[RejectedFence] = []
    for fence, replacement in zip(rejected, returned, strict=True):
        if parse_reply(replacement).rejected:
            still_rejected.append(fence)
        else:
            corrected = corrected.replace(fence.fence, replacement, 1)
    return corrected, still_rejected
