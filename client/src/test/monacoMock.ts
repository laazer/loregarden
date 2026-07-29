const React = require("react");

type ChangeEvent = { target: { value: string } };

type MockEditorProps = {
  value?: string;
  onChange?: (value: string) => void;
};

type MockDiffEditorProps = {
  original?: string;
  modified?: string;
  onMount?: (editor: {
    getModifiedEditor: () => {
      getValue: () => string;
      onDidChangeModelContent: (listener: () => void) => void;
    };
  }) => void;
};

function MockEditor(props: MockEditorProps) {
  return React.createElement("textarea", {
    "data-testid": "monaco-editor",
    value: props.value ?? "",
    onChange: (event: ChangeEvent) => props.onChange?.(event.target.value),
  });
}

function MockDiffEditor(props: MockDiffEditorProps) {
  return React.createElement(
    "div",
    { "data-testid": "monaco-diff-editor" },
    React.createElement("pre", { "data-testid": "monaco-diff-original" }, props.original ?? ""),
    React.createElement("textarea", {
      "data-testid": "monaco-diff-modified",
      value: props.modified ?? "",
      onChange: (event: ChangeEvent) => {
        const next = event.target.value;
        // Mimic DiffEditor onMount wiring used by EditPrimitive.
        props.onMount?.({
          getModifiedEditor: () => ({
            getValue: () => next,
            onDidChangeModelContent: () => {},
          }),
        });
      },
    }),
  );
}

module.exports = {
  __esModule: true,
  default: MockEditor,
  DiffEditor: MockDiffEditor,
};
