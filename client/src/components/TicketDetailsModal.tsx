import React, { useEffect, useState } from 'react';
import * as apiClient from '../api/client';
import type { TicketState } from '../api/client';
import { priorityLabel } from '../lib/importTicketPreview';
import { IconCloseButton } from './IconCloseButton';
import { TicketDependencies } from './TicketDependencies';
import { TicketRelations } from './TicketRelations';
import { STATE_LABELS } from './UpdateStateModal';
import { useDialogFocusTrap } from '../hooks/useDialogFocusTrap';
import { AddToTabMenu } from './AddToTabMenu';

const STATE_OPTIONS = Object.keys(STATE_LABELS) as TicketState[];
const PRIORITY_OPTIONS = [1, 2, 3] as const;

export interface TicketDetailsSaveDraft {
  title: string;
  description: string;
  acceptanceCriteria: string[];
  tags: string[];
  state: TicketState;
  priority: number;
}

/** One criterion per line, blank lines dropped — mirrors the server's normalization. */
function parseCriteria(text: string): string[] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
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
  onSave?: (draft: TicketDetailsSaveDraft) => Promise<void>;
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
}) => {
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [criteriaText, setCriteriaText] = useState('');
  const [tagsText, setTagsText] = useState('');
  const [state, setState] = useState<TicketState>('backlog');
  const [priority, setPriority] = useState(3);
  const dialogRef = useDialogFocusTrap<HTMLDivElement>();

  // Joined rather than the array itself: a refetch hands back a new array identity
  // every time, which would re-seed the textarea and discard an in-progress edit.
  const criteriaSeed = asStringArray(ticket?.acceptance_criteria).join('\n');
  const tagsSeed = asStringArray(ticket?.tags).join(', ');

  useEffect(() => {
    if (ticket) {
      setTitle(asDisplayString(ticket.title));
      setDescription(asDisplayString(ticket.description));
      setCriteriaText(criteriaSeed);
      setTagsText(tagsSeed);
      setState(ticket.state);
      setPriority(ticket.priority);
    }
  }, [ticket?.id, ticket?.title, ticket?.description, ticket?.state, ticket?.priority, criteriaSeed, tagsSeed]);

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && onClose) {
        onClose();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) {
    return null;
  }

  if (!ticket && !isLoading && !error) {
    return null;
  }

  const acceptanceCriteria = parseCriteria(criteriaText);
  const tags = parseTags(tagsText);
  const isDirty =
    !!ticket &&
    (title.trim() !== asDisplayString(ticket.title) ||
      description !== asDisplayString(ticket.description) ||
      acceptanceCriteria.join('\n') !== parseCriteria(criteriaSeed).join('\n') ||
      tags.join(',') !== parseTags(tagsSeed).join(',') ||
      state !== ticket.state ||
      priority !== ticket.priority);
  const canSave = isDirty && title.trim().length > 0 && !!onSave;

  const handleSave = async () => {
    if (!canSave) return;
    await onSave({ title: title.trim(), description, acceptanceCriteria, tags, state, priority });
  };

  const diffArtifact = ticket?.artifacts?.diff;
  const diffSummary = diffArtifact
    ? `Files: ${diffArtifact.files || diffArtifact.sections?.length || '?'} | Added: ${diffArtifact.add || '0'} | Removed: ${diffArtifact.del || '0'}`
    : null;
  const testsArtifact = ticket?.artifacts?.tests;
  const testsSummary =
    testsArtifact?.summary ||
    (testsArtifact as { status?: string; passed?: number; failed?: number } | null | undefined)?.status ||
    ((testsArtifact as { passed?: number; failed?: number } | null | undefined)?.passed != null
      ? `Passed: ${(testsArtifact as { passed?: number }).passed} | Failed: ${(testsArtifact as { failed?: number }).failed ?? 0}`
      : null);

  return (
    <>
      <div
        className="modal-overlay"
        data-testid="modal-backdrop"
        onClick={onClose}
        role="presentation"
      />
      <div
        ref={dialogRef}
        className="modal-panel"
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
            <div className="state-label">Ticket</div>
            {!isLoading && !error && ticket ? (
              <input
                id="modal-title"
                className="btn-secondary filter-select modal-title"
                style={{ width: '100%', fontSize: 16, fontWeight: 600, marginTop: 4 }}
                value={title}
                disabled={isSaving}
                placeholder="Ticket title"
                onChange={(e) => setTitle(e.target.value)}
              />
            ) : (
              <h2 id="modal-title" className="modal-title">{asDisplayString(ticket?.title, 'Loading...')}</h2>
            )}
            <p id="modal-description" className="modal-subtitle">{asDisplayString(ticket?.external_id)}</p>
          </div>
          {ticket ? (
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
          <IconCloseButton onClick={onClose} disabled={isSaving} aria-label="Close ticket details" />
        </div>

        <div className="modal-body">
          {error && (
            <p className="modal-hint" style={{ color: 'var(--red)' }}>{error}</p>
          )}

          {saveError && (
            <p className="modal-hint" style={{ color: 'var(--red)' }}>{saveError}</p>
          )}

          {isLoading && (
            <p className="modal-hint">Loading ticket details…</p>
          )}

          {!isLoading && !error && ticket && (
            <>
              <div className="state-card">
                <div className="state-label">Status</div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                  <div>
                    <div style={{ fontSize: 11, color: 'var(--txm)' }}>State</div>
                    <select
                      aria-label="State"
                      className="btn-secondary filter-select"
                      style={{ width: '100%', fontSize: 13, marginTop: 4 }}
                      value={state}
                      disabled={isSaving}
                      onChange={(e) => setState(e.target.value as TicketState)}
                    >
                      {STATE_OPTIONS.map((s) => (
                        <option key={s} value={s}>{STATE_LABELS[s]}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <div style={{ fontSize: 11, color: 'var(--txm)' }}>Priority</div>
                    <select
                      aria-label="Priority"
                      className="btn-secondary filter-select"
                      style={{ width: '100%', fontSize: 13, marginTop: 4 }}
                      value={priority}
                      disabled={isSaving}
                      onChange={(e) => setPriority(Number(e.target.value))}
                    >
                      {PRIORITY_OPTIONS.map((p) => (
                        <option key={p} value={p}>{priorityLabel(p)}</option>
                      ))}
                    </select>
                  </div>
                </div>
              </div>

              <div className="state-card">
                <div className="state-label">Description</div>
                <textarea
                  className="btn-secondary filter-select"
                  style={{ width: '100%', fontSize: 13, minHeight: 96, resize: 'vertical', marginTop: 4 }}
                  value={description}
                  disabled={isSaving}
                  placeholder="Add a description…"
                  onChange={(e) => setDescription(e.target.value)}
                />
              </div>

              <div className="state-card">
                <div className="state-label">Acceptance Criteria</div>
                <textarea
                  aria-label="Acceptance criteria, one per line"
                  className="btn-secondary filter-select"
                  style={{ width: '100%', fontSize: 13, minHeight: 96, resize: 'vertical', marginTop: 4 }}
                  value={criteriaText}
                  disabled={isSaving}
                  placeholder="One criterion per line…"
                  onChange={(e) => setCriteriaText(e.target.value)}
                />
                <p className="modal-hint" style={{ marginTop: 4 }}>
                  {acceptanceCriteria.length === 1
                    ? '1 criterion'
                    : `${acceptanceCriteria.length} criteria`}
                  , one per line
                </p>
              </div>

              <div className="state-card">
                <div className="state-label">Tags</div>
                <input
                  type="text"
                  aria-label="Tags, comma separated"
                  className="btn-secondary filter-select"
                  style={{ width: '100%', fontSize: 13, marginTop: 4 }}
                  value={tagsText}
                  disabled={isSaving}
                  placeholder="backend, needs-design…"
                  onChange={(e) => setTagsText(e.target.value)}
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

              <TicketDependencies ticket={ticket} />

              <TicketRelations ticket={ticket} />

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
          )}
        </div>

        <div className="modal-footer">
          <button type="button" className="btn-secondary" onClick={onClose} disabled={isSaving}>
            Close
          </button>
          {onSave && (
            <button
              type="button"
              className="btn-primary"
              disabled={!canSave || isSaving}
              onClick={handleSave}
            >
              {isSaving ? 'Saving…' : 'Save changes'}
            </button>
          )}
        </div>
      </div>
    </>
  );
};
