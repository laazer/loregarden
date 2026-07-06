/**
 * Side-by-side diff viewer for queue operations (before/after states)
 */

import React, { useMemo } from 'react';
import './QueueDiffViewer.css';

export interface DiffChange {
  type: 'added' | 'removed' | 'modified';
  run_id: string;
  ticket_id: string;
  position?: number;
  before?: Record<string, any>;
  after?: Record<string, any>;
  fields_changed?: string[];
}

export interface QueueDiffViewerProps {
  beforeState: any[];
  afterState: any[];
  changes: DiffChange[];
  operationType: string;
  description?: string;
}

export function QueueDiffViewer({
  beforeState,
  afterState,
  changes,
  operationType,
  description,
}: QueueDiffViewerProps) {
  const stats = useMemo(() => {
    const added = changes.filter((c) => c.type === 'added').length;
    const removed = changes.filter((c) => c.type === 'removed').length;
    const modified = changes.filter((c) => c.type === 'modified').length;

    return { added, removed, modified, total: changes.length };
  }, [changes]);

  const groupedChanges = useMemo(() => {
    return {
      added: changes.filter((c) => c.type === 'added'),
      removed: changes.filter((c) => c.type === 'removed'),
      modified: changes.filter((c) => c.type === 'modified'),
    };
  }, [changes]);

  return (
    <div className="queue-diff-viewer">
      {/* Header */}
      <div className="diff-header">
        <div className="diff-title">
          <span className="operation-type">{operationType}</span>
          {description && <span className="diff-description">{description}</span>}
        </div>

        <div className="diff-stats">
          {stats.added > 0 && (
            <span className="stat added">
              +{stats.added} added
            </span>
          )}
          {stats.removed > 0 && (
            <span className="stat removed">
              -{stats.removed} removed
            </span>
          )}
          {stats.modified > 0 && (
            <span className="stat modified">
              ~{stats.modified} modified
            </span>
          )}
        </div>
      </div>

      {/* Diff Sections */}
      <div className="diff-content">
        {/* Added Runs */}
        {groupedChanges.added.length > 0 && (
          <div className="diff-section added-section">
            <div className="section-title">
              <span className="section-icon">+</span>
              <span>Added Runs ({groupedChanges.added.length})</span>
            </div>

            <div className="changes-list">
              {groupedChanges.added.map((change) => (
                <div key={change.run_id} className="change-item added">
                  <div className="change-header">
                    <span className="run-id">{change.run_id}</span>
                    <span className="ticket-id">{change.ticket_id}</span>
                    {change.position && (
                      <span className="position">Position {change.position}</span>
                    )}
                  </div>
                  <div className="change-icon">+ Added</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Removed Runs */}
        {groupedChanges.removed.length > 0 && (
          <div className="diff-section removed-section">
            <div className="section-title">
              <span className="section-icon">−</span>
              <span>Removed Runs ({groupedChanges.removed.length})</span>
            </div>

            <div className="changes-list">
              {groupedChanges.removed.map((change) => (
                <div key={change.run_id} className="change-item removed">
                  <div className="change-header">
                    <span className="run-id">{change.run_id}</span>
                    <span className="ticket-id">{change.ticket_id}</span>
                    {change.position && (
                      <span className="position">Position {change.position}</span>
                    )}
                  </div>
                  <div className="change-icon">− Removed</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Modified Runs */}
        {groupedChanges.modified.length > 0 && (
          <div className="diff-section modified-section">
            <div className="section-title">
              <span className="section-icon">~</span>
              <span>Modified Runs ({groupedChanges.modified.length})</span>
            </div>

            <div className="changes-list">
              {groupedChanges.modified.map((change) => (
                <div key={change.run_id} className="change-item modified">
                  <div className="change-header">
                    <span className="run-id">{change.run_id}</span>
                    <span className="ticket-id">{change.ticket_id}</span>
                  </div>

                  {change.fields_changed && change.fields_changed.length > 0 && (
                    <div className="field-changes">
                      {change.fields_changed.map((field) => {
                        const before = change.before?.[field];
                        const after = change.after?.[field];

                        return (
                          <div key={field} className="field-change">
                            <div className="field-name">{field}</div>
                            <div className="field-values">
                              {before !== undefined && (
                                <div className="before-value">
                                  <span className="label">before:</span>
                                  <span className="value">
                                    {JSON.stringify(before)}
                                  </span>
                                </div>
                              )}
                              {after !== undefined && (
                                <div className="after-value">
                                  <span className="label">after:</span>
                                  <span className="value">
                                    {JSON.stringify(after)}
                                  </span>
                                </div>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}

                  <div className="change-icon">~ Modified</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {stats.total === 0 && (
          <div className="no-changes">
            <div className="no-changes-icon">✓</div>
            <div className="no-changes-text">No changes in this operation</div>
          </div>
        )}
      </div>

      {/* Footer Stats */}
      <div className="diff-footer">
        <span className="total-changes">
          {stats.total} total change{stats.total === 1 ? '' : 's'}
        </span>
        <span className="queue-sizes">
          {beforeState.length} → {afterState.length} runs
        </span>
      </div>
    </div>
  );
}
