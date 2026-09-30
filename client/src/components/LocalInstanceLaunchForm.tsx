import { useId, useMemo, useState } from "react";

import type {
  LocalInstanceLaunch,
  LocalInstanceTemplate,
  LocalInstanceTemplateParam,
} from "../api/localInstancesTypes";

interface LocalInstanceLaunchFormProps {
  templates: LocalInstanceTemplate[];
  launching: boolean;
  onLaunch: (body: LocalInstanceLaunch) => void;
}

const defaultsFor = (template: LocalInstanceTemplate | undefined): Record<string, string> =>
  Object.fromEntries((template?.params ?? []).map((p) => [p.key, p.default ?? ""]));

function ParamField({
  param,
  value,
  onChange,
}: {
  param: LocalInstanceTemplateParam;
  value: string;
  onChange: (value: string) => void;
}) {
  const id = useId();
  const hintId = `${id}-hint`;
  return (
    <div className="local-instances-field">
      <label className="field-label" htmlFor={id}>
        {param.label}
        {param.required ? " (required)" : ""}
      </label>
      {param.choices ? (
        <select
          id={id}
          className="input"
          value={value}
          aria-describedby={param.description ? hintId : undefined}
          onChange={(e) => onChange(e.target.value)}
        >
          {param.choices.map((choice) => (
            <option key={choice} value={choice}>
              {choice}
            </option>
          ))}
        </select>
      ) : (
        <input
          id={id}
          className="input"
          value={value}
          required={param.required}
          aria-describedby={param.description ? hintId : undefined}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
      {param.description && (
        <p id={hintId} className="modal-hint">
          {param.description}
        </p>
      )}
    </div>
  );
}

export function LocalInstanceLaunchForm({ templates, launching, onLaunch }: LocalInstanceLaunchFormProps) {
  const templateFieldId = useId();
  const nameFieldId = useId();
  const [templateName, setTemplateName] = useState(templates[0]?.name ?? "");
  const template = useMemo(
    () => templates.find((t) => t.name === templateName) ?? templates[0],
    [templates, templateName],
  );
  const [name, setName] = useState("");
  // Kept per template, so switching templates starts from that template's
  // defaults and switching back finds what was typed there.
  const [edits, setEdits] = useState<Record<string, Record<string, string>>>({});

  if (!template) {
    return <p className="modal-hint">The server offers no launch templates.</p>;
  }

  const params = edits[template.name] ?? defaultsFor(template);
  const setParam = (key: string, value: string) =>
    setEdits((prev) => ({ ...prev, [template.name]: { ...params, [key]: value } }));
  const missing = template.params.some((p) => p.required && !params[p.key]);
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (launching || missing) return;
    const filled = Object.fromEntries(Object.entries(params).filter(([, v]) => v !== ""));
    onLaunch({ template: template.name, name: name.trim() || undefined, params: filled });
  };

  return (
    <form className="local-instances-form" onSubmit={submit} aria-label="Launch an instance">
      <div className="local-instances-field">
        <label className="field-label" htmlFor={templateFieldId}>
          Launch
        </label>
        <select
          id={templateFieldId}
          className="input"
          value={template.name}
          onChange={(e) => setTemplateName(e.target.value)}
        >
          {templates.map((t) => (
            <option key={t.name} value={t.name}>
              {t.name} — {t.description}
            </option>
          ))}
        </select>
      </div>
      {template.params.map((param) => (
        <ParamField
          key={param.key}
          param={param}
          value={params[param.key] ?? ""}
          onChange={(value) => setParam(param.key, value)}
        />
      ))}
      <div className="local-instances-field">
        <label className="field-label" htmlFor={nameFieldId}>
          Name (optional)
        </label>
        <input
          id={nameFieldId}
          className="input"
          value={name}
          placeholder="Defaults to the template and branch"
          onChange={(e) => setName(e.target.value)}
        />
      </div>
      <button type="submit" className="btn-primary" disabled={launching || missing} aria-busy={launching}>
        {launching ? "Launching…" : "Launch"}
      </button>
    </form>
  );
}
