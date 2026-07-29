const React = require("react");

module.exports = {
  ReactFlow: ({
    children,
    nodes = [],
    edges = [],
  }: {
    children?: React.ReactNode;
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
  Background: () => null,
  Controls: () => null,
  MarkerType: { ArrowClosed: "arrowclosed" },
  Position: { Left: "left", Right: "right" },
};
