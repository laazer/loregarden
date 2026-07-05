import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api, type EditorRefsResponse } from "../../api/client";

interface GitRefSwitcherProps {
  workspaceSlug: string;
  refs: EditorRefsResponse | undefined;
  contextRoot: string;
  isLoading?: boolean;
  disabled?: boolean;
}

export function GitRefSwitcher({
  workspaceSlug,
  refs,
  contextRoot,
  isLoading = false,
  disabled = false,
}: GitRefSwitcherProps) {
  const qc = useQueryClient();

  const checkout = useMutation({
    mutationFn: (body: { branch?: string; worktree_path?: string }) =>
      api.editorCheckout(workspaceSlug, body),
    onSuccess: (data) => {
      qc.setQueryData(["editor-refs", workspaceSlug, data.context_root], data);
      qc.invalidateQueries({ queryKey: ["editor-browse", workspaceSlug] });
      qc.invalidateQueries({ queryKey: ["editor-file", workspaceSlug] });
    },
  });

  const busy = disabled || isLoading || checkout.isPending;
  const currentLabel =
    refs?.worktrees.find((item) => item.current)?.label ||
    refs?.current_branch ||
    contextRoot ||
    "workspace";

  return (
    <div className="git-ref-switcher">
      <label className="git-ref-switcher-label">
        <span>Branch</span>
        <select
          className="btn-secondary filter-select git-ref-select"
          value={refs?.current_branch ?? ""}
          disabled={busy || !refs?.branches.length}
          onChange={(event) => {
            const branch = event.target.value;
            if (!branch || branch === refs?.current_branch) return;
            checkout.mutate({ branch });
          }}
        >
          {!refs?.branches.length ? <option value="">No branches</option> : null}
          {(refs?.branches ?? []).map((branch) => (
            <option key={branch.name} value={branch.name}>
              {branch.name}
              {branch.current ? " (current)" : ""}
            </option>
          ))}
        </select>
      </label>

      <label className="git-ref-switcher-label">
        <span>Worktree</span>
        <select
          className="btn-secondary filter-select git-ref-select"
          value={refs?.context_path ?? ""}
          disabled={busy || !refs?.worktrees.length}
          onChange={(event) => {
            const worktreePath = event.target.value;
            if (!worktreePath || worktreePath === refs?.context_path) return;
            checkout.mutate({ worktree_path: worktreePath });
          }}
        >
          {!refs?.worktrees.length ? <option value="">No worktrees</option> : null}
          {(refs?.worktrees ?? []).map((worktree) => (
            <option key={worktree.path} value={worktree.path}>
              {worktree.label}
              {worktree.current ? " (active)" : ""}
            </option>
          ))}
        </select>
      </label>

      <span className="git-ref-current" title={refs?.context_path}>
        {checkout.isPending ? "Switching…" : currentLabel}
      </span>

      {checkout.error ? (
        <span className="git-ref-error">
          {checkout.error instanceof Error ? checkout.error.message : "Checkout failed"}
        </span>
      ) : null}
    </div>
  );
}
