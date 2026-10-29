/**
 * The confirm before a Needs fix runs: it says what will change, where, and
 * how long it may take, and names its submit by the action.
 *
 * A pull request is cut from the repository's default branch in a throwaway
 * worktree, so the operator's checkout is never touched; pushing it runs that
 * repository's own pre-push checks, which is why the wait can run to minutes.
 * Creating a repository writes into the folder, so it says which folder.
 *
 * Focus is trapped and returned to the button that opened it. Nothing
 * dismisses it while the request is in flight, and a failure keeps it open
 * with the reason under the text.
 */

import type { WorkspaceInstaller } from "../../api/localInstancesTypes";
import type { WorkspaceFix } from "../../lib/workspaceNeeds";
import { describeError } from "../../state/toastStore";
import { IconCloseButton } from "../IconCloseButton";
import { Button } from "../ui/Button";
import { ModalShell } from "../ui/ModalShell";

export interface PendingFix {
  slug: string;
  name: string;
  repoRoot: string;
  fix: WorkspaceFix;
}

const BLOCK_FILE: Record<WorkspaceInstaller, string> = {
  hooks: "loregarden's gate block in lefthook.yml",
  docs: "loregarden's section of AGENTS.md",
};

function wording(pending: PendingFix): { title: string; effect: string; submit: string; busy: string } {
  const { fix, name, repoRoot } = pending;
  if (fix.kind === "create_repository") {
    return {
      title: `Create ${name}'s repository?`,
      effect: `Creates a git repository at ${repoRoot} and commits loregarden's gates and AGENTS.md to it.`,
      submit: "Create repository",
      busy: "Creating…",
    };
  }
  return {
    title: `Open a pull request in ${name}?`,
    effect:
      `loregarden cuts a branch from the repository's default branch in a throwaway worktree, writes ` +
      `${BLOCK_FILE[fix.installer]} there, then commits, pushes and opens a pull request. Your checkout ` +
      `at ${repoRoot} is not touched. Pushing runs that repository's own pre-push checks, so this can ` +
      `take a few minutes.`,
    submit: "Open pull request",
    busy: "Opening pull request…",
  };
}

export function WorkspaceFixDialog({
  pending,
  inFlight,
  error,
  onClose,
  onConfirm,
}: {
  pending: PendingFix | null;
  inFlight: boolean;
  error: unknown;
  onClose: () => void;
  onConfirm: (pending: PendingFix) => void;
}) {
  const words = pending ? wording(pending) : null;
  return (
    <ModalShell
      open={pending !== null}
      onDismiss={inFlight ? undefined : onClose}
      labelledBy="workspace-fix-title"
      describedBy="workspace-fix-effect"
    >
      {pending && words ? (
        <>
          <div className="modal-header">
            <div>
              <div className="state-label">{pending.fix.label.replace(/…$/, "")}</div>
              <h2 id="workspace-fix-title" className="modal-title">
                {words.title}
              </h2>
            </div>
            <IconCloseButton disabled={inFlight} onClick={onClose} />
          </div>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (!inFlight) onConfirm(pending);
            }}
          >
            <div className="modal-body">
              <p id="workspace-fix-effect" className="workspace-fix-effect">
                {words.effect}
              </p>
              {inFlight && pending.fix.kind === "pull_request" ? (
                <p className="workspace-fix-note" role="status">
                  Waiting on the push and on GitHub. This dialog closes when the pull request is open.
                </p>
              ) : null}
              {error ? (
                <p className="workspace-fix-error" role="alert">
                  {describeError(error, "It did not complete; nothing was opened.")}
                </p>
              ) : null}
            </div>
            <div className="modal-footer">
              <Button variant="secondary" disabled={inFlight} onClick={onClose}>
                Cancel
              </Button>
              <Button variant="primary" type="submit" disabled={inFlight}>
                {inFlight ? words.busy : words.submit}
              </Button>
            </div>
          </form>
        </>
      ) : null}
    </ModalShell>
  );
}
