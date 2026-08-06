import { announceQueueEvent, queueEventToToast } from "../queueNotifications";
import { useNotificationStore } from "../../state/notificationStore";
import { useToastStore } from "../../state/toastStore";

beforeEach(() => {
  useToastStore.getState().clear();
  useNotificationStore.getState().clear();
});

describe("queueEventToToast", () => {
  test("names the ticket and stage on a completed run", () => {
    const toast = queueEventToToast({
      type: "run_completed",
      data: {
        runId: "run-abc12345",
        status: "succeeded",
        ticketTitle: "Bootstrap vertical slice",
        stageKey: "implement",
        agentId: "backend_implementer",
      },
    });

    expect(toast).toMatchObject({ tone: "success", title: "Run complete" });
    expect(toast.message).toContain("Bootstrap vertical slice");
    expect(toast.message).toContain("implement");
  });

  test("falls back to the agent when stage is missing", () => {
    const toast = queueEventToToast({
      type: "run_completed",
      data: {
        runId: "run-abc12345",
        status: "failed",
        ticketTitle: "Fix queue",
        agentId: "debugger",
      },
    });

    expect(toast).toMatchObject({ tone: "error", title: "Run failed" });
    expect(toast.message).toContain("Fix queue");
    expect(toast.message).toContain("debugger");
  });

  test("a promotion names the ticket, step, and slot", () => {
    const toast = queueEventToToast({
      type: "queue_promoted",
      data: {
        runId: "run-abc12345",
        slotNumber: 2,
        ticketTitle: "Bootstrap vertical slice",
        stageKey: "implement",
      },
    });

    expect(toast).toMatchObject({ tone: "info", title: "Run promoted" });
    expect(toast.message).toMatch(/Bootstrap vertical slice · implement/);
    expect(toast.message).toMatch(/slot 2/);
  });

  test("an error event carries the server message", () => {
    expect(
      queueEventToToast({
        type: "error",
        data: { runId: "run-1", message: "Failed to create run: no slots" },
      }),
    ).toMatchObject({
      tone: "error",
      title: "Queue error",
      message: "Failed to create run: no slots",
      duration: 0,
    });
  });
});

describe("announceQueueEvent", () => {
  test("records a durable inbox notification alongside the toast", () => {
    announceQueueEvent({
      type: "run_completed",
      timestamp: "2026-07-30T10:00:00Z",
      data: {
        runId: "run-abc12345",
        status: "succeeded",
        ticketId: "ticket-1",
        ticketTitle: "Bootstrap vertical slice",
        stageKey: "implement",
      },
    });

    expect(useToastStore.getState().toasts).toHaveLength(1);
    expect(useNotificationStore.getState().notifications).toHaveLength(1);
    expect(useNotificationStore.getState().notifications[0]).toMatchObject({
      tone: "success",
      title: "Run complete",
      ticketId: "ticket-1",
      runId: "run-abc12345",
    });
    expect(useNotificationStore.getState().notifications[0].message).toContain(
      "Bootstrap vertical slice",
    );
  });
});
