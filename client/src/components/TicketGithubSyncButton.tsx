import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { githubIssueApi, type GithubLinkSyncResult } from "../api/githubIssueApi";
import { describeLinkSync, syncFieldList } from "../lib/githubSyncSummary";
import { describeError, pushToast } from "../state/toastStore";
import { Button } from "./ui/Button";

/**
 * Sync this one ticket with its GitHub issue, from the ticket's own header.
 *
 * It replaces reaching for the topbar's workspace-wide sync to bring a single
 * ticket up to date. Drawn only for a linked ticket: publishing opens an issue
 * in someone's repository, so it stays in Details beside the words that say so.
 * The link query shares its key with `TicketGithubIssue`, so opening Details
 * costs no second request and the two never disagree.
 *
 * A conflict (a field changed on both sides) is never resolved here; the toast
 * names the fields and sends the operator to Details, where they pick a side.
 */
export function TicketGithubSyncButton({ ticketId }: { ticketId: string }) {
  const qc = useQueryClient();
  const link = useQuery({
    queryKey: ["ticket", ticketId, "github-issue"],
    queryFn: () => githubIssueApi.link(ticketId),
  });

  const sync = useMutation({
    meta: { errorTitle: "Sync with GitHub" },
    mutationFn: () => githubIssueApi.sync(ticketId, "report"),
    onSuccess: (result: GithubLinkSyncResult) => {
      if (result.error) {
        pushToast({ tone: "error", title: "GitHub sync failed", message: result.error });
      } else if (result.conflicts.length) {
        pushToast({
          tone: "warning",
          title: "Changed on both sides",
          message: `${syncFieldList(result.conflicts.map((c) => c.field))} changed in the ticket and on GitHub. Open Details to keep one version.`,
        });
      } else {
        pushToast({ tone: "success", title: "Synced with GitHub", message: describeLinkSync(result) });
      }
      void qc.invalidateQueries({ queryKey: ["ticket", ticketId] });
      void qc.invalidateQueries({ queryKey: ["tickets"] });
    },
  });

  if (link.isError) {
    // Not hidden: a link we could not read is not the same as no link.
    return (
      <Button
        variant="secondary"
        compact
        disabled
        title={`Couldn't read this ticket's GitHub link: ${describeError(link.error, "request failed")}`}
      >
        GitHub unavailable
      </Button>
    );
  }
  if (!link.data) return null;

  const issue = `${link.data.repo}#${link.data.issue_number}`;
  return (
    <Button
      variant="secondary"
      compact
      disabled={sync.isPending}
      aria-label={`Sync with GitHub issue ${issue}`}
      title={`Sync with ${issue}`}
      onClick={() => sync.mutate()}
    >
      <span>{sync.isPending ? "Syncing…" : "Sync"}</span>
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" aria-hidden>
        <path d="M21 12a9 9 0 0 1-15.5 6.2M3 12a9 9 0 0 1 15.5-6.2" />
        <path d="M21 4v5h-5M3 20v-5h5" />
      </svg>
    </Button>
  );
}
