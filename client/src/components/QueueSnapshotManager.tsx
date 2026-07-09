/**
 * Queue snapshot manager: save, restore, and replay queue states
 */

import { useState, useMemo } from 'react';
import './QueueSnapshotManager.css';

export interface Snapshot {
  id: string;
  name: string;
  description?: string;
  tags?: string[];
  created_at: string;
  created_by?: string;
  stats?: Record<string, number>;
}

export interface QueueSnapshotManagerProps {
  workspaceId: string;
  snapshots: Snapshot[];
  onSave?: (name: string, description?: string, tags?: string[]) => void;
  onRestore?: (snapshotId: string) => void;
  onDelete?: (snapshotId: string) => void;
  onReplay?: (count: number) => void;
  isLoading?: boolean;
}

export function QueueSnapshotManager({
  snapshots = [],
  onSave,
  onRestore,
  onDelete,
  onReplay,
  isLoading = false,
}: QueueSnapshotManagerProps) {
  const [activeTab, setActiveTab] = useState<'snapshots' | 'save' | 'replay'>(
    'snapshots'
  );
  const [saveName, setSaveName] = useState('');
  const [saveDescription, setSaveDescription] = useState('');
  const [saveTags, setSaveTags] = useState('');
  const [replayCount, setReplayCount] = useState(5);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedTag, setSelectedTag] = useState<string | null>(null);

  // Extract unique tags from snapshots
  const uniqueTags = useMemo(() => {
    const tags = new Set<string>();
    snapshots.forEach((s) => {
      s.tags?.forEach((t) => tags.add(t));
    });
    return Array.from(tags);
  }, [snapshots]);

  // Filter snapshots by search and tag
  const filteredSnapshots = useMemo(() => {
    return snapshots.filter((s) => {
      const matchesSearch =
        !searchQuery ||
        s.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        s.description?.toLowerCase().includes(searchQuery.toLowerCase());

      const matchesTag = !selectedTag || s.tags?.includes(selectedTag);

      return matchesSearch && matchesTag;
    });
  }, [snapshots, searchQuery, selectedTag]);

  const handleSave = () => {
    if (!saveName.trim()) {
      alert('Snapshot name is required');
      return;
    }

    const tags = saveTags
      .split(',')
      .map((t) => t.trim())
      .filter((t) => t.length > 0);

    onSave?.(saveName, saveDescription, tags);

    // Reset form
    setSaveName('');
    setSaveDescription('');
    setSaveTags('');
    setActiveTab('snapshots');
  };

  const handleReplay = () => {
    if (replayCount < 1) {
      alert('Replay count must be at least 1');
      return;
    }

    onReplay?.(replayCount);
    setActiveTab('snapshots');
  };

  return (
    <div className="queue-snapshot-manager">
      {/* Tabs */}
      <div className="manager-tabs">
        <button
          className={`tab-btn ${activeTab === 'snapshots' ? 'active' : ''}`}
          onClick={() => setActiveTab('snapshots')}
        >
          📸 Snapshots ({snapshots.length})
        </button>
        <button
          className={`tab-btn ${activeTab === 'save' ? 'active' : ''}`}
          onClick={() => setActiveTab('save')}
        >
          💾 Save
        </button>
        <button
          className={`tab-btn ${activeTab === 'replay' ? 'active' : ''}`}
          onClick={() => setActiveTab('replay')}
        >
          🔄 Replay
        </button>
      </div>

      {/* Content */}
      <div className="manager-content">
        {/* Snapshots List Tab */}
        {activeTab === 'snapshots' && (
          <div className="snapshots-panel">
            {snapshots.length === 0 ? (
              <div className="empty-state">
                <div className="empty-icon">📸</div>
                <div className="empty-text">No snapshots yet</div>
                <div className="empty-hint">
                  Save the current queue state to create a checkpoint
                </div>
              </div>
            ) : (
              <>
                <div className="search-section">
                  <input
                    type="text"
                    className="search-input"
                    placeholder="Search by name or description..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                  />

                  {uniqueTags.length > 0 && (
                    <div className="tags-filter">
                      <button
                        className={`tag-filter-btn ${!selectedTag ? 'active' : ''}`}
                        onClick={() => setSelectedTag(null)}
                      >
                        All Tags
                      </button>
                      {uniqueTags.map((tag) => (
                        <button
                          key={tag}
                          className={`tag-filter-btn ${
                            selectedTag === tag ? 'active' : ''
                          }`}
                          onClick={() => setSelectedTag(tag)}
                        >
                          {tag}
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                <div className="snapshots-list">
                  {filteredSnapshots.length === 0 ? (
                    <div className="no-results">
                      No snapshots match your search
                    </div>
                  ) : (
                    filteredSnapshots.map((snapshot) => (
                      <div key={snapshot.id} className="snapshot-card">
                        <div className="card-header">
                          <div className="snapshot-name">{snapshot.name}</div>
                          <div className="snapshot-time">
                            {new Date(snapshot.created_at).toLocaleString()}
                          </div>
                        </div>

                        {snapshot.description && (
                          <div className="snapshot-description">
                            {snapshot.description}
                          </div>
                        )}

                        {snapshot.tags && snapshot.tags.length > 0 && (
                          <div className="snapshot-tags">
                            {snapshot.tags.map((tag) => (
                              <span key={tag} className="tag">
                                {tag}
                              </span>
                            ))}
                          </div>
                        )}

                        {snapshot.stats && (
                          <div className="snapshot-stats">
                            <div className="stat-item">
                              <span className="stat-label">Total Runs:</span>
                              <span className="stat-value">
                                {snapshot.stats.total_runs || 0}
                              </span>
                            </div>
                            <div className="stat-item">
                              <span className="stat-label">Active:</span>
                              <span className="stat-value">
                                {snapshot.stats.active_count || 0}
                              </span>
                            </div>
                            <div className="stat-item">
                              <span className="stat-label">Queued:</span>
                              <span className="stat-value">
                                {snapshot.stats.queued_count || 0}
                              </span>
                            </div>
                            {snapshot.stats.failed_count > 0 && (
                              <div className="stat-item failed">
                                <span className="stat-label">Failed:</span>
                                <span className="stat-value">
                                  {snapshot.stats.failed_count}
                                </span>
                              </div>
                            )}
                          </div>
                        )}

                        <div className="card-actions">
                          <button
                            className="btn btn-restore"
                            onClick={() => onRestore?.(snapshot.id)}
                            disabled={isLoading}
                          >
                            🔄 Restore
                          </button>
                          <button
                            className="btn btn-delete"
                            onClick={() => {
                              if (
                                window.confirm(
                                  `Delete snapshot "${snapshot.name}"?`
                                )
                              ) {
                                onDelete?.(snapshot.id);
                              }
                            }}
                            disabled={isLoading}
                          >
                            🗑 Delete
                          </button>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </>
            )}
          </div>
        )}

        {/* Save Tab */}
        {activeTab === 'save' && (
          <div className="save-panel">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                handleSave();
              }}
            >
              <div className="form-group">
                <label htmlFor="snapshot-name">Snapshot Name *</label>
                <input
                  id="snapshot-name"
                  type="text"
                  className="form-input"
                  placeholder="e.g., Before reorder, Stable checkpoint"
                  value={saveName}
                  onChange={(e) => setSaveName(e.target.value)}
                  disabled={isLoading}
                />
              </div>

              <div className="form-group">
                <label htmlFor="snapshot-desc">Description</label>
                <textarea
                  id="snapshot-desc"
                  className="form-textarea"
                  placeholder="Optional notes about this snapshot..."
                  value={saveDescription}
                  onChange={(e) => setSaveDescription(e.target.value)}
                  disabled={isLoading}
                  rows={3}
                />
              </div>

              <div className="form-group">
                <label htmlFor="snapshot-tags">Tags</label>
                <input
                  id="snapshot-tags"
                  type="text"
                  className="form-input"
                  placeholder="Comma-separated tags (e.g., backup, important)"
                  value={saveTags}
                  onChange={(e) => setSaveTags(e.target.value)}
                  disabled={isLoading}
                />
              </div>

              <div className="form-actions">
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={isLoading || !saveName.trim()}
                >
                  {isLoading ? '⏳ Saving...' : '💾 Save Snapshot'}
                </button>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setActiveTab('snapshots')}
                  disabled={isLoading}
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        )}

        {/* Replay Tab */}
        {activeTab === 'replay' && (
          <div className="replay-panel">
            <div className="replay-info">
              <div className="info-icon">ℹ️</div>
              <div className="info-text">
                Replay the last N runs back into the queue for re-execution
              </div>
            </div>

            <form
              onSubmit={(e) => {
                e.preventDefault();
                handleReplay();
              }}
            >
              <div className="form-group">
                <label htmlFor="replay-count">Number of Runs to Replay</label>
                <div className="input-with-slider">
                  <input
                    id="replay-count"
                    type="number"
                    className="form-input"
                    min="1"
                    max="100"
                    value={replayCount}
                    onChange={(e) => setReplayCount(Number(e.target.value))}
                    disabled={isLoading}
                  />
                  <input
                    type="range"
                    className="form-range"
                    min="1"
                    max="100"
                    value={replayCount}
                    onChange={(e) => setReplayCount(Number(e.target.value))}
                    disabled={isLoading}
                  />
                </div>
                <div className="range-label">
                  Will replay the last {replayCount} completed/failed runs
                </div>
              </div>

              <div className="form-actions">
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={isLoading}
                >
                  {isLoading ? '⏳ Replaying...' : '🔄 Replay Runs'}
                </button>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setActiveTab('snapshots')}
                  disabled={isLoading}
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        )}
      </div>

      {isLoading && (
        <div className="manager-loading">
          <div className="spinner"></div>
          <span>Processing...</span>
        </div>
      )}
    </div>
  );
}
