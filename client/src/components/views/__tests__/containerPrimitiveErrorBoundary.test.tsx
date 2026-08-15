/**
 * A primitive that throws loses its own pane and nothing else.
 *
 * The unknown-id fallback covers the case where the registry has no component
 * to mount. This is the other one: a *known* primitive whose component raises —
 * xterm failing to attach, a ledger row the panel cannot read. React has one
 * answer for that, an error boundary, and if none is mounted the exception
 * unwinds to the root and unmounts the whole tree. A view renders N containers
 * in one tree (440's grid, 442's canvas), so that is every pane in the view
 * going blank because one of them broke.
 *
 * The boundary therefore belongs *inside* `ContainerPrimitiveHost`, so every
 * consumer inherits it rather than each view kind remembering to add one. That
 * is what the sibling assertion below pins: two hosts in one tree, one of them
 * broken.
 *
 * The crash is injected by making the real `TerminalPanel` throw, not by
 * registering a synthetic primitive, so the assertion runs through the same
 * dispatch path a real container uses.
 */

import { render, screen } from "@testing-library/react";

import { ContainerPrimitiveHost } from "../primitives/registry";

const BOOM = "xterm could not attach";

/**
 * Counts attempts to mount the broken primitive.
 *
 * The reset-key tests below are about *when* React tries again, which no
 * rendered output distinguishes: a boundary that thrashed and re-caught looks
 * exactly like one that stayed put. The attempt count is the difference.
 */
const mockTerminalMounts = jest.fn();

jest.mock("../../TerminalPanel", () => ({
  TerminalPanel: () => {
    mockTerminalMounts();
    throw new Error(BOOM);
  },
}));

beforeEach(() => {
  // React logs every error it catches. Silence it; nothing here asserts on it.
  jest.spyOn(console, "error").mockImplementation(() => {});
  mockTerminalMounts.mockClear();
});

afterEach(() => jest.restoreAllMocks());

const TERMINAL = { primitive_id: "terminal", workspace_slug: "loregarden" };
const EMBED = { primitive_id: "web_embed", url: "https://example.com/app" };

describe("a known primitive that throws is contained", () => {
  it("does not let the exception escape the host", () => {
    expect(() =>
      render(
        <ContainerPrimitiveHost
          containerId="c1"
          settings={{ primitive_id: "terminal", workspace_slug: "loregarden" }}
        />,
      ),
    ).not.toThrow();
  });

  it("shows a fallback in the crashed pane instead of blanking it", () => {
    const { container } = render(
      <ContainerPrimitiveHost
        containerId="c1"
        settings={{ primitive_id: "terminal", workspace_slug: "loregarden" }}
      />,
    );
    expect(container.querySelector("[data-primitive-crashed='true']")).not.toBeNull();
  });

  it("leaves every other container in the same tree mounted", () => {
    // The whole point. If the boundary were the caller's job, this render would
    // take the sibling down with it.
    render(
      <div>
        <ContainerPrimitiveHost
          containerId="broken"
          settings={{ primitive_id: "terminal", workspace_slug: "loregarden" }}
        />
        <ContainerPrimitiveHost
          containerId="fine"
          settings={{ primitive_id: "web_embed", url: "https://example.com/app" }}
        />
      </div>,
    );

    expect(screen.getByTitle(/example\.com/)).toBeInTheDocument();
    expect(document.querySelector("[data-container-id='fine']")).not.toBeNull();
  });

  it("does not report the healthy pane as crashed", () => {
    const { container } = render(
      <ContainerPrimitiveHost
        containerId="c1"
        settings={{ primitive_id: "web_embed", url: "https://example.com/app" }}
      />,
    );
    expect(container.querySelector("[data-primitive-crashed='true']")).toBeNull();
  });
});

/**
 * `resetKey` is the boundary's only non-trivial logic, and the grid (440) and
 * canvas (442) will lean on it the moment a pane can be re-pointed: a boundary
 * that never clears leaves a pane dead for the rest of the session, and one
 * that clears on every render re-mounts a component that is still broken, on
 * every parent render, forever. Both directions are pinned here.
 */
describe("re-pointing a crashed pane", () => {
  it("clears the caught error when the container's primitive changes", () => {
    const { container, rerender } = render(
      <ContainerPrimitiveHost containerId="c1" settings={TERMINAL} />,
    );
    expect(container.querySelector("[data-primitive-crashed='true']")).not.toBeNull();

    rerender(<ContainerPrimitiveHost containerId="c1" settings={EMBED} />);

    expect(container.querySelector("[data-primitive-crashed='true']")).toBeNull();
    expect(container.querySelector("iframe")).toHaveAttribute("src", "https://example.com/app");
  });

  it("retries when the same primitive is pointed at a different container", () => {
    // The container id is the other half of the key, and a pane moved to a
    // different container is a genuinely different render — the previous
    // crash says nothing about it, so the boundary has to try again. Only the
    // mount count shows that it did: this primitive throws either way.
    const { rerender } = render(<ContainerPrimitiveHost containerId="c1" settings={TERMINAL} />);
    const mountsWhenItCrashed = mockTerminalMounts.mock.calls.length;

    rerender(<ContainerPrimitiveHost containerId="c2" settings={TERMINAL} />);

    expect(mockTerminalMounts.mock.calls.length).toBeGreaterThan(mountsWhenItCrashed);
  });

  it("stays in the fallback, without retrying, while the key is unchanged", () => {
    const { container, rerender } = render(
      <ContainerPrimitiveHost containerId="c1" settings={TERMINAL} />,
    );
    const mountsWhenItCrashed = mockTerminalMounts.mock.calls.length;
    expect(mountsWhenItCrashed).toBeGreaterThan(0);

    // A parent re-render is not a reason to try a component that just threw.
    rerender(<ContainerPrimitiveHost containerId="c1" settings={TERMINAL} />);
    rerender(<ContainerPrimitiveHost containerId="c1" settings={TERMINAL} />);

    expect(container.querySelector("[data-primitive-crashed='true']")).not.toBeNull();
    expect(mockTerminalMounts).toHaveBeenCalledTimes(mountsWhenItCrashed);
  });
});
