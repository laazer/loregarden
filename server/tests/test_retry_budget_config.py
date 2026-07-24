"""RetryBudgetConfig on OrchestrationProfile (ticket 105, Requirement 4).

Mirrors GatesConfig's existing default/override/enabled-flag pattern
(test_orchestration_profile_gates.py) so a workspace that never mentions
`retry_budget:` keeps today's behavior, and one that does gets exactly the
override it asked for.
"""

from loregarden.config import settings
from loregarden.models.domain import Workspace
from loregarden.services.orchestration_profile import (
    OrchestrationProfile,
    RetryBudgetConfig,
    load_profile_from_path,
    orchestration_dir,
    resolve_orchestration_profile,
)


def _workspace(slug="retry-budget-test") -> Workspace:
    return Workspace(slug=slug, name="Retry Budget Test", repo_path=".")


def test_default_profile_has_a_five_attempt_budget_enabled():
    """AC4.1: loading with no `retry_budget` key at all."""
    profile = OrchestrationProfile(slug="default")
    assert profile.retry_budget.enabled is True
    assert profile.retry_budget.max_attempts_per_stage == 5


def test_profile_yaml_without_retry_budget_key_keeps_the_default(tmp_path, monkeypatch):
    """AC4.1: an existing profile file that predates this feature must not break."""
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    ws = _workspace()
    path = orchestration_dir() / f"{ws.slug}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "slug: retry-budget-test\nworkflow_template: loregarden-tdd\n",
        encoding="utf-8",
    )

    profile = resolve_orchestration_profile(ws)
    assert profile.retry_budget.enabled is True
    assert profile.retry_budget.max_attempts_per_stage == 5


def test_profile_yaml_overrides_only_the_declared_retry_budget_field(tmp_path, monkeypatch):
    """AC4.2: overriding max_attempts_per_stage alone leaves `enabled` at its default."""
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    ws = _workspace()
    path = orchestration_dir() / f"{ws.slug}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "slug: retry-budget-test\nretry_budget:\n  max_attempts_per_stage: 10\n",
        encoding="utf-8",
    )

    profile = resolve_orchestration_profile(ws)
    assert profile.retry_budget.max_attempts_per_stage == 10
    assert profile.retry_budget.enabled is True


def test_retry_budget_enabled_false_is_an_escape_hatch(tmp_path, monkeypatch):
    """AC4.3: disabling it must be expressible per workspace, mirroring GatesConfig.enabled."""
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    ws = _workspace()
    path = orchestration_dir() / f"{ws.slug}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "slug: retry-budget-test\nretry_budget:\n  enabled: false\n",
        encoding="utf-8",
    )

    profile = resolve_orchestration_profile(ws)
    assert profile.retry_budget.enabled is False
    # Disabling must not silently reset the threshold too.
    assert profile.retry_budget.max_attempts_per_stage == 5


def test_retry_budget_config_model_validates_standalone():
    cfg = RetryBudgetConfig()
    assert cfg.enabled is True
    assert cfg.max_attempts_per_stage == 5

    overridden = RetryBudgetConfig(max_attempts_per_stage=2, enabled=False)
    assert overridden.max_attempts_per_stage == 2
    assert overridden.enabled is False


def test_load_profile_from_path_round_trips_retry_budget(tmp_path):
    path = tmp_path / "wf.yaml"
    path.write_text(
        "slug: wf\nretry_budget:\n  enabled: true\n  max_attempts_per_stage: 3\n",
        encoding="utf-8",
    )
    profile = load_profile_from_path(path)
    assert profile.retry_budget.max_attempts_per_stage == 3
    assert profile.retry_budget.enabled is True
