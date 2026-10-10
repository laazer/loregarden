"""Verify the HMAC signature GitHub puts on a webhook delivery.

Shared by every endpoint GitHub posts to — CI runs and issue events — so the
check, and its behaviour when no secret is configured, cannot drift apart.
"""

import hashlib
import hmac
import logging

from loregarden.config import settings

logger = logging.getLogger(__name__)


def verify_github_signature(
    payload_bytes: bytes,
    signature_header: str | None,
    *,
    require_secret: bool = False,
) -> bool:
    """Verify GitHub webhook HMAC signature.

    With no secret configured the check is skipped — unless `require_secret`,
    for an endpoint whose payload is written into tickets, where an unsigned
    delivery is refused instead.
    """
    secret = settings.ci_webhook_secret
    if not secret:
        if require_secret:
            logger.warning("GitHub webhook refused: LOREGARDEN_CI_WEBHOOK_SECRET is not set")
            return False
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
            secret.encode(),
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(computed_sig, expected_sig)
    except (ValueError, TypeError):
        # A malformed header is attacker-controlled input: reject it, but say so.
        logger.warning("Malformed GitHub webhook signature header", exc_info=True)
        return False
