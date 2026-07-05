import { buildPermissionDetails } from "../utils/permissionDetails";

export function PermissionDetails({
  toolName,
  toolInputJson,
}: {
  toolName: string;
  toolInputJson: string;
}) {
  const details = buildPermissionDetails(toolName, toolInputJson);

  return (
    <div className="permission-details">
      <div className="permission-details-header">
        <span className="permission-details-kind">{details.toolLabel}</span>
        {details.subtitle && <span className="permission-details-tool">{details.subtitle}</span>}
      </div>

      {details.primary && (
        <div className="permission-details-primary">
          <div className="permission-details-label">{details.primary.label}</div>
          <pre className="permission-details-value">{details.primary.value}</pre>
        </div>
      )}

      {details.fields.map((field) => (
        <div key={`${field.label}:${field.value.slice(0, 40)}`} className="permission-details-field">
          <div className="permission-details-label">{field.label}</div>
          {field.multiline ? (
            <pre className={`permission-details-value${field.mono === false ? " prose" : ""}`}>{field.value}</pre>
          ) : (
            <div className={`permission-details-inline${field.mono === false ? " prose" : ""}`}>{field.value}</div>
          )}
        </div>
      ))}

      {!details.primary && details.fields.length === 0 && (
        <div className="permission-details-empty">No additional details captured for this request.</div>
      )}
    </div>
  );
}
