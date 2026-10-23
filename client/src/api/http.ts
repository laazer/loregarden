import { VITE_API_BASE } from "./viteEnv";

/** Where the API lives when the page cannot reach it at its own origin. */
export const LOOPBACK_API_BASE = "http://127.0.0.1:8000";

/** The API's base URL as seen from wherever this page is running.
 *
 * Same origin by default, so nothing has to be told this machine's address.
 * The dev server proxies `/api`, `/health` and both socket paths through to the
 * backend, which is what lets a browser on another device reach the whole app
 * through one origin. Naming an address here instead breaks the moment a DHCP
 * lease moves the host, and the failure reads as a dead server rather than a
 * moved one.
 *
 * Tauri is the exception the fallback exists for: its packaged build serves the
 * page from `tauri://localhost`, which proxies nothing, so the backend has to be
 * named outright. That origin sits in the server's CORS allowlist for exactly
 * this reason. A `file://` page is treated the same way.
 *
 * An explicit `VITE_API_BASE` still wins, for pointing a build at a backend that
 * is neither. Blank counts as unset: an empty base yields relative URLs, and
 * `new WebSocket("/ws/queue")` throws on one.
 */
export function resolveApiBase(
  viteApiBase: string | undefined,
  origin: string | undefined,
): string {
  if (viteApiBase) return viteApiBase;
  if (origin && /^https?:\/\//.test(origin)) return origin;
  return LOOPBACK_API_BASE;
}

export const API_BASE = resolveApiBase(VITE_API_BASE, globalThis.location?.origin);

export class ApiError extends Error {
  status: number;

  /** The error response's parsed JSON body, when it had one.
   *
   * `status` and `message` answer "what failed"; a refusal that also carries
   * structure — the agent delete's `{detail, references}` — needs the structure
   * itself, and the alternatives are a second request for what the rejection
   * already said, or parsing identifiers back out of the sentence. `undefined`
   * when the body was not JSON, so "no body" and "an empty one" stay apart.
   */
  body?: unknown;

  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    if (body !== undefined) this.body = body;
  }
}

/** The one fetch wrapper every endpoint goes through: JSON in, JSON out, and a
 * server `detail` message surfaced as the thrown error rather than raw text. */
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // A multipart body carries its own Content-Type, boundary included; naming
  // JSON over it would make the server read the upload as a broken JSON body.
  const jsonHeader: Record<string, string> =
    init?.body instanceof FormData ? {} : { "Content-Type": "application/json" };
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { ...jsonHeader, ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    // Read once. A `Response` is consumed on the first read, so calling
    // `res.json()` after this throws `TypeError: Body is unusable` in a browser
    // while passing happily against a test double that allows both reads.
    const text = await res.text();
    let message = text || res.statusText;
    let body: unknown;
    try {
      body = JSON.parse(text);
    } catch {
      // silent-ok: the body is not JSON, so the raw text already read into
      // `message` is the best description there is; the ApiError below still
      // throws, still carries the status, and leaves `body` undefined so a
      // caller can tell an unparsed body from an empty one.
    }
    const detail = (body as { detail?: unknown } | null)?.detail;
    if (typeof detail === "string") message = detail;
    throw new ApiError(res.status, message, body);
  }
  // No Content has no body to parse; `res.json()` would throw on success.
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}
