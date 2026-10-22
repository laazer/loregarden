import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { LibraryDetail } from "../../api/referenceLibraryApi";
import { shortDate, upstreamChange } from "../../lib/referenceLibrary";
import { describeError, pushToast } from "../../state/toastStore";
import { Button } from "../ui/Button";

/**
 * When we last reviewed this repository, and whether it has moved since: the
 * default branch's newest commit and how many commits it is ahead of the one we
 * read. Checked daily in the background; "Check now" reads it immediately.
 * Rendered as two `<dt>`/`<dd>` pairs inside the source's facts list.
 */
export function ReferenceSourceUpstream({ detail }: { detail: LibraryDetail }) {
  const queryClient = useQueryClient();
  const { source } = detail;
  const check = useMutation({
    mutationFn: () => api.checkSourceUpstream(source.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["reference-source", source.id] });
      void queryClient.invalidateQueries({ queryKey: ["reference-library"] });
    },
    onError: (error) =>
      pushToast({ tone: "error", title: "Could not check the repository", message: describeError(error) }),
  });
  const change = upstreamChange(source);

  return (
    <>
      <dt>Last reviewed</dt>
      <dd>{source.last_read_at ? shortDate(source.last_read_at) : "Not reviewed yet"}</dd>
      {source.kind === "repo" ? (
        <>
          <dt>Repo updated</dt>
          <dd>
            <span className="rl-upstream">
              {source.upstream_committed_at ? `${shortDate(source.upstream_committed_at)} · ` : ""}
              <span className={`rl-change rl-change--${change.tone}`}>{change.long}</span>
              <Button
                variant="plain"
                compact
                className="rl-check"
                disabled={check.isPending}
                onClick={() => check.mutate()}
              >
                {check.isPending ? "Checking…" : "Check now"}
              </Button>
            </span>
            {source.upstream_checked_at ? (
              <span className="rl-tags">checked {shortDate(source.upstream_checked_at)}</span>
            ) : null}
            {source.upstream_check_error ? (
              <span className="rl-warn rl-check-error" role="status">
                {source.upstream_check_error}
              </span>
            ) : null}
          </dd>
        </>
      ) : null}
    </>
  );
}
