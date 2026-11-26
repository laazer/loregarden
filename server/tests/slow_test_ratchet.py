"""Tests that were already over the per-test ceiling when it landed: they may only leave.

`tests/slow_test_ceiling.py` fails a test over its ceiling unless it is marked
`@pytest.mark.slow(reason=...)` or listed here. This list is the inherited debt,
so the ceiling blocks no one on day one, and `test_slow_test_ratchet.py` fails a
branch that adds an entry: a new slow test is marked with its reason, never
listed.

Remove an entry when its test gets fast (or is deleted, or is marked). Nothing
else may edit this file.

Built 2026-11-26 from the CI server-suite profiles of runs 45819701261,
45818593089 and 45817899729, after the test speedups (#980, #981, #987), and of
run 45717770597 from before them, which the build read by mistake: every
collected test whose slowest setup+call across the four exceeded 3.0 s. The
older run only adds entries (tests that were slow before the speedups), so the
list exempts more than it needs to, never less; it shrinks as they are removed.
That is 60% of the 5 s ceiling, not the ceiling itself, so a test that measured
4.5 s once does not fail at random on a noisier runner. The trailing comment is
that slowest measurement.
"""

INHERITED: frozenset[str] = frozenset(
    {
        "tests/test_artifact_kind_owners.py::test_a_fourth_writer_of_the_kind_column_cannot_appear_unnoticed",  # 3.32s
        "tests/test_artifact_kind_owners.py::test_the_artifact_row_is_never_constructed_under_an_alias",  # 3.19s
        "tests/test_auto_mode_subtree.py::test_a_child_runs_once_its_cross_workspace_prerequisite_is_settled",  # 3.24s
        "tests/test_auto_mode_subtree.py::test_a_sibling_prerequisite_that_is_merely_not_done_does_not_hold_its_dependent",  # 4.99s
        "tests/test_auto_mode_subtree.py::test_a_ticket_that_grows_oversized_after_a_stage_is_split_at_the_next_boundary",  # 6.69s
        "tests/test_auto_mode_subtree.py::test_an_unlanded_sibling_holds_only_the_child_that_depends_on_it",  # 3.45s
        "tests/test_auto_mode_subtree.py::test_child_run_does_not_get_auto_approve_without_it",  # 3.71s
        "tests/test_auto_mode_subtree.py::test_children_run_in_type_then_priority_order_not_alphabetical",  # 6.99s
        "tests/test_auto_mode_subtree.py::test_human_resolved_gate_is_not_labeled_automation",  # 3.48s
        "tests/test_auto_mode_subtree.py::test_integration_review_child_runs_last_via_dependency",  # 6.45s
        "tests/test_auto_mode_subtree.py::test_oversized_ticket_is_split_and_its_parts_run_to_completion",  # 8.09s
        "tests/test_auto_mode_subtree.py::test_subtree_bound_zero_means_unlimited",  # 5.32s
        "tests/test_auto_mode_subtree.py::test_subtree_with_two_children_completes_end_to_end_under_auto_mode",  # 4.52s
        "tests/test_backlog_features.py::test_orchestrate_accepts_auto_approve_and_stop_at",  # 3.06s
        "tests/test_base_branch_one_answer.py::test_no_module_reads_a_base_branch_it_was_not_handed",  # 5.21s
        "tests/test_base_branch_one_answer.py::test_the_detector_scans_the_whole_package",  # 3.31s
        "tests/test_capacity_passing.py::test_passing_claims_keep_their_own_order",  # 3.40s
        "tests/test_capacity_run.py::test_a_push_behind_six_heavy_suites_waits_its_turn_and_runs",  # 4.40s
        "tests/test_capacity_run.py::test_a_run_that_used_its_grant_is_not_told_to_ask_for_less",  # 3.18s
        "tests/test_capacity_run.py::test_sigterm_stops_the_command_and_releases_the_lease",  # 4.51s
        "tests/test_capacity_run.py::test_the_real_cli_script_runs_the_command_where_it_was_called",  # 4.30s
        "tests/test_capacity_run.py::test_two_runs_with_room_for_one_do_not_overlap",  # 8.54s
        "tests/test_capacity_run_wrapper.py::test_a_ledger_failure_is_retried_and_the_command_runs_once_it_clears",  # 3.12s
        "tests/test_capacity_run_wrapper.py::test_time_spent_queued_does_not_count_against_the_hard_cap",  # 4.11s
        "tests/test_capacity_run_wrapper.py::test_without_a_terminal_retries_stop_at_the_hard_cap",  # 3.11s
        "tests/test_cli_run_timeout.py::test_chatty_runaway_is_bounded_by_the_hard_cap",  # 7.06s
        "tests/test_cli_run_timeout.py::test_streaming_run_survives_past_the_idle_budget",  # 4.73s
        "tests/test_code_signals.py::test_a_refresh_over_this_checkout_fits_the_measured_shape",  # 3.93s
        "tests/test_criteria_sign_off.py::test_a_decision_taken_in_a_persons_place_keeps_the_gate_and_is_named_on_it[deferred_undecided_findings]",  # 3.12s
        "tests/test_criterion_gate.py::test_a_criterion_that_fails_again_after_a_split_goes_to_a_person",  # 3.57s
        "tests/test_criterion_gate.py::test_a_split_shows_in_the_digest_of_the_tree_above_it",  # 5.36s
        "tests/test_criterion_gate.py::test_gaps_verify_could_not_answer_split_off_to_a_sibling",  # 3.77s
        "tests/test_dependency_hold.py::test_every_function_that_starts_a_stage_is_held",  # 5.37s
        "tests/test_detached_run_adversarial.py::test_a_tmux_pane_receives_its_secrets_without_putting_them_in_argv",  # 3.12s
        "tests/test_directory_release_adversarial.py::test_every_production_construction_site_names_a_disposition",  # 3.45s
        "tests/test_durable_settlement_adversarial.py::test_a_run_that_blows_its_hard_cap_leaves_no_orphan_agent",  # 5.63s
        "tests/test_durable_settlement_adversarial.py::test_output_written_before_a_cancel_is_still_reported",  # 5.87s
        "tests/test_exit_action_continuation.py::test_a_failed_continuation_attests_nothing_and_does_not_finish_the_stage",  # 4.04s
        "tests/test_exit_action_continuation.py::test_a_grant_continuation_runs_the_action_and_does_not_reask_the_judgment",  # 4.77s
        "tests/test_exit_action_continuation.py::test_a_mixed_gate_offers_approve_only_after_its_recheck_clears",  # 3.86s
        "tests/test_exit_action_continuation.py::test_a_recheck_continuation_runs_only_the_newly_executable_action",  # 4.71s
        "tests/test_exit_action_continuation.py::test_the_continuation_keeps_the_parent_runs_flags",  # 3.55s
        "tests/test_gate_dispatch_check.py::test_a_check_with_no_commit_to_name_is_rerun_when_the_stage_starts",  # 4.46s
        "tests/test_gate_dispatch_check.py::test_a_commit_after_the_check_reruns_it_when_the_stage_starts",  # 4.31s
        "tests/test_gate_dispatch_check.py::test_a_gate_started_by_hand_with_a_failing_test_is_refused",  # 3.23s
        "tests/test_gate_dispatch_check.py::test_every_function_that_finishes_a_workflow_asks_the_criteria_check",  # 5.49s
        "tests/test_gate_dispatch_check.py::test_every_function_that_starts_a_stage_asks_the_criteria_check",  # 5.83s
        "tests/test_gate_timeouts.py::test_a_timed_out_commands_last_output_is_kept",  # 6.05s
        "tests/test_git_call_index.py::test_the_real_tree_names_every_branch_ref_writer",  # 8.59s
        "tests/test_git_subprocess_gate.py::test_real_services_are_clean",  # 3.53s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[active_worktree_rows_have_their_checkout]",  # 6.11s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[awaiting_stages_have_a_pending_approval]",  # 4.54s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[briefings_count_what_they_delivered]",  # 3.74s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[capacity_blocks_resume_when_the_line_clears]",  # 5.10s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[capacity_waits_are_not_work_blocks]",  # 4.31s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[checkouts_keep_their_branch]",  # 3.84s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[cleaned_worktree_rows_leave_no_checkout]",  # 3.61s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[closed_tickets_have_closed_workflows]",  # 4.28s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[closed_tickets_take_no_stage_answers]",  # 5.83s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[concluded_runs_grade_their_learnings]",  # 4.01s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[done_ticket_ship_is_observable]",  # 3.42s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[done_tickets_have_settled_criteria]",  # 4.60s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[every_block_is_settled]",  # 3.97s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[every_busy_lane_has_an_occupant]",  # 4.10s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[exhausted_gates_block_their_ticket]",  # 4.00s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[failed_runs_have_a_failure_kind]",  # 3.62s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[findings_do_not_outlive_the_source_that_decides_them]",  # 4.23s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[finished_survey_passes_close_their_carrier]",  # 4.63s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[follow_ups_leave_their_source]",  # 8.15s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[held_repairs_read_in_progress]",  # 5.90s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[held_tickets_resume_when_their_prerequisites_land]",  # 4.20s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[idle_tickets_are_held]",  # 3.54s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[land_verification_ran]",  # 4.06s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[land_verification_receipt_is_service_written]",  # 4.24s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[landed_work_is_committed]",  # 4.02s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[landed_work_reaches_the_base]",  # 4.22s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[landing_blocks_clear_with_their_cause]",  # 4.52s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[landings_carry_the_work]",  # 4.24s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[landings_survive_their_branch]",  # 3.75s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[live_workspaces_have_real_repositories]",  # 5.37s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[no_stage_runs_past_an_unrun_gate]",  # 4.79s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[no_ticket_depends_on_an_ancestor]",  # 6.00s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[no_ticket_is_blocked_by_a_live_run_refusal]",  # 4.11s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[no_wait_loop_through_a_parent]",  # 4.46s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[orphaned_symbol_map_partition]",  # 5.19s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[parents_close_with_their_own_work_landed]",  # 3.98s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[parked_starts_keep_their_key]",  # 4.08s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[proven_evidence_is_not_held_open]",  # 5.33s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[publish_cards_close_once_landed]",  # 4.19s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[queued_overrides_apply]",  # 4.89s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[refused_landings_wait_for_a_change]",  # 5.72s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[rework_pauses_stand_on_charged_rounds]",  # 3.93s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[run_filings_are_findings]",  # 4.92s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[run_parents_end_with_their_run]",  # 3.83s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[runs_complete_once]",  # 5.57s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[settled_holds_read_blocked]",  # 3.94s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[stale_parks_are_closed]",  # 4.12s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[stopped_entries_outlive_no_closed_pr]",  # 4.54s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[stopped_runs_do_not_stay_in_flight]",  # 4.28s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[survey_cards_close_with_their_cause]",  # 4.62s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[survey_maps_split_what_no_pass_can_hold]",  # 4.39s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[ticket_branches_hold_their_targets_history]",  # 5.79s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[tickets_are_created_under_the_size_caps]",  # 5.34s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[unavailable_gates_are_not_work_blocks]",  # 5.61s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[undecided_findings_do_not_run]",  # 6.60s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[unresolved_open_shrink_is_audited]",  # 3.73s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[unstarted_runs_hold_no_start_key]",  # 4.92s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[waiting_entries_have_no_live_work]",  # 4.49s
        "tests/test_invariants.py::test_one_pass_leaves_nothing_for_the_next[worktree_parked_at_names_a_live_path]",  # 3.70s
        "tests/test_land_verification.py::test_the_verifier_reaches_land_ticket_without_a_lazy_import",  # 3.15s
        "tests/test_land_verification_invariant.py::test_one_landing_after_the_guard_is_found_in_the_same_scenario",  # 8.60s
        "tests/test_land_verification_invariant.py::test_the_detector_runs_no_suite_and_shells_out_to_nothing",  # 7.00s
        "tests/test_land_verification_invariant.py::test_the_first_pass_over_productions_shape_reports_nothing",  # 5.34s
        "tests/test_land_verification_receipt.py::test_the_census_sees_every_writer_of_the_column_the_detector_reads",  # 5.82s
        "tests/test_land_verification_receipt.py::test_the_server_command_runs_what_the_landing_reaches_from_the_detached_root",  # 41.45s
        "tests/test_land_verify_command_process.py::test_a_timeout_kills_the_whole_tree_and_keeps_what_it_printed",  # 3.07s
        "tests/test_landing_survival.py::test_every_module_that_moves_a_branch_ref_is_accounted_for",  # 8.15s
        "tests/test_memory_store.py::test_sqlite_db_in_icloud_dir",  # 3.97s
        "tests/test_migration_forks_commute.py::test_migration_forks_commute",  # 7.89s
        "tests/test_no_toolchain_skips.py::test_no_test_skips_for_the_toolchain_this_repo_requires",  # 4.72s
        "tests/test_orchestrate_endpoint.py::test_without_an_effective_limit_it_runs_every_stage_up_to_the_human_gate[null]",  # 4.01s
        "tests/test_orchestration_start_keys.py::test_only_one_place_ends_an_orchestration_run",  # 4.19s
        "tests/test_pass_receipt.py::test_a_run_without_the_progress_plugin_hands_no_test_its_progress_file",  # 5.66s
        "tests/test_permission_bridge.py::test_permission_bridge_finishes_on_result_when_process_stays_alive",  # 5.35s
        "tests/test_prior_work_basis.py::test_the_ceiling_drops_an_overcommon_term_at_prod_shape_volume",  # 7.57s
        "tests/test_prior_work_basis.py::test_the_document_frequency_is_counted_in_the_pass_that_already_tokenizes",  # 7.93s
        "tests/test_prod_shape.py::test_activity_digest_matches_the_live_two_weeks",  # 6.33s
        "tests/test_prod_shape.py::test_building_twice_changes_nothing",  # 9.66s
        "tests/test_prod_shape.py::test_counts_match_the_calibration",  # 10.03s
        "tests/test_prod_shape.py::test_memory_has_no_recorded_links_but_real_groups",  # 7.20s
        "tests/test_prod_shape.py::test_memory_proposals_match_production",  # 8.11s
        "tests/test_prod_shape.py::test_one_initiative_claims_a_cross_workspace_theme",  # 7.15s
        "tests/test_prod_shape.py::test_the_monitor_sees_mostly_finished_tickets",  # 7.39s
        "tests/test_prod_shape.py::test_the_tree_nests_parks_and_holds_like_production",  # 6.54s
        "tests/test_prod_shape_fixtures.py::test_fixture_matches_the_api[attachable-milestones.json]",  # 7.70s
        "tests/test_prod_shape_fixtures.py::test_fixture_matches_the_api[handoffs-loregarden.json]",  # 3.70s
        "tests/test_prod_shape_fixtures.py::test_fixture_matches_the_api[handoffs-worked-ticket.json]",  # 3.76s
        "tests/test_prod_shape_fixtures.py::test_fixture_matches_the_api[initiative-suggestions.json]",  # 3.59s
        "tests/test_prod_shape_fixtures.py::test_fixture_matches_the_api[initiatives.json]",  # 5.77s
        "tests/test_prod_shape_fixtures.py::test_fixture_matches_the_api[memory-graph-loregarden.json]",  # 5.25s
        "tests/test_prod_shape_fixtures.py::test_fixture_matches_the_api[merge-queue-loregarden.json]",  # 4.11s
        "tests/test_prod_shape_fixtures.py::test_fixture_matches_the_api[merge-queue-overview.json]",  # 4.04s
        "tests/test_prod_shape_fixtures.py::test_fixture_matches_the_api[monitor-findings.json]",  # 3.76s
        "tests/test_prod_shape_fixtures.py::test_fixture_matches_the_api[reference-library-deep-source.json]",  # 5.64s
        "tests/test_prod_shape_fixtures.py::test_fixture_matches_the_api[reference-library-loregarden.json]",  # 7.46s
        "tests/test_prod_shape_fixtures.py::test_fixture_matches_the_api[ticket-artifacts.json]",  # 3.67s
        "tests/test_prod_shape_fixtures.py::test_fixture_matches_the_api[ticket-ci-status.json]",  # 4.14s
        "tests/test_prod_shape_fixtures.py::test_fixture_matches_the_api[ticket-history-artifacts.json]",  # 4.00s
        "tests/test_prod_shape_fixtures.py::test_fixture_matches_the_api[ticket-history-ledger.json]",  # 3.84s
        "tests/test_prod_shape_fixtures.py::test_fixture_matches_the_api[ticket-tree.json]",  # 3.81s
        "tests/test_prod_shape_fixtures.py::test_fixture_matches_the_api[tickets-under-the-largest-parent.json]",  # 4.38s
        "tests/test_prod_shape_fixtures.py::test_fixture_matches_the_api[tickets-waiting-on-tickets.json]",  # 5.71s
        "tests/test_prod_shape_fixtures.py::test_fixture_matches_the_api[workspace-activity-blobert.json]",  # 3.95s
        "tests/test_prod_shape_fixtures.py::test_fixture_matches_the_api[workspace-activity-loregarden.json]",  # 3.65s
        "tests/test_prod_shape_fixtures.py::test_worked_ticket_matches_its_calibration",  # 4.35s
        "tests/test_provenance.py::test_no_caller_types_a_registration_site_as_a_literal_keyword",  # 3.51s
        "tests/test_provenance.py::test_no_caller_types_a_registration_site_as_a_literal_positional",  # 3.53s
        "tests/test_pytest_target_selection.py::test_the_hook_config_selects_the_tests_that_check_it",  # 15.43s
        "tests/test_queue_events.py::test_an_event_for_another_workspace_does_not_wake_this_socket",  # 5.83s
        "tests/test_queue_lanes.py::test_every_entry_point_installs_a_lane_dispatcher[loregarden.cli.main]",  # 4.18s
        "tests/test_queue_lanes.py::test_every_entry_point_installs_a_lane_dispatcher[loregarden.cli.mcp_server]",  # 4.18s
        "tests/test_queue_lanes.py::test_every_entry_point_installs_a_lane_dispatcher[loregarden.main]",  # 5.30s
        "tests/test_queue_lanes.py::test_every_entry_point_installs_a_lane_dispatcher[loregarden.mcp.tools]",  # 4.54s
        "tests/test_queue_slot_claims.py::test_the_shared_predicate_does_not_close_an_import_cycle",  # 15.96s
        "tests/test_reference_survey_slicing.py::test_every_map_the_feeder_stores_splits_what_no_pass_can_hold",  # 3.42s
        "tests/test_reject_tests.py::test_a_deleted_reject_test_is_a_gap_at_the_gate",  # 3.29s
        "tests/test_reject_tests.py::test_complete_stage_refuses_a_reject_whose_test_passes",  # 3.58s
        "tests/test_reject_tests.py::test_one_refuted_test_leaves_the_reject_standing_on_the_other",  # 3.95s
        "tests/test_reject_tests.py::test_the_criteria_check_reruns_a_recorded_reject_test",  # 5.00s
        "tests/test_reject_tests.py::test_the_rerun_ignores_a_pyc_the_agents_own_run_left",  # 3.77s
        "tests/test_reload_hold.py::test_a_parent_whose_child_yielded_is_queued_even_if_the_hold_has_ended",  # 3.74s
        "tests/test_reload_hold.py::test_the_server_is_idle_once_the_ticket_has_yielded",  # 3.22s
        "tests/test_run_failure_kind.py::test_every_known_writer_goes_through_the_helper[services/triage_run_service.py-_finish]",  # 4.53s
        "tests/test_run_failure_kind.py::test_every_known_writer_goes_through_the_helper[services/triage_run_service.py-cancel_triage_turn]",  # 5.89s
        "tests/test_run_failure_kind.py::test_no_writer_sets_a_failed_status_around_set_run_status",  # 8.57s
        "tests/test_run_resupervise.py::test_a_reattached_run_records_its_git_evidence",  # 4.32s
        "tests/test_run_resupervise.py::test_an_unparseable_exit_code_fails_the_run_the_same_way",  # 3.57s
        "tests/test_run_supervision.py::test_no_module_enters_run_capacity_outside_supervised",  # 3.77s
        "tests/test_service_layering.py::test_every_entry_point_installs_the_operation_splitter[loregarden.cli.main]",  # 3.92s
        "tests/test_service_layering.py::test_every_entry_point_installs_the_operation_splitter[loregarden.cli.mcp_server]",  # 3.91s
        "tests/test_service_layering.py::test_every_entry_point_installs_the_operation_splitter[loregarden.main]",  # 5.16s
        "tests/test_service_layering.py::test_every_entry_point_installs_the_operation_splitter[loregarden.mcp.tools]",  # 4.38s
        "tests/test_service_layering.py::test_every_entry_point_installs_the_scheduler[loregarden.cli.main]",  # 3.99s
        "tests/test_service_layering.py::test_every_entry_point_installs_the_scheduler[loregarden.cli.mcp_server]",  # 3.87s
        "tests/test_service_layering.py::test_every_entry_point_installs_the_scheduler[loregarden.main]",  # 5.03s
        "tests/test_service_layering.py::test_every_entry_point_installs_the_scheduler[loregarden.mcp.tools]",  # 4.46s
        "tests/test_silent_except_gate.py::test_real_services_are_clean",  # 5.84s
        "tests/test_silent_except_gate.py::test_the_live_tree_holds_no_unclassifiable_constructs",  # 15.87s
        "tests/test_sql_like.py::test_escaping_the_memory_needle_drops_only_wildcard_hits_at_prod_shape_volume",  # 6.96s
        "tests/test_stage_git_snapshot.py::test_a_stage_starts_no_more_git_processes_than_its_ceiling",  # 7.91s
        "tests/test_stage_git_snapshot.py::test_the_doctor_asks_git_once_per_orchestration_run_per_repository",  # 5.93s
        "tests/test_stage_report_criteria.py::test_the_hook_process_reads_db_from_its_command_line",  # 3.06s
        "tests/test_stale_parked_at_rows.py::test_the_census_does_not_treat_a_comment_or_a_field_default_as_a_writer",  # 4.97s
        "tests/test_subticket_orchestration.py::test_parent_orchestration_runs_child_workflow_first",  # 5.79s
        "tests/test_suite_clock_hermeticity.py::test_no_unreviewed_absolute_bound_on_a_measured_duration",  # 6.38s
        "tests/test_suite_clock_hermeticity.py::test_the_allowlist_has_no_stale_entries",  # 6.35s
        "tests/test_symbol_map_on_this_repo.py::test_a_build_in_this_checkout_leaves_git_status_exactly_as_it_found_it",  # 17.75s
        "tests/test_symbol_map_on_this_repo.py::test_a_refresh_of_this_checkout_leaves_git_status_exactly_as_it_found_it",  # 11.90s
        "tests/test_symbol_map_on_this_repo.py::test_a_refreshed_checkout_reads_back_as_fresh_at_its_real_lines",  # 12.53s
        "tests/test_symbol_map_on_this_repo.py::test_a_symbol_this_suite_can_name_is_findable_at_its_real_line",  # 12.61s
        "tests/test_symbol_map_on_this_repo.py::test_the_build_over_this_checkout_reproduces_the_recorded_measurement",  # 11.04s
        "tests/test_symbol_map_on_this_repo.py::test_the_stored_rows_agree_with_the_report_on_this_checkouts_scale",  # 12.03s
        "tests/test_symbol_map_on_this_repo.py::test_the_three_refresh_latencies_on_this_checkout_stay_inside_their_budgets",  # 25.77s
        "tests/test_ticket_list_paging.py::test_paged_listing_matches_the_unpaged_order",  # 4.25s
        "tests/test_ticket_size_guard.py::test_every_ticket_content_writer_is_a_guarded_seam_or_a_named_exception",  # 5.20s
        "tests/test_ticket_size_guard.py::test_the_census_scanner_over_reports_nothing_on_the_tree_it_scans",  # 6.16s
        "tests/test_ticket_size_guard.py::test_the_prod_shape_scenario_carries_a_ticket_at_each_cap_boundary",  # 6.85s
        "tests/test_ticket_tree_filtering.py::TestMatchingTicketsStayAnchored::test_matching_ticket_keeps_its_real_position",  # 3.08s
        "tests/test_ticket_tree_filtering.py::test_ancestor_filter_on_a_leaf_is_empty",  # 3.15s
        "tests/test_ticket_workflow_template.py::test_clear_workflow_template_from_ticket",  # 4.82s
        "tests/test_ticket_workspace_one_answer.py::test_every_receiver_resolves_to_a_class_the_package_declares",  # 9.89s
        "tests/test_ticket_workspace_one_answer.py::test_every_workspace_lookup_receiver_resolves_to_a_class",  # 13.70s
        "tests/test_ticket_workspace_one_answer.py::test_the_detector_scans_the_whole_package",  # 13.48s
        "tests/test_toolchain_links.py::test_every_reader_of_a_ticket_tree_passes_over_loregardens_own_files",  # 3.65s
        "tests/test_unpublished_trees.py::test_a_landing_nothing_published_is_found_and_published",  # 3.10s
        "tests/test_unresolved_ledger.py::test_rework_pause_empty_approve_keeps_names_resets_budget_sets_pending",  # 4.72s
        "tests/test_unresolved_ledger.py::test_rework_pause_with_no_surviving_names_is_one_click_done",  # 5.28s
        "tests/test_unresolved_ledger.py::test_visible_unions_persisted_and_live_and_does_not_write",  # 4.78s
        "tests/test_unresolved_ledger.py::test_waive_refuses_empty_and_whitespace_reason[ \\t\\n ]",  # 4.83s
        "tests/test_views_api.py::test_a_malformed_viewport_is_refused_and_stores_nothing[pan-past-the-canvas]",  # 3.17s
        "tests/test_views_api.py::test_a_reorder_naming_an_unknown_entry_is_rejected",  # 3.04s
        "tests/test_views_api.py::test_a_split_chain_within_the_bound_is_accepted",  # 3.29s
        "tests/test_views_api.py::test_an_unknown_view_kind_is_rejected",  # 3.05s
        "tests/test_views_api.py::test_creating_a_view_with_a_non_finite_setting_is_refused[nested]",  # 3.19s
        "tests/test_views_api.py::test_creating_views_concurrently_gives_each_its_own_rank",  # 3.34s
        "tests/test_work_landed.py::test_no_surface_asks_the_question_any_other_way",  # 4.75s
        "tests/test_workflow_any_ticket_adversarial.py::TestAssumptionChecks::test_next_agent_matches_current_stage_agent",  # 3.05s
        "tests/test_workflow_any_ticket_adversarial.py::TestCombinatorialInputs::test_deeply_nested_hierarchy_all_get_workflows",  # 3.12s
        "tests/test_workflow_any_ticket_adversarial.py::TestNullAndEmptyValues::test_capability_workflow_stages_not_empty_array",  # 3.11s
        "tests/test_workflow_gate_reject_reroute.py::test_approving_playtest_gate_still_marks_stage_done",  # 4.17s
        "tests/test_workflow_monitor_autofix.py::test_no_autofix_path_dispatches_an_agent_run",  # 3.11s
        "tests/test_workflow_routing.py::test_no_ui_work_skips_only_when_a_plan_names_no_ui_path[no plan yet]",  # 3.24s
        "tests/test_workflow_state.py::test_seeded_ticket_states_agree",  # 4.98s
        "tests/test_workflow_state.py::test_stage_wont_do_counts_toward_required_completion",  # 4.76s
        "tests/test_workspace_paths.py::test_init_db_cli_creates_seeded_database",  # 5.24s
        "tests/test_workspace_paths.py::test_init_db_empty_skips_seed",  # 3.14s
        "tests/test_worktree_gate_adversarial.py::test_no_module_reaches_the_destructive_half_under_another_name[release_directory]",  # 4.26s
        "tests/test_worktree_gate_adversarial.py::test_no_module_reaches_the_destructive_half_under_another_name[remove_checkout]",  # 4.65s
        "tests/test_worktree_gate_chokepoint.py::test_only_worktree_lifecycle_calls_the_destructive_half[remove_checkout]",  # 3.73s
        "tests/test_worktree_lifecycle.py::test_every_writer_of_parked_at_sets_it_only_from_an_unrestored_release",  # 4.33s
    }
)
