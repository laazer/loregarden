import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";

import type { FeatureLoopStartRequest } from "../../../api/chatWorkbenchTypes";
import { api } from "../../../api/client";
import { describeError } from "../../../state/toastStore";
import { IconCloseButton } from "../../IconCloseButton";
import { Button } from "../../ui/Button";
import { Input } from "../../ui/Input";
import { ModalShell } from "../../ui/ModalShell";
import { Select } from "../../ui/Select";

type Mode = "new" | "existing";

/**
 * Bind this chat to the ticket its feature loop builds: a new one, or one
 * that exists. A new one is a feature under a milestone, or a stand-alone
 * milestone when none is picked, since only a milestone may stand alone.
 * Nothing runs until the operator accepts the first criteria.
 */
export function FeatureLoopStartModal({
  open,
  workspaces,
  defaultWorkspace,
  saving,
  onStart,
  onClose,
}: {
  open: boolean;
  /** Where the new ticket may live: the chat's workspaces in scope. */
  workspaces: string[];
  defaultWorkspace: string;
  saving: boolean;
  onStart: (body: FeatureLoopStartRequest) => Promise<unknown>;
  onClose: () => void;
}) {
  const ids = { title: useId(), name: useId(), workspace: useId(), parent: useId(), existing: useId() };
  const [mode, setMode] = useState<Mode>("new");
  const [title, setTitle] = useState("");
  const [workspace, setWorkspace] = useState(defaultWorkspace);
  const [parent, setParent] = useState("");
  const [existing, setExisting] = useState("");
  const [failure, setFailure] = useState("");

  const milestones = useQuery({
    queryKey: ["feature-loop-milestones", workspace],
    queryFn: () =>
      api.tickets({
        workspace,
        work_item_type: "milestone",
        state: ["backlog", "in_progress", "blocked"],
      }),
    enabled: open && mode === "new" && Boolean(workspace),
  });

  const ready = mode === "new" ? title.trim() !== "" && workspace !== "" : existing.trim() !== "";
  const body: FeatureLoopStartRequest =
    mode === "new"
      ? { title: title.trim(), workspace_slug: workspace, parent_ticket_id: parent }
      : { existing_ticket: existing.trim() };

  return (
    <ModalShell open={open} onDismiss={saving ? undefined : onClose} labelledBy={ids.title}>
      <form
        aria-label="Start a feature loop"
        onSubmit={(event) => {
          event.preventDefault();
          if (!ready || saving) return;
          setFailure("");
          onStart(body).then(onClose, (error) =>
            setFailure(describeError(error, "the loop did not start")),
          );
        }}
      >
        <div className="modal-header">
          <div>
            <h2 id={ids.title} className="modal-title">
              Build a feature from this chat
            </h2>
            <p className="modal-subtitle">
              Baxter proposes acceptance criteria as you talk; you accept or drop each one. Accepted
              criteria run through the ticket&apos;s workflow a round at a time.
            </p>
          </div>
          <IconCloseButton disabled={saving} onClick={onClose} />
        </div>
        <div className="modal-body">
          <fieldset className="chat-wb-modes">
            <legend>Ticket</legend>
            <label>
              <Input
                type="radio"
                name="feature-loop-mode"
                checked={mode === "new"}
                onChange={() => setMode("new")}
              />
              A new ticket
            </label>
            <label>
              <Input
                type="radio"
                name="feature-loop-mode"
                checked={mode === "existing"}
                onChange={() => setMode("existing")}
              />
              A ticket that exists
            </label>
          </fieldset>
          {mode === "new" ? (
            <>
              <label htmlFor={ids.name}>Title</label>
              <Input
                id={ids.name}
                value={title}
                autoFocus
                onChange={(event) => setTitle(event.target.value)}
                placeholder="e.g. Dark mode for the dashboard"
              />
              <label htmlFor={ids.workspace}>Workspace</label>
              <Select
                id={ids.workspace}
                value={workspace}
                onChange={(event) => {
                  setWorkspace(event.target.value);
                  setParent("");
                }}
              >
                {workspaces.map((slug) => (
                  <option key={slug} value={slug}>
                    {slug}
                  </option>
                ))}
              </Select>
              <label htmlFor={ids.parent}>Under milestone</label>
              <Select
                id={ids.parent}
                value={parent}
                disabled={milestones.isPending}
                onChange={(event) => setParent(event.target.value)}
              >
                <option value="">None: file it as its own milestone</option>
                {(milestones.data ?? []).map((milestone) => (
                  <option key={milestone.id} value={milestone.id}>
                    {milestone.external_id} · {milestone.title}
                  </option>
                ))}
              </Select>
              {milestones.isError ? (
                <p className="chat-wb-warn" role="status">
                  Could not list milestones ({describeError(milestones.error, "the request failed")});
                  it can still be filed as its own milestone.
                </p>
              ) : null}
            </>
          ) : (
            <>
              <label htmlFor={ids.existing}>Ticket id or external id</label>
              <Input
                id={ids.existing}
                value={existing}
                autoFocus
                onChange={(event) => setExisting(event.target.value)}
                placeholder="e.g. lg-feature-42"
              />
            </>
          )}
          {failure ? (
            <p className="chat-wb-warn" role="alert">
              Loop not started: {failure}
            </p>
          ) : null}
        </div>
        <div className="modal-footer">
          <Button variant="secondary" disabled={saving} onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={!ready || saving}>
            {saving ? "Starting…" : mode === "new" ? "Create and start loop" : "Start loop"}
          </Button>
        </div>
      </form>
    </ModalShell>
  );
}
