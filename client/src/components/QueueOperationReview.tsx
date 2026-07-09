/**
 * GitHub-style review interface for queue operations
 * Inline comments, threads, and approval workflow
 */

import { useState } from 'react';
import './QueueOperationReview.css';

export interface OperationComment {
  id: string;
  line_number?: number;
  run_id?: string;
  content: string;
  created_by: string;
  created_at: string;
  resolved: boolean;
}

export interface QueueOperationReviewProps {
  operationId: string;
  comments: OperationComment[];
  approved: boolean;
  approvedBy?: string;
  onAddComment?: (content: string, runId?: string, lineNumber?: number) => void;
  onApprove?: () => void;
  onSubmitToAgent?: (agentId: string, instructions?: string) => void;
  isLoading?: boolean;
}

export function QueueOperationReview({
  operationId,
  comments = [],
  approved,
  approvedBy,
  onAddComment,
  onApprove,
  onSubmitToAgent,
  isLoading = false,
}: QueueOperationReviewProps) {
  const [newComment, setNewComment] = useState('');
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [showAgentSubmit, setShowAgentSubmit] = useState(false);
  const [agentId, setAgentId] = useState('default-orchestrator');
  const [agentInstructions, setAgentInstructions] = useState('');

  const handleAddComment = () => {
    if (!newComment.trim()) return;

    onAddComment?.(newComment, selectedRunId || undefined, undefined);
    setNewComment('');
    setSelectedRunId(null);
  };

  const handleSubmitToAgent = () => {
    onSubmitToAgent?.(agentId, agentInstructions);
    setShowAgentSubmit(false);
    setAgentInstructions('');
  };

  const groupedComments = {
    general: comments.filter((c) => !c.run_id),
    byRun: comments.reduce(
      (acc, c) => {
        if (c.run_id) {
          if (!acc[c.run_id]) acc[c.run_id] = [];
          acc[c.run_id].push(c);
        }
        return acc;
      },
      {} as Record<string, OperationComment[]>
    ),
  };

  return (
    <div className="queue-operation-review">
      {/* Header */}
      <div className="review-header">
        <h3>Review & Approve</h3>
        {approved && (
          <div className="approval-badge">
            ✓ Approved by {approvedBy || 'system'}
          </div>
        )}
      </div>

      {/* Comments Section */}
      <div className="comments-section">
        <h4 className="section-title">
          Comments ({comments.length})
        </h4>

        {comments.length === 0 ? (
          <div className="no-comments">
            <span className="icon">💬</span>
            <span className="text">No comments yet</span>
          </div>
        ) : (
          <>
            {/* General Comments */}
            {groupedComments.general.length > 0 && (
              <div className="comment-thread">
                <div className="thread-header">General</div>
                {groupedComments.general.map((comment) => (
                  <div key={comment.id} className="comment">
                    <div className="comment-header">
                      <span className="author">{comment.created_by}</span>
                      <span className="time">
                        {new Date(comment.created_at).toLocaleString()}
                      </span>
                      {comment.resolved && (
                        <span className="resolved-badge">Resolved</span>
                      )}
                    </div>
                    <div className="comment-body">{comment.content}</div>
                  </div>
                ))}
              </div>
            )}

            {/* Per-Run Comments */}
            {Object.entries(groupedComments.byRun).map(([runId, runComments]) => (
              <div key={runId} className="comment-thread">
                <div className="thread-header">
                  <span className="run-label">Run:</span>
                  <span className="run-id">{runId}</span>
                </div>
                {runComments.map((comment) => (
                  <div key={comment.id} className="comment">
                    <div className="comment-header">
                      <span className="author">{comment.created_by}</span>
                      <span className="time">
                        {new Date(comment.created_at).toLocaleString()}
                      </span>
                      {comment.resolved && (
                        <span className="resolved-badge">Resolved</span>
                      )}
                    </div>
                    <div className="comment-body">{comment.content}</div>
                  </div>
                ))}
              </div>
            ))}
          </>
        )}
      </div>

      {/* Add Comment Section */}
      <div className="add-comment-section">
        <h4 className="section-title">Add Comment</h4>

        <div className="comment-form">
          <textarea
            className="comment-input"
            placeholder="Add a comment (markdown supported)..."
            value={newComment}
            onChange={(e) => setNewComment(e.target.value)}
            disabled={isLoading}
            rows={3}
          />

          <div className="form-actions">
            <button
              className="btn btn-comment"
              onClick={handleAddComment}
              disabled={isLoading || !newComment.trim()}
            >
              💬 Comment
            </button>

            {!approved && (
              <>
                <button
                  className="btn btn-approve"
                  onClick={onApprove}
                  disabled={isLoading}
                >
                  ✓ Approve
                </button>

                <button
                  className="btn btn-submit-agent"
                  onClick={() => setShowAgentSubmit(true)}
                  disabled={isLoading}
                >
                  🤖 Submit to Agent
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Agent Submission Modal */}
      {showAgentSubmit && (
        <div className="agent-submit-modal">
          <div className="modal-content">
            <h4>Submit to Agent</h4>

            <div className="form-group">
              <label htmlFor="agent-id">Agent ID</label>
              <select
                id="agent-id"
                className="form-select"
                value={agentId}
                onChange={(e) => setAgentId(e.target.value)}
              >
                <option value="default-orchestrator">
                  Default Orchestrator
                </option>
                <option value="code-reviewer">Code Reviewer</option>
                <option value="general">General Purpose</option>
              </select>
            </div>

            <div className="form-group">
              <label htmlFor="agent-instructions">
                Additional Instructions
              </label>
              <textarea
                id="agent-instructions"
                className="form-textarea"
                placeholder="Any special instructions for the agent..."
                value={agentInstructions}
                onChange={(e) => setAgentInstructions(e.target.value)}
                rows={3}
              />
            </div>

            <div className="submission-context">
              <div className="context-item">
                <span className="label">Operation:</span>
                <span className="value">{operationId}</span>
              </div>
              <div className="context-item">
                <span className="label">Comments:</span>
                <span className="value">{comments.length}</span>
              </div>
              <div className="context-hint">
                All comments will be included in the agent submission
              </div>
            </div>

            <div className="modal-actions">
              <button
                className="btn btn-primary"
                onClick={handleSubmitToAgent}
                disabled={isLoading}
              >
                {isLoading ? '⏳ Submitting...' : '🤖 Submit to Agent'}
              </button>
              <button
                className="btn btn-secondary"
                onClick={() => setShowAgentSubmit(false)}
                disabled={isLoading}
              >
                Cancel
              </button>
            </div>
          </div>
          <div
            className="modal-backdrop"
            onClick={() => setShowAgentSubmit(false)}
          />
        </div>
      )}

      {isLoading && (
        <div className="review-loading">
          <div className="spinner"></div>
          <span>Processing...</span>
        </div>
      )}
    </div>
  );
}
