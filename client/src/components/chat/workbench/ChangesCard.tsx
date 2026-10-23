import type { ChatBranch, FeatureLoopRun } from "../../../api/chatWorkbenchTypes";
import { CopyValueButton } from "../../CopyValueButton";
import { relativeTime } from "../relativeTime";
import { WorkbenchCard } from "./WorkbenchCard";

const LIVE = new Set(["queued", "running", "awaiting_permission"]);

/**
 * "What has this chat changed, and what is running for it?" The branch its
 * acting turns write to, and the feature ticket's agent runs, newest first.
 */
export function ChangesCard({
  branch,
  runs,
  hasLoop,
}: {
  branch: ChatBranch | null;
  runs: FeatureLoopRun[];
  hasLoop: boolean;
}) {
  const live = runs.filter((run) => LIVE.has(run.status)).length;
  return (
    <WorkbenchCard title="Changes & runs" meta={live ? `${live} running` : undefined}>
      {branch ? (
        <div className="chat-wb-row">
          <span className="chat-wb-glyph" aria-hidden>⑂</span>
          <span className="chat-wb-row-main" title={branch.worktree_path}>
            {branch.branch}
          </span>
          {branch.has_conflicts ? <span className="chat-wb-tag chat-wb-tag--warn">conflicts</span> : null}
          <CopyValueButton value={branch.branch} what="branch name" />
        </div>
      ) : (
        <p className="chat-side-card-empty">
          No branch yet. The first turn that changes files gets its own.
        </p>
      )}
      {runs.length ? (
        <ul className="chat-wb-rows" aria-label="Feature runs">
          {runs.map((run) => (
            <li key={run.id} className="chat-wb-row">
              <span className={`chat-wb-dot chat-wb-dot--${LIVE.has(run.status) ? "live" : run.status}`} aria-hidden />
              <span className="chat-wb-row-main">
                {run.stage_key || run.agent_id}
                <span className="chat-wb-sub"> · {run.agent_id}</span>
              </span>
              <span className="chat-wb-tag" title={run.run_code}>
                {LIVE.has(run.status)
                  ? run.status.replace("_", " ")
                  : `${run.status} ${run.finished_at ? relativeTime(run.finished_at) : ""}`.trim()}
              </span>
            </li>
          ))}
        </ul>
      ) : hasLoop ? (
        <p className="chat-side-card-empty">No runs yet. A round starts when you accept criteria.</p>
      ) : null}
    </WorkbenchCard>
  );
}
