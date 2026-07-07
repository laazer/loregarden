import { IconCloseButton } from "./IconCloseButton";

import { useEffect, useState } from "react";

import type { MemoryConfigResponse, MemoryConfigSettings } from "../api/client";
import { RepoPathExplorer, sanitizeBrowsePath } from "./RepoPathExplorer";

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
    a.obsidian_blogposts_subdir === b.obsidian_blogposts_subdir &&
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
  const [icloudJump, setIcloudJump] = useState("");
  const [obsidianJump, setObsidianJump] = useState("");

  useEffect(() => {
    if (!open || !data?.config) return;
    setDraft(data.config);
    setIcloudJump("");
    setObsidianJump("");
  }, [open, data?.config]);

  if (!open) return null;

  const saved = data?.config ?? emptyConfig();
  const dirty = !configEqual(draft, saved);
  const status = data?.status;
  const defaultIcloud = data?.defaults.icloud_root;
  const mobileDocuments = data?.defaults.mobile_documents_dir;
  const obsidianIcloud = data?.defaults.obsidian_icloud_dir;
  const obsidianDocuments = data?.defaults.obsidian_documents_dir;

  const jumpIcloudExplorer = (path: string) => {
    const target = sanitizeBrowsePath(path);
    if (!target) return;
    setDraft((d) => ({ ...d, icloud_root: target }));
    setIcloudJump(target);
  };

  const applyDefaultIcloud = () => {
    if (!defaultIcloud) return;
    jumpIcloudExplorer(defaultIcloud);
  };

  const applyMobileDocuments = () => {
    if (!mobileDocuments) return;
    jumpIcloudExplorer(mobileDocuments);
  };

  const applyObsidianIcloud = () => {
    if (!obsidianIcloud) return;
    jumpIcloudExplorer(obsidianIcloud);
  };

  const openTypedObsidianPath = () => {
    const target = sanitizeBrowsePath(draft.obsidian_vault_dir);
    if (!target) return;
    setDraft((d) => ({ ...d, obsidian_vault_dir: target }));
    setObsidianJump(target);
  };

  const icloudBrowseStart =
    sanitizeBrowsePath(draft.icloud_root) || mobileDocuments || defaultIcloud || ".";

  const obsidianBrowseStart =
    sanitizeBrowsePath(draft.obsidian_vault_dir) ||
    obsidianDocuments ||
    obsidianIcloud ||
    sanitizeBrowsePath(draft.icloud_root) ||
    mobileDocuments ||
    defaultIcloud ||
    ".";

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
              Configure synced markdown notes and optional SQLite graph storage. Notes and graph DBs
              are organized per workspace under your vault.
            </p>
          </div>
          <IconCloseButton disabled={isSaving} onClick={onClose} />
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
                  onChange={(e) =>
                    setDraft((d) => ({ ...d, icloud_root: sanitizeBrowsePath(e.target.value) }))
                  }
                />
                <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
                  <button
                    type="button"
                    className="btn-secondary btn-compact"
                    disabled={isSaving || !defaultIcloud}
                    onClick={applyDefaultIcloud}
                  >
                    iCloud Drive
                  </button>
                  <button
                    type="button"
                    className="btn-secondary btn-compact"
                    disabled={isSaving || !mobileDocuments}
                    onClick={applyMobileDocuments}
                  >
                    Mobile Documents
                  </button>
                  <button
                    type="button"
                    className="btn-secondary btn-compact"
                    disabled={isSaving || !obsidianIcloud}
                    onClick={applyObsidianIcloud}
                  >
                    Obsidian sync
                  </button>
                  <button
                    type="button"
                    className="btn-secondary btn-compact"
                    disabled={isSaving || !draft.icloud_root.trim()}
                    onClick={() => jumpIcloudExplorer(draft.icloud_root)}
                  >
                    Open typed path
                  </button>
                </div>
                <p className="modal-hint" style={{ marginTop: 6 }}>
                  Obsidian&apos;s iCloud vault is <code>iCloud~md~obsidian</code> under Mobile
                  Documents — not inside iCloud Drive. Use <strong>Obsidian sync</strong> or{" "}
                  <strong>Mobile Documents</strong>, then open <code>Documents</code>.
                </p>
                <RepoPathExplorer
                  explorerKey="memory-icloud"
                  absolutePaths
                  value={draft.icloud_root}
                  startPath={icloudBrowseStart}
                  navigateTo={icloudJump}
                  onChange={(icloud_root) =>
                    setDraft((d) => ({ ...d, icloud_root: sanitizeBrowsePath(icloud_root) }))
                  }
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
                  placeholder={
                    obsidianDocuments
                      ? `${obsidianDocuments}/Aetherium/Project Vault`
                      : "/path/to/MyVault"
                  }
                  onChange={(e) =>
                    setDraft((d) => ({ ...d, obsidian_vault_dir: sanitizeBrowsePath(e.target.value) }))
                  }
                />
                <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
                  <button
                    type="button"
                    className="btn-secondary btn-compact"
                    disabled={isSaving || !draft.obsidian_vault_dir.trim()}
                    onClick={openTypedObsidianPath}
                  >
                    Open typed path
                  </button>
                  <button
                    type="button"
                    className="btn-secondary btn-compact"
                    disabled={isSaving || !obsidianDocuments}
                    onClick={() => setObsidianJump(obsidianDocuments ?? "")}
                  >
                    Browse Obsidian Documents
                  </button>
                </div>
                <RepoPathExplorer
                  key={`memory-obsidian-${obsidianBrowseStart}`}
                  explorerKey="memory-obsidian"
                  absolutePaths
                  value={draft.obsidian_vault_dir}
                  startPath={obsidianBrowseStart}
                  navigateTo={obsidianJump}
                  onChange={(obsidian_vault_dir) =>
                    setDraft((d) => ({ ...d, obsidian_vault_dir: sanitizeBrowsePath(obsidian_vault_dir) }))
                  }
                  disabled={isSaving}
                />
                <p className="modal-hint" style={{ marginTop: 6 }}>
                  Obsidian sync lives under{" "}
                  <code>~/Library/Mobile Documents/iCloud~md~obsidian</code>, not iCloud Drive. Use
                  “Browse Obsidian Documents”, then open <code>Aetherium</code> →{" "}
                  <code>Project Vault</code>.
                </p>
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
                <div className="modal-field-label">Blog posts subfolder</div>
                <input
                  className="btn-secondary filter-select"
                  style={{ width: "100%", fontSize: 12, fontFamily: "var(--mono)" }}
                  value={draft.obsidian_blogposts_subdir}
                  disabled={isSaving}
                  onChange={(e) => setDraft((d) => ({ ...d, obsidian_blogposts_subdir: e.target.value }))}
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
                  Base path for graph DBs. Each workspace gets its own subfolder (e.g. Loregarden/
                  {"{workspace}"}/memory.db). iCloud uses DELETE journal mode to avoid sync conflicts.
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
                  {status.obsidian_blogposts_dir && <div>Blog posts dir: {status.obsidian_blogposts_dir}</div>}
                  {status.memory_sqlite_path && <div>Graph DB: {status.memory_sqlite_path}</div>}
                  {status.memory_graph_node_types?.length ? (
                    <div>SQLite stores: {status.memory_graph_node_types.join(", ")} nodes</div>
                  ) : null}
                  {status.memory_graph_excludes?.length ? (
                    <div>Not in SQLite: {status.memory_graph_excludes.join(", ")}</div>
                  ) : null}
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
    obsidian_blogposts_subdir: "Loregarden/BlogPosts",
    memory_sqlite_url: "",
    database_url: "sqlite:///data/loregarden.db",
  };
}
