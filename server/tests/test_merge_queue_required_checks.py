"""Which checks the merge queue finds required on a base branch, from GitHub's answers.

GitHub keeps required checks in two places, rulesets and classic branch
protection, and enforces both. The answers below are the two endpoints' own
shapes; only `read_gh`, the `gh` call itself, is faked.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
from loregarden.services import merge_queue_github
from loregarden.services.github_pr_listing import GitHubReadError
from loregarden.services.merge_queue_github import GhQueueGitHub, RequiredChecksUnavailable

RULES = "repos/{owner}/{repo}/rules/branches/main"
BRANCH = "repos/{owner}/{repo}/branches/main"
PLAN_REFUSAL = (
    "gh: Upgrade to GitHub Pro or make this repository public to enable this feature. (HTTP 403)"
)


def _rules(*contexts: str) -> str:
    if not contexts:
        return "[]"
    checks = [{"context": context, "integration_id": 15368} for context in contexts]
    return json.dumps(
        [
            {"type": "deletion"},
            {"type": "required_status_checks", "parameters": {"required_status_checks": checks}},
        ]
    )


def _branch(*contexts: str) -> str:
    return json.dumps(
        {
            "name": "main",
            "protected": bool(contexts),
            "protection": {
                "enabled": bool(contexts),
                "required_status_checks": {
                    "enforcement_level": "everyone" if contexts else "off",
                    "contexts": list(contexts),
                    "checks": [{"context": context, "app_id": 15368} for context in contexts],
                },
            },
        }
    )


def _required(tmp_path: Path, answers: dict[str, str | GitHubReadError]) -> list[str]:
    def read_gh(_root: Path, args: list[str], _timeout: float) -> str:
        assert args[0] == "api"
        answer = answers[args[1]]
        if isinstance(answer, GitHubReadError):
            raise answer
        return answer

    with mock.patch.object(merge_queue_github, "read_gh", side_effect=read_gh):
        return GhQueueGitHub(tmp_path).required_contexts("main")


@pytest.mark.parametrize(
    ("rules", "branch", "expected"),
    [
        pytest.param(_rules("server", "client"), _branch(), ["client", "server"], id="rulesets"),
        pytest.param(_rules(), _branch("ui", "gates"), ["gates", "ui"], id="classic"),
        pytest.param(
            _rules("server", "ui"), _branch("ui", "gates"), ["gates", "server", "ui"], id="both"
        ),
        pytest.param(_rules(), _branch(), [], id="neither"),
    ],
)
def test_required_checks_are_the_union_of_rulesets_and_classic_protection(
    tmp_path, rules, branch, expected
):
    assert _required(tmp_path, {RULES: rules, BRANCH: branch}) == expected


def test_a_plan_with_no_protection_is_not_a_branch_that_requires_nothing(tmp_path):
    """On GitHub Free a private repo refuses the rules but answers the branch, with none."""
    with pytest.raises(RequiredChecksUnavailable):
        _required(tmp_path, {RULES: GitHubReadError(PLAN_REFUSAL), BRANCH: _branch()})


@pytest.mark.parametrize("endpoint", [RULES, BRANCH])
@pytest.mark.parametrize(
    "answer",
    [
        GitHubReadError("GitHub did not answer within 30s"),
        GitHubReadError("gh: Not Found (HTTP 404)"),
        "not json",
    ],
    ids=["timeout", "not-found", "unreadable"],
)
def test_half_an_answer_is_a_failure_to_ask(tmp_path, endpoint, answer):
    """One place read and the other not must never pass for the whole list."""
    answers = {RULES: _rules("server"), BRANCH: _branch("ui"), endpoint: answer}
    with pytest.raises(GitHubReadError) as raised:
        _required(tmp_path, answers)
    assert type(raised.value) is GitHubReadError


@pytest.mark.parametrize("branch", ["{}", '{"protection": {"enabled": true}}'])
def test_a_branch_that_does_not_state_its_protection_is_not_unprotected(tmp_path, branch):
    with pytest.raises(GitHubReadError):
        _required(tmp_path, {RULES: _rules("server"), BRANCH: branch})
