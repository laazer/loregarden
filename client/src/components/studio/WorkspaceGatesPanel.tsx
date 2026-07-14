import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, type OrchestrationProfileView, type WorkspaceSummary } from "../../api/client";

export function WorkspaceGatesPanel({ workspaces }: { workspaces: WorkspaceSummary[] }) {
  const qc = useQueryClient();
  const [selectedSlug, setSelectedSlug] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [commands, setCommands] = useState<string[]>([]);
  const [transitionScript, setTransitionScript] = useState("");
  const [savedAt, setSavedAt] = useState<number | null>(null);

  useEffect(() => {
    if (!selectedSlug && workspaces.length > 0) setSelectedSlug(workspaces[0].slug);
  }, [workspaces, selectedSlug]);

  const profile = useQuery({
    queryKey: ["orchestration-profile", selectedSlug],
    queryFn: () => api.orchestrationProfile(selectedSlug),
    enabled: Boolean(selectedSlug),
  });

  // Reset the "Saved." indicator only when the user switches workspaces —
  // NOT on every profile.data change, since a successful save writes its
  // response into this same query's cache and would otherwise immediately
  // clobber the indicator it just set.
  useEffect(() => {
    setSavedAt(null);
  }, [selectedSlug]);

  useEffect(() => {
    if (!profile.data) return;
    setEnabled(profile.data.gates_enabled);
    setCommands(profile.data.gates_commands.length > 0 ? profile.data.gates_commands : [""]);
    setTransitionScript(profile.data.gates_transition_script);
  }, [profile.data]);

  const saveGates = useMutation({
    mutationFn: () =>
      api.updateWorkspaceGates(selectedSlug, {
        enabled,
        commands: commands.map((c) => c.trim()).filter(Boolean),
        transition_script: transitionScript.trim(),
      }),
    onSuccess: (updated: OrchestrationProfileView) => {
      qc.setQueryData(["orchestration-profile", selectedSlug], updated);
      setSavedAt(Date.now());
    },
  });

  const updateCommand = (index: number, value: string) => {
    setCommands((prev) => prev.map((c, i) => (i === index ? value : c)));
  };

  const removeCommand = (index: number) => {
    setCommands((prev) => (prev.length <= 1 ? prev : prev.filter((_, i) => i !== index)));
  };

  return (
    <div className="studio-shell">
      <aside className="studio-library-rail">
        <div className="studio-library-section-label">Workspace</div>
        {workspaces.map((ws) => (
          <button
            key={ws.slug}
            type="button"
            className={`studio-library-cta${selectedSlug === ws.slug ? " active" : ""}`}
            onClick={() => setSelectedSlug(ws.slug)}
          >
            {ws.name}
          </button>
        ))}
        {workspaces.length === 0 && <p className="studio-preview-hint">No workspaces yet.</p>}
      </aside>

      <div className="studio-editor">
        <div className="studio-editor-inner studio-editor-inner--gates">
          {!selectedSlug ? (
            <p className="studio-preview-hint">Select a workspace to view its transition gates.</p>
          ) : profile.isLoading ? (
            <p className="studio-preview-hint">Loading profile…</p>
          ) : (
            <>
              <div className="studio-card-title">Transition gates — {profile.data?.name}</div>
              <p className="studio-card-hint" style={{ marginTop: 0 }}>
                Commands run after each stage completes and before the ticket hands off to the
                next one. A non-zero exit blocks the handoff and reroutes the ticket back to the
                same stage for another pass, instead of advancing.
              </p>

              <label
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 9,
                  marginTop: 14,
                  fontSize: 13,
                  cursor: "pointer",
                  width: "fit-content",
                }}
              >
                <input
                  type="checkbox"
                  checked={enabled}
                  onChange={(e) => setEnabled(e.target.checked)}
                  style={{ accentColor: "var(--ac)" }}
                />
                Enable transition gates for this workspace
              </label>

              <div className="studio-field" style={{ marginTop: 16 }}>
                <div className="studio-field-label">Gate commands</div>
                {commands.map((command, index) => (
                  <div
                    key={index}
                    style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}
                  >
                    <input
                      className="studio-input"
                      value={command}
                      placeholder="e.g. lefthook run pre-commit --files-from-stdin"
                      onChange={(e) => updateCommand(index, e.target.value)}
                    />
                    <button
                      type="button"
                      className="btn-secondary"
                      disabled={commands.length <= 1}
                      onClick={() => removeCommand(index)}
                    >
                      Remove
                    </button>
                  </div>
                ))}
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setCommands((prev) => [...prev, ""])}
                >
                  Add command
                </button>
                <p className="studio-card-hint">
                  Supports placeholders: {"{ticket_id}"}, {"{external_id}"}, {"{transition}"},{" "}
                  {"{from_stage}"}, {"{to_stage}"}, {"{workspace_root}"}, {"{workspace_slug}"}.
                </p>
              </div>

              <div className="studio-field">
                <div className="studio-field-label">Transition script (optional)</div>
                <input
                  className="studio-input"
                  value={transitionScript}
                  placeholder="e.g. ci/scripts/run_workflow_transition_gates.py"
                  onChange={(e) => setTransitionScript(e.target.value)}
                />
                <p className="studio-card-hint">
                  Path relative to the workspace repo root. Runs before the commands above, called
                  with <code>--ticket-id</code> and <code>--transition</code>.
                </p>
              </div>

              <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 8 }}>
                <button
                  type="button"
                  className="btn-primary btn-cta"
                  disabled={saveGates.isPending}
                  onClick={() => saveGates.mutate()}
                >
                  {saveGates.isPending ? "Saving…" : "Save gates"}
                </button>
                {savedAt && !saveGates.isPending && (
                  <span className="studio-preview-hint">Saved.</span>
                )}
                {saveGates.isError && (
                  <span className="studio-preview-hint" style={{ color: "var(--rdl)" }}>
                    {(saveGates.error as Error).message}
                  </span>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
