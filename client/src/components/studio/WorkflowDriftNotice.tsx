import { useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";

interface WorkflowDriftNoticeProps {
  slug: string;
}

/**
 * Warns that publishing this draft would change the workflow that actually runs.
 *
 * The draft and the published template are separate rows and nothing keeps them
 * in step: the `loregarden-tdd-v3` draft once sat at 9 stages against a live
 * 12-stage template, and pressing publish would have dropped `verify` and the
 * terminal stage. This is the thing that would have said so first.
 *
 * Renders nothing when the draft matches, or has never been published — a
 * banner that is always on is a banner nobody reads.
 */
export function WorkflowDriftNotice({ slug }: WorkflowDriftNoticeProps) {
  const { data: drift } = useQuery({
    queryKey: ["studio-workflow-drift", slug],
    queryFn: () => api.studioWorkflowDrift(slug),
    enabled: Boolean(slug),
  });

  if (!drift?.published || !drift.drifted) return null;

  const changed = Object.keys(drift.stages_changed);
  return (
    <div className="studio-drift-notice" role="status">
      <strong>This draft differs from {drift.published_template_slug}.</strong>{" "}
      Publishing overwrites the live workflow with what you see here.
      <ul>
        {drift.stages_removed.length > 0 && (
          <li>
            Would remove: <code>{drift.stages_removed.join(", ")}</code>
          </li>
        )}
        {drift.stages_added.length > 0 && (
          <li>
            Would add: <code>{drift.stages_added.join(", ")}</code>
          </li>
        )}
        {changed.length > 0 && (
          <li>
            Would change: <code>{changed.join(", ")}</code>
          </li>
        )}
        {drift.draft_transition_count !== drift.template_transition_count && (
          <li>
            Transitions: {drift.draft_transition_count} here vs{" "}
            {drift.template_transition_count} live
          </li>
        )}
        {drift.stranded.count > 0 && (
          <li>
            <strong>
              {drift.stranded.count} live ticket(s) are on a stage this would remove
            </strong>{" "}
            ({drift.stranded.stage_keys.join(", ")}) — publishing needs confirmation.
          </li>
        )}
      </ul>
    </div>
  );
}
