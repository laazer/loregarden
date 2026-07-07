import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { pageFromPath, ticketPath } from "../appNavigation";
import { useAppPage, useTicketIdFromRoute } from "../useAppNavigation";

function PageProbe() {
  const page = useAppPage();
  const ticketId = useTicketIdFromRoute();
  return (
    <div>
      <div data-testid="page">{page}</div>
      <div data-testid="ticket">{ticketId ?? ""}</div>
    </div>
  );
}

describe("useAppNavigation", () => {
  it("derives the active page from the current route", () => {
    render(
      <MemoryRouter initialEntries={["/queue"]}>
        <Routes>
          <Route path="/queue/*" element={<PageProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByTestId("page")).toHaveTextContent("queue");
    expect(pageFromPath("/queue")).toBe("queue");
  });

  it("reads ticket id from ticket routes", () => {
    render(
      <MemoryRouter initialEntries={[ticketPath("ticket-42")]}>
        <Routes>
          <Route path="/tickets/:ticketId" element={<PageProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByTestId("page")).toHaveTextContent("dashboard");
    expect(screen.getByTestId("ticket")).toHaveTextContent("ticket-42");
  });
});
