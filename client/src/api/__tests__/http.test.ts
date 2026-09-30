/** Which address the client talks to, and why it is usually its own.
 *
 * The bug this pins: the LAN address was passed in at launch, so a renewed DHCP
 * lease left the page calling a host that no longer existed. Every request
 * failed, and it read as a dead backend rather than a moved one.
 */

import { LOOPBACK_API_BASE, resolveApiBase } from "../http";

describe("resolveApiBase", () => {
  it("uses the page's own origin, so no address has to be configured", () => {
    // The dev server proxies /api, /health and the sockets, so same-origin
    // reaches the backend from any device that can load the page at all.
    expect(resolveApiBase(undefined, "http://192.168.0.165:5173")).toBe(
      "http://192.168.0.165:5173",
    );
  });

  it("follows the host when the address changes", () => {
    const before = resolveApiBase(undefined, "http://192.168.0.163:5173");
    const after = resolveApiBase(undefined, "http://192.168.0.165:5173");

    expect(before).not.toBe(after);
    expect(after).toBe("http://192.168.0.165:5173");
  });

  it("names the backend outright for a Tauri page, which proxies nothing", () => {
    // The packaged desktop build serves from tauri://localhost. Same-origin
    // there reaches no server at all, which is why that origin is in the
    // backend's CORS allowlist.
    expect(resolveApiBase(undefined, "tauri://localhost")).toBe(LOOPBACK_API_BASE);
  });

  it("does the same for a file:// page", () => {
    expect(resolveApiBase(undefined, "file://")).toBe(LOOPBACK_API_BASE);
  });

  it("falls back when there is no origin to read", () => {
    expect(resolveApiBase(undefined, undefined)).toBe(LOOPBACK_API_BASE);
  });

  it("lets an explicit VITE_API_BASE win over both", () => {
    expect(resolveApiBase("http://10.0.0.2:8000", "http://localhost:5173")).toBe(
      "http://10.0.0.2:8000",
    );
  });

  it("treats a blank VITE_API_BASE as unset", () => {
    // An empty base yields relative URLs, and `new WebSocket("/ws/queue")`
    // throws on one — so blank must not be honoured as a real setting.
    expect(resolveApiBase("", "http://localhost:5173")).toBe("http://localhost:5173");
  });

  it("gives the socket helpers an absolute ws:// URL to build from", () => {
    // Every socket URL is this base with http swapped for ws; a relative base
    // would produce "/ws/queue", which the WebSocket constructor rejects.
    const base = resolveApiBase(undefined, "http://192.168.0.165:5173");

    expect(base.replace(/^http/, "ws")).toBe("ws://192.168.0.165:5173");
  });
});
