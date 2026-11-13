import { useState } from "react";

import { diskApi } from "../../api/diskApi";
import type {
  DockerReclaimPreview,
  DockerReclaimResult,
} from "../../api/diskTypes";
import { formatDiskBytes } from "../../lib/diskUsage";
import { describeError, pushToast } from "../../state/toastStore";
import { IconCloseButton } from "../IconCloseButton";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { ModalShell } from "../ui/ModalShell";

interface DockerCleanupModalProps {
  open: boolean;
  preview: DockerReclaimPreview;
  onClose: () => void;
  /** After it ran, so the page can re-read Docker, the volume and the history. */
  onCleaned: () => void;
}

type Part = "images" | "build_cache" | "volumes";

/**
 * Confirm what Docker frees: unused images, the build cache, unattached
 * anonymous volumes. Each is a checkbox with its size; containers and named
 * volumes are never on the list, and the kept named ones are shown so nobody
 * wonders where their database went.
 */
export function DockerCleanupModal({
  open,
  preview,
  onClose,
  onCleaned,
}: DockerCleanupModalProps) {
  const [running, setRunning] = useState(false);
  return (
    <ModalShell
      open={open}
      onDismiss={running ? undefined : onClose}
      labelledBy="docker-cleanup-title"
      describedBy="docker-cleanup-effect"
      panelClassName="modal-panel-wide disk-cleanup-panel"
    >
      {open ? (
        <DockerCleanupBody
          preview={preview}
          onClose={onClose}
          onCleaned={onCleaned}
          running={running}
          setRunning={setRunning}
        />
      ) : null}
    </ModalShell>
  );
}

function DockerCleanupBody({
  preview,
  onClose,
  onCleaned,
  running,
  setRunning,
}: Omit<DockerCleanupModalProps, "open"> & {
  running: boolean;
  setRunning: (value: boolean) => void;
}) {
  const parts: { key: Part; label: string; bytes: number; count: number }[] = [
    {
      key: "images",
      label: "Images no container uses",
      bytes: preview.unused_images_bytes,
      count: preview.unused_images,
    },
    {
      key: "build_cache",
      label: "Build cache",
      bytes: preview.build_cache_bytes,
      count: preview.build_cache_entries,
    },
    {
      key: "volumes",
      label: "Unnamed volumes no container is attached to",
      bytes: preview.volumes_bytes,
      count: preview.volumes.length,
    },
  ];
  const [chosen, setChosen] = useState<ReadonlySet<Part>>(
    new Set(parts.filter((p) => p.count > 0).map((p) => p.key)),
  );
  const [error, setError] = useState("");
  const [result, setResult] = useState<DockerReclaimResult | null>(null);

  const chosenBytes = parts
    .filter((p) => chosen.has(p.key))
    .reduce((sum, p) => sum + p.bytes, 0);
  const toggle = (key: Part) =>
    setChosen((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const run = async () => {
    if (running || chosen.size === 0) return;
    setRunning(true);
    setError("");
    try {
      const outcome = await diskApi.reclaimDocker({
        images: chosen.has("images"),
        build_cache: chosen.has("build_cache"),
        volumes: chosen.has("volumes")
          ? preview.volumes.map((v) => v.name)
          : [],
      });
      setResult(outcome);
      onCleaned();
      pushToast({
        tone: outcome.failed_count ? "warning" : "success",
        title: `Docker freed ${formatDiskBytes(outcome.reclaimed_bytes)}`,
        message: outcome.failed_count
          ? "Some steps failed; the dialog says which."
          : "macOS gets the space back once Docker Desktop shrinks its disk.",
      });
    } catch (caught) {
      setError(describeError(caught, "The Docker cleanup failed."));
    } finally {
      setRunning(false);
    }
  };

  return (
    <>
      <div className="modal-header">
        <div>
          <div className="state-label">Machine · Disk</div>
          <h2 id="docker-cleanup-title" className="modal-title">
            {result ? "Docker cleanup finished" : "Clean up Docker"}
          </h2>
          <p id="docker-cleanup-effect" className="modal-subtitle">
            Deleted for good; Docker downloads or rebuilds them when something
            needs them again. Containers, running or stopped, and named volumes
            are never touched.
          </p>
        </div>
        <IconCloseButton disabled={running} onClick={onClose} />
      </div>
      <div className="modal-body disk-cleanup-body">
        {result ? (
          <DockerOutcome result={result} />
        ) : (
          <>
            {error ? (
              <p className="disk-cleanup-error" role="alert">
                {error}
              </p>
            ) : null}
            <ul className="disk-cleanup-list">
              {parts.map((part) => (
                <li key={part.key} className="disk-cleanup-item">
                  <label className="disk-cleanup-check">
                    <Input
                      type="checkbox"
                      checked={chosen.has(part.key)}
                      disabled={running || part.count === 0}
                      onChange={() => toggle(part.key)}
                    />
                    <span className="disk-cleanup-path">
                      {part.label} ({part.count})
                    </span>
                    <span className="disk-cleanup-size">
                      {formatDiskBytes(part.bytes)}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
            {preview.kept_named_volumes.length > 0 ? (
              <details className="disk-reclaim-kept">
                <summary>
                  Kept: {preview.kept_named_volumes.length} named volume
                  {preview.kept_named_volumes.length === 1 ? "" : "s"} no
                  container uses
                </summary>
                <p className="disk-cleanup-note">
                  A name usually means a database someone meant to keep. Remove
                  one yourself with docker volume rm if you are sure.
                </p>
                <ul className="disk-reclaim-volumes">
                  {preview.kept_named_volumes.map((v) => (
                    <li key={v.name}>
                      <span className="disk-folders-path">{v.name}</span>
                      <span>{formatDiskBytes(v.size_bytes)}</span>
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
            <p className="disk-cleanup-note">
              Sizes are Docker&apos;s own estimate. The space frees inside
              Docker&apos;s disk image first; macOS gets it back when Docker
              Desktop shrinks that image, which can take until Docker restarts.
            </p>
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
              disabled={running || chosen.size === 0}
              onClick={() => void run()}
            >
              {running
                ? "Cleaning up Docker…"
                : `Free about ${formatDiskBytes(chosenBytes)} from Docker`}
            </Button>
          </>
        )}
      </div>
    </>
  );
}

function DockerOutcome({ result }: { result: DockerReclaimResult }) {
  return (
    <>
      <p className="disk-cleanup-summary" role="status">
        Docker freed {formatDiskBytes(result.reclaimed_bytes)}
        {result.failed_count ? `; ${result.failed_count} step(s) failed` : ""}.
      </p>
      {result.steps.length === 0 ? (
        <p className="disk-cleanup-summary">Nothing ran.</p>
      ) : (
        <ul
          className="disk-cleanup-list scroll-focus-ring"
          aria-label="Docker cleanup outcome by step"
          tabIndex={0}
        >
          {result.steps.map((step) => (
            <li
              key={step.label}
              className={`disk-cleanup-item is-${step.outcome}`}
            >
              <div className="disk-cleanup-check">
                <span className="disk-cleanup-path">{step.label}</span>
                <span className="disk-cleanup-size">
                  {step.outcome === "deleted"
                    ? formatDiskBytes(step.reclaimed_bytes)
                    : step.outcome === "refused"
                      ? "Skipped"
                      : "Failed"}
                </span>
              </div>
              {step.detail ? (
                <p className="disk-cleanup-note">{step.detail}</p>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
