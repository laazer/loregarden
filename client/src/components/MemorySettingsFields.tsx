/**
 * The iCloud / Obsidian memory form, without a dialog around it.
 *
 * It used to be a modal of its own reached from a topbar button; memory is a
 * workspace setting, so it lives in the Settings dialog's Memory tab now and
 * this module is only the fields. Draft state, the save and the footer belong
 * to whoever renders it — one dialog, one dirty check, one Save button.
 */

import { useState } from "react";

import type { MemoryConfigResponse, MemoryConfigSettings } from "../api/client";
import { RepoPathExplorer, sanitizeBrowsePath } from "./RepoPathExplorer";

export function emptyMemoryConfig(): MemoryConfigSettings {
  return {
    icloud_root: "",
    obsidian_vault_dir: "",
    obsidian_memory_subdir: "Loregarden/Memory",
    obsidian_learnings_subdir: "Loregarden/Learnings",
    obsidian_blogposts_subdir: "Loregarden/BlogPosts",
    obsidian_checkpoints_subdir: "Loregarden/Checkpoints",
    memory_sqlite_url: "",
    database_url: "sqlite:///data/loregarden.db",
  };
}

export function memoryConfigEqual(a: MemoryConfigSettings, b: MemoryConfigSettings): boolean {
  return (
    a.icloud_root === b.icloud_root &&
    a.obsidian_vault_dir === b.obsidian_vault_dir &&
    a.obsidian_memory_subdir === b.obsidian_memory_subdir &&
    a.obsidian_learnings_subdir === b.obsidian_learnings_subdir &&
    a.obsidian_blogposts_subdir === b.obsidian_blogposts_subdir &&
    a.obsidian_checkpoints_subdir === b.obsidian_checkpoints_subdir &&
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

export function MemorySettingsFields({
  draft,
  data,
  disabled,
  onChange,
}: {
  draft: MemoryConfigSettings;
  data: MemoryConfigResponse | undefined;
  disabled: boolean;
  onChange: (next: MemoryConfigSettings) => void;
}) {
  const [icloudJump, setIcloudJump] = useState("");
  const [obsidianJump, setObsidianJump] = useState("");

  const patch = (fields: Partial<MemoryConfigSettings>) => onChange({ ...draft, ...fields });

  const status = data?.status;
  const defaultIcloud = data?.defaults.icloud_root;
  const mobileDocuments = data?.defaults.mobile_documents_dir;
  const obsidianIcloud = data?.defaults.obsidian_icloud_dir;
  const obsidianDocuments = data?.defaults.obsidian_documents_dir;

  const jumpIcloudExplorer = (path: string) => {
    const target = sanitizeBrowsePath(path);
    if (!target) return;
    patch({ icloud_root: target });
    setIcloudJump(target);
  };

  const openTypedObsidianPath = () => {
    const target = sanitizeBrowsePath(draft.obsidian_vault_dir);
    if (!target) return;
    patch({ obsidian_vault_dir: target });
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
      patch({ memory_sqlite_url: `sqlite:///${vault.replace(/\/$/, "")}/Loregarden/memory.db` });
      return;
    }
    const icloud = draft.icloud_root.trim() || defaultIcloud || "";
    if (icloud) {
      patch({ memory_sqlite_url: `sqlite:///${icloud.replace(/\/$/, "")}/Loregarden/memory.db` });
    }
  };

  return (
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
          disabled={disabled}
          placeholder={defaultIcloud ?? "~/Library/Mobile Documents/com~apple~CloudDocs"}
          onChange={(e) => patch({ icloud_root: sanitizeBrowsePath(e.target.value) })}
        />
        <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
          <button
            type="button"
            className="btn-secondary btn-compact"
            disabled={disabled || !defaultIcloud}
            onClick={() => jumpIcloudExplorer(defaultIcloud ?? "")}
          >
            iCloud Drive
          </button>
          <button
            type="button"
            className="btn-secondary btn-compact"
            disabled={disabled || !mobileDocuments}
            onClick={() => jumpIcloudExplorer(mobileDocuments ?? "")}
          >
            Mobile Documents
          </button>
          <button
            type="button"
            className="btn-secondary btn-compact"
            disabled={disabled || !obsidianIcloud}
            onClick={() => jumpIcloudExplorer(obsidianIcloud ?? "")}
          >
            Obsidian sync
          </button>
          <button
            type="button"
            className="btn-secondary btn-compact"
            disabled={disabled || !draft.icloud_root.trim()}
            onClick={() => jumpIcloudExplorer(draft.icloud_root)}
          >
            Open typed path
          </button>
        </div>
        <p className="modal-hint" style={{ marginTop: 6 }}>
          Obsidian&apos;s iCloud vault is <code>iCloud~md~obsidian</code> under Mobile Documents — not
          inside iCloud Drive. Use <strong>Obsidian sync</strong> or <strong>Mobile Documents</strong>,
          then open <code>Documents</code>.
        </p>
        <RepoPathExplorer
          explorerKey="memory-icloud"
          absolutePaths
          value={draft.icloud_root}
          startPath={icloudBrowseStart}
          navigateTo={icloudJump}
          onChange={(icloud_root) => patch({ icloud_root: sanitizeBrowsePath(icloud_root) })}
          disabled={disabled}
        />
      </div>

      <div className="modal-field">
        <div className="modal-field-label">Obsidian vault folder</div>
        <input
          className="btn-secondary filter-select"
          style={{ width: "100%", fontSize: 12, fontFamily: "var(--mono)" }}
          value={draft.obsidian_vault_dir}
          disabled={disabled}
          placeholder={
            obsidianDocuments ? `${obsidianDocuments}/Aetherium/Project Vault` : "/path/to/MyVault"
          }
          onChange={(e) => patch({ obsidian_vault_dir: sanitizeBrowsePath(e.target.value) })}
        />
        <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
          <button
            type="button"
            className="btn-secondary btn-compact"
            disabled={disabled || !draft.obsidian_vault_dir.trim()}
            onClick={openTypedObsidianPath}
          >
            Open typed path
          </button>
          <button
            type="button"
            className="btn-secondary btn-compact"
            disabled={disabled || !obsidianDocuments}
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
            patch({ obsidian_vault_dir: sanitizeBrowsePath(obsidian_vault_dir) })
          }
          disabled={disabled}
        />
        <p className="modal-hint" style={{ marginTop: 6 }}>
          Obsidian sync lives under <code>~/Library/Mobile Documents/iCloud~md~obsidian</code>, not
          iCloud Drive. Use “Browse Obsidian Documents”, then open <code>Aetherium</code> →{" "}
          <code>Project Vault</code>.
        </p>
      </div>

      <div className="modal-field">
        <div className="modal-field-label">Memory notes subfolder</div>
        <input
          className="btn-secondary filter-select"
          style={{ width: "100%", fontSize: 12, fontFamily: "var(--mono)" }}
          value={draft.obsidian_memory_subdir}
          disabled={disabled}
          onChange={(e) => patch({ obsidian_memory_subdir: e.target.value })}
        />
      </div>

      <div className="modal-field">
        <div className="modal-field-label">Learnings subfolder</div>
        <input
          className="btn-secondary filter-select"
          style={{ width: "100%", fontSize: 12, fontFamily: "var(--mono)" }}
          value={draft.obsidian_learnings_subdir}
          disabled={disabled}
          onChange={(e) => patch({ obsidian_learnings_subdir: e.target.value })}
        />
      </div>

      <div className="modal-field">
        <div className="modal-field-label">Blog posts subfolder</div>
        <input
          className="btn-secondary filter-select"
          style={{ width: "100%", fontSize: 12, fontFamily: "var(--mono)" }}
          value={draft.obsidian_blogposts_subdir}
          disabled={disabled}
          onChange={(e) => patch({ obsidian_blogposts_subdir: e.target.value })}
        />
      </div>

      <div className="modal-field">
        <div className="modal-field-label">Checkpoints subfolder</div>
        <input
          className="btn-secondary filter-select"
          style={{ width: "100%", fontSize: 12, fontFamily: "var(--mono)" }}
          value={draft.obsidian_checkpoints_subdir}
          disabled={disabled}
          onChange={(e) => patch({ obsidian_checkpoints_subdir: e.target.value })}
        />
      </div>

      <div className="modal-field">
        <div className="modal-field-label">Memory graph SQLite URL (optional)</div>
        <input
          className="btn-secondary filter-select"
          style={{ width: "100%", fontSize: 12, fontFamily: "var(--mono)" }}
          value={draft.memory_sqlite_url}
          disabled={disabled}
          placeholder="sqlite:///…/Loregarden/memory.db"
          onChange={(e) => patch({ memory_sqlite_url: e.target.value })}
        />
        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
          <button
            type="button"
            className="btn-secondary btn-compact"
            disabled={disabled}
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
          disabled={disabled}
          placeholder="sqlite:///data/loregarden.db"
          onChange={(e) => patch({ database_url: e.target.value })}
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
          {status.obsidian_checkpoints_dir && (
            <div>Checkpoints dir: {status.obsidian_checkpoints_dir}</div>
          )}
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
  );
}
