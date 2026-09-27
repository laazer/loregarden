"""Confidence in a learning, from how the runs it was surfaced into ended (178).

A Beta posterior over "surfacing this learning goes with a run that holds up".
Every settled `learning_applications` row is one observation, graded by its
ladder rung into fractional success and failure evidence, and the posterior is
the prior plus the sum of that evidence.

Why a Beta posterior and not a running multiplier. The rejected prior art
nudged a score up or down by a factor per self-reported outcome, which made the
answer depend on the order events arrived in and let one report swing a fresh
lesson as far as fifty. A sum is commutative — shuffle the events and the
posterior is identical, which a test pins — and the Beta's width is the sample
size: one clean pass and fifty clean passes have similar means and very
different lower bounds.

Rung → evidence. Each observation carries one unit of evidence split between
success and failure:

    ============================  =======  =======
    rung                          success  failure
    ============================  =======  =======
    clean_pass                    1.00     0.00
    passed_after_autofix          0.75     0.25
    rerouted                      0.25     0.75
    blocked                       0.00     1.00
    ============================  =======  =======

A mechanical fixer clearing a gate means the work mostly held; a reroute means
it went back for another agent turn without needing a person; a block needed a
person. Cancelled runs are settled with no rung and contribute nothing.

Two ideas borrowed from graphify's reflect pass, both deterministic and LLM-free:

- **Corroboration gate.** One save cannot mint a trusted lesson. `trusted`
  requires the one-sided 95% lower bound to clear 0.5, which a single clean pass
  on the uniform prior cannot do; it takes three fresh ones. The posterior is
  the continuous form of "promotion needs distinct results", and distinctness is
  enforced upstream by the one-row-per-(run, learning) constraint.
- **Asymmetric recency.** Evidence decays with age, and failure evidence decays
  slower than success evidence — `FAILURE_HALF_LIFE_DAYS` against
  `SUCCESS_HALF_LIFE_DAYS` — so a fresh negative outweighs an old positive, and
  a lesson that went bad recently cannot coast on a good record from last year.

Age is measured against an explicit `as_of`, never the wall clock inside the
sum, so the same events score the same no matter when or in what order they are
folded in.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from loregarden.models.domain import LearningOutcomeRung

PRIOR_SUCCESS = 1.0
PRIOR_FAILURE = 1.0
SUCCESS_HALF_LIFE_DAYS = 45.0
FAILURE_HALF_LIFE_DAYS = 180.0
#: One-sided 95% normal quantile, for the lower bound.
_Z = 1.645
TRUST_THRESHOLD = 0.5

RUNG_EVIDENCE: dict[LearningOutcomeRung, tuple[float, float]] = {
    LearningOutcomeRung.CLEAN_PASS: (1.0, 0.0),
    LearningOutcomeRung.PASSED_AFTER_AUTOFIX: (0.75, 0.25),
    LearningOutcomeRung.REROUTED: (0.25, 0.75),
    LearningOutcomeRung.BLOCKED: (0.0, 1.0),
}


@dataclass(frozen=True, slots=True)
class Observation:
    rung: LearningOutcomeRung
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class LearningConfidence:
    """The posterior, and what it rests on.

    `observations` is the raw count of settled runs with a rung. It is kept
    apart from the weighted evidence so "never observed" (0) cannot be mistaken
    for "observed long ago" (decayed to near the prior).
    """

    alpha: float
    beta: float
    observations: int

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def lower_bound(self) -> float:
        total = self.alpha + self.beta
        variance = (self.alpha * self.beta) / (total * total * (total + 1.0))
        return max(0.0, self.mean - _Z * math.sqrt(variance))

    @property
    def trusted(self) -> bool:
        return self.lower_bound >= TRUST_THRESHOLD


UNOBSERVED = LearningConfidence(alpha=PRIOR_SUCCESS, beta=PRIOR_FAILURE, observations=0)


def _decay(age_days: float, half_life_days: float) -> float:
    return 0.5 ** (max(age_days, 0.0) / half_life_days)


def score(observations: Iterable[Observation], *, as_of: datetime) -> LearningConfidence:
    """Fold observations into a posterior. Order-independent by construction.

    `math.fsum`, not `+=`: floating-point addition is not associative, so a
    running sum over shuffled input differs in the last bits. `fsum` is
    correctly rounded, which makes the result exactly independent of order.
    """
    successes: list[float] = [PRIOR_SUCCESS]
    failures: list[float] = [PRIOR_FAILURE]
    count = 0
    for obs in observations:
        success, failure = RUNG_EVIDENCE[obs.rung]
        age_days = (as_of - obs.observed_at).total_seconds() / 86400.0
        successes.append(success * _decay(age_days, SUCCESS_HALF_LIFE_DAYS))
        failures.append(failure * _decay(age_days, FAILURE_HALF_LIFE_DAYS))
        count += 1
    return LearningConfidence(
        alpha=math.fsum(successes), beta=math.fsum(failures), observations=count
    )


def describe(confidence: LearningConfidence) -> str:
    """The annotation a briefing carries for one learning."""
    if confidence.observations == 0:
        return "no observed outcomes yet"
    runs = "run" if confidence.observations == 1 else "runs"
    verdict = "trusted" if confidence.trusted else "unproven"
    return (
        f"confidence {confidence.mean:.2f} over {confidence.observations} observed {runs}, "
        f"{verdict}"
    )
