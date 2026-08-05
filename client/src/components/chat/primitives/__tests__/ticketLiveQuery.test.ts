import { ApiError } from "../../../../api/client";
import type { TicketDetail } from "../../../../api/types";
import {
  isTicketNotFound,
  ticketQueryRetry,
  ticketRefetchInterval,
} from "../ticketLiveQuery";

describe("ticketLiveQuery", () => {
  it("treats 404 ApiErrors as missing tickets", () => {
    expect(isTicketNotFound(new ApiError(404, "Ticket not found"))).toBe(true);
    expect(isTicketNotFound(new ApiError(500, "boom"))).toBe(false);
    expect(isTicketNotFound(new Error("Ticket not found"))).toBe(true);
  });

  it("does not retry missing tickets", () => {
    expect(ticketQueryRetry(0, new ApiError(404, "Ticket not found"))).toBe(false);
    expect(ticketQueryRetry(0, new ApiError(500, "boom"))).toBe(true);
    expect(ticketQueryRetry(2, new ApiError(500, "boom"))).toBe(false);
  });

  it("stops the refetch interval after an error", () => {
    const errored = {
      state: { error: new ApiError(404, "Ticket not found"), data: undefined },
    };
    expect(ticketRefetchInterval(errored as never)).toBe(false);
  });

  it("polls faster while a live ticket is running", () => {
    const running = {
      state: {
        error: null,
        data: { workflow_stage_status: "running" } as TicketDetail,
      },
    };
    const idle = {
      state: {
        error: null,
        data: { workflow_stage_status: "pending" } as TicketDetail,
      },
    };
    expect(ticketRefetchInterval(running as never)).toBe(1000);
    expect(ticketRefetchInterval(idle as never)).toBe(5000);
  });
});
