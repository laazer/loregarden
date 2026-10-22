import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api } from "../../api/client";
import { pathForPage } from "../../lib/appNavigation";
import { describeError } from "../../state/toastStore";
import { Input } from "../ui/Input";

const SETTLED = new Set(["done", "wont_do"]);

function toggle(list: string[], value: string, on: boolean): string[] {
  return on ? [...list, value] : list.filter((item) => item !== value);
}

/**
 * Who a review is for: the workspaces it reads for (one reading serves them all)
 * and the initiatives its tickets join. Workspaces the source already serves,
 * and initiatives already following it, show as fixed so the choice reads as
 * "and also".
 */
export function ReferenceTargetsPicker({
  fixedWorkspaces,
  workspaces,
  onWorkspaces,
  fixedInitiatives = [],
  initiatives,
  onInitiatives,
  showInitiatives = true,
}: {
  /** Already served (or the workspace being added from): shown checked, not editable. */
  fixedWorkspaces: string[];
  workspaces: string[];
  onWorkspaces: (next: string[]) => void;
  /** Initiative ids already following the source. */
  fixedInitiatives?: string[];
  initiatives: string[];
  onInitiatives: (next: string[]) => void;
  showInitiatives?: boolean;
}) {
  const workspaceList = useQuery({ queryKey: ["workspaces"], queryFn: () => api.workspaces() });
  const initiativeList = useQuery({
    queryKey: ["initiatives"],
    queryFn: () => api.initiatives(),
    enabled: showInitiatives,
  });
  const activeWorkspaces = (workspaceList.data ?? []).filter((w) => !w.archived_at);
  const openInitiatives = (initiativeList.data ?? []).filter((i) => !SETTLED.has(i.state));

  return (
    <div className="rl-targets">
      <fieldset className="rl-target-group">
        <legend>Read it for</legend>
        <div className="rl-target-options rl-target-options--row">
        {workspaceList.isPending ? (
          <span className="rl-muted">Loading workspaces…</span>
        ) : workspaceList.isError ? (
          <span className="rl-warn" role="status">
            Could not list workspaces ({describeError(workspaceList.error, "the request failed")}); it reads for{" "}
            {fixedWorkspaces.join(", ")} only.
          </span>
        ) : (
          activeWorkspaces.map((workspace) => {
            const fixed = fixedWorkspaces.includes(workspace.slug);
            return (
              <label key={workspace.slug} className="rl-check-option">
                <Input
                  type="checkbox"
                  checked={fixed || workspaces.includes(workspace.slug)}
                  disabled={fixed}
                  onChange={(event) => onWorkspaces(toggle(workspaces, workspace.slug, event.target.checked))}
                />
                {workspace.slug}
                {fixed ? <span className="rl-muted"> (already)</span> : null}
              </label>
            );
          })
        )}
        </div>
      </fieldset>
      {showInitiatives ? (
        <fieldset className="rl-target-group">
          <legend>Feed its tickets into</legend>
          {initiativeList.isPending ? (
            <span className="rl-muted">Loading initiatives…</span>
          ) : initiativeList.isError ? (
            <span className="rl-warn" role="status">
              Could not list initiatives ({describeError(initiativeList.error, "the request failed")}); link them from
              the source afterwards.
            </span>
          ) : openInitiatives.length === 0 ? (
            <span className="rl-muted">
              No open initiative. <Link to={pathForPage("initiatives")}>Start one on the Initiatives page</Link>.
            </span>
          ) : (
            openInitiatives.map((initiative) => {
              const fixed = fixedInitiatives.includes(initiative.id);
              return (
                <label key={initiative.id} className="rl-check-option">
                  <Input
                    type="checkbox"
                    checked={fixed || initiatives.includes(initiative.id)}
                    disabled={fixed}
                    onChange={(event) =>
                      onInitiatives(toggle(initiatives, initiative.id, event.target.checked))
                    }
                  />
                  <span className="rl-option-text">
                    {initiative.title}{" "}
                    <span className="rl-option-id">{initiative.external_id}</span>
                    {fixed ? <span className="rl-muted"> · already following</span> : null}
                  </span>
                </label>
              );
            })
          )}
        </fieldset>
      ) : null}
    </div>
  );
}
