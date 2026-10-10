import React, { useEffect, useState } from 'react';
import * as apiClient from '../api/client';
import type { TicketState } from '../api/client';
import { priorityLabel } from '../lib/importTicketPreview';
import { TICKET_STATE_COLORS } from '../lib/ticketStates';
import { IconCloseButton } from './IconCloseButton';
import { TicketDependencies } from './TicketDependencies';
import { TicketGithubIssue } from './TicketGithubIssue';
import { TicketRelations } from './TicketRelations';
import { STATE_LABELS } from './UpdateStateModal';
import { useDialogFocusTrap } from '../hooks/useDialogFocusTrap';
import { AddToTabMenu } from './AddToTabMenu';
import { MarkdownContent } from './chat/MarkdownContent';
import { CopyValueButton } from './CopyValueButton';
import { TicketCriteriaEditor, TicketCriteriaView } from './TicketCriteriaChecklist';
import { criteriaDrafts, draftChecked, draftCriteria, type CriterionDraft } from '../lib/criterionDrafts';
import './TicketDetailsModal.css';

const STATE_OPTIONS = Object.keys(STATE_LABELS) as TicketState[];
const PRIORITY_OPTIONS = [1, 2, 3] as const;

export interface TicketDetailsSaveDraft {
  title: string;
  description: string;
  acceptanceCriteria: string[];
  /** The subset of `acceptanceCriteria` that is checked off. */
  checkedAcceptanceCriteria: string[];
  tags: string[];
  state: TicketState;
  priority: number;
}

/** Comma-separated, blanks and case-insensitive duplicates dropped — mirrors
 * services/ticket_tags.normalize_tags, which has the last word on what is stored. */
function parseTags(text: string): string[] {
  const seen = new Set<string>();
  const tags: string[] = [];
  for (const raw of text.split(',')) {
    const tag = raw.trim();
    if (!tag) continue;
    const key = tag.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    tags.push(tag);
  }
  return tags;
}

export interface TicketDetailsModalProps {
  ticket: apiClient.TicketDetail | null;
  isOpen: boolean;
  onClose: () => void;
  isLoading?: boolean;
  error?: string;
  isSaving?: boolean;
  saveError?: string;
  /** Saves an edit. Rejects when the save failed; the modal then stays in edit mode. */
  onSave?: (draft: TicketDetailsSaveDraft) => Promise<void>;
  /** Saves which criteria are checked off, from read mode. Rejects when it failed. */
  onSaveChecks?: (checked: string[]) => Promise<void>;
  isSavingChecks?: boolean;
}

type ModalMode = 'view' | 'edit';

interface EditDraft {
  title: string;
  description: string;
  criteria: CriterionDraft[];
  tagsText: string;
  state: TicketState;
  priority: number;
}

function asDisplayString(value: unknown, fallback = ''): string {
  if (value == null) return fallback;
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) return value.map((item) => asDisplayString(item)).join(', ');
  try {
    return String(value);
  } catch {
    return fallback;
  }
}

function asStringArray(value: unknown): string[] {
  if (!value) return [];
  if (Array.isArray(value)) return value.map((item) => asDisplayString(item));
  if (typeof value === 'string') return [value];
  return [asDisplayString(value)];
}

function seedDraft(ticket: apiClient.TicketDetail): EditDraft {
  return {
    title: asDisplayString(ticket.title),
    description: asDisplayString(ticket.description),
    criteria: criteriaDrafts(asStringArray(ticket.acceptance_criteria), asStringArray(ticket.checked_acceptance_criteria)),
    tagsText: asStringArray(ticket.tags).join(', '),
    state: ticket.state,
    priority: ticket.priority,
  };
}

function hasArtifactContent(artifacts: apiClient.TicketDetail['artifacts']): boolean {
  if (!artifacts) return false;
  return Boolean(
    artifacts.diff ||
      (artifacts.logs && artifacts.logs.length > 0) ||
      artifacts.tests ||
      artifacts.error ||
      artifacts.live ||
      (artifacts.context && artifacts.context.length > 0)
  );
}

export const TicketDetailsModal: React.FC<TicketDetailsModalProps> = ({
  ticket,
  isOpen,
  onClose,
  isLoading = false,
  error,
  isSaving = false,
  saveError,
  onSave,
  onSaveChecks,
  isSavingChecks = false,
}) => {
  const [mode, setMode] = useState<ModalMode>('view');
  const [draft, setDraft] = useState<EditDraft | null>(null);
  // Read-mode checks shown before the server confirms them; null = show the ticket's.
  const [pendingChecks, setPendingChecks] = useState<string[] | null>(null);
  const dialogRef = useDialogFocusTrap<HTMLDivElement>();

  const criteria = asStringArray(ticket?.acceptance_criteria);
  const savedChecks = asStringArray(ticket?.checked_acceptance_criteria);
  const savedChecksKey = savedChecks.join('\n');

  // Another ticket, or the modal reopening, starts over in read mode.
  useEffect(() => {
    setMode('view');
    setDraft(null);
    setPendingChecks(null);
  }, [ticket?.id, isOpen]);

  // The refetch after a check landed is the confirmation; show the server's answer.
  useEffect(() => {
    setPendingChecks(null);
  }, [savedChecksKey]);

  const editing = mode === 'edit' && draft !== null && ticket !== null;

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      // Escape backs out one level: out of the editor first, then out of the modal.
      if (editing) {
        if (isSaving) return;
        setMode('view');
        setDraft(null);
        return;
      }
      onClose?.();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose, editing, isSaving]);

  if (!isOpen) {
    return null;
  }

  if (!ticket && !isLoading && !error) {
    return null;
  }

  const startEditing = () => {
    if (!ticket) return;
    setDraft(seedDraft(ticket));
    setMode('edit');
  };

  const cancelEditing = () => {
    setMode('view');
    setDraft(null);
  };

  const patchDraft = (patch: Partial<EditDraft>) => setDraft((current) => (current ? { ...current, ...patch } : current));

  const draftTags = draft ? parseTags(draft.tagsText) : [];
  const draftCriteriaList = draft ? draftCriteria(draft.criteria) : [];
  const draftChecks = draft ? draftChecked(draft.criteria) : [];
  const isDirty =
    editing &&
    (draft.title.trim() !== asDisplayString(ticket.title) ||
      draft.description !== asDisplayString(ticket.description) ||
      draftCriteriaList.join('\n') !== criteria.join('\n') ||
      draftChecks.join('\n') !== savedChecks.filter((c) => criteria.includes(c)).join('\n') ||
      draftTags.join(',') !== parseTags(asStringArray(ticket.tags).join(',')).join(',') ||
      draft.state !== ticket.state ||
      draft.priority !== ticket.priority);
  const canSave = isDirty && !!draft && draft.title.trim().length > 0 && !!onSave;

  const handleSave = async () => {
    if (!canSave || !draft || !onSave || isSaving) return;
    try {
      await onSave({
        title: draft.title.trim(),
        description: draft.description,
        acceptanceCriteria: draftCriteriaList,
        checkedAcceptanceCriteria: draftChecks,
        tags: draftTags,
        state: draft.state,
        priority: draft.priority,
      });
    } catch {
      // silent-ok: the caller reports the failure through `saveError`, rendered
      // above the form; staying in edit mode keeps the operator's draft.
      return;
    }
    setMode('view');
    setDraft(null);
  };

  const shownChecks = pendingChecks ?? savedChecks;
  const handleToggle = async (criterion: string, checked: boolean) => {
    if (!onSaveChecks || isSavingChecks) return;
    const next = criteria.filter((c) => (c === criterion ? checked : shownChecks.includes(c)));
    setPendingChecks(next);
    try {
      await onSaveChecks(next);
    } catch {
      // silent-ok: the caller reports the failure through `saveError`; the box
      // snaps back to what the server holds so it never shows an unsaved tick.
      setPendingChecks(null);
    }
  };

  const ticketNumber = asDisplayString(ticket?.external_id) || asDisplayString(ticket?.id);
  const ticketTitle = asDisplayString(ticket?.title);
  const busy = isSaving || isSavingChecks;

  return (
    <>
      <div
        className="modal-overlay"
        data-testid="modal-backdrop"
        onClick={busy ? undefined : onClose}
        role="presentation"
      />
      <div
        ref={dialogRef}
        className="modal-panel ticket-details-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
        aria-describedby="modal-description"
        tabIndex={-1}
        data-testid="modal-content"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header">
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="ticket-modal-id-row">
              <span className="state-label">{editing ? 'Editing ticket' : 'Ticket'}</span>
              {ticket && ticketNumber && (
                <span className="ticket-modal-number">
                  <code id="modal-description">{ticketNumber}</code>
                  <CopyValueButton value={ticketNumber} what="ticket number" />
                </span>
              )}
            </div>
            {editing ? (
              <input
                id="modal-title"
                aria-label="Ticket title"
                className="btn-secondary filter-select modal-title"
                style={{ width: '100%', fontSize: 16, fontWeight: 600, marginTop: 4 }}
                value={draft.title}
                disabled={isSaving}
                placeholder="Ticket title"
                onChange={(e) => patchDraft({ title: e.target.value })}
              />
            ) : (
              <div className="ticket-modal-title-row">
                <h2 id="modal-title" className="modal-title">{ticketTitle || 'Loading...'}</h2>
                {ticket && ticketTitle && <CopyValueButton value={ticketTitle} what="ticket name" />}
              </div>
            )}
            {!ticket && <p id="modal-description" className="modal-subtitle" />}
          </div>
          {ticket && !editing ? (
            /* The ticket card and its run ledger are both panes of exactly this
               ticket, and this modal is the one place that already has its id.
               The external id is the tab's name: `lg-flex-views-561` is what an
               operator recognises in a tab list, not "Ticket". */
            <AddToTabMenu
              primitiveId="chat_ticket"
              values={new Map([['ticket_id', ticket.external_id || ticket.id]])}
              title={ticket.external_id || ticket.title}
              label="Add this ticket to a tab"
            />
          ) : null}
          {ticket && !editing && onSave && !isLoading && !error ? (
            <button type="button" className="btn-secondary btn-compact" onClick={startEditing} disabled={busy}>
              Edit
            </button>
          ) : null}
          <IconCloseButton onClick={onClose} disabled={busy} aria-label="Close ticket details" />
        </div>

        <div className="modal-body">
          {error && (
            <p className="modal-hint" style={{ color: 'var(--red)' }}>{error}</p>
          )}

          {saveError && (
            <p className="modal-hint" role="alert" style={{ color: 'var(--red)' }}>{saveError}</p>
          )}

          {isLoading && (
            <p className="modal-hint">Loading ticket details…</p>
          )}

          {!isLoading && !error && ticket && editing && (
            <TicketEditForm
              draft={draft}
              tags={draftTags}
              disabled={isSaving}
              onChange={patchDraft}
            />
          )}

          {!isLoading && !error && ticket && !editing && (
            <>
              <div className="state-card">
                <div className="state-label">Status</div>
                <div className="ticket-modal-status-row">
                  <span className="ticket-modal-state" style={{ color: TICKET_STATE_COLORS[ticket.state] ?? 'var(--tx)' }}>
                    {STATE_LABELS[ticket.state] ?? asDisplayString(ticket.state)}
                  </span>
                  <span className="ticket-modal-priority">Priority: {priorityLabel(ticket.priority)}</span>
                </div>
              </div>

              <div className="state-card">
                <div className="state-label">Description</div>
                {asDisplayString(ticket.description).trim() ? (
                  <MarkdownContent
                    content={asDisplayString(ticket.description)}
                    className="ticket-modal-markdown"
                    readerTitle={ticketTitle || 'Ticket description'}
                    readerSubtitle={ticketNumber}
                  />
                ) : (
                  <p className="modal-hint ticket-empty-hint">
                    No description.{onSave ? ' Edit the ticket to add one.' : ''}
                  </p>
                )}
              </div>

              <TicketCriteriaView
                criteria={criteria}
                checked={shownChecks}
                onToggle={onSaveChecks ? (c, checked) => void handleToggle(c, checked) : undefined}
                disabled={busy}
              />

              {asStringArray(ticket.tags).length > 0 && (
                <div className="state-card">
                  <div className="state-label">Tags</div>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 4 }}>
                    {asStringArray(ticket.tags).map((tag) => (
                      <span key={tag.toLowerCase()} className="count-pill">{tag}</span>
                    ))}
                  </div>
                </div>
              )}

              <TicketDependencies ticket={ticket} />

              <TicketRelations ticket={ticket} />

              {ticket.work_item_type !== 'initiative' && <TicketGithubIssue ticket={ticket} />}

              <TicketReadOnlyDetails ticket={ticket} />
            </>
          )}
        </div>

        <div className="modal-footer">
          {editing ? (
            <>
              <button type="button" className="btn-secondary" onClick={cancelEditing} disabled={isSaving}>
                Cancel
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={!canSave || isSaving}
                onClick={() => void handleSave()}
              >
                {isSaving ? 'Saving…' : 'Save changes'}
              </button>
            </>
          ) : (
            <button type="button" className="btn-secondary" onClick={onClose} disabled={busy}>
              Close
            </button>
          )}
        </div>
      </div>
    </>
  );
};

interface TicketEditFormProps {
  draft: EditDraft;
  tags: string[];
  disabled: boolean;
  onChange: (patch: Partial<EditDraft>) => void;
}

function TicketEditForm({ draft, tags, disabled, onChange }: TicketEditFormProps) {
  return (
    <>
      <div className="state-card">
        <div className="state-label">Status</div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          <label>
            <div style={{ fontSize: 11, color: 'var(--txm)' }}>State</div>
            <select
              aria-label="State"
              className="btn-secondary filter-select"
              style={{ width: '100%', fontSize: 13, marginTop: 4 }}
              value={draft.state}
              disabled={disabled}
              onChange={(e) => onChange({ state: e.target.value as TicketState })}
            >
              {STATE_OPTIONS.map((s) => (
                <option key={s} value={s}>{STATE_LABELS[s]}</option>
              ))}
            </select>
          </label>
          <label>
            <div style={{ fontSize: 11, color: 'var(--txm)' }}>Priority</div>
            <select
              aria-label="Priority"
              className="btn-secondary filter-select"
              style={{ width: '100%', fontSize: 13, marginTop: 4 }}
              value={draft.priority}
              disabled={disabled}
              onChange={(e) => onChange({ priority: Number(e.target.value) })}
            >
              {PRIORITY_OPTIONS.map((p) => (
                <option key={p} value={p}>{priorityLabel(p)}</option>
              ))}
            </select>
          </label>
        </div>
      </div>

      <div className="state-card">
        <div className="state-label">Description</div>
        <textarea
          aria-label="Description (markdown)"
          className="btn-secondary filter-select ticket-modal-textarea"
          value={draft.description}
          disabled={disabled}
          placeholder="Add a description… Markdown is supported."
          onChange={(e) => onChange({ description: e.target.value })}
        />
      </div>

      <TicketCriteriaEditor rows={draft.criteria} onChange={(criteria) => onChange({ criteria })} disabled={disabled} />

      <div className="state-card">
        <div className="state-label">Tags</div>
        <input
          type="text"
          aria-label="Tags, comma separated"
          className="btn-secondary filter-select"
          style={{ width: '100%', fontSize: 13, marginTop: 4 }}
          value={draft.tagsText}
          disabled={disabled}
          placeholder="backend, needs-design…"
          onChange={(e) => onChange({ tagsText: e.target.value })}
        />
        {tags.length > 0 && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
            {tags.map((tag) => (
              <span key={tag.toLowerCase()} className="count-pill">
                {tag}
              </span>
            ))}
          </div>
        )}
        <p className="modal-hint" style={{ marginTop: 4 }}>
          Comma separated
        </p>
      </div>
    </>
  );
}

/** Everything the modal shows but never edits: blockers, stages, artifacts, metadata. */
function TicketReadOnlyDetails({ ticket }: { ticket: apiClient.TicketDetail }) {
  const diffArtifact = ticket.artifacts?.diff;
  const diffSummary = diffArtifact
    ? `Files: ${diffArtifact.files || diffArtifact.sections?.length || '?'} | Added: ${diffArtifact.add || '0'} | Removed: ${diffArtifact.del || '0'}`
    : null;
  const testsArtifact = ticket.artifacts?.tests;
  const testsSummary =
    testsArtifact?.summary ||
    (testsArtifact as { status?: string; passed?: number; failed?: number } | null | undefined)?.status ||
    ((testsArtifact as { passed?: number; failed?: number } | null | undefined)?.passed != null
      ? `Passed: ${(testsArtifact as { passed?: number }).passed} | Failed: ${(testsArtifact as { failed?: number }).failed ?? 0}`
      : null);

  return (
    <>
      {asDisplayString(ticket.blocking_issues) && (
        <div className="state-card">
          <div className="state-label">Blocking Issues</div>
          <p style={{ fontSize: 13, color: 'var(--red)', margin: 0 }}>{asDisplayString(ticket.blocking_issues)}</p>
        </div>
      )}

      {ticket.stages && ticket.stages.length > 0 ? (
        <div className="state-card">
          <div className="state-label">Workflow Stages</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {ticket.stages.map((stage) => (
              <div key={stage.key} style={{ fontSize: 12, color: 'var(--txm)' }}>
                <div style={{ color: 'var(--tx)' }}>{asDisplayString(stage.name)}</div>
                <div>Agent: {asDisplayString(stage.agent_id, 'N/A')} · Status: {asDisplayString(stage.status)}</div>
              </div>
            ))}
          </div>
        </div>
      ) : asDisplayString(ticket.workflow_stage_name) ? (
        <div className="state-card">
          <div className="state-label">Workflow Stage</div>
          <div style={{ fontSize: 13, color: 'var(--tx)' }}>{asDisplayString(ticket.workflow_stage_name)}</div>
        </div>
      ) : null}

      {ticket.artifacts && hasArtifactContent(ticket.artifacts) && (
        <div className="state-card">
          <div className="state-label">Artifacts</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--txm)' }}>
            {diffArtifact && (
              <div><strong>Code Diff:</strong> {diffSummary}</div>
            )}
            {testsArtifact && (
              <div><strong>Test Results:</strong> {testsSummary}</div>
            )}
            {ticket.artifacts.logs && ticket.artifacts.logs.length > 0 && (
              <div><strong>Logs:</strong> {ticket.artifacts.logs.length} entries</div>
            )}
            {ticket.artifacts.error && (
              <div style={{ color: 'var(--red)' }}><strong>Error:</strong> {asDisplayString(ticket.artifacts.error.message)}</div>
            )}
            {ticket.artifacts.live && (
              <div><strong>Status:</strong> {asDisplayString(ticket.artifacts.live)}</div>
            )}
          </div>
        </div>
      )}

      <div className="state-card">
        <div className="state-label">Metadata</div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, fontSize: 12 }}>
          <div>
            <div style={{ color: 'var(--txm)' }}>ID</div>
            <div style={{ marginTop: 4, fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--tx)', wordBreak: 'break-all' }}>{asDisplayString(ticket.id)}</div>
          </div>
          <div>
            <div style={{ color: 'var(--txm)' }}>Revision</div>
            <div style={{ marginTop: 4, color: 'var(--tx)' }}>{asDisplayString(ticket.revision)}</div>
          </div>
          {asDisplayString(ticket.work_item_type) && (
            <div>
              <div style={{ color: 'var(--txm)' }}>Type</div>
              <div style={{ marginTop: 4, color: 'var(--tx)' }}>{asDisplayString(ticket.work_item_type)}</div>
            </div>
          )}
          {asDisplayString(ticket.last_updated_by) && (
            <div>
              <div style={{ color: 'var(--txm)' }}>Last updated by</div>
              <div style={{ marginTop: 4, color: 'var(--tx)' }}>{asDisplayString(ticket.last_updated_by)}</div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
