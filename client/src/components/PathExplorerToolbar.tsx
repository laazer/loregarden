interface PathExplorerToolbarProps {
  disabled?: boolean;
  loading?: boolean;
  parentPath?: string | null;
  onUp?: () => void;
  onRoot?: () => void;
  rootLabel?: string;
  onStart?: () => void;
  startLabel?: string;
  showSelect?: boolean;
  onSelect?: () => void;
  selectDisabled?: boolean;
}

export function PathExplorerToolbar({
  disabled = false,
  loading = false,
  parentPath,
  onUp,
  onRoot,
  rootLabel = "Loregarden root",
  onStart,
  startLabel = "Start folder",
  showSelect = false,
  onSelect,
  selectDisabled = false,
}: PathExplorerToolbarProps) {
  return (
    <div className="repo-path-explorer-toolbar">
      <button
        type="button"
        className="btn-secondary btn-compact"
        disabled={disabled || !parentPath || loading}
        onClick={onUp}
      >
        ↑ Up
      </button>
      {onRoot ? (
        <button
          type="button"
          className="btn-secondary btn-compact"
          disabled={disabled || loading}
          onClick={onRoot}
        >
          {rootLabel}
        </button>
      ) : null}
      {onStart ? (
        <button
          type="button"
          className="btn-secondary btn-compact"
          disabled={disabled || loading}
          onClick={onStart}
        >
          {startLabel}
        </button>
      ) : null}
      {showSelect ? (
        <button
          type="button"
          className="btn-primary btn-compact"
          disabled={disabled || selectDisabled || loading}
          onClick={onSelect}
        >
          Use this folder
        </button>
      ) : null}
    </div>
  );
}
