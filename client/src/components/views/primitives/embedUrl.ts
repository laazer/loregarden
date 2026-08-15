/**
 * The whole of the web-embed URL policy, in one function.
 *
 * A container's `settings` map is stored verbatim by the server, so the URL is
 * attacker-influencable text. Two rules:
 *
 *   - the decision is made on the *parsed* scheme, never on a substring. A
 *     denylist (`includes("javascript:")`) refuses a legitimate page whose query
 *     string mentions one, and an allowlist (`startsWith("https")`) accepts
 *     `https-evil:` — the same bug from either side.
 *   - refusing means returning `null`, and the caller renders no frame at all.
 *     A warning next to a frame that loaded the URL is not a refusal.
 *
 * Nothing here fetches the URL. Resolving it, previewing it, or checking it for
 * reachability would send a request the operator did not make, from the app's
 * own network position.
 *
 * What this file does *not* own, and leans on:
 *
 *   - The frame is sandboxed without `allow-same-origin`, so it is a unique
 *     opaque origin and cannot read the app's storage or reach its Tauri IPC.
 *     That is `webEmbedPrimitive`'s `sandbox` attribute, not this function.
 *   - The embedded page can still *send* cross-origin requests to the local
 *     backend. It is stopped there by two properties of the server, neither of
 *     which is enforced here: `cors_origins` in `server/loregarden/config.py`
 *     is a fixed allowlist, so no reply is readable by an embedded origin; and
 *     every mutating route takes JSON, so no form post — which CORS does not
 *     gate — can reach one. Relaxing `cors_origins` (a wildcard, a
 *     reflected `Origin`) or adding a form-encoded endpoint would weaken the
 *     embed without touching this module or its tests.
 *   - The packaged shell's CSP (`src-tauri/tauri.conf.json`) declares
 *     `frame-src`, which is what stops a frame this function never saw. Its
 *     value and this allowlist are one decision in two places, so they say the
 *     same thing: `https:` from any host, and `http:` from loopback only. A
 *     function that admitted `http://nas.local/ui` the CSP then blocked would
 *     hand the operator a blank frame and a console violation — a refusal with
 *     no explanation, in the one place this module exists to explain.
 */

const ALLOWED_PROTOCOLS = new Set(["http:", "https:"]);

/**
 * The hosts `http:` is allowed from — nothing else is reachable in cleartext.
 *
 * `URL` lower-cases the host and keeps an IPv6 literal in its brackets, so
 * these are compared against `hostname` exactly as the parser produces it.
 */
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "[::1]", "localhost"]);

/**
 * The URL to hand an iframe, or `null` if it must not be loaded.
 *
 * Returns the *normalised* href rather than the raw text, so a caller can never
 * echo back something the parser read differently than the browser would, and
 * with any userinfo removed.
 */
export function safeEmbedUrl(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const candidate = raw.trim();
  if (candidate === "") return null;

  let parsed: URL;
  try {
    // No base: a relative or scheme-relative URL ("/x", "//evil.example") has
    // no scheme of its own to allowlist and must not inherit the app's.
    parsed = new URL(candidate);
  } catch {
    return null;
  }

  const protocol = parsed.protocol.toLowerCase();
  if (!ALLOWED_PROTOCOLS.has(protocol)) return null;

  // Cleartext only to loopback. This is the line that keeps the function and
  // the packaged CSP in agreement; widening it without widening `frame-src`
  // turns a refusal the operator can read into a blank pane they cannot.
  if (protocol === "http:" && !LOOPBACK_HOSTS.has(parsed.hostname)) return null;

  // Drop credentials rather than refuse the URL: `https://user:pw@host/` is a
  // legitimate address, but the returned href is written into the iframe `src`
  // and into its accessible name, so the password would be on screen and in
  // the a11y tree. The browser also sends userinfo as an Authorization header
  // to a host the operator only meant to *display*.
  parsed.username = "";
  parsed.password = "";
  return parsed.href;
}
