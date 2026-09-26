"""The signature check every GitHub webhook endpoint shares.

It used to read a setting that does not exist and a header GitHub never sends,
so a signed delivery to the CI webhook could not pass.
"""

import hashlib
import hmac
from unittest.mock import patch

from loregarden.config import settings

BODY = b'{"zen": "Keep it logically awesome."}'


def _sign(secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), BODY, hashlib.sha256).hexdigest()


def _post(client, signature: str):
    return client.post(
        "/api/ci/webhook/any-workspace",
        content=BODY,
        headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": signature},
    )


def test_ci_webhook_accepts_a_correctly_signed_delivery(client):
    with patch.object(settings, "ci_webhook_secret", "s3cret"):
        res = _post(client, _sign("s3cret"))

    assert res.status_code == 200, res.text
    assert res.json()["status"] == "ignored"


def test_ci_webhook_refuses_a_wrongly_signed_delivery(client):
    with patch.object(settings, "ci_webhook_secret", "s3cret"):
        res = _post(client, _sign("not-the-secret"))

    assert res.status_code == 403
