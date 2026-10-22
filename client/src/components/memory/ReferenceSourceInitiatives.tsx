import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../../api/client";
import type { LibraryDetail, LinkResult, LinkedInitiative } from "../../api/referenceLibraryApi";
import { initiativePath, pathForPage } from "../../lib/appNavigation";
import { describeError, pushToast } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { Select } from "../ui/Select";

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** What a link did, in one sentence a person can check against the initiative. */
function linkSummary(result: LinkResult): string {
  const parts = [`${plural(result.added.length, "open ticket")} joined ${result.initiative}`];
  if (result.settled_left_out > 0) parts.push(`${plural(result.settled_left_out, "settled ticket")} stayed out`);
  if (result.skipped.length > 0) parts.push(`${result.skipped.length} the initiative already covers`);
  return `${parts.join("; ")}.`;
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
  const queryClient = useQueryClient();
  const pickerId = useId();
  const sourceId = detail.source.id;
  const [chosen, setChosen] = useState("");
  const [message, setMessage] = useState("");
  const [skipped, setSkipped] = useState<LinkResult["skipped"]>([]);

  const initiatives = useQuery({ queryKey: ["initiatives"], queryFn: api.initiatives });
  const linkedIds = new Set(detail.initiatives.map((i) => i.id));
  const choices = (initiatives.data ?? []).filter((i) => !linkedIds.has(i.id));

  const link = useMutation({
    mutationFn: (initiativeId: string) => api.linkSourceInitiative(sourceId, initiativeId),
    onSuccess: (result) => {
      setChosen("");
      setMessage(linkSummary(result));
      setSkipped(result.skipped);
      void queryClient.invalidateQueries({ queryKey: ["reference-source", sourceId] });
      void queryClient.invalidateQueries({ queryKey: ["initiatives"] });
    },
    onError: (error) =>
      pushToast({ tone: "error", title: "Could not link the initiative", message: describeError(error) }),
  });

  let picker;
  if (initiatives.isPending) {
    picker = <p className="rl-muted">Loading initiatives…</p>;
  } else if (initiatives.isError) {
    picker = (
      <p className="rl-warn" role="alert">
        Initiatives did not load: {describeError(initiatives.error, "the request failed")}.{" "}
        <Button variant="plain" compact onClick={() => void initiatives.refetch()}>
          Retry
        </Button>
      </p>
    );
  } else if ((initiatives.data ?? []).length === 0) {
    picker = (
      <p className="rl-muted">
        No initiatives yet. <Link to={pathForPage("initiatives")}>Create one on the Initiatives page</Link>, then link it here.
      </p>
    );
  } else if (choices.length === 0) {
    picker = <p className="rl-muted">Every initiative already follows this source.</p>;
  } else {
    picker = (
      <form
        className="rl-link-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (chosen && !link.isPending) link.mutate(chosen);
        }}
      >
        <label htmlFor={pickerId}>Link an initiative</label>
        <Select id={pickerId} value={chosen} onChange={(event) => setChosen(event.target.value)}>
          <option value="">Choose an initiative…</option>
          {choices.map((i) => (
            <option key={i.id} value={i.id}>
              {i.external_id} — {i.title}
            </option>
          ))}
        </Select>
        <Button type="submit" variant="secondary" compact disabled={!chosen || link.isPending}>
          {link.isPending ? "Linking…" : "Link"}
        </Button>
      </form>
    );
  }

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
      {picker}
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
