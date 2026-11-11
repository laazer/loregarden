import type { ChatMode } from "../api/client";

/**
 * Whether Baxter can change anything in this conversation, always on show.
 *
 * Persistent rather than advisory-only on purpose: "can act" and "cannot act"
 * are both facts the operator wants before typing, and a badge that only
 * appears when something is wrong teaches people to read its absence as
 * "loading" rather than as "fine".
 */

/** Fixes the operator can apply from here. Only for causes marked remediable. */
export type ChatModeFix = "runtime" | "checkout";

const FIX_LABEL: Record<ChatModeFix, string> = {
  runtime: "Change runtime",
  checkout: "Check out branch",
};

function fixFor(mode: ChatMode): ChatModeFix | null {
  if (!mode.remediable) return null;
  if (mode.cause === "adapter_cannot_execute") return "runtime";
  if (mode.cause === "adapter_needs_permission_bypass") return "runtime";
  if (mode.cause === "adapter_cannot_research") return "runtime";
  if (mode.cause === "branch_not_checked_out") return "checkout";
  return null;
}

export function ChatModePill({
  mode,
  canAct,
  onFix,
}: {
  mode: ChatMode | undefined;
  /**
   * The boolean the session has always carried. Used only when `mode` is
   * absent — a snapshot from a server that predates the field still knows
   * whether it can act, and losing the badge entirely there would be a
   * regression dressed as a redesign.
   */
  canAct?: boolean;
  /** Omit to render the reason without an action. */
  onFix?: (fix: ChatModeFix) => void;
}) {
  const resolved: ChatMode | undefined =
    mode ??
    (canAct === undefined
      ? undefined
      : canAct
        ? { mode: "act", cause: null, reason: "", advice: "", remediable: false }
        : {
            mode: "advisory",
            cause: null,
            reason:
              "Baxter can only answer in this conversation — the adapter behind it has no way " +
              "to run tools.",
            advice: "",
            remediable: false,
          });
  if (!resolved) return null;
  const mode_ = resolved;

  const acting = mode_.mode === "act";
  const fix = acting ? null : fixFor(mode_);
  // The reason and the remedy are the server's words, so this badge and
  // Baxter's own answer to "why can't you edit?" cannot disagree.
  const title = acting
    ? "Baxter can run tools in this conversation — read code, edit files, and use the Loregarden tools."
    : `${mode_.reason} ${mode_.advice}`.trim();

  return (
    <span
      className={`app-action-bar-pill${acting ? " app-action-bar-pill--context" : " app-action-bar-pill--waiting"}`}
      title={title}
    >
      <span className="app-action-bar-pill-dot" aria-hidden />
      {/* A research thread is read-only by design, so it says what it is doing. */}
      <span>{acting ? "can act" : mode_.cause === "research_thread" ? "researching" : "advisory"}</span>
      {fix && onFix ? (
        <button
          type="button"
          className="app-action-bar-pill-fix"
          onClick={() => onFix(fix)}
          title={mode_.advice}
        >
          {FIX_LABEL[fix]}
        </button>
      ) : null}
    </span>
  );
}
