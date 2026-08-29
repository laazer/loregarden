/**
 * A pane tells the primitive inside it how much room it has.
 *
 * jsdom has no layout engine and no `ResizeObserver`, so nothing here measures
 * anything — the observer is a fake that reports whatever a test hands it. That
 * is the honest boundary: what is testable is the vocabulary (which size is
 * which tier), the plumbing (does the measurement reach the primitive and the
 * DOM), and the degradation (what happens where `ResizeObserver` does not
 * exist). Whether a real browser reports the number this expects is not a
 * question this file can ask.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";

import {
  COMPACT_HEIGHT,
  COMPACT_WIDTH,
  PaneSizeContext,
  WIDE_WIDTH,
  paneTierFor,
  usePaneSize,
} from "../paneSize";
import { ContainerPrimitiveHost } from "../primitives/registry";

/** Observers created during a test, with the callback each would receive. */
const observed: { target: Element; fire: (width: number, height: number) => void }[] = [];

beforeEach(() => {
  observed.length = 0;
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    callback: ResizeObserverCallback;
    constructor(callback: ResizeObserverCallback) {
      this.callback = callback;
    }
    observe(target: Element): void {
      observed.push({
        target,
        fire: (width, height) =>
          this.callback(
            [
              {
                target,
                contentRect: { width, height, top: 0, left: 0, bottom: height, right: width, x: 0, y: 0 },
              } as unknown as ResizeObserverEntry,
            ],
            this as unknown as ResizeObserver,
          ),
      });
    }
    disconnect(): void {}
    unobserve(): void {}
  };
});

describe("paneTierFor", () => {
  it("calls a pane compact when either axis is short", () => {
    // Height is not a footnote: 900×90 is a letterbox, and a rule that only
    // read width would lay a three-column card into it.
    expect(paneTierFor(900, COMPACT_HEIGHT - 1)).toBe("compact");
    expect(paneTierFor(COMPACT_WIDTH - 1, 900)).toBe("compact");
  });

  it("is regular between the thresholds and wide at the top", () => {
    expect(paneTierFor(COMPACT_WIDTH, COMPACT_HEIGHT)).toBe("regular");
    expect(paneTierFor(WIDE_WIDTH - 1, 600)).toBe("regular");
    expect(paneTierFor(WIDE_WIDTH, 600)).toBe("wide");
  });

  it("calls a pane with no area compact rather than wide", () => {
    expect(paneTierFor(0, 0)).toBe("compact");
  });
});

/**
 * The host, mounted on a real registered primitive.
 *
 * A synthetic probe is not available: the registry resolves ids through a `Map`
 * built once at module load, deliberately, so an entry pushed onto
 * `CONTAINER_PRIMITIVES` at test time resolves to nothing. `queue_lane` with no
 * slot chosen renders its unconfigured line and fetches nothing, which makes it
 * the quietest real primitive to hang this on.
 */
function renderHost() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ContainerPrimitiveHost containerId="c1" settings={{ primitive_id: "queue_lane" }} />
    </QueryClientProvider>,
  );
}

function hostOf(container: HTMLElement): HTMLElement {
  const host = container.querySelector<HTMLElement>("[data-container-id='c1']");
  if (host === null) throw new Error("host did not render");
  return host;
}

describe("the host measures the pane and publishes the tier", () => {
  it("reports regular until something has actually measured it", () => {
    // Not compact: guessing small would flash the dense layout on every mount.
    const { container } = renderHost();
    expect(hostOf(container)).toHaveAttribute("data-pane-tier", "regular");
  });

  it("observes the host and republishes the tier as it changes", () => {
    const { container } = renderHost();
    const host = hostOf(container);
    const observer = observed.find((entry) => entry.target === host);
    expect(observer).toBeDefined();

    act(() => observer?.fire(200, 400));
    expect(host).toHaveAttribute("data-pane-tier", "compact");

    act(() => observer?.fire(900, 700));
    expect(host).toHaveAttribute("data-pane-tier", "wide");

    act(() => observer?.fire(500, 400));
    expect(host).toHaveAttribute("data-pane-tier", "regular");
  });

  it("degrades to regular where ResizeObserver does not exist", () => {
    // Older embedded webviews, and jsdom itself. The size stays unmeasured and
    // every primitive gets the layout it was written for before this existed.
    const saved = (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver;
    delete (globalThis as unknown as Record<string, unknown>).ResizeObserver;

    const { container } = renderHost();
    expect(hostOf(container)).toHaveAttribute("data-pane-tier", "regular");
    expect(observed).toHaveLength(0);

    (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = saved;
  });
});

describe("usePaneSize", () => {
  function Probe() {
    const size = usePaneSize();
    return <span data-testid="probe">{`${size.tier} ${size.width}x${size.height}`}</span>;
  }

  it("hands a consumer the measurement the host provided", () => {
    render(
      <PaneSizeContext.Provider value={{ width: 220, height: 140, tier: "compact" }}>
        <Probe />
      </PaneSizeContext.Provider>,
    );
    expect(screen.getByTestId("probe")).toHaveTextContent("compact 220x140");
  });

  it("answers regular outside any pane, so a primitive can be rendered bare", () => {
    render(<Probe />);
    expect(screen.getByTestId("probe")).toHaveTextContent("regular 0x0");
  });
});
