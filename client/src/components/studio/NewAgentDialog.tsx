/**
 * Agent Studio → New agent…: the few fields an agent needs to exist, in a
 * dialog, after which its editor opens for the rest (role, runtime, tools).
 *
 * The slug follows the name until it is typed into, so most agents need only
 * a name. "Describe it" asks the server to draft the agent; the draft fills the
 * name, slug and description here and carries its role and tools into the
 * create, so the editor opens on the drafted agent rather than a blank one.
 *
 * Fields stay put on failure with the server's reason under them, and nothing
 * dismisses the dialog while a request is in flight.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { api } from "../../api/client";
import type { StudioGeneratedAgent } from "../../api/types";
import { slugify } from "../../lib/slugify";
import { describeError } from "../../state/toastStore";
import { IconCloseButton } from "../IconCloseButton";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { ModalShell } from "../ui/ModalShell";
import { Textarea } from "../ui/Textarea";
import "./NewAgentDialog.css";

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: (slug: string) => void;
}

export function NewAgentDialog({ open, onClose, onCreated }: Props) {
  const [busy, setBusy] = useState(false);
  return (
    <ModalShell
      open={open}
      onDismiss={busy ? undefined : onClose}
      labelledBy="new-agent-title"
      describedBy="new-agent-effect"
    >
      {open ? <NewAgentForm onClose={onClose} onCreated={onCreated} onBusy={setBusy} /> : null}
    </ModalShell>
  );
}

function NewAgentForm({
  onClose,
  onCreated,
  onBusy,
}: Omit<Props, "open"> & { onBusy: (busy: boolean) => void }) {
  const id = useId();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugEdited, setSlugEdited] = useState(false);
  const [description, setDescription] = useState("");
  const [brief, setBrief] = useState("");
  const [draft, setDraft] = useState<StudioGeneratedAgent | null>(null);

  const effectiveSlug = slugEdited ? slug : slugify(name);

  const generate = useMutation({
    mutationFn: () => api.generateStudioAgent(brief.trim()),
    onSuccess: (generated) => {
      setDraft(generated);
      setName(generated.name);
      setDescription(generated.description);
      if (generated.slug) {
        setSlug(generated.slug);
        setSlugEdited(true);
      }
    },
  });

  const create = useMutation({
    mutationFn: () =>
      api.createStudioAgent({
        name: name.trim(),
        slug: effectiveSlug,
        description: description.trim(),
        ...(draft
          ? {
              role_body: draft.role_body,
              ...(draft.adapter ? { adapter: draft.adapter } : {}),
              default_skill: draft.default_skill,
              ...(draft.mcp_tools.length ? { mcp_tools: draft.mcp_tools, mcp_enabled: true } : {}),
            }
          : {}),
      }),
    onSuccess: (saved) => {
      void qc.invalidateQueries({ queryKey: ["studio-agents"] });
      onBusy(false);
      onCreated(saved.slug);
    },
    onError: () => onBusy(false),
  });

  const inFlight = generate.isPending || create.isPending;
  const canCreate = Boolean(name.trim() && effectiveSlug) && !inFlight;

  return (
    <>
      <div className="modal-header">
        <div>
          <div className="state-label">Agent Studio</div>
          <h2 id="new-agent-title" className="modal-title">
            New agent
          </h2>
          <p id="new-agent-effect" className="modal-subtitle">
            Creates the agent, then opens its editor for its role, runtime and tools.
          </p>
        </div>
        <IconCloseButton disabled={inFlight} onClick={onClose} />
      </div>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (!canCreate) return;
          onBusy(true);
          create.mutate();
        }}
      >
        <div className="modal-body new-agent-body">
          <div className="new-agent-describe">
            <label className="new-agent-field">
              <span>Describe it (optional)</span>
              <Textarea
                value={brief}
                onChange={(event) => setBrief(event.target.value)}
                rows={2}
                disabled={inFlight}
                placeholder="Its role, its limits, when to use it"
              />
            </label>
            <Button
              variant="secondary"
              compact
              disabled={inFlight || !brief.trim()}
              onClick={() => {
                onBusy(true);
                generate.mutate(undefined, { onSettled: () => onBusy(false) });
              }}
            >
              {generate.isPending ? "Drafting…" : "Draft from this"}
            </Button>
            {generate.isError ? (
              <p className="new-agent-error" role="alert">
                {describeError(generate.error, "The draft did not come back; fill the fields in yourself.")}
              </p>
            ) : draft ? (
              <p className="new-agent-note" role="status">
                Drafted. Its role and tools go in with it; change anything below first.
              </p>
            ) : null}
          </div>

          <label className="new-agent-field" htmlFor={`${id}-name`}>
            <span>Name</span>
          </label>
          <Input
            id={`${id}-name`}
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
            disabled={inFlight}
            autoFocus
          />
          <label className="new-agent-field" htmlFor={`${id}-slug`}>
            <span>Slug</span>
          </label>
          <Input
            id={`${id}-slug`}
            className="new-agent-slug"
            value={effectiveSlug}
            onChange={(event) => {
              setSlug(event.target.value);
              setSlugEdited(true);
            }}
            required
            disabled={inFlight}
            aria-describedby={`${id}-slug-hint`}
          />
          <p id={`${id}-slug-hint`} className="new-agent-note">
            How workflows and runs name it. It cannot be changed later.
          </p>
          <label className="new-agent-field" htmlFor={`${id}-description`}>
            <span>Description</span>
          </label>
          <Input
            id={`${id}-description`}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            disabled={inFlight}
          />
          {create.isError ? (
            <p className="new-agent-error" role="alert">
              {describeError(create.error, "The agent was not created; try again.")}
            </p>
          ) : null}
        </div>
        <div className="modal-footer">
          <Button variant="secondary" disabled={inFlight} onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" disabled={!canCreate}>
            {create.isPending ? "Creating…" : "Create agent"}
          </Button>
        </div>
      </form>
    </>
  );
}
