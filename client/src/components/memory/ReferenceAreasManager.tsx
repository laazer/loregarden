import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";

import { api } from "../../api/client";
import type { AreaView } from "../../api/referenceAreasApi";
import { describeError, pushToast } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { ModalShell } from "../ui/ModalShell";
import { Select } from "../ui/Select";
import { Textarea } from "../ui/Textarea";
import { IconCloseButton } from "../IconCloseButton";
import { AREAS_QUERY_KEY } from "./useAreas";
import "./ReferenceAreas.css";

const ALL_AREAS_KEY = [...AREAS_QUERY_KEY, "with-archived"] as const;

function splitKeywords(text: string): string[] {
  return text.split(",").map((word) => word.trim()).filter(Boolean);
}

/** The toolbar button and the modal it opens. */
export function ReferenceAreasButton({ workspace }: { workspace: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant="secondary" compact aria-expanded={open} aria-haspopup="dialog" onClick={() => setOpen(true)}>
        Project areas
      </Button>
      {open ? <ReferenceAreasManager workspace={workspace} onClose={() => setOpen(false)} /> : null}
    </>
  );
}

/**
 * Which parts of each product a reference can be read for. Answers "what can I
 * label a reference with, and where do its findings go?"; each row edits in
 * place of the list, so the modal keeps one focus trap and one Escape.
 */
export function ReferenceAreasManager({ workspace, onClose }: { workspace: string; onClose: () => void }) {
  const titleId = useId();
  const [editing, setEditing] = useState<AreaView | "new" | null>(null);
  const queryClient = useQueryClient();
  const areas = useQuery({ queryKey: ALL_AREAS_KEY, queryFn: () => api.referenceAreas(true) });

  const archive = useMutation({
    mutationFn: (area: AreaView) => api.updateReferenceArea(area.id, { archived: !area.archived }),
    onSuccess: (area) => {
      void queryClient.invalidateQueries({ queryKey: AREAS_QUERY_KEY });
      pushToast({
        tone: "success",
        title: area.archived ? `Archived ${area.label}` : `Restored ${area.label}`,
        message: area.archived ? "Sources keep the link; new ones can no longer pick it." : "It can be picked again.",
      });
    },
    onError: (error) => pushToast({ tone: "error", title: "The area was not changed", message: describeError(error, "the request failed") }),
  });

  const groups = new Map<string, AreaView[]>();
  for (const area of areas.data ?? []) groups.set(area.workspace_slug, [...(groups.get(area.workspace_slug) ?? []), area]);
  // The workspace in view first, then the rest by name.
  const order = [...groups.keys()].sort((a, b) => (a === workspace ? -1 : b === workspace ? 1 : a.localeCompare(b)));

  return (
    <ModalShell open onDismiss={onClose} labelledBy={titleId} panelClassName="modal-panel-wide">
      <div className="modal-header">
        <div>
          <h2 id={titleId} className="modal-title">
            {editing === "new" ? "Add a project area" : editing ? `Edit ${editing.label}` : "Project areas"}
          </h2>
          <p className="modal-subtitle">
            The parts of each product a reference can be read for. A reference labelled with an area is read with it in
            mind, and a finding no ticket covers is filed under the area's home ticket.
          </p>
        </div>
        <IconCloseButton onClick={onClose} />
      </div>
      {editing ? (
        <AreaForm area={editing === "new" ? null : editing} workspace={workspace} onDone={() => setEditing(null)} />
      ) : (
        <>
          <div className="modal-body rl-modal-body">
            {areas.isPending ? (
              <p className="rl-muted">Loading areas…</p>
            ) : areas.isError ? (
              <p className="rl-warn" role="alert">
                Areas did not load: {describeError(areas.error, "the request failed")}.{" "}
                <Button variant="secondary" compact onClick={() => void areas.refetch()}>
                  Try again
                </Button>
              </p>
            ) : order.length === 0 ? (
              <p className="rl-muted">
                No project areas yet. Add one for each part of a product you collect references for, such as
                loremaker's stratos or blobert's enemy generator.
              </p>
            ) : (
              <table className="rl-areas-table" aria-label="Project areas">
                <thead>
                  <tr>
                    <th scope="col">Area</th>
                    <th scope="col">What it is</th>
                    <th scope="col">Findings go under</th>
                    <th scope="col">
                      <span className="rl-sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                {order.map((slug) => (
                  <tbody key={slug}>
                    <tr className="rl-area-group">
                      <th scope="rowgroup" colSpan={4}>
                        {slug} <span className="rl-muted">· {groups.get(slug)?.length}</span>
                      </th>
                    </tr>
                    {(groups.get(slug) ?? []).map((area) => (
                      <tr key={area.id} className={area.archived ? "rl-area--archived" : undefined}>
                        <td data-label="Area">
                          <Button variant="plain" className="rl-name" onClick={() => setEditing(area)}>
                            {area.name}
                          </Button>
                          <span className="rl-url">{area.label}{area.archived ? " · archived" : ""}</span>
                        </td>
                        <td data-label="What it is">
                          {area.description || <span className="rl-muted">No description</span>}
                          {area.keywords.length ? <span className="rl-url">{area.keywords.join(", ")}</span> : null}
                        </td>
                        <td data-label="Findings go under">
                          {area.home_ticket ?? <span className="rl-muted">Workspace findings home</span>}
                        </td>
                        <td>
                          <span className="rl-area-actions">
                            <Button
                              variant="secondary"
                              compact
                              disabled={archive.isPending}
                              aria-label={`${area.archived ? "Restore" : "Archive"} ${area.label}`}
                              onClick={() => archive.mutate(area)}
                            >
                              {area.archived ? "Restore" : "Archive"}
                            </Button>
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                ))}
              </table>
            )}
          </div>
          <div className="modal-footer">
            <Button variant="secondary" onClick={onClose}>
              Done
            </Button>
            <Button variant="primary" onClick={() => setEditing("new")}>
              Add an area
            </Button>
          </div>
        </>
      )}
    </ModalShell>
  );
}

function AreaForm({ area, workspace, onDone }: { area: AreaView | null; workspace: string; onDone: () => void }) {
  const queryClient = useQueryClient();
  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: () => api.workspaces(), enabled: area === null });
  const [workspaceSlug, setWorkspaceSlug] = useState(area?.workspace_slug ?? workspace);
  const [slug, setSlug] = useState(area?.slug ?? "");
  const [name, setName] = useState(area?.name ?? "");
  const [description, setDescription] = useState(area?.description ?? "");
  const [keywords, setKeywords] = useState(area?.keywords.join(", ") ?? "");
  const [home, setHome] = useState(area?.home_ticket ?? "");
  const [failure, setFailure] = useState("");
  const ids = { ws: useId(), slug: useId(), name: useId(), desc: useId(), kw: useId(), home: useId(), homeHint: useId() };

  const save = useMutation({
    mutationFn: () =>
      area
        ? api.updateReferenceArea(area.id, { name: name.trim(), description, keywords: splitKeywords(keywords), home_ticket: home.trim() })
        : api.addReferenceArea({
            workspace_slug: workspaceSlug,
            slug: slug.trim(),
            name: name.trim(),
            description,
            keywords: splitKeywords(keywords),
            home_ticket: home.trim(),
          }),
    onSuccess: (saved) => {
      void queryClient.invalidateQueries({ queryKey: AREAS_QUERY_KEY });
      pushToast({ tone: "success", title: area ? `Saved ${saved.label}` : `Added ${saved.label}`, message: "References can be labelled with it now." });
      onDone();
    },
    onError: (error) => setFailure(describeError(error, "the request failed")),
  });

  const slugOk = area !== null || /^[a-z0-9][a-z0-9-]{0,47}$/.test(slug.trim());
  const ready = slugOk && name.trim().length > 0 && !save.isPending;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (ready) save.mutate();
  }

  return (
    <form aria-label={area ? `Edit ${area.label}` : "Add a project area"} onSubmit={submit}>
      <div className="modal-body rl-modal-body">
        {area === null ? (
          <>
            <div className="rl-field">
              <label htmlFor={ids.ws}>Workspace</label>
              <Select id={ids.ws} value={workspaceSlug} onChange={(event) => setWorkspaceSlug(event.target.value)}>
                {(workspaces.data ?? [{ slug: workspace, archived_at: null }])
                  .filter((w) => !w.archived_at)
                  .map((w) => (
                    <option key={w.slug} value={w.slug}>
                      {w.slug}
                    </option>
                  ))}
              </Select>
            </div>
            <div className="rl-field">
              <label htmlFor={ids.slug}>Code name</label>
              <Input id={ids.slug} value={slug} onChange={(event) => setSlug(event.target.value.toLowerCase())} placeholder="stratos" autoFocus />
              {!slugOk && slug ? (
                <span className="rl-warn" role="status">
                  Lowercase letters, digits and dashes, at most 48 characters.
                </span>
              ) : null}
            </div>
          </>
        ) : null}
        <div className="rl-field">
          <label htmlFor={ids.name}>Name</label>
          <Input id={ids.name} value={name} onChange={(event) => setName(event.target.value)} placeholder="Stratos" autoFocus={area !== null} />
        </div>
        <div className="rl-field">
          <label htmlFor={ids.desc}>What it is</label>
          <Textarea id={ids.desc} rows={2} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Workflow management: structured data models and projects." />
        </div>
        <div className="rl-field">
          <label htmlFor={ids.kw}>Words a reference about it would use (comma-separated)</label>
          <Input id={ids.kw} value={keywords} onChange={(event) => setKeywords(event.target.value)} placeholder="workflow, data model, schema" />
        </div>
        <div className="rl-field">
          <label htmlFor={ids.home}>File its findings under (ticket id)</label>
          <Input id={ids.home} value={home} onChange={(event) => setHome(event.target.value)} placeholder="lm-observability-12" aria-describedby={ids.homeHint} />
          <span id={ids.homeHint} className="rl-muted">
            A milestone or feature in {area?.workspace_slug ?? workspaceSlug}. Empty files them under the workspace's
            "Adopt from other projects" milestone.
          </span>
        </div>
        {failure ? (
          <p className="rl-warn" role="alert">
            Not saved: {failure}
          </p>
        ) : null}
      </div>
      <div className="modal-footer">
        <Button variant="secondary" disabled={save.isPending} onClick={onDone}>
          Back to areas
        </Button>
        <Button type="submit" variant="primary" disabled={!ready}>
          {save.isPending ? "Saving…" : area ? `Save ${area.label}` : "Add area"}
        </Button>
      </div>
    </form>
  );
}
