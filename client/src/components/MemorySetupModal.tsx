import { useEffect, useState } from "react";

import type { MemoryConfigResponse, MemoryConfigSettings } from "../api/client";
import { RepoPathExplorer } from "./RepoPathExplorer";

interface MemorySetupModalProps {
  open: boolean;
  data: MemoryConfigResponse | undefined;
  isLoading: boolean;
  isSaving: boolean;
  errorMessage?: string;
  onClose: () => void;
  onSave: (config: MemoryConfigSettings) => void | Promise<void>;
  onRefresh: () => void;
}

function configEqual(a: MemoryConfigSettings, b: MemoryConfigSettings): boolean {
  return (
    a.icloud_root === b.icloud_root &&
    a.obsidian_vault_dir === b.obsidian_vault_dir &&
    a.obsidian_memory_subdir === b.obsidian_memory_subdir &&
    a.obsidian_learnings_subdir === b.obsidian_learnings_subdir &&
    a.memory_sqlite_url === b.memory_sqlite_url &&
    a.database_url === b.database_url
  );
}

function StatusPill({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className="count-pill"
      style={{
        background: ok ? "rgba(46,160,67,.15)" : "rgba(240,96,63,.12)",
        color: ok ? "var(--grn)" : "var(--rdl)",
        border: `1px solid ${ok ? "rgba(46,160,67,.35)" : "rgba(240,96,63,.35)"}`,
      }}
    >
      {label}
    </span>
  );
}

export function MemorySetupModal({
  open,
  data,
  isLoading,
  isSaving,
  errorMessage,
  onClose,
  onSave,
  onRefresh,
}: MemorySetupModalProps) {
  const [draft, setDraft] = useState<MemoryConfigSettings>(() => data?.config ?? emptyConfig());

  useEffect(() => {
    if (!open || !data?.config) return;
    setDraft(data.config);
  }, [open, data?.config]);

  if (!open) return null;

  const saved = data?.config ?? emptyConfig();
  const dirty = !configEqual(draft, saved);
  const status = data?.status;
  const defaultIcloud = data?.defaults.icloud_root;

  const applyDefaultIcloud = () => {
    if (!defaultIcloud) return;
    setDraft((d) => ({ ...d, icloud_root: defaultIcloud }));
  };

  const suggestMemoryDb = () => {
    const vault = draft.obsidian_vault_dir.trim();
    if (vault) {
      const base = vault.replace(/\/$/, "");
      setDraft((d) => ({ ...d, memory_sqlite_url: `sqlite:///${base}/Loregarden/memory.db` }));
      return;
    }
    const icloud = draft.icloud_root.trim() || defaultIcloud || "";
    if (icloud) {
      setDraft((d) => ({
        ...d,
        memory_sqlite_url: `sqlite:///${icloud.replace(/\/$/, "")}/Loregarden/memory.db`,
      }));
    }
  };

  const handleSave = () => {
    if (!dirty) return;
    void onSave(draft);
  };

  return (
    <>
      <div className="modal-overlay" onClick={isSaving ? undefined : onClose} role="presentation" />
      <div className="modal-panel modal-panel-wide" role="dialog" aria-labelledby="memory-setup-title">
        <div className="modal-header">
          <div>
            <div className="state-label">Agent memory</div>
            <h2 id="memory-setup-title" className="modal-title">
              iCloud &amp; Obsidian setup
            </h2>
            <p className="modal-subtitle">
              Configure synced markdown notes and optional SQLite graph storage for agent learnings
            </p>
          </div>
          <button type="button" className="btn-secondary" disabled={isSaving} onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="modal-body">
          {errorMessage && (
            <p className="modal-hint" style={{ color: "var(--rdl)" }}>
              {errorMessage}
            </p>
          )}

          {isLoading && !data ? (
            <p className="modal-hint">Loading memory configuration…</p>
          ) : (
            <>
              {status && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 16 }}>
                  <StatusPill ok={status.enabled} label={status.enabled ? "Memory active" : "Memory inactive"} />
                  {status.obsidian_vault ? (
                    <StatusPill ok label="Obsidian vault" />
                  ) : (
                    <StatusPill ok={false} label="No Obsidian vault" />
                  )}
                  {status.memory_sqlite_path ? (
                    <StatusPill ok label="SQLite graph" />
                  ) : (
                    <StatusPill ok={false} label="No SQLite graph" />
                  )}
                  {status.memory_sqlite_in_icloud ? <StatusPill ok label="DB in iCloud" /> : null}
                </div>
              )}

              <div className="modal-field">
                <div className="modal-field-label">iCloud Drive root</div>
                <input
                  className="btn-secondary filter-select"
                  style={{ width: "100%", fontSize: 12, fontFamily: "var(--mono)" }}
                  value={draft.icloud_root}
                  disabled={isSaving}
                  placeholder={defaultIcloud ?? "~/Library/Mobile Documents/com~apple~CloudDocs"}
                  onChange={(e) => setDraft((d) => ({ ...d, icloud_root: e.target.value }))}
                />
                <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
                  <button
                    type="button"
                    className="btn-secondary btn-compact"
                    disabled={isSaving || !defaultIcloud}
                    onClick={applyDefaultIcloud}
                  >
                    Use detected iCloud
                  </button>
                </div>
                <RepoPathExplorer
                  value={draft.icloud_root || defaultIcloud || "."}
                  onChange={(icloud_root) => setDraft((d) => ({ ...d, icloud_root }))}
                  disabled={isSaving}
                />
              </div>

              <div className="modal-field">
                <div className="modal-field-label">Obsidian vault folder</div>
                <input
                  className="btn-secondary filter-select"
                  style={{ width: "100%", fontSize: 12, fontFamily: "var(--mono)" }}
                  value={draft.obsidian_vault_dir}
                  disabled={isSaving}
                  placeholder="/path/to/MyVault"
                  onChange={(e) => setDraft((d) => ({ ...d, obsidian_vault_dir: e.target.value }))}
                />
                <RepoPathExplorer
                  value={draft.obsidian_vault_dir || draft.icloud_root || defaultIcloud || "."}
                  onChange={(obsidian_vault_dir) => setDraft((d) => ({ ...d, obsidian_vault_dir }))}
                  disabled={isSaving}
                />
              </div>

              <div className="modal-field">
                <div className="modal-field-label">Memory notes subfolder</div>
                <input
                  className="btn-secondary filter-select"
                  style={{ width: "100%", fontSize: 12, fontFamily: "var(--mono)" }}
                  value={draft.obsidian_memory_subdir}
                  disabled={isSaving}
                  onChange={(e) => setDraft((d) => ({ ...d, obsidian_memory_subdir: e.target.value }))}
                />
              </div>

              <div className="modal-field">
                <div className="modal-field-label">Learnings subfolder</div>
                <input
                  className="btn-secondary filter-select"
                  style={{ width: "100%", fontSize: 12, fontFamily: "var(--mono)" }}
                  value={draft.obsidian_learnings_subdir}
                  disabled={isSaving}
                  onChange={(e) => setDraft((d) => ({ ...d, obsidian_learnings_subdir: e.target.value }))}
                />
              </div>

              <div className="modal-field">
                <div className="modal-field-label">Memory graph SQLite URL (optional)</div>
                <input
                  className="btn-secondary filter-select"
                  style={{ width: "100%", fontSize: 12, fontFamily: "var(--mono)" }}
                  value={draft.memory_sqlite_url}
                  disabled={isSaving}
                  placeholder="sqlite:///…/Loregarden/memory.db"
                  onChange={(e) => setDraft((d) => ({ ...d, memory_sqlite_url: e.target.value }))}
                />
                <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                  <button
                    type="button"
                    className="btn-secondary btn-compact"
                    disabled={isSaving}
                    onClick={suggestMemoryDb}
                  >
                    Suggest from vault / iCloud
                  </button>
                </div>
                <p className="modal-hint" style={{ marginTop: 6 }}>
                  Stored in iCloud uses DELETE journal mode to avoid sync conflicts.
                </p>
              </div>

              <div className="modal-field">
                <div className="modal-field-label">Control-plane database URL</div>
                <input
                  className="btn-secondary filter-select"
                  style={{ width: "100%", fontSize: 12, fontFamily: "var(--mono)" }}
                  value={draft.database_url}
                  disabled={isSaving}
                  placeholder="sqlite:///data/loregarden.db"
                  onChange={(e) => setDraft((d) => ({ ...d, database_url: e.target.value }))}
                />
                <p className="modal-hint" style={{ marginTop: 6 }}>
                  Changing this may require restarting the server to reconnect the control-plane DB.
                </p>
              </div>

              {status && (
                <div className="modal-hint" style={{ marginTop: 8, fontFamily: "var(--mono)", fontSize: 11 }}>
                  {status.obsidian_memory_dir && <div>Memory dir: {status.obsidian_memory_dir}</div>}
                  {status.obsidian_learnings_dir && <div>Learnings dir: {status.obsidian_learnings_dir}</div>}
                  {status.memory_sqlite_path && <div>Graph DB: {status.memory_sqlite_path}</div>}
                </div>
              )}
            </>
          )}
        </div>

        <div className="modal-footer">
          <button type="button" className="btn-secondary" disabled={isSaving} onClick={() => onRefresh()}>
            Refresh
          </button>
          <div style={{ flex: 1 }} />
          <button type="button" className="btn-secondary" disabled={isSaving} onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={isSaving || isLoading || !dirty}
            onClick={handleSave}
          >
            {isSaving ? "Saving…" : "Save setup"}
          </button>
        </div>
      </div>
    </>
  );
}

function emptyConfig(): MemoryConfigSettings {
  return {
    icloud_root: "",
    obsidian_vault_dir: "",
    obsidian_memory_subdir: "Loregarden/Memory",
    obsidian_learnings_subdir: "Loregarden/Learnings",
    memory_sqlite_url: "",
    database_url: "sqlite:///data/loregarden.db",
  };
}
