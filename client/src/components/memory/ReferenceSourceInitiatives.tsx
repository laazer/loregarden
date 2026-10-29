import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../../api/client";
import type { LibraryDetail, LinkResult, LinkedInitiative } from "../../api/referenceLibraryApi";
import { initiativePath } from "../../lib/appNavigation";
import { linkTargetsSummary } from "../../lib/referenceLibrary";
import { describeError, pushToast } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { ReferenceLinkButton } from "./ReferenceLinkModal";

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

function LinkedRow({
  sourceId,
  initiative,
  onUnlinked,
}: {
  sourceId: string;
  initiative: LinkedInitiative;
  onUnlinked: (message: string) => void;
}) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const unlink = useMutation({
    mutationFn: () => api.unlinkSourceInitiative(sourceId, initiative.id),
    onSuccess: (result) => {
      setConfirming(false);
      onUnlinked(
        `Unlinked ${result.initiative}: ${plural(result.removed.length, "ticket")} left it` +
          (result.kept.length > 0 ? `; ${result.kept.length} stayed, held by another source` : "") +
          ".",
      );
      void queryClient.invalidateQueries({ queryKey: ["reference-source", sourceId] });
    },
    onError: (error) =>
      pushToast({ tone: "error", title: "Could not unlink the initiative", message: describeError(error) }),
  });

  return (
    <li className="rl-initiative">
      <Link className="rl-ticket-link" to={initiativePath(initiative.id)}>
        <span className="rl-ticket-id">{initiative.external_id}</span> {initiative.title}
      </Link>
      <span className="rl-tags">brought {plural(initiative.members_added, "ticket")}</span>
      {confirming ? (
        <span className="rl-confirm" role="group" aria-label={`Unlink ${initiative.external_id}?`}>
          <span>
            The {plural(initiative.members_added, "ticket")} this link added leave {initiative.external_id}; tickets
            added by hand or by another source stay.
          </span>
          <Button
            variant="secondary"
            compact
            disabled={unlink.isPending}
            onClick={() => unlink.mutate()}
          >
            {unlink.isPending ? "Unlinking…" : "Unlink"}
          </Button>
          <Button variant="plain" compact disabled={unlink.isPending} onClick={() => setConfirming(false)}>
            Cancel
          </Button>
        </span>
      ) : (
        <Button
          variant="plain"
          compact
          className="rl-unlink"
          aria-label={`Unlink ${initiative.external_id}`}
          onClick={() => setConfirming(true)}
        >
          Unlink…
        </Button>
      )}
    </li>
  );
}

/**
 * Which initiatives this source feeds. Linking one brings the source's open
 * tickets into it as members (nothing is re-parented), and later passes add
 * the tickets they touch; unlinking takes out only what the link added.
 */
export function ReferenceSourceInitiatives({ detail }: { detail: LibraryDetail }) {
  const sourceId = detail.source.id;
  const [message, setMessage] = useState("");
  const [skipped, setSkipped] = useState<LinkResult["skipped"]>([]);

  return (
    <section className="rl-section" aria-labelledby="rl-initiatives-heading">
      <h3 id="rl-initiatives-heading">
        Initiatives following it <span className="count-pill">{detail.initiatives.length}</span>
      </h3>
      {detail.initiatives.length === 0 ? (
        <p className="rl-muted">
          No initiative follows this source. Link one and its open tickets join the initiative, now and as later
          passes add more; settled tickets stay out.
        </p>
      ) : (
        <ul className="rl-plain">
          {detail.initiatives.map((initiative) => (
            <LinkedRow
              key={initiative.id}
              sourceId={sourceId}
              initiative={initiative}
              onUnlinked={(text) => {
                setMessage(text);
                setSkipped([]);
              }}
            />
          ))}
        </ul>
      )}
      <ReferenceLinkButton
        detail={detail}
        onLinked={(result) => {
          setMessage(linkTargetsSummary(detail.source.name, result));
          setSkipped(result.initiatives.flatMap((link) => link.skipped));
        }}
      />
      <p className="rl-result" role="status">
        {message}
      </p>
      {skipped.length > 0 ? (
        <details className="rl-details">
          <summary>Why {plural(skipped.length, "ticket")} did not join</summary>
          <ul className="rl-plain">
            {skipped.map((skip) => (
              <li key={skip.ticket}>{skip.reason}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}
