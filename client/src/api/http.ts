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
  /** The server's `detail` when it is structured (`{message, ...}`), for a caller
   * that acts on more than the words; undefined for a plain string detail. */
  detail: unknown;

  /** The parsed response body, when the body was JSON; undefined when it was not.
   *
   * `message` only ever carries a *string* `detail` (or a structured detail's
   * own `message`), so an endpoint whose error is structured — a 409 carrying
   * `{error, expected_revision, current_revision, message}` — reached its
   * caller as raw JSON text and nothing else, and the caller had to re-parse
   * in a component a string this wrapper had already parsed. The whole body is
   * kept, not a hand-picked field: the one fetch wrapper every endpoint throws
   * through is not the place that decides which keys an endpoint's error carries.
   */
  payload?: unknown;

  constructor(status: number, message: string, detail?: unknown, payload?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.payload = payload;
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
    const text = await res.text();
    let message = text || res.statusText;
    let payload: unknown;
    let detail: unknown;
    try {
      const parsed: unknown = JSON.parse(text);
      payload = parsed;
      const parsedDetail = (parsed as { detail?: unknown } | null)?.detail;
      if (typeof parsedDetail === "string") {
        message = parsedDetail;
      } else if (
        parsedDetail &&
        typeof parsedDetail === "object" &&
        typeof (parsedDetail as { message?: unknown }).message === "string"
      ) {
        message = (parsedDetail as { message: string }).message;
        detail = parsedDetail;
      }
    } catch {
      // silent-ok: the body is not JSON, so the raw text already read into
      // `message` is the best description there is; the ApiError below still
      // throws and still carries the status.
    }
    throw new ApiError(res.status, message, detail, payload);
  }
  // No Content has no body to parse; `res.json()` would throw on success.
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}
