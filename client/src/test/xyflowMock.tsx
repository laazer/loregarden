const React = require("react");

module.exports = {
  ReactFlow: ({
    children,
    nodes = [],
    edges = [],
    onNodeClick,
  }: {
    children?: React.ReactNode;
    onNodeClick?: (event: unknown, node: { id: string }) => void;
    nodes?: Array<{
      id: string;
      position: { x: number; y: number };
      data?: { label?: React.ReactNode };
    }>;
    edges?: Array<{ id: string; label?: React.ReactNode }>;
  }) =>
    React.createElement(
      "div",
      { "data-testid": "react-flow" },
      ...nodes.map((node) =>
        React.createElement(
          "div",
          {
            key: node.id,
            "data-testid": "react-flow-node",
            "data-node-id": node.id,
            "data-x": node.position.x,
            "data-y": node.position.y,
            onClick: onNodeClick ? (event: unknown) => onNodeClick(event, node) : undefined,
          },
          node.data?.label,
        ),
      ),
      ...edges
        .filter((edge) => edge.label)
        .map((edge) =>
          React.createElement(
            "span",
            { key: edge.id, "data-testid": "react-flow-edge-label" },
            edge.label,
          ),
        ),
      children,
    ),
  ReactFlowProvider: ({ children }: { children?: React.ReactNode }) =>
    React.createElement(React.Fragment, null, children),
  useReactFlow: () => ({ fitView: () => Promise.resolve(true) }),
  Background: () => null,
  Controls: () => null,
  MarkerType: { ArrowClosed: "arrowclosed" },
  Position: { Left: "left", Right: "right" },
};
