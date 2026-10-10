import { useId, useState } from "react";

import type { LocalInstanceKind, TemplateParamSpec, TemplateSpec } from "../../api/localInstancesTypes";

/**
 * A stored instance template, as a form.
 *
 * The command is one argument per line rather than a shell string: it runs
 * as argv, never through a shell, and a single line that "looks right" with
 * quoting would not. The server validates the rest (cwd staying inside the
 * worktree, port range, reserved parameter names) and its message is shown
 * as-is, so the form does not duplicate those rules.
 */
interface TemplateEditorProps {
  /** Editing an existing template; its name is fixed. Absent for a new one. */
  initial?: TemplateSpec;
  saving: boolean;
  error: string | null;
  onSave: (spec: TemplateSpec) => void;
  onCancel: () => void;
}

const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

const lines = (text: string): string[] =>
  text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

function parseEnv(text: string): { env: Record<string, string>; bad: string[] } {
  const env: Record<string, string> = {};
  const bad: string[] = [];
  for (const line of lines(text)) {
    const at = line.indexOf("=");
    if (at <= 0) bad.push(line);
    else env[line.slice(0, at).trim()] = line.slice(at + 1).trim();
  }
  return { env, bad };
}

interface ParamRow {
  key: string;
  label: string;
  defaultValue: string;
  choices: string;
  required: boolean;
}

const toRow = (param: TemplateParamSpec): ParamRow => ({
  key: param.key,
  label: param.label,
  defaultValue: param.default ?? "",
  choices: (param.choices ?? []).join(", "),
  required: param.required ?? false,
});

const fromRow = (row: ParamRow): TemplateParamSpec => {
  const choices = row.choices
    .split(",")
    .map((choice) => choice.trim())
    .filter(Boolean);
  return {
    key: row.key.trim(),
    label: row.label.trim() || row.key.trim(),
    required: row.required,
    default: row.defaultValue.trim() || null,
    choices: choices.length ? choices : null,
  };
};

function Field({ label, hint, children }: { label: string; hint?: string; children: (id: string) => React.ReactNode }) {
  const id = useId();
  return (
    <div className="instances-field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      {children(id)}
      {hint && <p className="modal-hint">{hint}</p>}
    </div>
  );
}

export function TemplateEditor({ initial, saving, error, onSave, onCancel }: TemplateEditorProps) {
  const [name, setName] = useState(initial?.name ?? "");
  const [kind, setKind] = useState<LocalInstanceKind>(initial?.kind ?? "server");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [cwd, setCwd] = useState(initial?.cwd ?? ".");
  const [command, setCommand] = useState((initial?.command ?? []).join("\n"));
  const [envText, setEnvText] = useState(
    Object.entries(initial?.env ?? {})
      .map(([key, value]) => `${key}=${value}`)
      .join("\n"),
  );
  const [healthPath, setHealthPath] = useState(initial ? (initial.health_path ?? "") : "/");
  const [readyWithin, setReadyWithin] = useState(String(initial?.ready_timeout_seconds ?? 120));
  const [portLow, setPortLow] = useState(String(initial?.port_range[0] ?? 8100));
  const [portHigh, setPortHigh] = useState(String(initial?.port_range[1] ?? 8999));
  const [targetEnv, setTargetEnv] = useState(initial?.target?.env ?? "");
  const [params, setParams] = useState<ParamRow[]>((initial?.params ?? []).map(toRow));

  const { env, bad: badEnv } = parseEnv(envText);
  const argv = lines(command);
  const problems = [
    !SLUG.test(name) && "Name must be lowercase letters, digits and dashes.",
    argv.length === 0 && "Command needs at least one argument.",
    badEnv.length > 0 && `Environment lines need KEY=value: ${badEnv.join(", ")}`,
    params.some((row) => !row.key.trim()) && "Every parameter needs a key.",
  ].filter((problem): problem is string => Boolean(problem));

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (saving || problems.length) return;
    onSave({
      name,
      kind,
      description: description.trim(),
      command: argv,
      cwd: cwd.trim() || ".",
      env,
      health_path: healthPath.trim() || null,
      ready_timeout_seconds: Number(readyWithin) || 120,
      port_range: [Number(portLow), Number(portHigh)],
      params: params.map(fromRow),
      target: targetEnv.trim() ? { env: targetEnv.trim(), allow_main: true } : null,
    });
  };

  const setParam = (index: number, patch: Partial<ParamRow>) =>
    setParams((rows) => rows.map((row, at) => (at === index ? { ...row, ...patch } : row)));

  return (
    <form className="instances-editor" onSubmit={submit} aria-label={initial ? `Edit ${initial.name}` : "New template"}>
      <div className="instances-editor-grid">
        <Field label="Name" hint={initial ? "Names cannot change; create a new template instead." : "Lowercase, dashes."}>
          {(id) => (
            <input id={id} className="input" value={name} disabled={Boolean(initial)} onChange={(e) => setName(e.target.value)} />
          )}
        </Field>
        <Field label="Kind">
          {(id) => (
            <select id={id} className="input" value={kind} onChange={(e) => setKind(e.target.value as LocalInstanceKind)}>
              <option value="server">server</option>
              <option value="client">client</option>
            </select>
          )}
        </Field>
        <Field label="Description">
          {(id) => <input id={id} className="input" value={description} onChange={(e) => setDescription(e.target.value)} />}
        </Field>
        <Field label="Working directory" hint="Relative to the worktree picked at launch.">
          {(id) => <input id={id} className="input" value={cwd} onChange={(e) => setCwd(e.target.value)} />}
        </Field>
      </div>
      <Field label="Command" hint="One argument per line; runs without a shell. {port}, {host}, {url} and {param:key} are filled in.">
        {(id) => (
          <textarea id={id} className="input instances-editor-code" rows={4} value={command} onChange={(e) => setCommand(e.target.value)} />
        )}
      </Field>
      <Field label="Environment" hint="KEY=value, one per line.">
        {(id) => (
          <textarea id={id} className="input instances-editor-code" rows={3} value={envText} onChange={(e) => setEnvText(e.target.value)} />
        )}
      </Field>
      <div className="instances-editor-grid">
        <Field label="Readiness path" hint="Blank: ready once the port accepts connections.">
          {(id) => <input id={id} className="input" value={healthPath} onChange={(e) => setHealthPath(e.target.value)} />}
        </Field>
        <Field label="Ready within (s)">
          {(id) => <input id={id} className="input" type="number" min={1} value={readyWithin} onChange={(e) => setReadyWithin(e.target.value)} />}
        </Field>
        <Field label="Ports from">
          {(id) => <input id={id} className="input" type="number" value={portLow} onChange={(e) => setPortLow(e.target.value)} />}
        </Field>
        <Field label="Ports to">
          {(id) => <input id={id} className="input" type="number" value={portHigh} onChange={(e) => setPortHigh(e.target.value)} />}
        </Field>
        <Field label="Server URL variable" hint="Set to pick a server at launch; its URL goes in this variable.">
          {(id) => <input id={id} className="input" value={targetEnv} placeholder="e.g. API_URL" onChange={(e) => setTargetEnv(e.target.value)} />}
        </Field>
      </div>

      <fieldset className="instances-editor-params">
        <legend className="field-label">Parameters</legend>
        {params.length === 0 ? (
          <p className="modal-hint">None. Every template already asks for a worktree.</p>
        ) : (
          params.map((row, index) => (
            <div className="instances-editor-param" key={index}>
              <input className="input" aria-label={`Parameter ${index + 1} key`} placeholder="key" value={row.key} onChange={(e) => setParam(index, { key: e.target.value })} />
              <input className="input" aria-label={`Parameter ${index + 1} label`} placeholder="label" value={row.label} onChange={(e) => setParam(index, { label: e.target.value })} />
              <input className="input" aria-label={`Parameter ${index + 1} default`} placeholder="default" value={row.defaultValue} onChange={(e) => setParam(index, { defaultValue: e.target.value })} />
              <input className="input" aria-label={`Parameter ${index + 1} choices`} placeholder="choices, comma separated" value={row.choices} onChange={(e) => setParam(index, { choices: e.target.value })} />
              <label className="instances-editor-check">
                <input type="checkbox" checked={row.required} onChange={(e) => setParam(index, { required: e.target.checked })} />
                required
              </label>
              <button type="button" className="btn-secondary" aria-label={`Remove parameter ${row.key || index + 1}`} onClick={() => setParams((rows) => rows.filter((_, at) => at !== index))}>
                Remove
              </button>
            </div>
          ))
        )}
        <button
          type="button"
          className="btn-secondary"
          onClick={() => setParams((rows) => [...rows, { key: "", label: "", defaultValue: "", choices: "", required: false }])}
        >
          Add parameter
        </button>
      </fieldset>

      {problems.length > 0 && (
        <ul className="instances-error" aria-live="polite">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
      {error && (
        <p className="instances-error" role="alert">
          {error}
        </p>
      )}
      <div className="instances-editor-actions">
        <button type="button" className="btn-secondary" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
        <button type="submit" className="btn-primary" disabled={saving || problems.length > 0} aria-busy={saving}>
          {saving ? "Saving…" : initial ? "Save changes" : "Create template"}
        </button>
      </div>
    </form>
  );
}
