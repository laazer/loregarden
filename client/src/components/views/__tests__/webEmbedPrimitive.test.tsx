/**
 * AC6 — "a web-embed primitive renders a sandboxed frame from a URL setting."
 *
 * The policy, fixed at triage and owned entirely by this ticket (433's server
 * deliberately does not validate `settings`):
 *
 *   - allowlist `http` and `https` at the point the container renders;
 *   - refuse `javascript:`, `data:`, `blob:`, `file:` — and *refused* means no
 *     frame carrying that src exists, not that a warning was logged next to a
 *     frame that loaded it;
 *   - sandbox the frame **without** `allow-same-origin`. This is the app's
 *     first iframe and it renders inside a Tauri webview, where a same-origin
 *     frame reaches the app's own storage and its Tauri IPC surface.
 */

import { fireEvent, render } from "@testing-library/react";

import { safeEmbedUrl } from "../primitives/embedUrl";
import { ContainerPrimitiveHost } from "../primitives/registry";

function renderEmbed(url: unknown) {
  return render(
    <ContainerPrimitiveHost containerId="c1" settings={{ primitive_id: "web_embed", url }} />,
  );
}

describe("AC6 — an allowed URL renders a sandboxed frame", () => {
  it("renders a frame whose src is the http(s) URL from settings", () => {
    const { container } = renderEmbed("https://example.com/app");
    const frame = container.querySelector("iframe");
    expect(frame).not.toBeNull();
    expect(frame).toHaveAttribute("src", "https://example.com/app");
  });

  it("allows plain http as well as https", () => {
    // The live-app preview this container kind is meant to host (ticket 344)
    // is a localhost dev server, which is http.
    const { container } = renderEmbed("http://localhost:5173/");
    expect(container.querySelector("iframe")).toHaveAttribute("src", "http://localhost:5173/");
  });

  it("sandboxes the frame and never grants it same-origin", () => {
    const { container } = renderEmbed("https://example.com/app");
    const frame = container.querySelector("iframe");
    expect(frame).toHaveAttribute("sandbox");

    const tokens = (frame?.getAttribute("sandbox") ?? "").split(/\s+/).filter(Boolean);
    expect(tokens).not.toContain("allow-same-origin");
    // These four each hand the frame a way out of the box it was put in.
    expect(tokens).not.toContain("allow-top-navigation");
    expect(tokens).not.toContain("allow-popups-to-escape-sandbox");
    expect(tokens).not.toContain("allow-modals");
    expect(tokens).not.toContain("allow-pointer-lock");
  });

  it("never fetches or previews the URL itself", () => {
    // The ticket: "treat any preview or fetch of an embed URL as SSRF-capable,
    // not trusted because it came from the store". The browser fetching it as
    // the frame's own load is the point; *this app* fetching it — for a title,
    // a favicon, a reachability check, a screenshot — is a request the operator
    // did not make, sent with the app's own network position.
    const fetchSpy = jest.fn();
    (globalThis as unknown as { fetch: unknown }).fetch = fetchSpy;
    const xhrSpy = jest.spyOn(XMLHttpRequest.prototype, "open");

    renderEmbed("https://internal.example/admin");

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(xhrSpy).not.toHaveBeenCalled();
    xhrSpy.mockRestore();
  });

  it("carries no srcdoc and no ambient permissions", () => {
    // `srcdoc` is the second way content enters a frame and the URL allowlist
    // never sees it; `allow` hands the frame camera, mic, and geolocation from
    // the app's own grants.
    const { container } = renderEmbed("https://example.com/app");
    const frame = container.querySelector("iframe");
    expect(frame).not.toHaveAttribute("srcdoc");
    expect(frame?.getAttribute("allow") ?? "").toBe("");
  });

  it("grants nothing beyond scripts", () => {
    // A sandbox is only worth the tokens it withholds. Anything past
    // allow-scripts needs a reason this ticket does not have.
    const { container } = renderEmbed("https://example.com/app");
    const tokens = (container.querySelector("iframe")?.getAttribute("sandbox") ?? "")
      .split(/\s+/)
      .filter(Boolean);
    expect(tokens.filter((t) => t !== "allow-scripts")).toEqual([]);
  });
});

describe("AC6 — a refused URL produces no frame at all", () => {
  const REFUSED = [
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "  javascript:alert(1)",
    "java\tscript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "DATA:text/html;base64,PHNjcmlwdD4=",
    "blob:https://example.com/8f2c",
    "file:///etc/passwd",
    "vbscript:msgbox(1)",
    "about:blank",
  ];

  it.each(REFUSED)("renders no frame for %s", (url) => {
    const { container } = renderEmbed(url);
    expect(container.querySelector("iframe")).toBeNull();
    // "No frame" has a second, unintended way to be true: if `web_embed` is not
    // the registered id, every refusal above passes because the host fell back
    // to the unknown-primitive placeholder and never consulted the policy at
    // all. Pin the refusal to the embed primitive actually having run.
    const host = container.querySelector("[data-container-id='c1']");
    expect(host).toHaveAttribute("data-primitive-id", "web_embed");
    expect(host).not.toHaveAttribute("data-primitive-unknown", "true");
  });

  it.each(REFUSED)("puts %s into no navigable attribute either", (url) => {
    // A refused URL that is echoed back as an `href` or a `data-*` the pane
    // later navigates to is the same hole with an extra click in front of it.
    // Showing the offending URL as *text* is fine and is how an operator fixes
    // the setting; putting it somewhere the browser will follow is not.
    const NAVIGABLE = ["src", "href", "action", "formaction", "srcdoc", "data", "poster"];
    const { container } = renderEmbed(url);
    for (const el of Array.from(container.querySelectorAll("*"))) {
      for (const attr of NAVIGABLE) {
        const value = el.getAttribute(attr);
        const echoed = value !== null && value.trim() === url.trim();
        expect({ attr, value, echoed }).toEqual({ attr, value, echoed: false });
      }
    }
  });

  it("renders no frame for a URL that is missing, empty, or not a string", () => {
    for (const url of [undefined, null, "", "   ", 42, {}, ["https://example.com"]]) {
      const { container } = renderEmbed(url);
      expect(container.querySelector("iframe")).toBeNull();
    }
  });

  it("renders no frame for a relative or scheme-relative URL", () => {
    // `//evil.example` inherits the page's scheme and is not a refusal the
    // scheme allowlist catches unless the URL is resolved before it is checked.
    for (const url of ["/dashboard", "//evil.example/", "example.com"]) {
      const { container } = renderEmbed(url);
      expect(container.querySelector("iframe")).toBeNull();
    }
  });

  it("renders no frame for http on a host the packaged shell would block", () => {
    // The CSP admits `http://127.0.0.1:*` and nothing else in cleartext, so a
    // frame pointed at `http://192.168.1.50:3000/` loads in the dev build and
    // is silently blanked in the shipped app. Refusing it here is what turns
    // that into the echo-back message the operator can actually read.
    const { container } = renderEmbed("http://192.168.1.50:3000/grafana");
    expect(container.querySelector("iframe")).toBeNull();

    const host = container.querySelector("[data-container-id='c1']");
    expect(host).toHaveAttribute("data-primitive-id", "web_embed");
    expect(host).not.toHaveAttribute("data-primitive-unknown", "true");
  });

  it("still renders the container, so the operator can fix the setting", () => {
    // Refusing the URL must not blank the pane or throw — the container is
    // still there and still identifies itself.
    const { container } = renderEmbed("javascript:alert(1)");
    expect(container.querySelector("[data-container-id='c1']")).not.toBeNull();
  });
});

describe("safeEmbedUrl is the single place the policy lives", () => {
  it("returns the URL for http and https and null for everything else", () => {
    expect(safeEmbedUrl("https://example.com/")).toBe("https://example.com/");
    expect(safeEmbedUrl("http://127.0.0.1:8000/")).toBe("http://127.0.0.1:8000/");
    expect(safeEmbedUrl("javascript:alert(1)")).toBeNull();
    expect(safeEmbedUrl("data:text/html,x")).toBeNull();
    expect(safeEmbedUrl("blob:https://example.com/x")).toBeNull();
    expect(safeEmbedUrl("file:///etc/passwd")).toBeNull();
    expect(safeEmbedUrl("")).toBeNull();
  });

  it("admits http from loopback only, matching the shell's frame-src", () => {
    // `embedUrl.ts` and `src-tauri/tauri.conf.json` are one decision written
    // twice, and `frame-src` spends its http allowance on `127.0.0.1:*`. An
    // http URL this function accepts and the CSP then blocks is a blank pane
    // with a console violation behind it — a refusal with no explanation.
    for (const url of ["http://127.0.0.1:8000/", "http://localhost:5173/", "http://[::1]:8000/"]) {
      expect({ url, result: safeEmbedUrl(url) }).not.toEqual({ url, result: null });
    }
    for (const url of [
      "http://internal.example/",
      "http://192.168.1.50:3000/grafana",
      "http://nas.local/ui",
      "http://127.0.0.1.evil.example/",
    ]) {
      expect({ url, result: safeEmbedUrl(url) }).toEqual({ url, result: null });
    }
  });

  it("leaves https open to any host", () => {
    // Only the cleartext branch narrowed. https to an arbitrary host is the
    // primitive's main use and `frame-src https:` admits it.
    for (const url of ["https://internal.example/", "https://192.168.1.50:3000/grafana"]) {
      expect({ url, result: safeEmbedUrl(url) }).not.toEqual({ url, result: null });
    }
  });

  it("decides on the parsed scheme, not on a substring of the text", () => {
    // A denylist written as `url.includes("javascript:")` refuses this, which
    // is a legitimate page, and an allowlist written as
    // `url.startsWith("https")` accepts `https-evil:` — both are the same bug
    // seen from opposite sides.
    const withQuery = safeEmbedUrl("https://example.com/?next=javascript:alert(1)");
    expect(withQuery).not.toBeNull();
    expect(withQuery).toMatch(/^https:\/\/example\.com\//);
    expect(safeEmbedUrl("https-evil://example.com/")).toBeNull();
    expect(safeEmbedUrl("httpx://example.com/")).toBeNull();
  });

  it("accepts an allowed scheme however it is cased", () => {
    // Schemes are case-insensitive, so a check written as
    // `url.startsWith("https:")` refuses a URL a browser would happily load —
    // the same bug as the refusal side, and the one that turns into an
    // `as any`-shaped workaround later.
    expect(safeEmbedUrl("HTTPS://example.com/")).not.toBeNull();
    expect(safeEmbedUrl("Http://localhost:5173/")).not.toBeNull();
  });

  it("strips userinfo from the URL it returns", () => {
    // `https://user:hunter2@example.com/` parses as an allowed scheme, so the
    // policy accepts the page — but the credentials must not travel with it.
    // The returned href is written into the frame's `src` *and* into its
    // `title`, so a password left in it is on screen and in the accessibility
    // tree, and the browser would send it as an Authorization header to a host
    // the operator only meant to display.
    expect(safeEmbedUrl("https://user:hunter2@example.com/app")).toBe("https://example.com/app");
    expect(safeEmbedUrl("http://admin@127.0.0.1:8000/")).toBe("http://127.0.0.1:8000/");
    expect(safeEmbedUrl("https://user:hunter2@example.com/")).not.toContain("hunter2");
    expect(safeEmbedUrl("https://user:hunter2@example.com/")).not.toContain("user");
  });

  it("keeps userinfo out of the rendered frame and its accessible name", () => {
    const { container } = renderEmbed("https://user:hunter2@example.com/app");
    const frame = container.querySelector("iframe");
    expect(frame).not.toBeNull();
    expect(frame).toHaveAttribute("src", "https://example.com/app");
    // The whole rendered subtree, not just `src`: the title attribute is the
    // frame's accessible name and echoed the href verbatim.
    expect(container.innerHTML).not.toContain("hunter2");
  });

  it("refuses a URL that smuggles a second scheme past the first", () => {
    for (const url of [
      "https:/\\evil.example/",
      "https://example.com\t@evil.example/",
      " javascript:alert(1)",
      "javascript\n:alert(1)",
      "%6Aavascript:alert(1)",
    ]) {
      const result = safeEmbedUrl(url);
      // Either refused outright, or normalised to something whose scheme is
      // still one of the two allowed — never returned as the raw text.
      if (result !== null) {
        expect({ url, result }).toEqual({ url, result: expect.stringMatching(/^https?:\/\//i) });
      }
    }
  });
});

describe("the frame covers itself until it paints", () => {
  // An iframe that has not loaded is a white rectangle in a dark app, which
  // reads as a page that loaded blank. The placeholder covers it until the
  // browser says it is done — and must never delay the frame's own load, so it
  // sits over a mounted frame rather than replacing one that is not there yet.
  it("shows a placeholder over a frame that has not loaded", () => {
    const { container } = renderEmbed("https://example.com/app");
    expect(container.querySelector("iframe")).not.toBeNull();
    expect(container.querySelector(".pane-skeleton")).not.toBeNull();
  });

  it("drops the placeholder once the frame loads", () => {
    const { container } = renderEmbed("https://example.com/app");
    fireEvent.load(container.querySelector("iframe")!);
    expect(container.querySelector(".pane-skeleton")).toBeNull();
  });

  it("brings the placeholder back when the URL changes", () => {
    // Otherwise the previous page's "loaded" state uncovers a frame that is
    // fetching, and the operator watches a stale page sit there.
    const { container, rerender } = render(
      <ContainerPrimitiveHost
        containerId="c1"
        settings={{ primitive_id: "web_embed", url: "https://example.com/one" }}
      />,
    );
    fireEvent.load(container.querySelector("iframe")!);
    expect(container.querySelector(".pane-skeleton")).toBeNull();

    rerender(
      <ContainerPrimitiveHost
        containerId="c1"
        settings={{ primitive_id: "web_embed", url: "https://example.com/two" }}
      />,
    );
    expect(container.querySelector(".pane-skeleton")).not.toBeNull();
  });
});
