import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { PageTopbar, TopbarCrumbs, TopbarPageSlot, TopbarPageSlotProvider } from "../TopbarPageSlot";

function renderAt(path: string, page: React.ReactNode = null) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <TopbarPageSlotProvider>
        <TopbarCrumbs />
        <TopbarPageSlot />
        {page}
      </TopbarPageSlotProvider>
    </MemoryRouter>,
  );
  return screen.getByRole("navigation", { name: "Breadcrumb" });
}

test("a page with no title of its own is named as the rail names it", () => {
  const crumbs = renderAt("/chat");
  expect(within(crumbs).getByRole("heading", { level: 1, name: "Chat" })).toBeInTheDocument();
  expect(within(crumbs).queryByRole("link")).not.toBeInTheDocument();
});

test("a title that only repeats the page's name is drawn once", () => {
  const crumbs = renderAt("/machine", <PageTopbar title="Machine" />);
  expect(within(crumbs).getAllByRole("heading")).toHaveLength(1);
  expect(within(crumbs).getByRole("heading", { level: 1, name: "Machine" })).toBeInTheDocument();
  expect(within(crumbs).queryByRole("link")).not.toBeInTheDocument();
});

test("a title that says more follows the page's name, which links back", () => {
  const crumbs = renderAt("/initiatives/abc", <PageTopbar title="Release train" />);
  expect(within(crumbs).getByRole("link", { name: "Initiatives" })).toHaveAttribute("href", "/initiatives");
  expect(within(crumbs).getByRole("heading", { level: 1, name: "Release train" })).toBeInTheDocument();
  expect(within(crumbs).getAllByRole("heading")).toHaveLength(1);
});

test("the page's controls land in the page slot, not the breadcrumb", () => {
  const crumbs = renderAt(
    "/machine",
    <PageTopbar title="Machine">
      <button type="button">Instances</button>
    </PageTopbar>,
  );
  const control = screen.getByRole("button", { name: "Instances" });
  expect(crumbs).not.toContainElement(control);
});
