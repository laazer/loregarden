export function StudioDescribeBar({
  value,
  onChange,
  onGenerate,
  placeholder,
  generateLabel = "Generate",
  generatingLabel = "Generating…",
  pending,
  disabled,
  error,
}: {
  value: string;
  onChange: (value: string) => void;
  onGenerate: () => void;
  placeholder: string;
  generateLabel?: string;
  generatingLabel?: string;
  pending?: boolean;
  disabled?: boolean;
  error?: string | null;
}) {
  const canGenerate = value.trim().length > 0 && !pending && !disabled;

  return (
    <div className="studio-describe-bar">
      <input
        className="studio-describe-input"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled || pending}
        onKeyDown={(e) => {
          if (e.key === "Enter" && canGenerate) {
            e.preventDefault();
            onGenerate();
          }
        }}
      />
      <button
        type="button"
        className="ticket-studio-composer-action accent studio-describe-generate"
        disabled={!canGenerate}
        onClick={onGenerate}
      >
        {pending ? generatingLabel : generateLabel}
      </button>
      {error ? <p className="studio-describe-error">{error}</p> : null}
    </div>
  );
}
