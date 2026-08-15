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
 */

const ALLOWED_PROTOCOLS = new Set(["http:", "https:"]);

/**
 * The URL to hand an iframe, or `null` if it must not be loaded.
 *
 * Returns the *normalised* href rather than the raw text, so a caller can never
 * echo back something the parser read differently than the browser would.
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

  if (!ALLOWED_PROTOCOLS.has(parsed.protocol.toLowerCase())) return null;
  return parsed.href;
}
