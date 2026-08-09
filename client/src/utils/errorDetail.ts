/** Pull the server's `detail` out of a thrown API error.
 *
 * FastAPI returns `{"detail": "..."}` and the api client throws that body as the
 * error message, so nearly every mutation needs the same unwrap. It was copied
 * inline at a dozen call sites; this is now the only implementation.
 *
 * Returns null for a non-Error, so callers can render "no error" without a
 * separate instanceof check.
 */
export function errorDetail(error: unknown, fallback = ""): string | null {
  if (!(error instanceof Error)) return null;
  try {
    const parsed = JSON.parse(error.message) as { detail?: string };
    return parsed.detail ?? error.message;
  } catch {
    return error.message || fallback;
  }
}
