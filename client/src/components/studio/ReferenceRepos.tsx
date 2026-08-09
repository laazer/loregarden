import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { errorDetail } from "../../utils/errorDetail";

import {
  api,
  type ReferenceRepo,
  type TicketStudioSession,
  type TicketStudioSurveyFinding,
} from "../../api/client";

const VERDICT_LABEL: Record<string, string> = {
  adopt: "Adopt",
  adapt: "Adapt",
  inspire: "Inspire",
  skip: "Skip",
};

/** Add a repo to the workspace catalog, or pick from what is already cloned. */
export function ReferenceRepoPicker({
  workspaceSlug,
  selectedIds,
  onChange,
  disabled = false,
}: {
  workspaceSlug: string;
  selectedIds: string[];
  onChange: (ids: string[]) => void;
  disabled?: boolean;
}) {
  const qc = useQueryClient();
  const repos = useQuery({
    queryKey: ["reference-repos", workspaceSlug],
    queryFn: () => api.referenceRepos(workspaceSlug),
    enabled: Boolean(workspaceSlug),
  });
  const [url, setUrl] = useState("");
  const [notes, setNotes] = useState("");

  const addRepo = useMutation({
    mutationFn: () =>
      api.addReferenceRepo({ workspace_slug: workspaceSlug, url: url.trim(), notes: notes.trim() }),
    onSuccess: (repo) => {
      qc.invalidateQueries({ queryKey: ["reference-repos", workspaceSlug] });
      setUrl("");
      setNotes("");
      if (!selectedIds.includes(repo.id)) onChange([...selectedIds, repo.id]);
    },
  });

  const toggle = (repo: ReferenceRepo) => {
    onChange(
      selectedIds.includes(repo.id)
        ? selectedIds.filter((id) => id !== repo.id)
        : [...selectedIds, repo.id],
    );
  };

  const addError = errorDetail(addRepo.error);

  return (
    <div className="studio-field">
      <div className="studio-field-label">Reference repos</div>
      <p className="studio-card-hint" style={{ marginTop: 0 }}>
        Repos to scope against. Cloned once per workspace and reused by later sessions.
      </p>

      {(repos.data ?? []).length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 8 }}>
          {(repos.data ?? []).map((repo) => (
            <label
              key={repo.id}
              style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}
            >
              <input
                type="checkbox"
                checked={selectedIds.includes(repo.id)}
                disabled={disabled || !repo.cloned}
                onChange={() => toggle(repo)}
              />
              <span style={{ flex: 1, minWidth: 0 }}>{repo.slug}</span>
              {!repo.cloned && <span className="ticket-draft-meta">clone missing</span>}
            </label>
          ))}
        </div>
      )}

      <div style={{ display: "flex", gap: 6 }}>
        <input
          className="studio-input"
          placeholder="https://github.com/owner/repo"
          value={url}
          disabled={disabled}
          onChange={(e) => setUrl(e.target.value)}
        />
        <button
          type="button"
          className="btn-secondary btn-compact"
          disabled={disabled || !url.trim() || addRepo.isPending}
          onClick={() => addRepo.mutate()}
        >
          {addRepo.isPending ? "Cloning…" : "Add"}
        </button>
      </div>
      <input
        className="studio-input"
        style={{ marginTop: 6 }}
        placeholder="Why this repo is interesting (optional)"
        value={notes}
        disabled={disabled}
        onChange={(e) => setNotes(e.target.value)}
      />
      {addError && (
        <div className="studio-card-hint" role="alert" style={{ color: "var(--dgr)" }}>
          {addError}
        </div>
      )}
    </div>
  );
}

/** Attached repos plus the survey of what is worth taking from them. */
export function ReferenceReposSection({
  session,
  isReadOnly = false,
  onSessionUpdated,
}: {
  session: TicketStudioSession;
  isReadOnly?: boolean;
  onSessionUpdated?: (updated: TicketStudioSession) => void;
}) {
  const [findings, setFindings] = useState<TicketStudioSurveyFinding[]>(session.survey ?? []);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    setFindings(session.survey ?? []);
  }, [session.id, session.updated_at, session.survey]);

  const survey = useMutation({
    mutationFn: () => api.generateTicketStudioSurvey(session.id),
    onSuccess: (updated) => {
      setFindings(updated.survey ?? []);
      onSessionUpdated?.(updated);
    },
  });

  const saveSurvey = useMutation({
    mutationFn: (next: TicketStudioSurveyFinding[]) => api.saveTicketStudioSurvey(session.id, next),
    onSuccess: (updated) => onSessionUpdated?.(updated),
  });

  const detach = useMutation({
    mutationFn: (repoId: string) =>
      api.setTicketStudioReferenceRepos(
        session.id,
        session.reference_repos.filter((repo) => repo.id !== repoId).map((repo) => repo.id),
      ),
    onSuccess: (updated) => {
      setFindings(updated.survey ?? []);
      onSessionUpdated?.(updated);
    },
  });

  const repos = session.reference_repos ?? [];
  if (repos.length === 0) return null;

  const toggleFinding = (ref: string) => {
    const next = findings.map((finding) =>
      finding.ref === ref ? { ...finding, selected: !finding.selected } : finding,
    );
    setFindings(next);
    saveSurvey.mutate(next);
  };

  const surveyError = errorDetail(survey.error) ?? errorDetail(saveSurvey.error);
  const selectedCount = findings.filter((finding) => finding.selected).length;

  return (
    <div
      data-testid="reference-repos-section"
      style={{ marginBottom: 16, paddingBottom: 16, borderBottom: "1px solid var(--bd)" }}
    >
      <div
        style={{
          fontSize: 11,
          fontWeight: 600,
          color: "var(--txm)",
          marginBottom: 8,
          textTransform: "uppercase",
          letterSpacing: 0.5,
        }}
      >
        Reference repos
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 10 }}>
        {repos.map((repo) => (
          <div key={repo.id} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
            <span style={{ flex: 1, minWidth: 0 }}>{repo.slug}</span>
            {!repo.cloned && <span className="ticket-draft-meta">clone missing</span>}
            {!isReadOnly && (
              <button
                type="button"
                className="btn-secondary btn-compact"
                onClick={() => detach.mutate(repo.id)}
                aria-label={`Detach ${repo.slug}`}
              >
                Detach
              </button>
            )}
          </div>
        ))}
      </div>

      {!isReadOnly && (
        <button
          type="button"
          className="btn-secondary btn-compact"
          disabled={survey.isPending}
          onClick={() => survey.mutate()}
        >
          {survey.isPending
            ? "Surveying…"
            : findings.length > 0
              ? "Re-run survey"
              : "Survey what's useful"}
        </button>
      )}

      {surveyError && (
        <div className="studio-card-hint" role="alert" style={{ color: "var(--dgr)" }}>
          {surveyError}
        </div>
      )}

      {findings.length > 0 && (
        <>
          <p className="studio-card-hint">
            {selectedCount} of {findings.length} parts selected — only these are scoped into
            tickets.
          </p>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {findings.map((finding) => (
              <div
                key={finding.ref}
                className={`ticket-draft-card${finding.selected ? " selected" : ""}`}
              >
                <div style={{ display: "flex", alignItems: "flex-start", gap: 11 }}>
                  <button
                    type="button"
                    className={`ticket-draft-check${finding.selected ? " checked" : ""}`}
                    disabled={isReadOnly}
                    onClick={() => toggleFinding(finding.ref)}
                    aria-label={`Include ${finding.title}`}
                  >
                    {finding.selected && (
                      <svg
                        width="12"
                        height="12"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="var(--onac)"
                        strokeWidth="3.2"
                      >
                        <path d="M20 6 9 17l-5-5" />
                      </svg>
                    )}
                  </button>
                  <button
                    type="button"
                    style={{
                      flex: 1,
                      minWidth: 0,
                      border: "none",
                      background: "transparent",
                      padding: 0,
                      textAlign: "left",
                      cursor: "pointer",
                      color: "inherit",
                      font: "inherit",
                    }}
                    onClick={() => setExpanded(expanded === finding.ref ? null : finding.ref)}
                  >
                    <div
                      style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}
                    >
                      <span className="ticket-draft-meta">
                        {VERDICT_LABEL[finding.verdict] ?? finding.verdict}
                      </span>
                      {finding.effort && (
                        <span className="ticket-draft-meta">effort {finding.effort}</span>
                      )}
                    </div>
                    <div className="ticket-draft-title">{finding.title}</div>
                    {finding.what_it_gives && (
                      <div className="ticket-draft-desc">{finding.what_it_gives}</div>
                    )}
                    {expanded === finding.ref && (
                      <div className="ticket-draft-desc" style={{ marginTop: 6 }}>
                        <div>Source: {finding.source_paths.join(", ") || "—"}</div>
                        {finding.fit && <div>Fit: {finding.fit}</div>}
                        {finding.risks && <div>Risks: {finding.risks}</div>}
                      </div>
                    )}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
