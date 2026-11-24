import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import type { FinalizationConfirmationProps } from "../FinalizationConfirmation";
import { FinalizationConfirmation } from "../FinalizationConfirmation";

const RESULT = {
  created_count: 8,
  breakdown: { milestone: 1, feature: 2, capability: 2, task: 3 },
  root_ticket_id: "milestone 1",
};

function Where() {
  const { pathname, search } = useLocation();
  return <p data-testid="where">{pathname + search}</p>;
}

function renderConfirmation(overrides: Partial<FinalizationConfirmationProps> = {}) {
  const onClose = jest.fn();
  const { container } = render(
    <MemoryRouter initialEntries={["/studio"]}>
      <FinalizationConfirmation result={RESULT} isLoading={false} error={null} onClose={onClose} {...overrides} />
      <Routes>
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
  return { onClose, container };
}

const button = (name: string) => screen.getByRole("button", { name });
const summary = (container: HTMLElement) => container.querySelector(".finalization-breakdown-summary");

describe("a commit that created tickets", () => {
  it("announces it, with the total and every type it can create", () => {
    const { container } = renderConfirmation();

    expect(screen.getByRole("status")).toHaveTextContent("Hierarchy created successfully");
    expect(container.querySelector(".finalization-total")).toHaveTextContent(/^8items$/);
    expect(summary(container)).toHaveTextContent(
      "0 initiatives, 1 milestone, 2 features, 2 capabilities, 3 tasks, 0 bugs",
    );
  });

  it("counts bugs and initiatives, in the singular when there is one", () => {
    const { container } = renderConfirmation({
      result: { created_count: 1, breakdown: { bug: 1 }, root_ticket_id: "b" },
    });

    expect(container.querySelector(".finalization-total")).toHaveTextContent(/^1item$/);
    expect(summary(container)).toHaveTextContent(/0 tasks, 1 bug$/);
  });

  it("leaves the types out when nothing was created", () => {
    const { container } = renderConfirmation({
      result: { created_count: 0, breakdown: {}, root_ticket_id: null },
    });

    expect(container.querySelector(".finalization-total")).toHaveTextContent(/^0items$/);
    expect(summary(container)).toBeNull();
  });

  it("opens the root ticket on its default tab, its id escaped", async () => {
    renderConfirmation();
    await userEvent.click(button("View hierarchy"));
    expect(screen.getByTestId("where")).toHaveTextContent(/^\/tickets\/milestone%201\/timeline$/);
  });

  it("has nowhere to go when the commit named no root", () => {
    renderConfirmation({ result: { ...RESULT, root_ticket_id: null } });
    expect(button("View hierarchy")).toBeDisabled();
  });

  it("closes", async () => {
    const { onClose } = renderConfirmation();
    await userEvent.click(button("Close"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("a commit in flight or failed", () => {
  it("says it is finalizing, with nothing to press", () => {
    renderConfirmation({ result: null, isLoading: true });
    expect(screen.getByRole("status")).toHaveTextContent("Finalizing hierarchy…");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("shows the reason as text, and closes", async () => {
    const reason = "<img src=x onerror='alert(1)'> already exists";
    const { onClose, container } = renderConfirmation({ result: null, error: reason });

    expect(screen.getByText(reason)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    await userEvent.click(button("Close"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("draws nothing before there is a result", () => {
    const { container } = renderConfirmation({ result: null });
    expect(container).toHaveTextContent(/^\/studio$/);
  });
});
