/**
 * Side-by-side diff viewer for queue operations (before/after states)
 */

import { useMemo, useState } from 'react';
import type { OperationComment } from './QueueOperationReview';
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
  comments?: OperationComment[];
  onAddComment?: (content: string, runId?: string, lineNumber?: number) => void;
  onReviewRunOutput?: (runId: string) => void;
  isLoading?: boolean;
}

export function QueueDiffViewer({
  beforeState,
  afterState,
  changes,
  operationType,
  description,
  comments = [],
  onAddComment,
  onReviewRunOutput,
  isLoading = false,
}: QueueDiffViewerProps) {
  const [commentRunId, setCommentRunId] = useState<string | null>(null);
  const [commentDraft, setCommentDraft] = useState('');

  const commentsByRun = useMemo(() => {
    return comments.reduce(
      (acc, comment) => {
        const key = comment.run_id || '__general__';
        if (!acc[key]) acc[key] = [];
        acc[key].push(comment);
        return acc;
      },
      {} as Record<string, OperationComment[]>,
    );
  }, [comments]);
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

  const submitRunComment = (runId: string) => {
    if (!commentDraft.trim() || !onAddComment) return;
    onAddComment(commentDraft, runId);
    setCommentDraft('');
    setCommentRunId(null);
  };

  const renderChangeComments = (runId: string) => {
    const runComments = commentsByRun[runId] ?? [];
    return (
      <div className="diff-change-comments">
        {runComments.map((comment) => (
          <div key={comment.id} className="diff-change-comment">
            <div className="diff-change-comment-meta">
              <span>{comment.created_by}</span>
              <span>{new Date(comment.created_at).toLocaleString()}</span>
            </div>
            <div className="diff-change-comment-body">{comment.content}</div>
          </div>
        ))}
        {commentRunId === runId ? (
          <div className="diff-change-comment-form">
            <textarea
              className="diff-change-comment-input"
              rows={2}
              value={commentDraft}
              placeholder="Add a review comment…"
              disabled={isLoading}
              onChange={(e) => setCommentDraft(e.target.value)}
            />
            <div className="diff-change-comment-actions">
              <button
                type="button"
                className="btn-secondary btn-compact"
                disabled={isLoading || !commentDraft.trim()}
                onClick={() => submitRunComment(runId)}
              >
                Comment
              </button>
              <button
                type="button"
                className="btn-secondary btn-compact"
                disabled={isLoading}
                onClick={() => {
                  setCommentRunId(null);
                  setCommentDraft('');
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <div className="diff-change-comment-actions">
            {onAddComment ? (
              <button
                type="button"
                className="btn-secondary btn-compact"
                disabled={isLoading}
                onClick={() => setCommentRunId(runId)}
              >
                + Comment
              </button>
            ) : null}
            {onReviewRunOutput ? (
              <button
                type="button"
                className="btn-secondary btn-compact"
                disabled={isLoading}
                onClick={() => onReviewRunOutput(runId)}
              >
                Review output
              </button>
            ) : null}
          </div>
        )}
      </div>
    );
  };

  const renderChangeItem = (change: DiffChange, tone: 'added' | 'removed' | 'modified') => (
    <div key={change.run_id} className={`change-item ${tone}`}>
      <div className="change-header">
        <span className="run-id">{change.run_id}</span>
        <span className="ticket-id">{change.ticket_id}</span>
        {change.position ? <span className="position">Position {change.position}</span> : null}
        {(commentsByRun[change.run_id]?.length ?? 0) > 0 ? (
          <span className="diff-comment-count">💬 {commentsByRun[change.run_id].length}</span>
        ) : null}
      </div>

      {tone === 'modified' && change.fields_changed && change.fields_changed.length > 0 ? (
        <div className="field-changes">
          {change.fields_changed.map((field) => {
            const before = change.before?.[field];
            const after = change.after?.[field];
            return (
              <div key={field} className="field-change">
                <div className="field-name">{field}</div>
                <div className="field-values">
                  {before !== undefined ? (
                    <div className="before-value">
                      <span className="label">before:</span>
                      <span className="value">{JSON.stringify(before)}</span>
                    </div>
                  ) : null}
                  {after !== undefined ? (
                    <div className="after-value">
                      <span className="label">after:</span>
                      <span className="value">{JSON.stringify(after)}</span>
                    </div>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      ) : null}

      <div className="change-icon">
        {tone === 'added' ? '+ Added' : tone === 'removed' ? '− Removed' : '~ Modified'}
      </div>
      {renderChangeComments(change.run_id)}
    </div>
  );

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
              {groupedChanges.added.map((change) => renderChangeItem(change, 'added'))}
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
              {groupedChanges.removed.map((change) => renderChangeItem(change, 'removed'))}
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
              {groupedChanges.modified.map((change) => renderChangeItem(change, 'modified'))}
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
