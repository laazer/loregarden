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

// lg-external-harness-1212. The trigger is server-driven: `QueueNotificationsHost`
// is session-mounted on the `queueSocket`, so a crash-looping queue or any retry
// loop choosing the same `message` drives the rate at which this arm fires, and
// `announceQueueEvent` discards `pushToast`'s return value.
describe("A2 — a server-driven queue error mints no claim nobody can release", () => {
  const QUEUE_ERROR = {
    type: "error" as const,
    timestamp: "2026-10-17T03:00:00Z",
    // No `data.message`, so the constant fallback supplies the text — which is
    // what makes two events byte-identical and the dedupe branch the path they
    // take.
    data: { runId: "run-1" },
  };

  test("two identical events leave one toast whose claim set is empty", () => {
    // Verified through the real caller rather than by grep: A2 is a claim about
    // what the 81 discarding sites do to the store, and `announceQueueEvent` is
    // the one of them the server can drive.
    //
    // Empty, not merely bounded. A cap on appends would bound the COUNT of
    // unreleasable claims without removing any, so this assertion is what rules
    // out the filing's "minimal alternative" — A1 and A6 would both pass under
    // a cap.
    announceQueueEvent({ ...QUEUE_ERROR });
    announceQueueEvent({ ...QUEUE_ERROR });

    const toasts = useToastStore.getState().toasts;
    expect(toasts).toHaveLength(1);
    expect(toasts[0].message).toBe("The queue reported a failure.");
    expect(toasts[0].duration).toBe(0);
    expect(toasts[0].claims).toEqual([]);
  });

  test("a crash loop's worth of them is still one toast holding nothing", () => {
    for (let i = 0; i < 1000; i += 1) announceQueueEvent({ ...QUEUE_ERROR });

    const toasts = useToastStore.getState().toasts;
    expect(toasts).toHaveLength(1);
    expect(toasts[0].claims).toEqual([]);
    // The durable inbox is the surface that is SUPPOSED to accumulate, and it
    // is unaffected by this ticket: the growth being fixed is inside one toast,
    // not in the notification log beside it.
    expect(useNotificationStore.getState().notifications.length).toBeGreaterThan(0);
  });
});

// test-break (lg-external-harness-1212). A2 is asserted above through the one
// arm the filing names. The criterion is about every discarding call site, and
// `announceQueueEvent` is a single site that reaches three different toasts
// depending on the event — so the arm under test is chosen by the SERVER, not
// by the caller. A fix applied to the sticky error path alone would pass the
// block above and leave the other two minting.
describe("test-break — no arm of the queue announcer mints a claim, whatever the server sends", () => {
  const EVENTS = [
    { type: "error" as const, timestamp: "t", data: { runId: "run-1" } },
    { type: "error" as const, timestamp: "t", data: { runId: "run-1", message: "no slots" } },
    {
      type: "queue_promoted" as const,
      timestamp: "t",
      data: { runId: "run-1", slotNumber: 2, ticketTitle: "Bootstrap", stageKey: "implement" },
    },
    {
      type: "run_completed" as const,
      timestamp: "t",
      data: { runId: "run-1", status: "succeeded", ticketTitle: "Bootstrap", stageKey: "spec" },
    },
    {
      type: "run_completed" as const,
      timestamp: "t",
      data: { runId: "run-1", status: "failed", ticketTitle: "Bootstrap", stageKey: "spec" },
    },
    // Nothing at all: every label falls back to a constant, so these are
    // byte-identical to each other and take the dedupe branch too.
    { type: "error" as const, timestamp: "t", data: undefined },
    { type: "run_completed" as const, timestamp: "t", data: undefined },
  ];

  test("200 rounds of every arm leave a capped stack holding no claims", () => {
    for (let round = 0; round < 200; round += 1) {
      for (const event of EVENTS) announceQueueEvent({ ...event });

      const toasts = useToastStore.getState().toasts;
      // The cap is the only thing bounding the STACK; the claim set inside each
      // toast is bounded by nobody holding one.
      expect(toasts.length).toBeLessThanOrEqual(4);
      expect(toasts.flatMap((t) => t.claims)).toEqual([]);
    }
  });

  test("a crash loop alternating two messages still mints nothing", () => {
    // Two distinct messages keep both toasts live, so each one takes the dedupe
    // branch on every other event — the interleaving a single-message loop
    // never produces.
    for (let i = 0; i < 500; i += 1) {
      announceQueueEvent({
        type: "error",
        timestamp: "t",
        data: { runId: "run-1", message: i % 2 === 0 ? "no slots" : "lease expired" },
      });
    }

    const toasts = useToastStore.getState().toasts;
    expect(toasts).toHaveLength(2);
    expect(toasts.flatMap((t) => t.claims)).toEqual([]);
  });
});
