/**
 * Every write the UI can make has a recorded answer to "how does an agent do
 * this, or why may it not?" — read off the real `api` object, so a new
 * endpoint cannot ship unclassified.
 */

import fs from "fs";
import path from "path";
import { parse, type TSESTree } from "@typescript-eslint/typescript-estree";

import { API_WRITE_COVERAGE } from "../writeCoverage";

type Node = TSESTree.Node;

function visit(node: unknown, fn: (n: Node) => void): void {
  if (!node || typeof (node as Node).type !== "string") return;
  fn(node as Node);
  for (const [key, value] of Object.entries(node as object)) {
    if (key === "parent" || key === "loc" || key === "range") continue;
    if (Array.isArray(value)) value.forEach((child) => visit(child, fn));
    else if (value && typeof value === "object") visit(value, fn);
  }
}

/** Each `api` method and its HTTP verb, as `request(…, { method })` names it (GET when unnamed). */
function apiMethods(): Map<string, string> {
  const file = path.resolve(__dirname, "../../../api/client.ts");
  const ast = parse(fs.readFileSync(file, "utf8"), { loc: false });
  const methods = new Map<string, string>();
  visit(ast, (node) => {
    if (node.type !== "VariableDeclarator" || node.id.type !== "Identifier" || node.id.name !== "api") return;
    if (node.init?.type !== "ObjectExpression") return;
    for (const prop of node.init.properties) {
      if (prop.type !== "Property" || prop.key.type !== "Identifier") continue;
      let verb = "GET";
      visit(prop.value, (inner) => {
        if (
          inner.type === "Property" &&
          inner.key.type === "Identifier" &&
          inner.key.name === "method" &&
          inner.value.type === "Literal" &&
          typeof inner.value.value === "string"
        ) {
          verb = inner.value.value;
        }
      });
      methods.set(prop.key.name, verb);
    }
  });
  return methods;
}

const methods = apiMethods();
const writes = [...methods].filter(([, verb]) => verb !== "GET").map(([name]) => name);
const classified = Object.keys(API_WRITE_COVERAGE);

it("reads a real api surface", () => {
  // Without this, a parser that found nothing would make every check below vacuous.
  expect(methods.size).toBeGreaterThan(100);
  expect(writes.length).toBeGreaterThan(50);
});

it("classifies every write method", () => {
  const missing = writes.filter((name) => !classified.includes(name));
  expect(missing).toEqual([]);
});

it("classifies nothing that is not a write method", () => {
  const stray = classified.filter((name) => !writes.includes(name));
  expect(stray).toEqual([]);
});

it("gives every refusal a reason", () => {
  const unexplained = Object.entries(API_WRITE_COVERAGE)
    .filter(([, entry]) => "reason" in entry && entry.reason.trim().length < 12)
    .map(([name]) => name);
  expect(unexplained).toEqual([]);
});
