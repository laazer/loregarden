import type { RuntimeOptions } from "../api/client";

/**
 * What to say beneath a model field, per provider and per discovery state.
 *
 * A cold probe answers before it has an answer, so a model Select holding only
 * its default sentinel means one of four different things. Until this existed,
 * all four rendered the same placeholder, and that placeholder asserted a cause
 * ("install OpenCode to discover") nothing had established — which is what left
 * the picker looking permanently empty.
 *
 * The copy is pinned in the spec (R2/R4); it is not invented here.
 */

/** The three providers whose catalogue is probed. `claude`/`cursor` are static. */
export type ProbedProvider = "codex" | "lmstudio" | "opencode";

const DISCOVERING: Record<ProbedProvider, string> = {
  codex: "Discovering Codex models…",
  lmstudio: "Discovering LM Studio models…",
  opencode: "Discovering OpenCode models…",
};

const NOTHING_FOUND: Record<ProbedProvider, (target: string) => string> = {
  codex: () => "Codex reported no models beyond its profile default.",
  lmstudio: (target) =>
    `No chat models loaded at ${target}. Load one in LM Studio, or type its id below.`,
  opencode: () =>
    "OpenCode reported no models. Check `opencode auth login`, or type a provider/model id below.",
};

export interface ModelDiscoveryState {
  /** The one line to render beneath the control, or null when there is nothing to say. */
  hint: string | null;
  /** True only while probing with nothing cached: "there is nothing to pick yet". */
  selectDisabled: boolean;
}

/**
 * Resolve the discovery state for the provider whose field is on screen.
 *
 * A missing `model_discovery` block (a cached 200 from a pre-deploy server), an
 * entry this client has no copy for, and a status outside the union the client
 * knows are all read as `fresh`: an unrecognized value must never be the branch
 * that disables the control, because a permanently disabled, permanently empty
 * picker is the regression this whole surface exists to prevent.
 */
export function modelDiscoveryState(
  provider: ProbedProvider,
  discovery: RuntimeOptions["model_discovery"],
  optionCount: number,
): ModelDiscoveryState {
  const entry = discovery?.[provider];
  const status = entry?.status ?? "fresh";
  const hasCatalogue = optionCount > 1;

  if (status === "probing") {
    return { hint: DISCOVERING[provider], selectDisabled: !hasCatalogue };
  }
  if (status === "unavailable") {
    // Rendered verbatim: the reason is server-side knowledge ("connection
    // refused", the resolved base URL) the client cannot reconstruct.
    return { hint: entry?.detail || null, selectDisabled: false };
  }
  if (status === "stale" && hasCatalogue) {
    return { hint: "Refreshing…", selectDisabled: false };
  }
  // `fresh`, `stale` with nothing cached, and anything unrecognized.
  if (!hasCatalogue) {
    return { hint: NOTHING_FOUND[provider](entry?.target ?? ""), selectDisabled: false };
  }
  return { hint: null, selectDisabled: false };
}
