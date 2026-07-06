import { test, expect } from "@playwright/test";

/**
 * End-to-end test for CI Auto-Fix flow.
 *
 * Scenario:
 * 1. Create a feature ticket
 * 2. Run implementation stage (simulated)
 * 3. Simulate CI webhook with failure
 * 4. Verify auto-fix run created
 * 5. Simulate CI webhook with success
 * 6. Verify approval gate unblocked
 */

test.describe("CI Auto-Fix Flow", () => {
  let ticketId: string;

  test.beforeEach(async ({ page }) => {
    // Navigate to dashboard
    await page.goto("/");

    // Wait for app to load
    await page.waitForSelector('[data-testid="dashboard"]', { timeout: 10000 });
  });

  test("should detect CI failure and trigger auto-fix", async ({
    page,
    request,
  }) => {
    // Step 1: Create a feature ticket
    // Click "New Ticket"
    await page.click('button:has-text("Create Ticket")');
    await page.fill('input[placeholder*="Title"]', "Implement login flow");
    await page.click('button:has-text("Create")');

    // Get ticket ID from URL or element
    ticketId = await page
      .locator('[data-testid="ticket-id"]')
      .textContent()
      .then((t) => t?.trim() || "");
    expect(ticketId).toBeTruthy();

    // Step 2: Verify CI status widget not shown initially
    const ciWidget = page.locator('[data-testid="ci-status-widget"]');
    await expect(ciWidget).not.toBeVisible();

    // Step 3: Simulate CI webhook with FAILURE
    const failurePayload = {
      workflow_run: {
        id: 123456,
        conclusion: "failure",
        logs_url: "https://github.com/org/repo/runs/123456/logs",
        head_branch: `feature/ticket-${ticketId}`,
      },
    };

    const webhookResponse = await request.post(
      `/api/ci/webhook/test-workspace`,
      {
        data: failurePayload,
        headers: {
          "X-GitHub-Event": "workflow_run",
        },
      }
    );
    expect(webhookResponse.ok()).toBeTruthy();
    const webhookData = await webhookResponse.json();
    expect(webhookData.ci_status).toBe("failing");

    // Step 4: Reload page to see CI status
    await page.reload();

    // Wait for CI status widget to appear
    const ciStatusBadge = page.locator(
      '[data-testid="ci-status-badge"].ci-failing'
    );
    await expect(ciStatusBadge).toBeVisible({ timeout: 5000 });

    // Verify CI status shows "Failing"
    await expect(ciStatusBadge).toContainText("Failing");

    // Expand CI logs panel
    await ciStatusBadge.click();
    const ciPanel = page.locator('[data-testid="ci-logs-panel"]');
    await expect(ciPanel).toBeVisible();

    // Verify auto-fix section visible
    const autoFixSection = page.locator(
      '[data-testid="ci-logs-panel"] >> [data-testid="auto-fix-section"]'
    );
    await expect(autoFixSection).toBeVisible();

    // Verify "Retry Auto-Fix" button available
    const retryBtn = autoFixSection.locator('button:has-text("Retry Auto-Fix")');
    await expect(retryBtn).toBeVisible();
    await expect(retryBtn).not.toBeDisabled();

    // Step 5: Check CI status endpoint to verify auto-fix attempt created
    const statusResponse = await request.get(`/api/ci/status/${ticketId}`);
    const statusData = await statusResponse.json();
    expect(statusData.ci_status.status).toBe("failing");
    expect(statusData.auto_fix_history.length).toBeGreaterThan(0);
    expect(statusData.auto_fix_history[0].status).toBe("pending");

    // Step 6: Simulate CI webhook with SUCCESS (auto-fix worked)
    const successPayload = {
      workflow_run: {
        id: 123457,
        conclusion: "success",
        logs_url: "https://github.com/org/repo/runs/123457/logs",
        head_branch: `feature/ticket-${ticketId}`,
      },
    };

    const successResponse = await request.post(
      `/api/ci/webhook/test-workspace`,
      {
        data: successPayload,
        headers: {
          "X-GitHub-Event": "workflow_run",
        },
      }
    );
    expect(successResponse.ok()).toBeTruthy();
    const successData = await successResponse.json();
    expect(successData.ci_status).toBe("passing");

    // Step 7: Reload and verify CI status now shows passing
    await page.reload();

    const ciPassingBadge = page.locator(
      '[data-testid="ci-status-badge"].ci-passing'
    );
    await expect(ciPassingBadge).toBeVisible({ timeout: 5000 });
    await expect(ciPassingBadge).toContainText("Passing");

    // Step 8: Verify approval gate is not blocked by CI
    // Navigate to ticket details/approval section
    await page.click(`[data-testid="ticket-${ticketId}"]`);
    const approvalSection = page.locator(
      '[data-testid="approval-section"]'
    );
    await expect(approvalSection).toBeVisible();

    // Verify CI check shows passing
    const ciCheck = approvalSection.locator(
      '[data-testid="ci-approval-gate-check"].ci-passing'
    );
    await expect(ciCheck).toBeVisible();

    // Verify "Approve" button is NOT disabled
    const approveBtn = approvalSection.locator('button:has-text("Approve")');
    // Note: button should be clickable, no CI-based disable
    const isDisabled = await approveBtn.evaluate(
      (el) => (el as HTMLButtonElement).disabled
    );
    expect(isDisabled).toBeFalsy();

    // Step 9: Try approving (should succeed)
    await approveBtn.click();
    const confirmBtn = page.locator('button:has-text("Confirm")');
    if ((await confirmBtn.count()) > 0) {
      await confirmBtn.click();
    }

    // Verify approval succeeded
    await expect(page.locator('text="Approved"')).toBeVisible({
      timeout: 5000,
    });
  });

  test("should block approval if CI still failing", async ({
    page,
    request,
  }) => {
    // Step 1: Create ticket
    await page.click('button:has-text("Create Ticket")');
    await page.fill('input[placeholder*="Title"]', "Implement profile page");
    await page.click('button:has-text("Create")');

    ticketId = await page
      .locator('[data-testid="ticket-id"]')
      .textContent()
      .then((t) => t?.trim() || "");

    // Step 2: Send failing CI webhook
    const failurePayload = {
      workflow_run: {
        id: 123458,
        conclusion: "failure",
        logs_url: "https://github.com/org/repo/runs/123458/logs",
        head_branch: `feature/ticket-${ticketId}`,
      },
    };

    await request.post(`/api/ci/webhook/test-workspace`, {
      data: failurePayload,
      headers: { "X-GitHub-Event": "workflow_run" },
    });

    // Step 3: Reload and navigate to approval
    await page.reload();
    await page.click(`[data-testid="ticket-${ticketId}"]`);

    const approvalSection = page.locator(
      '[data-testid="approval-section"]'
    );
    await expect(approvalSection).toBeVisible();

    // Step 4: Verify CI check shows failing
    const ciFailingCheck = approvalSection.locator(
      '[data-testid="ci-approval-gate-check"].ci-failing'
    );
    await expect(ciFailingCheck).toBeVisible();
    await expect(ciFailingCheck).toContainText("Blocks approval");

    // Step 5: Verify "Approve" button is disabled
    const approveBtn = approvalSection.locator('button:has-text("Approve")');
    const isDisabled = await approveBtn.evaluate(
      (el) => (el as HTMLButtonElement).disabled
    );
    expect(isDisabled).toBeTruthy();

    // Verify tooltip or message
    const blockingMessage = approvalSection.locator('text="Wait for CI"');
    // May have message indicating blocked by CI
    if ((await blockingMessage.count()) > 0) {
      await expect(blockingMessage).toBeVisible();
    }
  });

  test("should allow manual auto-fix retry", async ({
    page,
    request,
  }) => {
    // Create ticket
    await page.click('button:has-text("Create Ticket")');
    await page.fill('input[placeholder*="Title"]', "Implement notifications");
    await page.click('button:has-text("Create")');

    ticketId = await page
      .locator('[data-testid="ticket-id"]')
      .textContent()
      .then((t) => t?.trim() || "");

    // Send failing CI webhook
    await request.post(`/api/ci/webhook/test-workspace`, {
      data: {
        workflow_run: {
          id: 123459,
          conclusion: "failure",
          logs_url: "https://github.com/org/repo/runs/123459/logs",
          head_branch: `feature/ticket-${ticketId}`,
        },
      },
      headers: { "X-GitHub-Event": "workflow_run" },
    });

    // Reload and open CI panel
    await page.reload();
    const ciStatusBadge = page.locator(
      '[data-testid="ci-status-badge"].ci-failing'
    );
    await expect(ciStatusBadge).toBeVisible();
    await ciStatusBadge.click();

    // Click "Retry Auto-Fix"
    const retryBtn = page.locator('button:has-text("Retry Auto-Fix")');
    await expect(retryBtn).toBeVisible();
    await retryBtn.click();

    // Verify button shows loading state
    await expect(retryBtn).toContainText(/Triggering|Retrying/);

    // Verify new attempt created
    const statusResponse = await request.get(`/api/ci/status/${ticketId}`);
    const statusData = await statusResponse.json();
    expect(statusData.auto_fix_history.length).toBeGreaterThanOrEqual(2);
  });
});
