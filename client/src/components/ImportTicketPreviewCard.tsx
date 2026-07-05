import { useEffect, useState } from "react";

import type { TicketImportItem, TicketSummary } from "../api/client";
import { isWorkflowWorkItem } from "../api/client";
import {
  applyMilestoneToTicket,
  applyParentToTicket,
  buildImportFeatureOptions,
  buildImportParentOptions,
  importParentTypes,
  importTicketHasParent,
  importTicketNeedsParent,
  parseAcceptanceCriteriaText,
  formatAcceptanceCriteriaText,
  priorityLabel,
  type ImportMilestoneOption,
} from "../lib/importTicketPreview";
import { workItemTypeLabel } from "../lib/workItemHierarchy";
import { ImportQuickCreate } from "./ImportQuickCreate";

interface ImportTicketPreviewCardProps {
  ticket: TicketImportItem;
  index: number;
  total: number;
  milestoneOptions: ImportMilestoneOption[];
  existingTickets: TicketSummary[];
  batchTickets: TicketImportItem[];
  onChange: (ticket: TicketImportItem) => void;
  onQuickCreateMilestone: (title: string, ticketIndex: number) => void;
  onQuickCreateCapability: (title: string, featureKey: string, ticketIndex: number) => void;
  disabled?: boolean;
}

function PreviewField({
  label,
  value,
  mono = false,
  empty = "—",
}: {
  label: string;
  value: string;
  mono?: boolean;
  empty?: string;
}) {
  const display = value.trim() || empty;
  return (
    <div className="import-preview-field">
      <div className="import-preview-field-label">{label}</div>
      <div
        className={`import-preview-field-value${mono ? " import-preview-field-mono" : ""}${
          !value.trim() ? " import-preview-field-empty" : ""
        }`.trim()}
      >
        {display}
      </div>
    </div>
  );
}

function optionSourceLabel(source: ImportMilestoneOption["source"]): string {
  if (source === "quick") return " (new)";
  if (source === "import") return " (import)";
  return "";
}

function selectedMilestoneId(
  ticket: TicketImportItem,
  options: ImportMilestoneOption[],
): string {
  if (ticket.parent_ticket_id) {
    const match = options.find((option) => option.id === ticket.parent_ticket_id);
    if (match) return match.external_id;
  }
  if (ticket.parent_external_id) {
    return ticket.parent_external_id;
  }
  if (ticket.milestone) {
    const match = options.find(
      (option) => option.milestone === ticket.milestone || option.external_id === ticket.milestone,
    );
    if (match) return match.external_id;
  }
  return "";
}

function selectedParentKey(ticket: TicketImportItem): string {
  if (ticket.parent_ticket_id) return `id:${ticket.parent_ticket_id}`;
  if (ticket.parent_external_id) return `ext:${ticket.parent_external_id}`;
  return "";
}

export function ImportTicketPreviewCard({
  ticket,
  index,
  total,
  milestoneOptions,
  existingTickets,
  batchTickets,
  onChange,
  onQuickCreateMilestone,
  onQuickCreateCapability,
  disabled = false,
}: ImportTicketPreviewCardProps) {
  const parentOptions = buildImportParentOptions(ticket, existingTickets, batchTickets);
  const featureOptions = buildImportFeatureOptions(existingTickets, batchTickets);
  const needsParent = importTicketNeedsParent(ticket.work_item_type);
  const missingParent = needsParent && !importTicketHasParent(ticket);
  const allowedParentTypes = importParentTypes(ticket.work_item_type);
  const canQuickCreateCapability = allowedParentTypes.includes("capability");
  const milestoneValue = selectedMilestoneId(ticket, milestoneOptions);
  const parentValue = selectedParentKey(ticket);
  const missingTitle = !ticket.title.trim();
  const [acceptanceCriteriaText, setAcceptanceCriteriaText] = useState(() =>
    formatAcceptanceCriteriaText(ticket.acceptance_criteria),
  );

  useEffect(() => {
    setAcceptanceCriteriaText(formatAcceptanceCriteriaText(ticket.acceptance_criteria));
  }, [index, ticket.source_label]);

  const handleMilestoneChange = (externalId: string) => {
    if (!externalId) {
      onChange({ ...ticket, milestone: "", parent_ticket_id: null, parent_external_id: "" });
      return;
    }
    const milestone = milestoneOptions.find((option) => option.external_id === externalId);
    if (!milestone) return;
    onChange(applyMilestoneToTicket(ticket, milestone));
  };

  const handleParentChange = (key: string) => {
    if (!key) {
      onChange({ ...ticket, parent_ticket_id: null, parent_external_id: "" });
      return;
    }
    const parent = parentOptions.find(
      (option) => (option.id ? `id:${option.id}` : `ext:${option.external_id}`) === key,
    );
    if (!parent) return;
    onChange(applyParentToTicket(ticket, parent));
  };

  return (
    <article
      className={`import-preview-card${missingParent || missingTitle ? " import-preview-card-warning" : ""}`.trim()}
    >
      {total > 1 && (
        <div className="import-preview-card-header">
          <span className="import-preview-card-index">
            {index + 1} / {total}
          </span>
          <span className="import-preview-card-source">{ticket.source_label || ticket.title}</span>
        </div>
      )}

      <div className="import-preview-grid">
        <div className="import-preview-field import-preview-field-span">
          <div className="import-preview-field-label">Title</div>
          <input
            className="btn-secondary filter-select"
            style={{ width: "100%", fontSize: 12 }}
            value={ticket.title}
            disabled={disabled}
            placeholder="Work item title"
            onChange={(event) => onChange({ ...ticket, title: event.target.value })}
          />
          {missingTitle && (
            <p className="modal-hint" style={{ margin: "4px 0 0", color: "var(--amb)" }}>
              Required before import.
            </p>
          )}
        </div>

        <PreviewField label="Type" value={workItemTypeLabel(ticket.work_item_type)} />
        <PreviewField
          label="External ID"
          value={ticket.external_id || ""}
          mono
          empty="Auto-generated on import"
        />
        <PreviewField label="Priority" value={priorityLabel(ticket.priority)} />
        <PreviewField label="Source file" value={ticket.source_label || ""} mono />
        <PreviewField label="Format" value={ticket.source_format || ""} mono />

        <div className="import-preview-field import-preview-field-span">
          <div className="import-preview-field-label">Milestone</div>
          <div className="import-preview-control-row">
            <select
              className="btn-secondary filter-select"
              style={{ flex: 1, fontSize: 12 }}
              value={milestoneValue}
              disabled={disabled}
              onChange={(event) => handleMilestoneChange(event.target.value)}
            >
              <option value="">No milestone assigned</option>
              {milestoneOptions.map((option) => (
                <option key={`${option.source}-${option.external_id}`} value={option.external_id}>
                  {option.external_id} · {option.title}
                  {optionSourceLabel(option.source)}
                </option>
              ))}
            </select>
            <ImportQuickCreate
              label="+ Milestone"
              placeholder="New milestone title"
              actionLabel="Add"
              disabled={disabled}
              onSubmit={(title) => onQuickCreateMilestone(title, index)}
            />
          </div>
          <p className="modal-hint" style={{ margin: "4px 0 0" }}>
            Groups the ticket under a milestone. Features and bugs also use this as their parent when
            no other parent is set.
          </p>
        </div>

        {needsParent && (
          <div className="import-preview-field import-preview-field-span">
            <div className="import-preview-field-label">Parent work item</div>
            <div className="import-preview-control-row">
              <select
                className="btn-secondary filter-select"
                style={{ flex: 1, fontSize: 12 }}
                value={parentValue}
                disabled={disabled}
                onChange={(event) => handleParentChange(event.target.value)}
              >
                <option value="">
                  {parentOptions.length === 0 && allowedParentTypes.length > 0
                    ? `No ${allowedParentTypes.map((type) => workItemTypeLabel(type)).join(" or ")} available`
                    : "Select parent…"}
                </option>
                {parentOptions.map((option) => (
                  <option
                    key={`${option.source}-${option.id ?? option.external_id}`}
                    value={option.id ? `id:${option.id}` : `ext:${option.external_id}`}
                  >
                    {option.label}
                  </option>
                ))}
              </select>
              {canQuickCreateCapability && (
                <CapabilityQuickCreate
                  disabled={disabled}
                  featureOptions={featureOptions}
                  onSubmit={(title, featureKey) =>
                    onQuickCreateCapability(title, featureKey, index)
                  }
                />
              )}
            </div>
            {missingParent && (
              <p className="modal-hint" style={{ margin: "4px 0 0", color: "var(--amb)" }}>
                Required before import.
              </p>
            )}
          </div>
        )}

        <div className="import-preview-field import-preview-field-span">
          <div className="import-preview-field-label">Description</div>
          <div className="import-preview-field-value import-preview-field-block">
            {ticket.description?.trim() || "—"}
          </div>
        </div>

        {isWorkflowWorkItem(ticket.work_item_type) && (
          <div className="import-preview-field import-preview-field-span">
            <div className="import-preview-field-label">Acceptance criteria</div>
            <textarea
              className="btn-secondary filter-select import-preview-textarea"
              value={acceptanceCriteriaText}
              disabled={disabled}
              placeholder="- Criterion one&#10;- Criterion two"
              onChange={(event) => {
                const text = event.target.value;
                setAcceptanceCriteriaText(text);
                onChange({
                  ...ticket,
                  acceptance_criteria: parseAcceptanceCriteriaText(text),
                });
              }}
            />
            <p className="modal-hint" style={{ margin: "4px 0 0" }}>
              One criterion per line.
            </p>
          </div>
        )}
      </div>
    </article>
  );
}

function CapabilityQuickCreate({
  disabled,
  featureOptions,
  onSubmit,
}: {
  disabled?: boolean;
  featureOptions: ReturnType<typeof buildImportFeatureOptions>;
  onSubmit: (title: string, featureKey: string) => void;
}) {
  const [featureKey, setFeatureKey] = useState("");

  return (
    <ImportQuickCreate
      label="+ Capability"
      placeholder="New capability title"
      actionLabel="Add"
      disabled={disabled}
      extraRequired={!featureKey}
      onSubmit={(title) => onSubmit(title, featureKey)}
      extra={
        <div style={{ marginTop: 6 }}>
          <div className="import-preview-field-label">Under feature</div>
          <select
            className="btn-secondary filter-select"
            style={{ width: "100%", fontSize: 12 }}
            value={featureKey}
            disabled={disabled}
            onChange={(event) => setFeatureKey(event.target.value)}
          >
            <option value="">
              {featureOptions.length === 0
                ? "No features available — create or import a feature first"
                : "Select feature…"}
            </option>
            {featureOptions.map((option) => (
              <option
                key={`cap-feature-${option.id ?? option.external_id}`}
                value={option.id ? `id:${option.id}` : `ext:${option.external_id}`}
              >
                {option.label}
              </option>
            ))}
          </select>
        </div>
      }
    />
  );
}
