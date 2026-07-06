"""CI integration API endpoints."""

import hashlib
import hmac
import json
import logging
from typing import Optional

from fastapi import APIRouter, Request, Header, HTTPException, status
from sqlmodel import Session

from loregarden.config import settings
from loregarden.db.session import get_session
from loregarden.models.domain import CIRunResult, AutoFixAttempt, AutoFixStatus
from loregarden.services.ci_service import CIService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ci", tags=["ci"])


def _verify_github_signature(
    payload_bytes: bytes,
    signature_header: Optional[str],
) -> bool:
    """Verify GitHub webhook HMAC signature."""
    if not settings.LOREGARDEN_CI_WEBHOOK_SECRET:
        logger.warning("GitHub webhook secret not configured, skipping signature verification")
        return True

    if not signature_header:
        return False

    # GitHub sends: X-Hub-Signature-256: sha256=<signature>
    try:
        algo, expected_sig = signature_header.split("=", 1)
        if algo != "sha256":
            return False

        computed_sig = hmac.new(
            settings.LOREGARDEN_CI_WEBHOOK_SECRET.encode(),
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(computed_sig, expected_sig)
    except Exception as e:
        logger.error(f"Error verifying GitHub signature: {e}")
        return False


@router.post("/webhook/{workspace_id}")
async def receive_ci_webhook(
    workspace_id: str,
    request: Request,
    x_github_event: Optional[str] = Header(None),
    x_github_signature_256: Optional[str] = Header(None),
    x_gitlab_event: Optional[str] = Header(None),
    session: Session = next(get_session()),
):
    """
    Receive CI results from GitHub Actions, GitLab CI, or generic webhook.

    Endpoints:
    - GitHub Actions: POST /ci/webhook/{workspace_id}
      Headers: X-GitHub-Event, X-Hub-Signature-256
      Body: workflow_run event

    - GitLab CI: POST /ci/webhook/{workspace_id}
      Headers: X-Gitlab-Event
      Body: pipeline event

    - Generic: POST /ci/webhook/{workspace_id}
      Body: JSON with status, ticket_id, logs, etc
    """
    try:
        # Read request body
        body = await request.body()

        # Detect provider and verify signature
        if x_github_event:
            # Verify GitHub signature
            if not _verify_github_signature(body, x_github_signature_256):
                logger.warning("GitHub webhook signature verification failed")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Invalid webhook signature",
                )

            # Only process workflow_run events
            if x_github_event != "workflow_run":
                logger.debug(f"Ignoring GitHub event type: {x_github_event}")
                return {"status": "ignored", "reason": f"Event type not supported: {x_github_event}"}

            provider = "github_actions"
            payload = json.loads(body)

        elif x_gitlab_event:
            provider = "gitlab_ci"
            payload = json.loads(body)

        else:
            provider = "generic_webhook"
            payload = json.loads(body)

        # Process webhook
        ci_service = CIService(session)
        ci_result = await ci_service.process_webhook(workspace_id, provider, payload)

        if ci_result:
            return {
                "status": "ok",
                "ci_result_id": ci_result.id,
                "ticket_id": ci_result.ticket_id,
                "ci_status": ci_result.status.value,
            }
        else:
            logger.warning(f"Failed to process {provider} webhook for workspace {workspace_id}")
            return {
                "status": "error",
                "reason": "Failed to process webhook",
            }

    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in webhook body: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        )
    except Exception as e:
        logger.error(f"Error processing CI webhook: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error processing webhook",
        )


@router.get("/status/{ticket_id}")
async def get_ci_status(
    ticket_id: str,
    session: Session = next(get_session()),
):
    """
    Get latest CI status and auto-fix history for a ticket.

    Returns:
    {
        "ci_status": {
            "id": "...",
            "status": "passing|failing|pending|partial|skipped",
            "provider": "github_actions|gitlab_ci|...",
            "logs_url": "...",
            "failure_summary": "...",
            "created_at": "...",
        },
        "auto_fix_history": [
            {
                "id": "...",
                "attempt_number": 1,
                "status": "pending|running|succeeded|failed",
                "created_at": "...",
            },
            ...
        ]
    }
    """
    try:
        ci_service = CIService(session)
        ci_result = await ci_service.get_latest_ci_status(ticket_id)
        auto_fix_history = await ci_service.get_auto_fix_history(ticket_id)

        return {
            "ci_status": ci_result.dict() if ci_result else None,
            "auto_fix_history": [attempt.dict() for attempt in auto_fix_history],
        }

    except Exception as e:
        logger.error(f"Error fetching CI status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error fetching CI status",
        )


@router.post("/manual-override/{ticket_id}")
async def skip_ci_check(
    ticket_id: str,
    session: Session = next(get_session()),
):
    """
    Admin: Skip CI gate and proceed to approval.

    Marks the latest CI result as SKIPPED.
    """
    try:
        ci_service = CIService(session)
        await ci_service.skip_ci_check(ticket_id)

        return {
            "status": "ok",
            "message": f"CI check skipped for ticket {ticket_id}",
        }

    except Exception as e:
        logger.error(f"Error skipping CI check: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error skipping CI check",
        )


@router.post("/trigger-auto-fix/{ticket_id}")
async def trigger_manual_auto_fix(
    ticket_id: str,
    session: Session = next(get_session()),
):
    """
    Manually trigger auto-fix for a failing CI.

    Useful if auto-fix didn't trigger automatically.
    """
    try:
        ci_service = CIService(session)
        ci_result = await ci_service.get_latest_ci_status(ticket_id)

        if not ci_result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No CI result found for ticket",
            )

        attempt = await ci_service.trigger_auto_fix(ci_result)

        if attempt:
            return {
                "status": "ok",
                "attempt_id": attempt.id,
                "attempt_number": attempt.attempt_number,
                "message": f"Auto-fix attempt {attempt.attempt_number} triggered",
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Max auto-fix attempts reached",
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error triggering auto-fix: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error triggering auto-fix",
        )
