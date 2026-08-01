import type { WorkspaceSummary } from "../../api/client";

/**
 * The studio rail's workspace switcher — one card whose whole surface is the
 * <select>. Shared so every studio section picks a workspace the same way.
 */
export function StudioWorkspacePicker({
  workspaces,
  value,
  onChange,
  label = "Workspace",
}: {
  workspaces: WorkspaceSummary[];
  value: string;
  onChange: (slug: string) => void;
  label?: string;
}) {
  const selected = workspaces.find((ws) => ws.slug === value);
  const initial = (selected?.name ?? value).charAt(0).toUpperCase();

  return (
    <div className="studio-workspace-card">
      <span className="studio-workspace-mark">{initial}</span>
      <select
        className="studio-workspace-select"
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {workspaces.map((ws) => (
          <option key={ws.slug} value={ws.slug}>
            {ws.name}
          </option>
        ))}
      </select>
      <svg
        className="studio-workspace-chevron"
        width="13"
        height="13"
        viewBox="0 0 24 24"
        fill="none"
        stroke="var(--txl)"
        strokeWidth="2"
        aria-hidden
      >
        <path d="m6 9 6 6 6-6" />
      </svg>
    </div>
  );
}
