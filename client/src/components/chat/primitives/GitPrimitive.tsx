import { useQuery } from "@tanstack/react-query";

import {
  fetchBranchActivity,
  fetchCommitSnapshot,
} from "../../../lib/branchTriageApi";
import { PrimitiveCard } from "./PrimitiveCard";
import {
  OpenBranchTriageButton,
  OpenIdeButton,
} from "./ResourceActionButton";
import type { BranchHistoryPart, CommitPart } from "./types";

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const minutes = Math.max(0, Math.floor((Date.now() - then) / 60_000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export function BranchHistoryPrimitive({ part }: { part: BranchHistoryPart }) {
  const limit = part.limit ?? 8;
  const query = useQuery({
    queryKey: ["branch-activity", part.workspace_slug, part.branch, limit],
    queryFn: () => fetchBranchActivity(part.workspace_slug, part.branch, limit),
  });
  const activity = query.data;
  const commits = activity?.commits ?? [];

  return (
    <PrimitiveCard
      title={part.title ?? part.branch}
      subtitle={
        activity ? (activity.upstream ? `tracking ${activity.upstream}` : "No upstream") : part.branch
      }
      loading={query.isLoading}
      error={
        query.error
          ? query.error instanceof Error
            ? query.error.message
            : "Failed to load branch history"
          : null
      }
      meta={
        activity ? (
          <>
            <span>{commits.length} recent commits</span>
            <span>{part.workspace_slug}</span>
          </>
        ) : null
      }
      resourceAction={
        <OpenBranchTriageButton
          workspaceSlug={part.workspace_slug}
          branch={part.branch}
        />
      }
    >
      {commits.length ? (
        <ol className="lg-primitive-commit-list">
          {commits.map((commit) => (
            <li key={commit.sha}>
              <span
                className={[
                  "lg-primitive-commit-dot",
                  commit.pushed ? "is-pushed" : null,
                ]
                  .filter(Boolean)
                  .join(" ")}
                aria-hidden
              />
              <div className="lg-primitive-commit-copy">
                <strong>{commit.message}</strong>
                <span>
                  <code>{commit.short_sha}</code> · {commit.author} ·{" "}
                  {commit.pushed ? "pushed" : "local only"}
                </span>
              </div>
              <time dateTime={commit.date}>{relativeTime(commit.date)}</time>
            </li>
          ))}
        </ol>
      ) : (
        <p className="lg-primitive-card-sub">No commits on this branch yet.</p>
      )}
    </PrimitiveCard>
  );
}

export function CommitPrimitive({ part }: { part: CommitPart }) {
  const query = useQuery({
    queryKey: ["commit", part.workspace_slug, part.sha],
    queryFn: () => fetchCommitSnapshot(part.workspace_slug, part.sha),
  });
  const commit = query.data;

  return (
    <PrimitiveCard
      title={part.title ?? commit?.message ?? "Commit"}
      subtitle={commit ? `${commit.short_sha} · ${commit.author}` : part.sha}
      loading={query.isLoading}
      error={
        query.error
          ? query.error instanceof Error
            ? query.error.message
            : "Failed to load commit"
          : null
      }
      tone={commit?.pushed ? "ok" : "default"}
      meta={
        commit ? (
          <>
            <span>{commit.pushed ? "pushed" : "local only"}</span>
            <span>{relativeTime(commit.date)}</span>
            {part.branch ? <span>{part.branch}</span> : null}
          </>
        ) : null
      }
      resourceAction={
        part.branch ? (
          <OpenBranchTriageButton
            workspaceSlug={part.workspace_slug}
            branch={part.branch}
          />
        ) : (
          <OpenIdeButton workspaceSlug={part.workspace_slug} />
        )
      }
    >
      {commit ? (
        <div className="lg-primitive-commit-detail">
          {commit.body ? <p>{commit.body}</p> : null}
          <div className="lg-primitive-commit-stats">
            <span>{commit.files_changed} files</span>
            <span className="is-add">+{commit.insertions}</span>
            <span className="is-delete">−{commit.deletions}</span>
          </div>
        </div>
      ) : null}
    </PrimitiveCard>
  );
}
