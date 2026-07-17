import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";

const BLOCKER_LABELS: Record<string, string> = {
  active_agent_runs: "an agent is running",
  active_orchestrations: "an orchestration is running",
  running_workflow_stages: "a workflow stage is running",
};

function blockerText(blockers: string[]): string {
  const parts = blockers.map((b) => BLOCKER_LABELS[b] ?? b);
  return parts.length ? `Can't reload — ${parts.join(", ")}.` : "";
}

/** Wait for the server to come back after it restarts out from under us. */
async function waitForHealth(timeoutMs = 30_000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  // The old worker answers /health until it actually dies, so miss the window and
  // we'd report success against the very process we're replacing.
  await new Promise((r) => setTimeout(r, 1000));
  while (Date.now() < deadline) {
    try {
      await api.health();
      return true;
    } catch {
      await new Promise((r) => setTimeout(r, 500));
    }
  }
  return false;
}

/**
 * Pull working-tree changes into the running server before a human tests at a gate.
 *
 * The dev server runs with `--reload-exclude *.py`, so an agent's backend fix does not
 * go live on its own: without this you test the old code and the fix looks broken.
 */
export function BringInChangesButton({ workspaceSlug }: { workspaceSlug: string }) {
  const [phase, setPhase] = useState<"idle" | "reloading" | "done" | "failed">("idle");
  const [error, setError] = useState<string | null>(null);

  const status = useQuery({
    queryKey: ["reload-status", workspaceSlug],
    queryFn: () => api.reloadStatus(workspaceSlug),
    enabled: Boolean(workspaceSlug),
    refetchInterval: 5000,
    retry: false,
  });

  // Reloading this server only means anything where the server is what's under test.
  if (!status.data?.supported) return null;

  const blockers = status.data.blockers ?? [];
  const busy = phase === "reloading";
  const disabled = busy || !status.data.ready;

  const run = async () => {
    setPhase("reloading");
    setError(null);
    try {
      await api.reloadServer(workspaceSlug);
    } catch (err) {
      // A dropped response is the expected case — the worker died mid-reply. Only a
      // refusal (409/400) is a real error, and that arrives as a proper response.
      const httpStatus = (err as { status?: number })?.status;
      if (httpStatus === 409 || httpStatus === 400) {
        setError((err as Error).message || "Reload refused");
        setPhase("failed");
        return;
      }
    }
    const back = await waitForHealth();
    setPhase(back ? "done" : "failed");
    if (!back) setError("Server did not come back — check the dev server logs.");
  };

  const label = { idle: "Bring in changes", reloading: "Reloading…", done: "Changes are live", failed: "Retry reload" }[
    phase
  ];

  return (
    <div className="bring-in-changes">
      <button type="button" className="btn-secondary btn-compact" disabled={disabled} onClick={run}>
        {label}
      </button>
      <span className="bring-in-changes-hint">
        {error ??
          (blockers.length
            ? blockerText(blockers)
            : phase === "done"
              ? "Server restarted onto the current working tree."
              : "Restart the server so it serves the current working tree.")}
      </span>
    </div>
  );
}
