"""Chat mode: every advisory cause, and the promise that mode and turn agree.

The bug this replaces was not a wrong answer — it was two answers. The snapshot
said a rail could act; the turn resolved advisory from gates the snapshot never
saw. So the parity tests here matter more than the enumeration ones.
"""

from __future__ import annotations

from loregarden.models.domain.enums import ChatAdvisoryCause, ChatMode
from loregarden.services.chat_mode import aside_mode, resolve_chat_mode

#: Adapters with neither a bridge nor a writable oneshot. See adapter_capabilities.
ADVISORY_ADAPTERS = ["local", "an-adapter-that-does-not-exist"]
ACTING_ADAPTERS = ["claude", "cursor", "codex", "lmstudio"]


class TestEveryCauseIsReachable:
    def test_execute_capable_adapters_can_act(self):
        for adapter in ACTING_ADAPTERS:
            assert resolve_chat_mode(adapter).mode is ChatMode.ACT, adapter

    def test_adapters_without_an_execute_path_are_advisory(self):
        for adapter in ADVISORY_ADAPTERS:
            mode = resolve_chat_mode(adapter)
            assert mode.mode is ChatMode.ADVISORY, adapter
            assert mode.cause is ChatAdvisoryCause.ADAPTER_CANNOT_EXECUTE

    def test_an_adapter_that_only_needs_bypass_is_told_so(self, monkeypatch):
        """opencode writes during stage runs, so "cannot execute" would be a
        lie that sends the operator to replace a working tool."""
        monkeypatch.delenv("LOREGARDEN_ALLOW_PERMISSION_BYPASS", raising=False)
        mode = resolve_chat_mode("opencode")
        assert mode.cause is ChatAdvisoryCause.ADAPTER_NEEDS_PERMISSION_BYPASS
        assert mode.remediable

    def test_that_adapter_acts_once_bypass_is_on(self, monkeypatch):
        monkeypatch.setenv("LOREGARDEN_ALLOW_PERMISSION_BYPASS", "1")
        assert resolve_chat_mode("opencode").mode is ChatMode.ACT

    def test_a_branch_with_no_worktree_is_advisory(self):
        mode = resolve_chat_mode("claude", branch_checked_out=False)
        assert mode.cause is ChatAdvisoryCause.BRANCH_NOT_CHECKED_OUT

    def test_a_bridge_turn_without_a_run_is_advisory(self):
        mode = resolve_chat_mode("claude", has_run_for_approvals=False)
        assert mode.cause is ChatAdvisoryCause.NO_RUN_FOR_APPROVALS

    def test_a_writable_oneshot_does_not_need_a_run(self):
        """Only the bridge attaches approvals to a run; cursor writes directly."""
        assert resolve_chat_mode("cursor", has_run_for_approvals=False).mode is ChatMode.ACT

    def test_a_read_only_surface_is_advisory_whatever_the_adapter(self):
        mode = resolve_chat_mode("claude", read_only_surface=True)
        assert mode.cause is ChatAdvisoryCause.SURFACE_IS_READ_ONLY

    def test_an_aside_is_advisory_by_design(self):
        assert aside_mode().cause is ChatAdvisoryCause.ASIDE_OBSERVER

    def test_every_cause_in_the_enum_is_produced_by_some_path(self, monkeypatch):
        """A cause nobody can reach is a cause the UI will never explain."""
        monkeypatch.delenv("LOREGARDEN_ALLOW_PERMISSION_BYPASS", raising=False)
        produced = {
            resolve_chat_mode("opencode").cause,
            resolve_chat_mode("local").cause,
            resolve_chat_mode("claude", branch_checked_out=False).cause,
            resolve_chat_mode("claude", has_run_for_approvals=False).cause,
            resolve_chat_mode("claude", read_only_surface=True).cause,
            aside_mode().cause,
        }
        assert produced == set(ChatAdvisoryCause)


class TestTheOperatorIsToldSomethingUseful:
    def test_acting_carries_no_cause_and_no_prose(self):
        mode = resolve_chat_mode("claude")
        assert mode.cause is None
        assert mode.reason == ""
        assert mode.advice == ""

    def test_every_advisory_cause_carries_a_reason_and_a_remedy(self):
        for mode in (
            resolve_chat_mode("local"),
            resolve_chat_mode("claude", branch_checked_out=False),
            resolve_chat_mode("claude", has_run_for_approvals=False),
            resolve_chat_mode("claude", read_only_surface=True),
            aside_mode(),
        ):
            assert mode.reason, mode.cause
            assert mode.advice, mode.cause

    def test_only_causes_an_operator_can_clear_are_marked_remediable(self):
        """A fix button that cannot fix is worse than no button."""
        assert resolve_chat_mode("local").remediable
        assert resolve_chat_mode("claude", branch_checked_out=False).remediable
        assert not resolve_chat_mode("claude", has_run_for_approvals=False).remediable
        assert not resolve_chat_mode("claude", read_only_surface=True).remediable
        assert not aside_mode().remediable

    def test_the_most_fundamental_block_is_reported_first(self):
        """An unusable adapter is named ahead of a missing worktree: fixing the
        checkout first would leave the operator still unable to act."""
        mode = resolve_chat_mode("local", branch_checked_out=False)
        assert mode.cause is ChatAdvisoryCause.ADAPTER_CANNOT_EXECUTE


class TestSerialisation:
    def test_as_dict_is_json_shaped(self):
        payload = resolve_chat_mode("local").as_dict()
        assert payload["mode"] == "advisory"
        assert payload["cause"] == "adapter_cannot_execute"
        assert payload["remediable"] is True
        assert isinstance(payload["reason"], str)

    def test_acting_serialises_a_null_cause_not_an_empty_string(self):
        assert resolve_chat_mode("claude").as_dict()["cause"] is None
