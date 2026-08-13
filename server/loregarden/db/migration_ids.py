"""The migration id ledger, and the guard that keeps it honest.

`apply_migrations` keys on the id string, so a migration id is a
database-visible name rather than a label. Rename one and its body runs again
against every database that already applied it under the old name — silently,
and twice over for anything that rewrites data instead of guarding itself.

That is not hypothetical: parallel branches both claim the next free number,
and whoever merges second has to renumber.

Lives apart from `migrations.py` because it is a different kind of thing — a
frozen list plus a validator, versus the migration bodies themselves.
"""

from __future__ import annotations

# Append-only. Every id that has shipped on main, in order.
#
# Ids *not* listed here are still free to renumber: an unmerged migration has
# not reached anyone else's database yet, which is exactly when renumbering is
# the right fix for a collision.
SHIPPED_MIGRATION_IDS: tuple[str, ...] = (
    "0001_workspace_workflow_override",
    "0002_ticket_columns",
    "0003_workspace_runtime_columns",
    "0004_approval_columns",
    "0005_agent_run_orchestration_id",
    "0006_orchestration_run_columns",
    "0007_triage_messages_table",
    "0008_ticket_studio_tables",
    "0009_ticket_diff_comments",
    "0010_branch_diff_comments",
    "0011_branch_triage_messages",
    "0012_agent_run_auto_approve",
    "0013_ticket_studio_preview_state",
    "0014_queued_run_failure_columns",
    "0015_agent_model_columns",
    "0016_triage_message_run_id",
    "0017_agent_run_timeout_override",
    "0018_approval_checklist",
    "0019_clear_classify_next_agent_backfill",
    "0020_compatibility_posture",
    "0021_branch_triage_message_status",
    "0022_definition_versioning",
    "0023_light_heavy_rigor_triage",
    "0024_agent_run_changed_paths",
    "0025_artifact_evidence",
    "0026_verify_stage_in_v3",
    "0027_parallel_review_in_v3",
    "0028_require_verify_evidence",
    "0029_require_implement_real_surface",
    "0030_refactor_skill_routes",
    "0031_plan_skill_on_plan_stage",
    "0032_adversarial_planning",
    "0033_run_messages_table",
    "0034_mcp_servers_table",
    "0035_mcp_server_tool_policy",
    "0036_mcp_tool_calls_table",
    "0037_mcp_server_health",
    "0038_mcp_server_rate_limit",
    "0039_queued_run_created_at",
    "0040_approval_auto_resolution_audit",
    "0041_agent_run_handoff_liveness",
    "0042_ticket_enum_values",
    "0043_run_approval_event_enum_values",
    "0044_ticket_scope_reroute_agent",
    "0045_ensure_terminal_stage",
    "0046_ticket_integration_review",
    "0047_ticket_dependencies_table",
    "0048_chat_message_parts",
    "0049_run_cancel_requested",
    "0050_baxter_chat_tables",
    "0051_ticket_studio_turn_lifecycle",
    "0052_git_automation",
    "0053_workspace_effort_columns",
    "0054_workspace_scoped_runs_and_approvals",
    "0055_branch_triage_message_run",
    "0056_reference_repos",
    "0057_mcp_server_tool_catalog",
    "0058_global_agent_slots",
    "0059_per_slot_queues",
    "0060_chat_turn_thinking",
    "0061_chat_turn_answer",
    "0062_lane_entry_kind",
    "0063_btw_exchanges",
    "0064_lane_entry_run_options",
    "0065_workspace_codex_model",
    "0066_baxter_chat_runtime",
    "0067_orchestration_timeout_override",
    "0068_clear_phantom_skill_names",
    "0069_skill_versioning",
    "0070_stage_fanout_groups",
    "0071_backfill_handoff_artifacts",
    "0072_ticket_tags",
    "0073_ticket_relations",
    "0074_lane_entry_dismissed",
    "0075_composer_commands",
    "0076_worktree_ticket_id",
    "0077_workspace_opencode_columns",
    "0078_agent_run_git_boundary",
    "0079_agent_run_boundary_verdict",
)


def assert_migration_ids_are_sound(ids: list[str]) -> None:
    """Raise if the migration list has been corrupted by a parallel branch.

    Two failure modes: a duplicate id (a merge that kept both sides of a
    collision), and a changed shipped id (a renumber applied to a migration
    that already ran somewhere).

    The shipped check is a *prefix* check on purpose — a migration written but
    not yet added to the ledger must still let the app boot, or writing one
    would be impossible. The test suite is what requires the two to match
    exactly before anything merges.
    """
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise RuntimeError(
            f"Duplicate migration id(s): {', '.join(duplicates)}. "
            "Two branches claimed the same number — renumber the newer one."
        )

    shipped = list(SHIPPED_MIGRATION_IDS)
    if ids[: len(shipped)] != shipped:
        raise RuntimeError(
            "Shipped migration ids changed. They are append-only: renaming one "
            "re-runs it against databases that already applied it. Restore the "
            "shipped prefix and append new migrations after it."
        )
