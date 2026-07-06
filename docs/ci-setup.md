# CI Integration Setup Guide

Loregarden can automatically detect CI failures and trigger agent-driven fixes. This guide shows how to set up CI webhooks for GitHub Actions.

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
- Loregarden running with CI integration enabled (`LOREGARDEN_CI_ENABLED=true`)
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

Loregarden extracts ticket IDs from your git branch names. Use this format:

```
feature/ticket-TICKET_ID
feature/ticket-auth-123
bugfix/ticket-payment-456
```

Or simpler (uses last component of branch):

```
feature/auth-system  # Loregarden will search for ticket with external_id "auth-system"
feature/payment-flow
```

**In GitHub**: When you create a branch for a feature, match the ticket ID:
```bash
git checkout -b feature/ticket-auth-123
```

**In Loregarden**: Ensure your ticket's `external_id` matches:
- Ticket ID: `auth-123`
- Branch: `feature/ticket-auth-123`

### 4. Configure Workflow to Trigger Webhook

Add a step at the END of your workflow to trigger the webhook:

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
            -H "X-Hub-Signature-256: ${{ secrets.WEBHOOK_SECRET }}" \
            -H "Content-Type: application/json" \
            -d '{
              "workflow_run": {
                "id": ${{ github.run_id }},
                "conclusion": "${{ job.status }}",
                "logs_url": "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}/logs",
                "head_branch": "${{ github.head_ref }}"
              }
            }' \
            https://your-loregarden-domain/api/ci/webhook/your-workspace-id
```

**Note**: Replace:
- `your-loregarden-domain`: Your Loregarden server domain/IP
- `your-workspace-id`: Your workspace ID (found in Loregarden settings or URL)

### Alternative: Use GitHub Actions App (Simpler)

If you prefer, GitHub can automatically send workflow_run events when you configure the webhook. Just ensure:
1. Webhook is configured (step 2)
2. Event type is set to "Workflow runs"
3. GitHub will automatically send `workflow_run` event on completion

You don't need the manual curl step in that case.

## Configuration Reference

Set these environment variables to customize CI integration:

```bash
# Enable/disable CI integration (default: true)
LOREGARDEN_CI_ENABLED=true

# GitHub webhook secret (for signature verification)
LOREGARDEN_CI_WEBHOOK_SECRET=your-secret-here

# Max auto-fix retry attempts (default: 3)
LOREGARDEN_CI_RETRY_LIMIT=3

# Timeout for fix agent (seconds, default: 600 = 10 min)
LOREGARDEN_CI_AUTO_FIX_TIMEOUT=600

# How long to keep CI logs (days, default: 30)
LOREGARDEN_CI_LOG_RETENTION_DAYS=30
```

## How It Works

### When CI Passes

1. GitHub webhook → Loregarden
2. Status recorded as `passing`
3. Dashboard shows ✓ (green checkmark)
4. Approval gate unblocks (no longer waits for CI)

### When CI Fails

1. GitHub webhook → Loregarden detects `failing`
2. Dashboard shows ✗ (red X) + failure summary
3. **Auto-fix triggered** (if enabled):
   - Error logs extracted (test names, error messages)
   - Child "fix-it" work item created
   - Implementer agent runs with error context in prompt
   - Agent analyzes logs and attempts fix
4. Fixed code committed
5. CI re-runs automatically
6. If passing, approval gate unblocks
7. If still failing, approval gate blocks + shows error + offers manual retry

### Manual Overrides

**Retry Auto-Fix**: If auto-fix failed, click "Retry Auto-Fix" to trigger another attempt

**Skip CI Check**: Admin can click "Skip CI Check" to bypass CI gate (not recommended)

## Troubleshooting

### Webhook Not Firing

1. Check GitHub repository Settings → Webhooks
2. Verify URL is correct: `https://domain/api/ci/webhook/workspace-id`
3. Check webhook delivery history (GitHub shows recent deliveries)
4. If `Recent Deliveries` is empty, try re-delivery from GitHub UI

### Ticket Not Found

Error: "Could not extract ticket ID from branch"

**Solution**: Ensure branch name matches ticket external_id:
- Branch: `feature/ticket-auth-123`
- Ticket external_id: `auth-123` (or full branch suffix works too)

### Signature Verification Failed

Error: "Invalid webhook signature"

**Solution**:
1. Verify webhook secret matches `LOREGARDEN_CI_WEBHOOK_SECRET`
2. If secret was changed, update GitHub webhook settings
3. Or disable verification by leaving `LOREGARDEN_CI_WEBHOOK_SECRET` empty (not recommended)

### CI Status Not Showing in Dashboard

1. Check logs for errors: `docker logs loregarden-api | grep CI`
2. Verify webhook was received: check GitHub webhook delivery history
3. Verify ticket was found (check browser console for errors)
4. Refresh dashboard (hard refresh: Ctrl+Shift+R)

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
                "conclusion": "${{ job.status }}",
                "logs_url": "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}/logs",
                "head_branch": "${{ github.head_ref || github.ref_name }}"
              }
            }' \
            "${{ secrets.LOREGARDEN_CI_WEBHOOK_URL }}"
        env:
          LOREGARDEN_CI_WEBHOOK_URL: https://your-loregarden.com/api/ci/webhook/your-workspace-id
```

## Next Steps

1. ✅ Configure webhook in GitHub
2. ✅ Set `LOREGARDEN_CI_WEBHOOK_SECRET` in your `.env`
3. ✅ Link a test feature to a ticket with matching branch
4. ✅ Push a commit that breaks a test
5. ✅ Verify webhook fires and auto-fix triggers
6. ✅ Check dashboard for CI status + auto-fix attempt

## Support

- Check Loregarden logs for CI service errors
- Enable debug logging: `LOG_LEVEL=DEBUG`
- Review webhook deliveries in GitHub repository settings
- Report issues with detailed logs and webhook payload
