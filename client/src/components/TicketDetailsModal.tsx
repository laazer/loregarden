import React, { useEffect, useState } from 'react';
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

  useEffect(() => {
    if (ticket) {
      setTitle(ticket.title);
      setDescription(ticket.description ?? '');
    }
  }, [ticket?.id, ticket?.title, ticket?.description]);

  if (!isOpen) {
    return null;
  }

  const isDirty =
    !!ticket &&
    (title.trim() !== ticket.title || description !== (ticket.description ?? ''));
  const canSave = isDirty && title.trim().length > 0 && !!onSave;

  const handleSave = async () => {
    if (!canSave) return;
    await onSave({ title: title.trim(), description });
  };

  return (
    <>
      <div className="modal-overlay" onClick={onClose} role="presentation" />
      <div className="modal-panel" role="dialog" aria-labelledby="modal-title">
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
              <h2 id="modal-title" className="modal-title">{ticket?.title || 'Loading...'}</h2>
            )}
            <p className="modal-subtitle">{ticket?.external_id || ''}</p>
          </div>
          <button type="button" className="btn-secondary" onClick={onClose} disabled={isSaving}>
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
                    <div style={{ marginTop: 4, fontSize: 13, color: 'var(--tx)' }}>{ticket.state}</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 11, color: 'var(--txm)' }}>Priority</div>
                    <div style={{ marginTop: 4, fontSize: 13, color: 'var(--tx)' }}>{ticket.priority}</div>
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

              {ticket.acceptance_criteria && ticket.acceptance_criteria.length > 0 && (
                <div className="state-card">
                  <div className="state-label">Acceptance Criteria</div>
                  <ul style={{ fontSize: 13, color: 'var(--tx)', margin: 0, paddingLeft: 20 }}>
                    {ticket.acceptance_criteria.map((criterion, index) => (
                      <li key={index}>{criterion}</li>
                    ))}
                  </ul>
                </div>
              )}

              {ticket.blocking_issues && (
                <div className="state-card">
                  <div className="state-label">Blocking Issues</div>
                  <p style={{ fontSize: 13, color: 'var(--red)', margin: 0 }}>{ticket.blocking_issues}</p>
                </div>
              )}

              {ticket.stages && ticket.stages.length > 0 && (
                <div className="state-card">
                  <div className="state-label">Workflow Stages</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    {ticket.stages.map((stage) => (
                      <div key={stage.key} style={{ fontSize: 12, color: 'var(--txm)' }}>
                        <div style={{ color: 'var(--tx)' }}>{stage.name}</div>
                        <div>Agent: {stage.agent_id || 'N/A'} · Status: {stage.status}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {ticket.artifacts && Object.keys(ticket.artifacts).some((k) => ticket.artifacts[k]) && (
                <div className="state-card">
                  <div className="state-label">Artifacts</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--txm)' }}>
                    {ticket.artifacts.diff && (
                      <div><strong>Code Diff:</strong> Files: {ticket.artifacts.diff.files || '?'} | Added: {ticket.artifacts.diff.add || '0'} | Removed: {ticket.artifacts.diff.del || '0'}</div>
                    )}
                    {ticket.artifacts.tests && (
                      <div><strong>Test Results:</strong> {ticket.artifacts.tests.summary}</div>
                    )}
                    {ticket.artifacts.logs && ticket.artifacts.logs.length > 0 && (
                      <div><strong>Logs:</strong> {ticket.artifacts.logs.length} entries</div>
                    )}
                    {ticket.artifacts.error && (
                      <div style={{ color: 'var(--red)' }}><strong>Error:</strong> {ticket.artifacts.error.message}</div>
                    )}
                    {ticket.artifacts.live && (
                      <div><strong>Status:</strong> {ticket.artifacts.live}</div>
                    )}
                  </div>
                </div>
              )}

              <div className="state-card">
                <div className="state-label">Metadata</div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, fontSize: 12 }}>
                  <div>
                    <div style={{ color: 'var(--txm)' }}>ID</div>
                    <div style={{ marginTop: 4, fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--tx)', wordBreak: 'break-all' }}>{ticket.id}</div>
                  </div>
                  <div>
                    <div style={{ color: 'var(--txm)' }}>Revision</div>
                    <div style={{ marginTop: 4, color: 'var(--tx)' }}>{ticket.revision}</div>
                  </div>
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
