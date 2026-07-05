import React, { useEffect, useRef, useState } from 'react';
import * as apiClient from '../api/client';

export interface TicketDetailsSaveDraft {
  title: string;
  description: string;
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
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (ticket) {
      setTitle(asDisplayString(ticket.title));
      setDescription(asDisplayString(ticket.description));
    }
  }, [ticket?.id, ticket?.title, ticket?.description]);

  useEffect(() => {
    if (!isOpen) return;
    panelRef.current?.focus();
  }, [isOpen, ticket?.id]);

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

  const acceptanceCriteria = asStringArray(ticket?.acceptance_criteria);
  const isDirty =
    !!ticket &&
    (title.trim() !== asDisplayString(ticket.title) ||
      description !== asDisplayString(ticket.description));
  const canSave = isDirty && title.trim().length > 0 && !!onSave;

  const handleSave = async () => {
    if (!canSave) return;
    await onSave({ title: title.trim(), description });
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
        ref={panelRef}
        className="modal-panel"
        role="dialog"
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
          <button type="button" className="btn-secondary" onClick={onClose} disabled={isSaving} aria-label="Close ticket details">
            ✕
          </button>
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
                    <div style={{ marginTop: 4, fontSize: 13, color: 'var(--tx)' }}>{asDisplayString(ticket.state)}</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 11, color: 'var(--txm)' }}>Priority</div>
                    <div style={{ marginTop: 4, fontSize: 13, color: 'var(--tx)' }}>{asDisplayString(ticket.priority)}</div>
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

              {acceptanceCriteria.length > 0 && (
                <div className="state-card">
                  <div className="state-label">Acceptance Criteria</div>
                  <ul style={{ fontSize: 13, color: 'var(--tx)', margin: 0, paddingLeft: 20 }}>
                    {acceptanceCriteria.map((criterion, index) => (
                      <li key={index}>{criterion}</li>
                    ))}
                  </ul>
                </div>
              )}

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
