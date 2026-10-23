import { useState } from "react";

import type { DiskRootSummary } from "../../api/diskTypes";
import { useDiskRoots } from "../../hooks/useDiskUsage";
import { describeError } from "../../state/toastStore";
import { IconCloseButton } from "../IconCloseButton";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { ModalShell } from "../ui/ModalShell";

interface DiskFoldersModalProps {
  open: boolean;
  onClose: () => void;
  /** What the last scan covered, workspaces and their worktrees included. */
  scannedRoots: DiskRootSummary[];
  added: string[];
}

const ORIGIN_LABELS: Record<DiskRootSummary["origin"], string> = {
  workspace: "Workspace",
  worktrees: "Worktrees",
  added: "Added",
};

/**
 * What the scan covers: every workspace and its worktrees (automatic), plus
 * folders added here, like ~/.cache or another repository's checkout.
 * Changes apply from the next scan; the page says so rather than rescanning.
 */
export function DiskFoldersModal({
  open,
  onClose,
  scannedRoots,
  added,
}: DiskFoldersModalProps) {
  const { add, remove } = useDiskRoots();
  const [path, setPath] = useState("");
  const busy = add.isPending || remove.isPending;
  const automatic = scannedRoots.filter((root) => root.origin !== "added");

  return (
    <ModalShell
      open={open}
      onDismiss={busy ? undefined : onClose}
      labelledBy="disk-folders-title"
      panelClassName="disk-folders-panel"
    >
      <div className="modal-header">
        <div>
          <div className="state-label">Machine · Disk</div>
          <h2 id="disk-folders-title" className="modal-title">
            Scanned folders
          </h2>
          <p className="modal-subtitle">Changes apply from the next scan.</p>
        </div>
        <IconCloseButton disabled={busy} onClick={onClose} />
      </div>
      <div className="modal-body disk-folders-body">
        <section aria-labelledby="disk-added-heading">
          <h3 id="disk-added-heading">Added folders</h3>
          {added.length === 0 ? (
            <p className="disk-folders-empty">
              None yet. Add a cache folder or another project to see it in the
              map.
            </p>
          ) : (
            <ul className="disk-folders-list">
              {added.map((folder) => (
                <li key={folder}>
                  <span className="disk-folders-path">{folder}</span>
                  <Button
                    variant="secondary"
                    compact
                    disabled={busy}
                    onClick={() => remove.mutate(folder)}
                    aria-label={`Stop scanning ${folder}`}
                  >
                    Remove
                  </Button>
                </li>
              ))}
            </ul>
          )}
          <form
            className="disk-folders-add"
            onSubmit={(event) => {
              event.preventDefault();
              const trimmed = path.trim();
              if (!trimmed || busy) return;
              add.mutate(trimmed, { onSuccess: () => setPath("") });
            }}
          >
            <label className="disk-folders-field">
              <span>Folder path</span>
              <Input
                value={path}
                placeholder="~/.cache"
                disabled={busy}
                aria-invalid={add.isError || undefined}
                aria-describedby={
                  add.isError ? "disk-folders-error" : undefined
                }
                onChange={(event) => setPath(event.target.value)}
              />
            </label>
            <Button
              variant="primary"
              type="submit"
              disabled={busy || !path.trim()}
            >
              {add.isPending ? "Adding…" : "Add folder"}
            </Button>
          </form>
          {add.isError ? (
            <p
              id="disk-folders-error"
              className="disk-folders-error"
              role="alert"
            >
              {describeError(add.error, "Could not add that folder.")}
            </p>
          ) : null}
        </section>
        <section aria-labelledby="disk-auto-heading">
          <h3 id="disk-auto-heading">Scanned automatically</h3>
          {automatic.length === 0 ? (
            <p className="disk-folders-empty">
              Run a scan to list the workspaces and worktrees it covers.
            </p>
          ) : (
            <ul className="disk-folders-list">
              {automatic.map((root) => (
                <li key={root.path}>
                  <span className="disk-folders-path">{root.path}</span>
                  <span className="disk-kind">
                    {ORIGIN_LABELS[root.origin]}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
      <div className="modal-footer">
        <Button variant="secondary" disabled={busy} onClick={onClose}>
          Close
        </Button>
      </div>
    </ModalShell>
  );
}
