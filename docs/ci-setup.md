# CI Integration Setup Guide

Loregarden can receive CI results from GitHub Actions and record them against tickets. This guide shows how to set up the webhook.

> **Status (2026-10-12).** Only the first part of the flow below is wired. `POST /api/ci/webhook/{workspace_id}` records a `CIRunResult` for a GitHub `workflow_run` delivery, and a `failure` conclusion creates an `AutoFixAttempt`. No agent runs after that. `CIService._trigger_implementer_agent` logs the context it would pass and marks the attempt `running`. Nothing fetches the CI logs (`full_logs` is never filled), and nothing is committed. GitLab and generic payloads are not processed; the endpoint answers them with an error. `CIStatusWidget` is not mounted on any page, and no approval gate reads CI status. Of the settings under *Configuration Reference*, only `LOREGARDEN_CI_WEBHOOK_SECRET` is read. Steps 3 to 7 of the overview are the intended design.

## Overview

When a test fails in CI:
1. GitHub Actions webhook sends result to Loregarden
2. Loregarden detects the failure
3. Auto-fix agent is triggered to analyze logs and fix the issue
4. Agent creates a new run with error context
5. Fixed code is committed
6. CI re-runs and (hopefully) passes
7. Approval gate unblocks

## Prerequisites

- GitHub Actions workflows configured (standard setup)
- Loregarden running and reachable from GitHub. The server binds to `127.0.0.1` by default. With `LOREGARDEN_API_TOKEN` set, the webhook path is not exempt from the token check, so a GitHub delivery (which carries no token) gets a 401.
- Repository with feature branches linked to tickets

## GitHub Actions Setup

### 1. Create Webhook Secret (Optional but Recommended)

Generate a random secret for webhook signature verification:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
# Example output: a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0u1v2w3x4y5z
```

Set this as an environment variable in your Loregarden `.env`:

```bash
LOREGARDEN_CI_WEBHOOK_SECRET=a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0u1v2w3x4y5z
```

### 2. Add Repository Webhook in GitHub

1. Go to your repository → **Settings → Webhooks → Add webhook**
2. Configure:
   - **Payload URL**: `https://your-loregarden-domain/api/ci/webhook/your-workspace-id`
   - **Content type**: `application/json`
   - **Secret**: (paste the secret from step 1, or leave blank to disable signature verification)
   - **Events**: Select `Workflow runs` (or manually select: workflow_run)
3. Click **Add webhook**

### 3. Link Tickets to Git Branches

Loregarden extracts ticket IDs from your git branch names. A `ticket-` prefix followed by the ticket's number works:

```
feature/ticket-142
bugfix/ticket-456
```

The part after `ticket-` is read up to the first hyphen, so `ticket-auth-123` looks for a ticket called `auth` and finds nothing.

Or simpler (uses the last component of the branch), which is what the ticket's own
default branch does:

```
queue-execution/lor-queue-execution-142   # resolves to ticket lor-queue-execution-142
feature/auth-system                        # resolves to a ticket with that external_id
```

A ticket's id is `<workspace prefix>-<milestone code>-<number>`, and the number is
the authoritative part: `lor-anything-142` and `lor-142` both resolve to ticket
142, as does the id it carried before the restructure. So a branch cut before a
ticket moved between milestones still matches.

**In GitHub**: When you create a branch by hand, end it with the ticket id:
```bash
git checkout -b feature/lor-queue-execution-142
```

### 4. Configure Workflow to Trigger Webhook

Add a step at the END of your workflow to trigger the webhook. A delivery sent this way has no signature, so the server accepts it only while `LOREGARDEN_CI_WEBHOOK_SECRET` is empty. With a secret set, use the repository webhook from step 2 instead (see the alternative below).

```yaml
# .github/workflows/test.yml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-node@v3
        with:
          node-version: '18'
      - run: npm install
      - run: npm test
      - run: npm run lint

  # Add this job to trigger Loregarden webhook
  notify-loregarden:
    needs: test
    if: always()  # Run even if test fails
    runs-on: ubuntu-latest
    steps:
      - name: Notify Loregarden CI
        run: |
          curl -X POST \
            -H "X-Github-Event: workflow_run" \
            -H "Content-Type: application/json" \
            -d '{
              "workflow_run": {
                "id": ${{ github.run_id }},
                "conclusion": "${{ needs.test.result }}",
                "logs_url": "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}/logs",
                "head_branch": "${{ github.head_ref || github.ref_name }}"
              }
            }' \
            https://your-loregarden-domain/api/ci/webhook/your-workspace-id
```

**Note**: Replace:
- `your-loregarden-domain`: Your Loregarden server domain/IP
- `your-workspace-id`: Your workspace ID (found in Loregarden settings or URL)

### Alternative: Use GitHub Actions App (Simpler)

If you prefer, GitHub can send `workflow_run` events itself once the webhook is configured. Just make sure:
1. Webhook is configured (step 2)
2. Event type is set to "Workflow runs"
3. GitHub will automatically send `workflow_run` event on completion

You don't need the manual curl step in that case.

## Configuration Reference

These settings are declared in `server/loregarden/config.py`. Only the webhook secret is read by any code today; the other four are listed with their declared defaults.

```bash
# Enable/disable CI integration (default: true; not read yet)
LOREGARDEN_CI_ENABLED=true

# GitHub webhook secret (for signature verification)
LOREGARDEN_CI_WEBHOOK_SECRET=your-secret-here

# Max auto-fix retry attempts (default: 3; not read yet, trigger_auto_fix has its own default of 3)
LOREGARDEN_CI_RETRY_LIMIT=3

# Timeout for fix agent (seconds, default: 600 = 10 min; not read yet)
LOREGARDEN_CI_AUTO_FIX_TIMEOUT=600

# How long to keep CI logs (days, default: 30; not read yet)
LOREGARDEN_CI_LOG_RETENTION_DAYS=30
```

## How It Works

### When CI Passes

1. GitHub webhook → Loregarden
2. Status recorded as `passing`
3. `GET /api/ci/status/{ticket_id}` returns it. No dashboard page shows it yet.

### When CI Fails

1. GitHub webhook → Loregarden records `failing`
2. An `AutoFixAttempt` is created, up to 3 per CI result
3. The service builds error context from the logs (empty, since none are fetched), logs it, and marks the attempt `running`
4. No implementer run starts, so nothing is committed and the attempt stays `running`

### Manual Overrides

**Retry Auto-Fix**: `POST /api/ci/trigger-auto-fix/{ticket_id}` creates another attempt for the latest CI result, with the same limits as above

**Skip CI Check**: `POST /api/ci/manual-override/{ticket_id}` marks the latest CI result `skipped`

`CIStatusWidget` has buttons for both, but it is not mounted anywhere.

## Troubleshooting

### Webhook Not Firing

1. Check GitHub repository Settings → Webhooks
2. Verify URL is correct: `https://domain/api/ci/webhook/workspace-id`
3. Check webhook delivery history (GitHub shows recent deliveries)
4. If `Recent Deliveries` is empty, try re-delivery from GitHub UI

### Ticket Not Found

The server logs a warning, "Could not extract ticket ID from branch", and the webhook answers `{"status": "error", "reason": "Failed to process webhook"}`.

**Solution**: End the branch with the ticket's id:
- Branch: `feature/lor-queue-execution-142`
- Ticket id: `lor-queue-execution-142` (its pre-restructure id also resolves)

### Signature Verification Failed

Error: "Invalid webhook signature"

**Solution**:
1. Verify webhook secret matches `LOREGARDEN_CI_WEBHOOK_SECRET`
2. If secret was changed, update GitHub webhook settings
3. Or disable verification by leaving `LOREGARDEN_CI_WEBHOOK_SECRET` empty (not recommended)

### CI Status Not Showing in Dashboard

No page renders CI status yet (see the status note at the top). To check that a delivery was recorded:

1. Read the server's console output (the terminal running `task dev` or `task server`) for warnings from the CI service
2. Verify webhook was received: check GitHub webhook delivery history
3. Query `GET /api/ci/status/{ticket_id}` for the ticket

## Examples

### Complete GitHub Actions Workflow with CI Integration

```yaml
name: Tests & Deploy

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main, develop]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-node@v3
        with:
          node-version: '18'
      - name: Install dependencies
        run: npm ci
      - name: Run tests
        run: npm test -- --coverage
      - name: Run linter
        run: npm run lint

  # Automatically uploads test results
  upload-coverage:
    needs: test
    if: always()
    runs-on: ubuntu-latest
    steps:
      - uses: codecov/codecov-action@v3
        with:
          fail_ci_if_error: false

  # Notify Loregarden about CI results
  notify-loregarden:
    needs: test
    if: always()
    runs-on: ubuntu-latest
    steps:
      - name: Notify Loregarden CI
        run: |
          curl -s -X POST \
            -H "X-Github-Event: workflow_run" \
            -H "Content-Type: application/json" \
            -d '{
              "workflow_run": {
                "id": "${{ github.run_id }}",
                "conclusion": "${{ needs.test.result }}",
                "logs_url": "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}/logs",
                "head_branch": "${{ github.head_ref || github.ref_name }}"
              }
            }' \
            "$LOREGARDEN_CI_WEBHOOK_URL"
        env:
          LOREGARDEN_CI_WEBHOOK_URL: https://your-loregarden.com/api/ci/webhook/your-workspace-id
```

## Next Steps

1. Configure webhook in GitHub
2. Set `LOREGARDEN_CI_WEBHOOK_SECRET` in your `.env`
3. Link a test feature to a ticket with matching branch
4. Push a commit that breaks a test
5. Verify the webhook fires and a `failing` result is recorded
6. Check `GET /api/ci/status/{ticket_id}` for the result and the auto-fix attempt

## Support

- Check Loregarden logs for CI service errors
- Loregarden's own loggers print at `WARNING` and above (`server/loregarden/console_logging.py`), and no setting lowers that. `LOREGARDEN_LOG_LEVEL` sets uvicorn's level only, and only under `python -m loregarden`.
- Review webhook deliveries in GitHub repository settings
- Report issues with detailed logs and webhook payload
