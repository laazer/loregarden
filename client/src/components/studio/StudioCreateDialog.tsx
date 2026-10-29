/**
 * Studio's create dialog: the few fields a Studio record needs to exist, after
 * which its editor opens for the rest. New agent… and New workflow… are this
 * dialog with their own words, their own create call and, for a workflow, a
 * "start from" field.
 *
 * The slug follows the name until it is typed into, so most records need only
 * a name. "Describe it" asks the server for a draft; the draft fills the name,
 * slug and description here and is handed to `create`, which carries the rest
 * of it (an agent's role, a workflow's stages) into the record.
 *
 * Fields stay put on failure with the server's reason under them, and nothing
 * dismisses the dialog while a request is in flight.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type ReactNode } from "react";

import { slugify } from "../../lib/slugify";
import { describeError } from "../../state/toastStore";
import { IconCloseButton } from "../IconCloseButton";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { ModalShell } from "../ui/ModalShell";
import { Textarea } from "../ui/Textarea";
import "./StudioCreateDialog.css";

export interface CreateFields {
  name: string;
  slug: string;
  description: string;
}

export interface StudioCreateSpec<D extends CreateFields> {
  /** "agent", "workflow": names the dialog, its button and its errors. */
  noun: string;
  /** What creating does, in a line under the title. */
  effect: string;
  describePlaceholder: string;
  /** What the draft carries beyond the three fields, said once it arrives. */
  draftNote: string;
  slugHint: string;
  /** Invalidated after a create, so the index shows the new record. */
  listKey: readonly unknown[];
  generate: (brief: string) => Promise<D>;
  create: (fields: CreateFields, draft: D | null) => Promise<{ slug: string }>;
  /** More fields, under the description. */
  extra?: (disabled: boolean) => ReactNode;
}

interface Props<D extends CreateFields> {
  open: boolean;
  onClose: () => void;
  onCreated: (slug: string) => void;
  spec: StudioCreateSpec<D>;
}

export function StudioCreateDialog<D extends CreateFields>({ open, ...props }: Props<D>) {
  const [busy, setBusy] = useState(false);
  const titleId = `studio-create-${props.spec.noun}-title`;
  return (
    <ModalShell
      open={open}
      onDismiss={busy ? undefined : props.onClose}
      labelledBy={titleId}
      describedBy={`${titleId}-effect`}
    >
      {open ? <CreateForm {...props} titleId={titleId} onBusy={setBusy} /> : null}
    </ModalShell>
  );
}

function CreateForm<D extends CreateFields>({
  onClose,
  onCreated,
  spec,
  titleId,
  onBusy,
}: Omit<Props<D>, "open"> & { titleId: string; onBusy: (busy: boolean) => void }) {
  const id = useId();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugEdited, setSlugEdited] = useState(false);
  const [description, setDescription] = useState("");
  const [brief, setBrief] = useState("");
  const [draft, setDraft] = useState<D | null>(null);

  const effectiveSlug = slugEdited ? slug : slugify(name);

  const generate = useMutation({
    mutationFn: () => spec.generate(brief.trim()),
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
      spec.create({ name: name.trim(), slug: effectiveSlug, description: description.trim() }, draft),
    onSuccess: (saved) => {
      void qc.invalidateQueries({ queryKey: spec.listKey });
      onBusy(false);
      onCreated(saved.slug);
    },
    onError: () => onBusy(false),
  });

  const inFlight = generate.isPending || create.isPending;
  const canCreate = Boolean(name.trim() && effectiveSlug) && !inFlight;
  const Noun = `${spec.noun[0].toUpperCase()}${spec.noun.slice(1)}`;

  return (
    <>
      <div className="modal-header">
        <div>
          <div className="state-label">{Noun} Studio</div>
          <h2 id={titleId} className="modal-title">
            New {spec.noun}
          </h2>
          <p id={`${titleId}-effect`} className="modal-subtitle">
            {spec.effect}
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
        <div className="modal-body studio-create-body">
          <div className="studio-create-describe">
            <label className="studio-create-field">
              <span>Describe it (optional)</span>
              <Textarea
                value={brief}
                onChange={(event) => setBrief(event.target.value)}
                rows={2}
                disabled={inFlight}
                placeholder={spec.describePlaceholder}
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
              <p className="studio-create-error" role="alert">
                {describeError(generate.error, "The draft did not come back; fill the fields in yourself.")}
              </p>
            ) : draft ? (
              <p className="studio-create-note" role="status">
                {spec.draftNote}
              </p>
            ) : null}
          </div>

          <label className="studio-create-field" htmlFor={`${id}-name`}>
            Name
          </label>
          <Input
            id={`${id}-name`}
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
            disabled={inFlight}
            autoFocus
          />
          <label className="studio-create-field" htmlFor={`${id}-slug`}>
            Slug
          </label>
          <Input
            id={`${id}-slug`}
            className="studio-create-slug"
            value={effectiveSlug}
            onChange={(event) => {
              setSlug(event.target.value);
              setSlugEdited(true);
            }}
            required
            disabled={inFlight}
            aria-describedby={`${id}-slug-hint`}
          />
          <p id={`${id}-slug-hint`} className="studio-create-note">
            {spec.slugHint}
          </p>
          <label className="studio-create-field" htmlFor={`${id}-description`}>
            Description
          </label>
          <Input
            id={`${id}-description`}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            disabled={inFlight}
          />
          {spec.extra?.(inFlight)}
          {create.isError ? (
            <p className="studio-create-error" role="alert">
              {describeError(create.error, `The ${spec.noun} was not created; try again.`)}
            </p>
          ) : null}
        </div>
        <div className="modal-footer">
          <Button variant="secondary" disabled={inFlight} onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" disabled={!canCreate}>
            {create.isPending ? "Creating…" : `Create ${spec.noun}`}
          </Button>
        </div>
      </form>
    </>
  );
}
