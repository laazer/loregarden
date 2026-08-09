import { VITE_API_BASE } from "./viteEnv";

export const API_BASE = VITE_API_BASE ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** The one fetch wrapper every endpoint goes through: JSON in, JSON out, and a
 * server `detail` message surfaced as the thrown error rather than raw text. */
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    let message = text || res.statusText;
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed.detail === "string") message = parsed.detail;
    } catch {
      // response wasn't JSON — fall back to raw text
    }
    throw new ApiError(res.status, message);
  }
  return res.json() as Promise<T>;
}
