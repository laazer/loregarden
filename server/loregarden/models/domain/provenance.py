"""What inferred a value, where that inferrer is wired, and how sure it is.

A control plane that derives facts has to be able to say which of its answers
were measured and which were guessed, and by what. Three fields answer it:

* `inferred_by` — the synthesizer: a heuristic, a classifier, a reviewing agent.
* `registered_at` — where that synthesizer is wired into the system.
* `confidence` — optional, and absent until something actually derives one. A
  number nobody computed is worse than no number (lg-code-knowledge-1140 owns
  calibration).

THE REGISTRY RULE, which is this module's substance: `registered_at` is always
resolved from a registry keyed by a stable synthesizer id. It is never a string
literal at the construction site and never a value a producer reports about
itself — a producer naming its own wiring site is asserting something it cannot
check, and a hallucinated module path reads as provenance while carrying none.
That is also why the agent-facing `FINDING_SCHEMA` offers `inferred_by` and
`confidence` and not `registered_at`: the control plane stamps that one.

An EMPTY `registered_at` is legal, and deliberately so: a producer that supplied
only `inferred_by` must record the OMISSION rather than have a site guessed for
it. The rule is that nothing but a registry may FILL the field, not that the
field is never empty. (`gate_unclassified.Unclassified`, the gates' own value,
refuses a blank one instead — there the finding is built at a site the gate
knows, so a blank would only ever be a bug.)

Nothing here is stored: the triple rides on read models, resolved by the reader.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = ["Provenance", "SynthesizerRegistry"]


@dataclass(frozen=True)
class Provenance:
    """One inference's provenance triple."""

    inferred_by: str
    registered_at: str
    confidence: float | None = None


@runtime_checkable
class SynthesizerRegistry(Protocol):
    """Resolves a synthesizer's wiring site from its stable id.

    Keyed by the id rather than by the producer, so the same heuristic resolves
    to the same site from every call site that reads it.
    """

    def registered_at(self, synthesizer_id: str) -> str:
        """Where `synthesizer_id` is wired, or raise if the registry does not know it."""
        ...
