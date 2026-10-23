import { useState } from "react";

import type { ChatFocus } from "../../../api/chatWorkbenchTypes";
import { navigateToInitiative } from "../../../lib/useAppNavigation";
import { Button } from "../../ui/Button";
import { ChatFocusModal } from "./ChatFocusModal";
import { WorkbenchCard } from "./WorkbenchCard";

/**
 * "What is this chat about, and where does it run?" The workspaces and
 * initiatives in scope, the checkout turns run in, and Edit to change them.
 */
export function ScopeCard({
  focus,
  isGlobal,
  hostSlug,
  saving,
  onSave,
}: {
  focus: ChatFocus;
  isGlobal: boolean;
  /** The checkout turns run in; null until the thread exists. */
  hostSlug: string | null;
  saving: boolean;
  onSave: (body: { workspace_slugs: string[]; initiative_ids: string[] }) => Promise<unknown>;
}) {
  const [editing, setEditing] = useState(false);
  const count = (focus.all_workspaces ? 0 : focus.workspaces.length) + focus.initiatives.length;
  return (
    <WorkbenchCard
      title="Scope"
      meta={focus.all_workspaces ? "all workspaces" : `${count} in focus`}
      action={
        <Button
          variant="plain"
          className="chat-side-card-link"
          aria-haspopup="dialog"
          aria-expanded={editing}
          onClick={() => setEditing(true)}
        >
          Edit
        </Button>
      }
    >
      <ul className="chat-wb-rows" aria-label="Workspaces in scope">
        {focus.all_workspaces ? (
          <li className="chat-wb-row">
            <span className="chat-wb-glyph" aria-hidden>◎</span>
            <span>Every workspace</span>
          </li>
        ) : (
          focus.workspaces.map((workspace) => (
            <li key={workspace.id} className="chat-wb-row">
              <span className="chat-wb-glyph" aria-hidden>▤</span>
              <span className="chat-wb-row-main">{workspace.slug}</span>
              {workspace.slug === hostSlug ? <span className="chat-wb-tag">runs here</span> : null}
            </li>
          ))
        )}
      </ul>
      {focus.initiatives.length ? (
        <ul className="chat-wb-rows" aria-label="Initiatives in scope">
          {focus.initiatives.map((initiative) => (
            <li key={initiative.id} className="chat-wb-row">
              <span className="chat-wb-glyph" aria-hidden>◆</span>
              <Button
                variant="plain"
                className="chat-wb-row-link"
                title={`Open the plan for ${initiative.title}`}
                onClick={() => navigateToInitiative(initiative.id)}
              >
                {initiative.title}
              </Button>
              <span className="chat-wb-tag">{initiative.external_id}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="chat-side-card-empty">No initiative. Edit to work on one or several.</p>
      )}
      {hostSlug && focus.all_workspaces ? (
        <p className="chat-wb-note">Turns run in the {hostSlug} checkout.</p>
      ) : null}
      {editing ? (
        <ChatFocusModal
          open={editing}
          focus={focus}
          isGlobal={isGlobal}
          saving={saving}
          onSave={onSave}
          onClose={() => setEditing(false)}
        />
      ) : null}
    </WorkbenchCard>
  );
}
