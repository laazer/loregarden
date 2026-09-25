import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { IconCloseButton } from "./IconCloseButton";

import { api } from "../api/client";
import type {
  MemoryConfigSettings,
  RuntimeOptions,
  WorkspaceRuntimeSettings,
  WorkspaceSummary,
} from "../api/client";
import {
  MemorySettingsFields,
  emptyMemoryConfig,
  memoryConfigEqual,
} from "./MemorySettingsFields";
import {
  WorkspaceRuntimeFields,
  runtimeFromWorkspace,
  runtimeSettingsEqual,
} from "./WorkspaceRuntimeFields";
import { useDialogDismiss } from "../hooks/useDialogDismiss";
import { useDialogFocusTrap } from "../hooks/useDialogFocusTrap";
import { describeError } from "../state/toastStore";

type SettingsTab = "runtime" | "memory";

const TABS: { key: SettingsTab; label: string }[] = [
  { key: "runtime", label: "Agent runtime" },
  { key: "memory", label: "Memory" },
];

const TAB_HEADINGS: Record<SettingsTab, { eyebrow: string; title: string; subtitle: string }> = {
  runtime: {
    eyebrow: "Workspace",
    title: "Agent runtime",
    subtitle: "Default provider and model for stage agent runs",
  },
  memory: {
    eyebrow: "Agent memory",
    title: "iCloud & Obsidian setup",
    subtitle:
      "Synced markdown notes and optional SQLite graph storage, organized per workspace under your vault.",
  },
};

interface SettingsModalProps {
  open: boolean;
  workspaceSlug: string;
  workspaces: WorkspaceSummary[];
  runtimeOptions: RuntimeOptions | undefined;
  isSaving: boolean;
  onClose: () => void;
  onWorkspaceChange: (slug: string) => void;
  onSave: (slug: string, runtime: WorkspaceRuntimeSettings) => Promise<void>;
}

export function SettingsModal({
  open,
  workspaceSlug,
  workspaces,
  runtimeOptions,
  isSaving,
  onClose,
  onWorkspaceChange,
  onSave,
}: SettingsModalProps) {
  const qc = useQueryClient();
  const dialogRef = useDialogFocusTrap<HTMLDivElement>();
  const [tab, setTab] = useState<SettingsTab>("runtime");
  const workspace = workspaces.find((w) => w.slug === workspaceSlug);
  const [draft, setDraft] = useState<WorkspaceRuntimeSettings>(() => runtimeFromWorkspace(workspace));

  // Memory is a machine-wide setup rather than a per-workspace one, so it is
  // read only once its tab is opened — the dialog's common case is the runtime
  // fields, and the config read walks iCloud paths.
  const memoryConfig = useQuery({
    queryKey: ["memory-config"],
    queryFn: api.memoryConfig,
    enabled: open && tab === "memory",
  });
  const [memoryDraft, setMemoryDraft] = useState<MemoryConfigSettings>(emptyMemoryConfig);

  const setMemoryConfig = useMutation({
    meta: { errorTitle: "Save memory settings" },
    mutationFn: api.setMemoryConfig,
    onSuccess: (data) => {
      qc.setQueryData(["memory-config"], data);
    },
  });

  // Either tab's in-flight save holds the dialog open: dismissing mid-write
  // would leave the user with no idea whether it landed.
  const busy = isSaving || setMemoryConfig.isPending;
  // Escape and the backdrop agree on purpose: whatever makes a click
  // dismiss this dialog is what makes the key dismiss it.
  useDialogDismiss(!open ? null : busy ? undefined : onClose);

  useEffect(() => {
    if (!open) return;
    setDraft(runtimeFromWorkspace(workspace));
  }, [open, workspace?.cli_adapter, workspace?.claude_model, workspace?.cursor_model, workspace?.codex_model, workspace?.lmstudio_base_url, workspace?.lmstudio_model, workspace?.opencode_model, workspace?.claude_effort, workspace?.cursor_effort, workspace?.lmstudio_effort, workspace?.opencode_effort, workspaceSlug]);

  const memorySaved = memoryConfig.data?.config;
  useEffect(() => {
    if (!open || !memorySaved) return;
    setMemoryDraft(memorySaved);
  }, [open, memorySaved]);

  if (!open) return null;

  const saved = runtimeFromWorkspace(workspace);
  const dirty = !runtimeSettingsEqual(draft, saved);
  const memoryDirty = !memoryConfigEqual(memoryDraft, memorySaved ?? emptyMemoryConfig());
  const memorySaveError = setMemoryConfig.error
    ? describeError(setMemoryConfig.error, "Could not save memory settings")
    : null;
  const memoryLoadError = memoryConfig.error
    ? describeError(memoryConfig.error, "Could not load memory settings")
    : null;
  const heading = TAB_HEADINGS[tab];

  const close = () => {
    if (busy) return;
    setMemoryConfig.reset();
    onClose();
  };

  const handleSave = async () => {
    if (!workspaceSlug || !runtimeOptions) return;
    await onSave(workspaceSlug, draft);
    onClose();
  };

  return (
    <>
      <div className="modal-overlay" onClick={busy ? undefined : close} role="presentation" />
      <div
        ref={dialogRef}
        className="modal-panel modal-panel-wide"
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-modal-title"
      >
        <div className="modal-header">
          <div>
            <div className="state-label">{heading.eyebrow}</div>
            <h2 id="settings-modal-title" className="modal-title">
              {heading.title}
            </h2>
            <p className="modal-subtitle">{heading.subtitle}</p>
          </div>
          <IconCloseButton disabled={busy} onClick={close} />
        </div>

        <div className="tab-bar">
          <div className="tab-bar-scroll" role="tablist" aria-label="Settings sections">
            {TABS.map((entry) => (
              <button
                key={entry.key}
                type="button"
                role="tab"
                aria-selected={tab === entry.key}
                className={`tab-btn${tab === entry.key ? " active" : ""}`}
                disabled={busy}
                onClick={() => setTab(entry.key)}
              >
                {entry.label}
              </button>
            ))}
          </div>
        </div>

        {tab === "runtime" ? (
          <>
            <div className="modal-body">
              {workspaces.length > 1 && (
                <div className="modal-field">
                  <div className="modal-field-label">Workspace</div>
                  <select
                    className="btn-secondary filter-select"
                    style={{ width: "100%", fontSize: 12 }}
                    value={workspaceSlug}
                    disabled={busy}
                    onChange={(e) => onWorkspaceChange(e.target.value)}
                  >
                    {workspaces.map((w) => (
                      <option key={w.id} value={w.slug}>
                        {w.name}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              {runtimeOptions ? (
                <WorkspaceRuntimeFields
                  runtime={draft}
                  options={runtimeOptions}
                  disabled={busy}
                  onChange={setDraft}
                />
              ) : (
                <p className="modal-hint">Loading runtime options…</p>
              )}

              <p className="modal-hint" style={{ marginTop: 4 }}>
                Workspace default uses each agent&apos;s registry CLI. Choose Claude, Cursor, or LM
                Studio to override for all stage runs in this workspace.
              </p>
            </div>

            <div className="modal-footer">
              <button type="button" className="btn-secondary" disabled={busy} onClick={close}>
                Cancel
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={busy || !runtimeOptions || !dirty}
                onClick={() => void handleSave()}
              >
                {isSaving ? "Saving…" : "Save settings"}
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="modal-body">
              {memorySaveError || memoryLoadError ? (
                <p className="modal-hint" style={{ color: "var(--rdl)" }}>
                  {memorySaveError ?? memoryLoadError}
                </p>
              ) : null}

              {memoryConfig.isLoading && !memoryConfig.data ? (
                <p className="modal-hint">Loading memory configuration…</p>
              ) : (
                <MemorySettingsFields
                  draft={memoryDraft}
                  data={memoryConfig.data}
                  disabled={busy}
                  onChange={setMemoryDraft}
                />
              )}
            </div>

            <div className="modal-footer">
              <button
                type="button"
                className="btn-secondary"
                disabled={busy}
                onClick={() => void memoryConfig.refetch()}
              >
                Refresh
              </button>
              <div style={{ flex: 1 }} />
              <button type="button" className="btn-secondary" disabled={busy} onClick={close}>
                Cancel
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={busy || memoryConfig.isLoading || !memoryDirty}
                onClick={() => void setMemoryConfig.mutateAsync(memoryDraft)}
              >
                {setMemoryConfig.isPending ? "Saving…" : "Save setup"}
              </button>
            </div>
          </>
        )}
      </div>
    </>
  );
}
