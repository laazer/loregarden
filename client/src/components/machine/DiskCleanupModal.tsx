import { useEffect, useState } from "react";

import { ApiError } from "../../api/http";
import { diskApi } from "../../api/diskApi";
import type { DiskCleanupPlan, DiskCleanupResult } from "../../api/diskTypes";
import { DISK_KIND_LABELS, formatDiskBytes } from "../../lib/diskUsage";
import { describeError, pushToast } from "../../state/toastStore";
import { IconCloseButton } from "../IconCloseButton";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { ModalShell } from "../ui/ModalShell";

interface DiskCleanupModalProps {
  open: boolean;
  paths: string[];
  onClose: () => void;
  /** After a cleanup ran, with what it moved, so the page can refresh and unmark. */
  onCleaned: (result: DiskCleanupResult) => void;
}

/**
 * Review, then move to the Trash: the only way anything on the Disk tab is removed.
 *
 * Opening it asks the server to check every marked path (git status, running
 * processes, irreplaceable files). Each one comes back allowed or refused, with
 * the reason. Only allowed ones can be confirmed, each can still be unticked,
 * and the button says exactly what it does. The server checks each path again
 * when it runs and refuses one that changed in between.
 */
export function DiskCleanupModal({
  open,
  paths,
  onClose,
  onCleaned,
}: DiskCleanupModalProps) {
  const [running, setRunning] = useState(false);
  return (
    <ModalShell
      open={open}
      onDismiss={running ? undefined : onClose}
      labelledBy="disk-cleanup-title"
      describedBy="disk-cleanup-effect"
      panelClassName="modal-panel-wide disk-cleanup-panel"
    >
      {open ? (
        <DiskCleanupBody
          paths={paths}
          onClose={onClose}
          onCleaned={onCleaned}
          running={running}
          setRunning={setRunning}
        />
      ) : null}
    </ModalShell>
  );
}

function DiskCleanupBody({
  paths,
  onClose,
  onCleaned,
  running,
  setRunning,
}: Omit<DiskCleanupModalProps, "open"> & {
  running: boolean;
  setRunning: (value: boolean) => void;
}) {
  const [plan, setPlan] = useState<DiskCleanupPlan | null>(null);
  const [checkError, setCheckError] = useState("");
  const [confirmed, setConfirmed] = useState<ReadonlySet<string>>(new Set());
  const [result, setResult] = useState<DiskCleanupResult | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;
    setPlan(null);
    setCheckError("");
    diskApi
      .previewCleanup(paths)
      .then((next) => {
        if (!live) return;
        setPlan(next);
        setConfirmed(
          new Set(
            next.items
              .filter((i) => i.verdict.allowed)
              .map((i) => i.verdict.path),
          ),
        );
      })
      .catch((error: unknown) => {
        if (live)
          setCheckError(describeError(error, "The safety check failed."));
      });
    return () => {
      live = false;
    };
  }, [paths, attempt]);

  const allowed = plan?.items.filter((item) => item.verdict.allowed) ?? [];
  const refused = plan?.items.filter((item) => !item.verdict.allowed) ?? [];
  const chosen = allowed.filter((item) => confirmed.has(item.verdict.path));
  const chosenBytes = chosen.reduce((sum, item) => sum + item.size_bytes, 0);

  const toggle = (path: string) =>
    setConfirmed((previous) => {
      const next = new Set(previous);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  const run = async () => {
    if (!plan || running || chosen.length === 0) return;
    setRunning(true);
    try {
      const outcome = await diskApi.executeCleanup(
        plan.plan_id,
        chosen.map((item) => item.verdict.path),
      );
      setResult(outcome);
      onCleaned(outcome);
      pushToast({
        tone:
          outcome.failed_count || outcome.refused_count ? "warning" : "success",
        title: `Moved ${outcome.trashed_count} item${outcome.trashed_count === 1 ? "" : "s"} to the Trash`,
        message: `${formatDiskBytes(outcome.trashed_bytes)} comes back when you empty the Trash.`,
      });
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        // The preview was used or timed out: check again rather than guess.
        setCheckError(describeError(error));
        setPlan(null);
      } else {
        setCheckError(describeError(error, "Moving to the Trash failed."));
      }
    } finally {
      setRunning(false);
    }
  };

  return (
    <>
      <div className="modal-header">
        <div>
          <div className="state-label">Machine · Disk</div>
          <h2 id="disk-cleanup-title" className="modal-title">
            {result ? "Cleanup finished" : "Review cleanup"}
          </h2>
          <p id="disk-cleanup-effect" className="modal-subtitle">
            Allowed items move to the Trash. Nothing is deleted until you empty
            it.
          </p>
        </div>
        <IconCloseButton disabled={running} onClick={onClose} />
      </div>
      <div
        className="modal-body disk-cleanup-body"
        aria-busy={!plan && !checkError && !result}
      >
        {result ? (
          <CleanupOutcome result={result} />
        ) : checkError ? (
          <div className="disk-cleanup-error" role="alert">
            <p>{checkError}</p>
            <Button
              variant="secondary"
              compact
              onClick={() => setAttempt((n) => n + 1)}
            >
              Check again
            </Button>
          </div>
        ) : !plan ? (
          <p className="disk-cleanup-checking" role="status">
            Checking {paths.length} item{paths.length === 1 ? "" : "s"}: git
            status, running processes and irreplaceable files…
          </p>
        ) : (
          <>
            {allowed.length > 0 ? (
              <section aria-labelledby="disk-allowed-heading">
                <h3 id="disk-allowed-heading">
                  Can go ({allowed.length},{" "}
                  {formatDiskBytes(plan.allowed_bytes)})
                </h3>
                <ul className="disk-cleanup-list">
                  {allowed.map(({ verdict, size_bytes }) => (
                    <li key={verdict.path} className="disk-cleanup-item">
                      <label className="disk-cleanup-check">
                        <Input
                          type="checkbox"
                          checked={confirmed.has(verdict.path)}
                          disabled={running}
                          onChange={() => toggle(verdict.path)}
                        />
                        <span className="disk-cleanup-path">
                          {verdict.path}
                        </span>
                        <span className="disk-cleanup-size">
                          {formatDiskBytes(size_bytes)}
                        </span>
                      </label>
                      <p className="disk-cleanup-note">
                        {DISK_KIND_LABELS[verdict.kind]}.{" "}
                        {verdict.notes.join(" ")}
                      </p>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
            {refused.length > 0 ? (
              <section aria-labelledby="disk-refused-heading">
                <h3 id="disk-refused-heading">Kept ({refused.length})</h3>
                <ul className="disk-cleanup-list">
                  {refused.map(({ verdict, size_bytes }) => (
                    <li
                      key={verdict.path}
                      className="disk-cleanup-item is-refused"
                    >
                      <div className="disk-cleanup-check">
                        <span className="disk-cleanup-path">
                          {verdict.path}
                        </span>
                        <span className="disk-cleanup-size">
                          {formatDiskBytes(size_bytes)}
                        </span>
                      </div>
                      <p className="disk-cleanup-note">
                        {verdict.reasons.join(" ")}
                      </p>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
            {plan.covered.length > 0 ? (
              <p className="disk-cleanup-note">
                {plan.covered.length} marked item
                {plan.covered.length === 1 ? " is" : "s are"} inside another
                marked folder and go with it.
              </p>
            ) : null}
            {allowed.length === 0 ? (
              <p className="disk-cleanup-note">
                Nothing marked can go safely. Each item above says why.
              </p>
            ) : null}
          </>
        )}
      </div>
      <div className="modal-footer">
        {result ? (
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        ) : (
          <>
            <Button variant="secondary" disabled={running} onClick={onClose}>
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={!plan || running || chosen.length === 0}
              onClick={() => void run()}
            >
              {running
                ? "Moving to Trash…"
                : `Move ${chosen.length} item${chosen.length === 1 ? "" : "s"} (${formatDiskBytes(chosenBytes)}) to Trash`}
            </Button>
          </>
        )}
      </div>
    </>
  );
}

function CleanupOutcome({ result }: { result: DiskCleanupResult }) {
  return (
    <>
      <p className="disk-cleanup-summary" role="status">
        Moved {result.trashed_count} to the Trash (
        {formatDiskBytes(result.trashed_bytes)}).
        {result.refused_count
          ? ` ${result.refused_count} kept because something changed.`
          : ""}
        {result.failed_count ? ` ${result.failed_count} failed.` : ""} Empty the
        Trash in Finder to get the space back.
      </p>
      <ul className="disk-cleanup-list">
        {result.results.map((item) => (
          <li
            key={item.path}
            className={`disk-cleanup-item is-${item.outcome}`}
          >
            <div className="disk-cleanup-check">
              <span className="disk-cleanup-path">{item.path}</span>
              <span className="disk-cleanup-size">
                {item.outcome === "trashed"
                  ? "In Trash"
                  : item.outcome === "refused"
                    ? "Kept"
                    : "Failed"}
              </span>
            </div>
            {item.detail ? (
              <p className="disk-cleanup-note">{item.detail}</p>
            ) : null}
          </li>
        ))}
      </ul>
    </>
  );
}
