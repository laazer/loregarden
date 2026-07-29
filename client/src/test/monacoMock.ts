const React = require("react");

function MockEditor(props) {
  return React.createElement("textarea", {
    "data-testid": "monaco-editor",
    value: props.value ?? "",
    onChange: (event) => props.onChange?.(event.target.value),
  });
}

function MockDiffEditor(props) {
  return React.createElement(
    "div",
    { "data-testid": "monaco-diff-editor" },
    React.createElement("pre", { "data-testid": "monaco-diff-original" }, props.original ?? ""),
    React.createElement("textarea", {
      "data-testid": "monaco-diff-modified",
      value: props.modified ?? "",
      onChange: (event) => {
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
