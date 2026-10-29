import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { api } from "../../api/client";
import type { TopicView } from "../../api/referenceLibraryApi";
import { describeError, pushToast } from "../../state/toastStore";
import { IconCloseButton } from "../IconCloseButton";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { ModalShell } from "../ui/ModalShell";
import { Select } from "../ui/Select";
import { ReferenceTargetsPicker } from "./ReferenceTargetsPicker";
import { TopicChips } from "./ReferenceTopics";

const HOW_MANY = [5, 10, 20];

/**
 * Have an agent search for references on a topic. What it finds lands in the
 * Queue view, each waiting for a person to keep or drop; nothing is read before
 * then. The run is one ticket, so it shows in the queue bar while it searches.
 */
export function ReferenceDiscoverButton({ workspace }: { workspace: string }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [topic, setTopic] = useState<TopicView | null>(null);
  const [focus, setFocus] = useState("");
  const [wanted, setWanted] = useState(5);
  const [alsoFor, setAlsoFor] = useState<string[]>([]);
  const [initiatives, setInitiatives] = useState<string[]>([]);
  const [failure, setFailure] = useState("");
  const ids = { title: useId(), focus: useId(), wanted: useId() };

  function close() {
    setOpen(false);
    setTopic(null);
    setFocus("");
    setFailure("");
  }

  const start = useMutation({
    mutationFn: (picked: TopicView) =>
      api.startDiscovery({
        topic: picked.id,
        workspace_slugs: [workspace, ...alsoFor.filter((slug) => slug !== workspace)],
        initiative_ids: initiatives,
        focus: focus.trim(),
        wanted,
      }),
    onSuccess: (run) => {
      pushToast({
        tone: "success",
        title: `Searching for ${run.label} references`,
        message: `Up to ${run.wanted} finds land in Queue as they come, each waiting for you to keep or drop.`,
      });
      void queryClient.invalidateQueries({ queryKey: ["reference-library"] });
      close();
    },
    onError: (error) => {
      const message = describeError(error, "the request failed");
      setFailure(message);
      pushToast({ tone: "error", title: "The search did not start", message });
    },
  });

  return (
    <>
      <Button
        variant="secondary"
        compact
        className="rl-add-toggle"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen(true)}
      >
        Find sources…
      </Button>
      <ModalShell
        open={open}
        onDismiss={start.isPending ? undefined : close}
        labelledBy={ids.title}
        panelClassName="modal-panel-wide"
      >
        <form
          aria-label="Find sources"
          onSubmit={(event) => {
            event.preventDefault();
            if (topic && !start.isPending) start.mutate(topic);
          }}
        >
          <div className="modal-header">
            <div>
              <h2 id={ids.title} className="modal-title">
                Find sources
              </h2>
              <p className="modal-subtitle">
                An agent searches the web and GitHub; what it finds waits in Queue for you to keep or drop.
              </p>
            </div>
            <IconCloseButton disabled={start.isPending} onClick={close} />
          </div>
          <div className="modal-body rl-modal-body">
            <TopicChips selected={topic?.id ?? null} onPick={setTopic} label="Topic" />
            {topic ? <p className="rl-muted rl-optional-note">Each find is read for: {topic.question}</p> : null}
            <div className="rl-add-row">
              <div className="rl-field rl-field--grow">
                <label htmlFor={ids.focus}>Narrow it (optional)</label>
                <Input
                  id={ids.focus}
                  value={focus}
                  onChange={(event) => setFocus(event.target.value)}
                  placeholder="e.g. graph-backed, self-hosted, TypeScript"
                />
              </div>
              <div className="rl-field">
                <label htmlFor={ids.wanted}>How many</label>
                <Select id={ids.wanted} value={String(wanted)} onChange={(event) => setWanted(Number(event.target.value))}>
                  {HOW_MANY.map((n) => (
                    <option key={n} value={n}>
                      Up to {n}
                    </option>
                  ))}
                </Select>
              </div>
            </div>
            <ReferenceTargetsPicker
              fixedWorkspaces={[workspace]}
              workspaces={alsoFor}
              onWorkspaces={setAlsoFor}
              initiatives={initiatives}
              onInitiatives={setInitiatives}
            />
            {failure ? (
              <p className="rl-warn" role="alert">
                Did not start: {failure}
              </p>
            ) : null}
          </div>
          <div className="modal-footer">
            <Button variant="secondary" disabled={start.isPending} onClick={close}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={!topic || start.isPending}>
              {start.isPending ? "Starting…" : "Find sources"}
            </Button>
          </div>
        </form>
      </ModalShell>
    </>
  );
}
