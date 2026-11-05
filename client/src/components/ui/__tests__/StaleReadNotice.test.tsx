/**
 * lg-milestone-that-2187 R0-5..R0-7 — the one inline notice, and the six
 * properties UX2(d) and UX3 pin on it at once.
 *
 * `queryClient.ts:28-31` suppresses the query toast whenever
 * `query.state.data !== undefined`, so on a polling surface with content
 * already on screen THIS NOTICE IS THE ONLY REPORT of the failed read. That is
 * why each property below is an assertion rather than a convention:
 *
 *  - `role="status"`, hardcoded, never `alert`. The feed polls at 5s; an
 *    assertive region there interrupts a screen-reader user every five seconds,
 *    denying them the surface rather than warning them.
 *  - no changing value — no timestamp, attempt counter, "retrying in Ns". Two
 *    identical consecutive failures must render identical text, so React writes
 *    no DOM mutation and the live region announces once per episode rather than
 *    720 times an hour.
 *  - no tab stop and no control: the next read is already on the surface's own
 *    timer, so a Retry here would duplicate an invisible timer AND add a tab
 *    stop that appears and disappears on one, changing a keyboard operator's
 *    focus order every 30 seconds (UX3(a)).
 *  - the reason is unbounded and wrapping, never clipped: a multi-KB proxy-page
 *    reason pushes content down instead of being truncated, because clipping
 *    the only report of a failure is lg-milestone-that-1465's defect (UX3(b)).
 *
 * R0-7 reads the stylesheet from disk, following `selectsAreThemed.test.ts`'s
 * pattern in `components/__tests__`: jest maps `.css` to a style mock, so the
 * declarations are invisible to a render test and a `max-height` could be added
 * with every render assertion still green.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { render, screen } from "@testing-library/react";

import { ApiError } from "../../../api/http";
import { describeError, endSentence } from "../../../state/toastStore";
import { StaleReadNotice } from "../StaleReadNotice";

const LEAD = "These work items may be out of date — the last refresh failed.";
const FALLBACK = "The initiative's tickets could not be read.";

function renderNotice(error: unknown, testId = "board-refresh-failed") {
  return render(
    <StaleReadNotice lead={LEAD} error={error} fallback={FALLBACK} testId={testId} />,
  );
}

describe("StaleReadNotice", () => {
  describe("R0-5 — one polite element, with the lead sentence and the reason", () => {
    it("renders exactly one role=status element and no alert", () => {
      renderNotice(new Error("the board endpoint did not answer"));

      expect(screen.getAllByRole("status")).toHaveLength(1);
      expect(screen.queryByRole("alert")).toBeNull();
    });

    it("renders the lead, a single space, then the ended reason", () => {
      // The single space is load-bearing: the monitor's JSX writes `{" "}`
      // between the two sentences, and R5 migrates its 11 existing assertions
      // onto this component unmodified. `toHaveTextContent` on the monitor's
      // suite collapses whitespace, so the exact string is asserted here.
      const error = new Error("the board endpoint did not answer");

      renderNotice(error);

      expect(screen.getByRole("status").textContent).toBe(
        `${LEAD} ${endSentence(describeError(error, FALLBACK))}`,
      );
    });

    it("ends the server's own sentence rather than running two together", () => {
      // The server's half of this contract is one line with no terminator.
      // Without endSentence the notice reads "… failed. gh timed out The …".
      renderNotice(new ApiError(502, "gh timed out"));

      expect(screen.getByRole("status").textContent).toBe(`${LEAD} gh timed out.`);
    });

    it("falls back to the surface's own words when the throw carries none", () => {
      // A rejection with no message must still name something the operator can
      // act on, which is what the per-surface `fallback` prop is for.
      renderNotice(null);

      expect(screen.getByRole("status").textContent).toBe(`${LEAD} ${endSentence(FALLBACK)}`);
    });

    it("carries the surface's own data-testid, so a page with two notices can count each", () => {
      // The plan page can carry its own notice and the board's on the same
      // failed tick: two different reads, each naming its own subject. "Exactly
      // one" is therefore scoped per surface by testid, never by role alone.
      renderNotice(new Error("boom"), "plan-refresh-failed");

      expect(screen.getByTestId("plan-refresh-failed")).toBe(screen.getByRole("status"));
    });

    it("adds no tab stop and no control of its own", () => {
      const { container } = renderNotice(new Error("boom"));
      const notice = screen.getByRole("status");

      expect(notice).not.toHaveAttribute("tabIndex");
      expect(notice).not.toHaveAttribute("title");
      expect(container.querySelector("button")).toBeNull();
      expect(container.querySelector("a")).toBeNull();
    });

    it("renders one element and nothing nested inside it", () => {
      // A nested element is where a counter, a timestamp or a clamp arrives
      // later. The notice is one paragraph of text.
      const { container } = renderNotice(new Error("boom"));

      expect(container.childElementCount).toBe(1);
      expect(screen.getByRole("status").childElementCount).toBe(0);
    });
  });

  describe("R0-6 — identical failures render identical text", () => {
    it("renders the same textContent twice for the same props", () => {
      // At a 5s poll, any changing value in here announces to a screen reader
      // every tick. Pinned as an equality rather than as "contains no digits",
      // because a timestamp is only one of the ways to break it.
      const error = new Error("the telemetry endpoint did not answer");

      const first = renderNotice(error).container.textContent;
      const second = renderNotice(error).container.textContent;

      expect(second).toBe(first);
    });

    it("renders the same text across a re-render with the same props", () => {
      const error = new Error("the telemetry endpoint did not answer");
      const view = renderNotice(error);
      const before = screen.getByRole("status").textContent;

      view.rerender(
        <StaleReadNotice lead={LEAD} error={error} fallback={FALLBACK} testId="board-refresh-failed" />,
      );

      expect(screen.getByRole("status").textContent).toBe(before);
    });
  });

  describe("R0-7 — the stylesheet neither clips the reason nor animates", () => {
    const css = readFileSync(join(__dirname, "..", "StaleReadNotice.css"), "utf8");

    it("owns .stale-read-notice, so five surfaces do not invent five looks", () => {
      // A scan that matched nothing would pass every assertion below.
      expect(css).toContain(".stale-read-notice");
    });

    it.each(["max-height", "overflow", "-webkit-line-clamp", "transition", "animation"])(
      "declares no %s",
      (property) => {
        // UX3(b) and UX3(e). The reason is unbounded and wrapping on purpose;
        // the state change is already carried by the text appearing, and at a
        // 5s cadence an entrance transition is a flicker the operator sees all
        // day.
        expect(css).not.toMatch(new RegExp(`^\\s*${property}\\s*:`, "m"));
      },
    );

    it("declares no @keyframes", () => {
      expect(css).not.toContain("@keyframes");
    });

    it("carries .wm-stale's five declarations, with no color literal", () => {
      // R5 deletes `.wm-stale` and migrates the monitor onto this class, so the
      // treatment has to be the same one — and expressed in tokens, which is
      // what the theme gate requires of a changed line.
      expect(css).toMatch(/font-size:\s*var\(--fs-small\)/);
      expect(css).toMatch(/color:\s*var\(--txm\)/);
      expect(css).toMatch(/padding-left:\s*10px/);
      expect(css).toMatch(/border-left:\s*3px solid color-mix\(in srgb, var\(--rdl\) 45%, transparent\)/);
      expect(css).toMatch(/margin:\s*0/);
      expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(/);
    });
  });

  describe("R5-2 — the monitor's own class is gone, not shadowed", () => {
    it("leaves no .wm-stale anywhere in the tree", () => {
      // Internal posture: a shared component whose canonical treatment lives in
      // another component's stylesheet is the shim this posture forbids. The
      // monitor migrates onto `.stale-read-notice` and `.wm-stale` is deleted,
      // so a leftover rule means two looks for one notice.
      const src = join(__dirname, "..", "..", "..");
      const files = (dir: string): string[] =>
        readdirSync(dir).flatMap((entry) => {
          const path = join(dir, entry);
          if (statSync(path).isDirectory()) {
            // `__tests__` is skipped for the same reason the census module
            // skips it: this suite's own prose names the deleted class, so a
            // scan including tests could never pass. The claim is about a
            // leftover RULE or className in the sources, not about prose.
            return entry === "__tests__" || entry === "node_modules" ? [] : files(path);
          }
          return /\.(tsx?|css)$/.test(entry) ? [path] : [];
        });

      const offenders = files(src)
        .filter((path) => readFileSync(path, "utf8").includes("wm-stale"))
        .map((path) => path.slice(src.length + 1));

      expect(offenders).toEqual([]);
    });
  });

  describe("UX3(d) — nothing here moves focus on a background poll", () => {
    it("leaves document.activeElement where it was", () => {
      // The notice appears on a timer. A focus() or a focus-restoring effect
      // in here would move a keyboard operator's cursor every 30 seconds
      // without them touching anything (lg-milestone-that-1464's defect).
      const pad = document.createElement("a");
      pad.href = "#pad";
      document.body.append(pad);
      pad.focus();
      const before = document.activeElement;

      renderNotice(new Error("boom"));

      expect(document.activeElement).toBe(before);
      pad.remove();
    });

    it("has no useEffect and no ref in its source", () => {
      // Asserted against the source because the render assertion above passes
      // under an effect that only fires on a prop change the test never makes.
      const source = readFileSync(join(__dirname, "..", "StaleReadNotice.tsx"), "utf8");

      expect(source).not.toMatch(/\buseEffect\b|\buseLayoutEffect\b|\buseRef\b|\bref=/);
      expect(source).not.toMatch(/\.focus\(\)|autoFocus/);
    });

    it("hardcodes role=status, taking no role or tone prop", () => {
      // The component must not offer the knob at all: UX2(d) says never
      // `alert`, and a `role` prop is how the fifth call site gets one.
      const source = readFileSync(join(__dirname, "..", "StaleReadNotice.tsx"), "utf8");

      expect(source).toContain('role="status"');
      expect(source).not.toMatch(/\brole\s*[?:]\s*string|\btone\b|role=\{/);
    });
  });
});

/**
 * lg-milestone-that-2187 test-break — the two boundaries of the reason string.
 *
 * The suite above pins the reason the server normally sends: one line, no
 * terminator. These are the ends of that range, and each catches a different
 * plausible shortcut:
 *
 *  - A component that appends `"."` itself instead of calling `endSentence`
 *    passes every assertion above, because every error they use ends without
 *    one. It renders ".." the first time a reason arrives already terminated,
 *    which is what `describeError`'s `ApiError` branch produces whenever the
 *    server's detail is a full sentence.
 *  - UX3(b) makes the reason deliberately unbounded and wrapping, and R0-7
 *    checks that by reading the stylesheet. A clamp applied in the TSX — a
 *    `slice`, a truncating helper, an ellipsis — is invisible to a CSS scan and
 *    silently discards the only report of the failed read the operator gets,
 *    since `queryClient.ts:28-31` suppresses the toast whenever data is present.
 *    That is lg-milestone-that-1465's defect.
 */
describe("the ends of the reason's range", () => {
  it("leaves a reason that already ends a sentence with exactly one terminator", () => {
    // `endSentence` treats . ! ? … : ; as terminators; a component doing its own
    // concatenation does not.
    renderNotice(new ApiError(502, "GitHub did not answer in time."));

    expect(screen.getByRole("status").textContent).toBe(`${LEAD} GitHub did not answer in time.`);
    expect(screen.getByRole("status").textContent).not.toContain("..");
  });

  it.each(["!", "?", "…", ":", ";"])("appends nothing after a reason ending in %s", (terminator) => {
    renderNotice(new ApiError(502, `the proxy refused${terminator}`));

    expect(screen.getByRole("status").textContent).toBe(`${LEAD} the proxy refused${terminator}`);
  });

  it("renders a multi-kilobyte reason in full, with nothing clipped in the markup", () => {
    // A proxy error page is the real source of this: 4KB of text where a line
    // was expected. UX3(b)'s stated trade is that it pushes content down rather
    // than being truncated, so the whole reason has to be IN the DOM — a CSS
    // clamp would still satisfy this, which is R0-7's half of the same claim.
    const reason = `upstream refused: ${"the proxy returned an html error page ".repeat(120)}`;

    renderNotice(new ApiError(502, reason));

    const text = screen.getByRole("status").textContent ?? "";
    expect(text.length).toBeGreaterThan(4000);
    expect(text).toBe(`${LEAD} ${endSentence(reason)}`);
    expect(text).not.toContain("…");
  });
});
