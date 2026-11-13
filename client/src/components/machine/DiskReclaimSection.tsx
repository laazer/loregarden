import { useState } from "react";

import { ApiError } from "../../api/http";
import type { DockerReclaimPreview, LocalModel } from "../../api/diskTypes";
import {
  useDockerReclaimable,
  useInvalidateDisk,
  useLocalModels,
} from "../../hooks/useDiskUsage";
import { formatDiskBytes, MODEL_SOURCE_LABELS } from "../../lib/diskUsage";
import { describeError } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { PaneSkeleton } from "../ui/PaneSkeleton";
import { DockerCleanupModal } from "./DockerCleanupModal";
import { LocalModelsTable } from "./LocalModelsTable";
import { ModelDeleteModal } from "./ModelDeleteModal";
import "./DiskReclaimSection.css";

/**
 * "What big things can I free without hunting through folders?"
 *
 * Docker and downloaded models are the two that a folder scan cannot help
 * with: Docker hides everything in one disk image, and a model folder is not a
 * cache the safety check will clear. Each gets its own card that asks the app
 * that owns the data, says what freeing it costs, and opens a confirm dialog.
 * Works without a scan, so it is the first thing a full disk shows.
 */
export function DiskReclaimSection() {
  const invalidate = useInvalidateDisk();
  return (
    <section className="disk-reclaim" aria-labelledby="disk-reclaim-heading">
      <h2 id="disk-reclaim-heading" className="disk-reclaim-heading">
        Quick wins
      </h2>
      <div className="disk-reclaim-cards">
        <DockerCard onCleaned={invalidate} />
        <ModelsCard onDeleted={invalidate} />
      </div>
    </section>
  );
}

function DockerCard({ onCleaned }: { onCleaned: () => void }) {
  const docker = useDockerReclaimable();
  // The preview the dialog opened with: the refetch after a cleanup must not
  // unmount the dialog (and its result) by dropping to "nothing to free".
  const [reviewing, setReviewing] = useState<DockerReclaimPreview | null>(null);
  // A 503 is Docker not installed or not running: a calm state, not an error.
  const notRunning =
    docker.error instanceof ApiError && docker.error.status === 503 // ts-org: allow-instanceof — the status picks the "not running" state, not the message
      ? docker.error.message
      : null;

  let body;
  if (docker.isPending) {
    body = <PaneSkeleton variant="list" rows={1} label="Asking Docker…" />;
  } else if (notRunning) {
    body = (
      <p className="disk-reclaim-note">
        {notRunning} Nothing to check until it runs.{" "}
        <Button
          variant="plain"
          className="disk-crumb"
          onClick={() => void docker.refetch()}
        >
          Check again
        </Button>
      </p>
    );
  } else if (docker.isError) {
    body = (
      <p className="disk-notice is-error" role="alert">
        {describeError(docker.error, "Could not ask Docker what it can free.")}{" "}
        <Button
          variant="plain"
          className="disk-crumb"
          onClick={() => void docker.refetch()}
        >
          Try again
        </Button>
      </p>
    );
  } else if (docker.data.total_bytes === 0) {
    body = (
      <p className="disk-reclaim-note">
        Nothing to free: every image and volume is in use, and there is no build
        cache.
        {docker.data.kept_named_volumes.length
          ? ` ${docker.data.kept_named_volumes.length} named volume(s) no container uses are kept on purpose.`
          : ""}
      </p>
    );
  } else {
    const d = docker.data;
    body = (
      <>
        <p className="disk-reclaim-figure">
          About {formatDiskBytes(d.total_bytes)} unused
        </p>
        <p className="disk-reclaim-note">
          {d.unused_images} unused image{d.unused_images === 1 ? "" : "s"} ·{" "}
          {formatDiskBytes(d.build_cache_bytes)} build cache ·{" "}
          {d.volumes.length} unattached volume
          {d.volumes.length === 1 ? "" : "s"}
        </p>
        <Button
          variant="primary"
          compact
          aria-expanded={reviewing !== null}
          onClick={() => setReviewing(d)}
        >
          Review Docker cleanup…
        </Button>
      </>
    );
  }
  return (
    <article
      className="disk-reclaim-card"
      aria-labelledby="disk-docker-heading"
    >
      <h3 id="disk-docker-heading">Docker</h3>
      {body}
      {reviewing ? (
        <DockerCleanupModal
          open
          preview={reviewing}
          onClose={() => setReviewing(null)}
          onCleaned={onCleaned}
        />
      ) : null}
    </article>
  );
}

function ModelsCard({ onDeleted }: { onDeleted: () => void }) {
  const models = useLocalModels();
  const [deleting, setDeleting] = useState<LocalModel | null>(null);

  let body;
  if (models.isPending) {
    body = <PaneSkeleton variant="list" rows={3} label="Listing models…" />;
  } else if (models.isError) {
    body = (
      <p className="disk-notice is-error" role="alert">
        {describeError(models.error, "Could not list the downloaded models.")}{" "}
        <Button
          variant="plain"
          className="disk-crumb"
          onClick={() => void models.refetch()}
        >
          Try again
        </Button>
      </p>
    );
  } else {
    const { locations, models: list } = models.data;
    const total = list.reduce((sum, m) => sum + m.frees_bytes, 0);
    const unreadable = locations.filter((l) => l.error);
    body = (
      <>
        {unreadable.map((l) => (
          <p key={l.path} className="disk-notice is-error" role="alert">
            Could not read {MODEL_SOURCE_LABELS[l.source]} models in {l.path}:{" "}
            {l.error}
          </p>
        ))}
        {list.length === 0 ? (
          <p className="disk-reclaim-note">
            No LM Studio or Ollama models found. Looked in{" "}
            {locations.map((l) => l.path).join(" and ")}.
          </p>
        ) : (
          <>
            <p className="disk-reclaim-figure">
              {list.length} model{list.length === 1 ? "" : "s"} ·{" "}
              {formatDiskBytes(total)}
            </p>
            <LocalModelsTable models={list} onDelete={setDeleting} />
          </>
        )}
      </>
    );
  }
  return (
    <article
      className="disk-reclaim-card disk-reclaim-card--wide"
      aria-labelledby="disk-models-heading"
    >
      <h3 id="disk-models-heading">Downloaded models</h3>
      {body}
      <ModelDeleteModal
        model={deleting}
        onClose={() => setDeleting(null)}
        onDeleted={onDeleted}
      />
    </article>
  );
}
