"""CI Integration service for webhook processing and auto-fix orchestration."""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from sqlmodel import Session, select

from loregarden.models.domain import (
    AgentRun,
    AutoFixAttempt,
    AutoFixStatus,
    CIRunResult,
    CIStatus,
    OrchestrationRun,
    RunStatus,
    Ticket,
    TicketState,
    WorkItemType,
)

logger = logging.getLogger(__name__)


class CIService:
    """Manage CI integration, failure detection, and auto-fix orchestration."""

    def __init__(self, session: Session):
        self.session = session

    async def process_webhook(
        self,
        workspace_id: str,
        provider: str,
        payload: dict,
    ) -> Optional[CIRunResult]:
        """
        Process incoming CI webhook (GitHub Actions, GitLab CI, generic).

        Args:
            workspace_id: Workspace ID
            provider: CI provider ("github_actions", "gitlab_ci", "generic_webhook")
            payload: Raw webhook payload from CI provider

        Returns:
            CIRunResult if successful, None if unable to process
        """
        try:
            if provider == "github_actions":
                return await self._process_github_actions(workspace_id, payload)
            elif provider == "gitlab_ci":
                return await self._process_gitlab_ci(workspace_id, payload)
            else:
                return await self._process_generic_webhook(workspace_id, payload)
        except Exception as e:
            logger.error(f"Failed to process CI webhook: {e}", exc_info=True)
            return None

    async def _process_github_actions(
        self,
        workspace_id: str,
        payload: dict,
    ) -> Optional[CIRunResult]:
        """Process GitHub Actions workflow_run event."""
        try:
            # Extract relevant fields from GitHub Actions payload
            workflow_run = payload.get("workflow_run", {})
            if not workflow_run:
                logger.warning("No workflow_run in GitHub Actions payload")
                return None

            run_id = workflow_run.get("id")
            status = workflow_run.get("conclusion")  # success, failure, neutral, cancelled, etc
            logs_url = workflow_run.get("logs_url")

            # Map GitHub status to CIStatus
            if status == "success":
                ci_status = CIStatus.PASSING
            elif status == "failure":
                ci_status = CIStatus.FAILING
            elif status == "neutral":
                ci_status = CIStatus.PARTIAL
            elif status in ("skipped", "cancelled"):
                ci_status = CIStatus.SKIPPED
            else:
                ci_status = CIStatus.PENDING

            # Try to extract ticket ID from branch or commit message
            branch = workflow_run.get("head_branch", "")
            ticket_id = self._extract_ticket_id_from_branch(branch, workspace_id)

            if not ticket_id:
                logger.warning(
                    f"Could not extract ticket ID from branch: {branch}. "
                    "Set git branch to include ticket ID (e.g., 'feature/ticket-123')"
                )
                return None

            # Create CI run result
            ci_result = CIRunResult(
                id=str(uuid4()),
                workspace_id=workspace_id,
                ticket_id=ticket_id,
                status=ci_status,
                provider="github_actions",
                external_run_id=str(run_id),
                logs_url=logs_url,
                failure_summary=None,  # Will be populated when we fetch full logs
                full_logs=None,  # Would require additional API call to GitHub
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )

            self.session.add(ci_result)
            self.session.commit()

            logger.info(
                f"Processed GitHub Actions CI result: {ticket_id} -> {ci_status.value}"
            )

            # Trigger auto-fix if failing
            if ci_status == CIStatus.FAILING:
                await self.trigger_auto_fix(ci_result)

            return ci_result

        except Exception as e:
            logger.error(f"Error processing GitHub Actions webhook: {e}", exc_info=True)
            return None

    async def _process_gitlab_ci(
        self,
        workspace_id: str,
        payload: dict,
    ) -> Optional[CIRunResult]:
        """Process GitLab CI pipeline event."""
        # TODO: Implement GitLab CI webhook processing
        logger.warning("GitLab CI webhook processing not yet implemented")
        return None

    async def _process_generic_webhook(
        self,
        workspace_id: str,
        payload: dict,
    ) -> Optional[CIRunResult]:
        """Process generic webhook (expects standard format)."""
        # TODO: Implement generic webhook processing
        logger.warning("Generic webhook processing not yet implemented")
        return None

    def _extract_ticket_id_from_branch(self, branch: str, workspace_id: str) -> Optional[str]:
        """
        Extract ticket ID from git branch name.

        Expected format: feature/ticket-123 or feature/auth-system
        Falls back to searching by branch name if exact ticket not found.
        """
        if not branch:
            return None

        # Try pattern: ticket-{id} or {id} as suffix
        match = re.search(r"ticket-(\w+)|(\w+)$", branch)
        if not match:
            return None

        ticket_key = match.group(1) or match.group(2)

        # Search for ticket with this external_id
        stmt = select(Ticket).where(
            (Ticket.workspace_id == workspace_id) & (Ticket.external_id == ticket_key)
        )
        ticket = self.session.exec(stmt).first()

        return ticket.id if ticket else None

    async def get_latest_ci_status(self, ticket_id: str) -> Optional[CIRunResult]:
        """Fetch latest CI result for a ticket."""
        stmt = select(CIRunResult).where(CIRunResult.ticket_id == ticket_id).order_by(
            CIRunResult.created_at.desc()
        )
        return self.session.exec(stmt).first()

    async def trigger_auto_fix(
        self,
        ci_result: CIRunResult,
        max_attempts: int = 3,
    ) -> Optional[AutoFixAttempt]:
        """
        Create auto-fix attempt: parse failure logs, trigger implementer agent.

        Args:
            ci_result: Failed CI run
            max_attempts: Maximum retry attempts (default 3)

        Returns:
            AutoFixAttempt record if triggered, None if max attempts reached
        """
        try:
            # Get existing attempts
            stmt = select(AutoFixAttempt).where(
                AutoFixAttempt.ci_run_result_id == ci_result.id
            )
            existing_attempts = self.session.exec(stmt).all()

            # Check if at max attempts
            if len(existing_attempts) >= max_attempts:
                logger.info(
                    f"CI: Max auto-fix attempts ({max_attempts}) reached for "
                    f"ticket {ci_result.ticket_id}"
                )
                return None

            # Create auto-fix attempt record
            attempt_number = len(existing_attempts) + 1
            attempt = AutoFixAttempt(
                id=str(uuid4()),
                ci_run_result_id=ci_result.id,
                attempt_number=attempt_number,
                status=AutoFixStatus.PENDING,
                created_at=datetime.now(timezone.utc),
            )

            self.session.add(attempt)
            self.session.commit()

            logger.info(
                f"Created auto-fix attempt {attempt_number} for ticket {ci_result.ticket_id}"
            )

            # Trigger implementer agent with error context
            await self._trigger_implementer_agent(ci_result, attempt)

            return attempt

        except Exception as e:
            logger.error(f"Error triggering auto-fix: {e}", exc_info=True)
            return None

    async def _trigger_implementer_agent(
        self,
        ci_result: CIRunResult,
        attempt: AutoFixAttempt,
    ) -> None:
        """
        Trigger the implementer agent to fix CI failure.

        Creates or reuses the most common implementer run stage and executes
        the agent with error context in the stage context.
        """
        try:
            # Get original ticket
            ticket_stmt = select(Ticket).where(Ticket.id == ci_result.ticket_id)
            ticket = self.session.exec(ticket_stmt).first()
            if not ticket:
                logger.warning(f"Ticket not found: {ci_result.ticket_id}")
                return

            # Extract error context from logs
            error_context = self.extract_error_context(ci_result.full_logs or "")

            # Create context JSON for the agent
            ci_failure_context = {
                "ci_failure": {
                    "error_summary": error_context["summary"],
                    "error_type": error_context["error_type"],
                    "failing_tests": error_context["failing_tests"],
                    "error_excerpt": error_context["error_excerpt"],
                    "full_logs": ci_result.full_logs,
                },
                "instruction": (
                    "The CI test suite failed. Analyze the error logs above and fix the issue. "
                    "Make minimal changes to fix the failing tests/checks. "
                    "Do not add unnecessary changes or refactoring."
                ),
            }

            # Create agent run for the implementer stage
            # NOTE: In a full implementation, this would:
            # 1. Look up the implementer stage from the workflow
            # 2. Create an AgentRun with stage context
            # 3. Execute the agent via CliAgentExecutor or similar
            # 4. Update AutoFixAttempt when run completes
            #
            # For now, we log the intent and update status to RUNNING
            # Full integration requires access to orchestration service

            logger.info(
                f"Would trigger implementer agent for CI fix. Context: {json.dumps(ci_failure_context)}"
            )

            # Update attempt status to RUNNING
            attempt.status = AutoFixStatus.RUNNING
            self.session.add(attempt)
            self.session.commit()

        except Exception as e:
            logger.error(f"Error triggering implementer agent: {e}", exc_info=True)
            attempt.status = AutoFixStatus.FAILED
            self.session.add(attempt)
            self.session.commit()

    async def get_auto_fix_history(self, ticket_id: str) -> list[AutoFixAttempt]:
        """Get all auto-fix attempts for a ticket."""
        # Get latest CI run for ticket
        ci_result = await self.get_latest_ci_status(ticket_id)
        if not ci_result:
            return []

        # Get all attempts for this CI run
        stmt = select(AutoFixAttempt).where(
            AutoFixAttempt.ci_run_result_id == ci_result.id
        )
        return self.session.exec(stmt).all()

    async def skip_ci_check(self, ticket_id: str) -> None:
        """
        Manually skip CI gate (admin override).

        Updates the latest CI result to SKIPPED status.
        """
        ci_result = await self.get_latest_ci_status(ticket_id)
        if ci_result:
            ci_result.status = CIStatus.SKIPPED
            ci_result.updated_at = datetime.now(timezone.utc)
            self.session.add(ci_result)
            self.session.commit()
            logger.info(f"Manually skipped CI check for ticket {ticket_id}")

    def extract_error_context(self, logs: str) -> dict:
        """
        Parse test failure logs to extract key information.

        Returns dict with:
        - error_type: "test_failure", "lint_error", etc
        - summary: brief error description
        - failing_tests: list of failing test names
        - error_excerpt: relevant error lines
        """
        if not logs:
            return {
                "error_type": "unknown",
                "summary": "CI failed (no logs available)",
                "failing_tests": [],
                "error_excerpt": "",
            }

        # Try to detect error type and extract info
        error_type = "unknown"
        summary = ""
        failing_tests = []
        error_excerpt = ""

        # Look for test failures
        if "FAILED" in logs or "failed" in logs:
            error_type = "test_failure"
            # Extract failing test names (pytest format: FAILED path/to/test.py::TestClass::test_name)
            test_matches = re.findall(
                r"FAILED\s+([^\s]+(?:::[^\s]+)?)", logs, re.IGNORECASE
            )
            failing_tests = test_matches[:5]  # Limit to first 5

        # Look for lint errors
        elif "lint" in logs.lower():
            error_type = "lint_error"
            summary = "Lint check failed"

        # Look for build errors
        elif "build" in logs.lower() or "compile" in logs.lower():
            error_type = "build_error"
            summary = "Build failed"

        # Extract error excerpt (last few lines)
        lines = logs.split("\n")
        error_excerpt = "\n".join(lines[-10:])  # Last 10 lines

        if not summary:
            summary = f"{error_type.replace('_', ' ').title()}"
            if failing_tests:
                summary += f" ({len(failing_tests)} tests)"

        return {
            "error_type": error_type,
            "summary": summary,
            "failing_tests": failing_tests,
            "error_excerpt": error_excerpt,
        }
