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

const API_DIR = path.resolve(__dirname, "../../../api");

function parseFile(file: string): TSESTree.Program {
  return parse(fs.readFileSync(file, "utf8"), { loc: false });
}

/** The object literal a top-level `const <name> = { … }` (exported or not) initialises. */
function objectLiteral(ast: TSESTree.Program, name: string): TSESTree.ObjectExpression | undefined {
  let found: TSESTree.ObjectExpression | undefined;
  visit(ast, (node) => {
    if (node.type !== "VariableDeclarator" || node.id.type !== "Identifier" || node.id.name !== name) return;
    if (node.init?.type === "ObjectExpression") found = node.init;
  });
  return found;
}

/** Local name → [module file, exported name], for each relative named import. */
function relativeImports(ast: TSESTree.Program, dir: string): Map<string, [string, string]> {
  const imports = new Map<string, [string, string]>();
  for (const stmt of ast.body) {
    if (stmt.type !== "ImportDeclaration" || stmt.importKind === "type") continue;
    const source = stmt.source.value;
    if (!source.startsWith(".")) continue;
    for (const spec of stmt.specifiers) {
      if (spec.type !== "ImportSpecifier" || spec.imported.type !== "Identifier") continue;
      imports.set(spec.local.name, [path.resolve(dir, `${source}.ts`), spec.imported.name]);
    }
  }
  return imports;
}

/** The verb `request(…, { method })` names inside a method body (GET when unnamed). */
function verbOf(value: Node): string {
  let verb = "GET";
  visit(value, (inner) => {
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
  return verb;
}

/**
 * Each `api` method and its HTTP verb, including those spread in from other
 * modules (`...ticketEdgeApi`). A spread this cannot follow throws rather than
 * skipping, so a new spread module cannot hide its writes from the checks below.
 */
function apiMethods(): { methods: Map<string, string>; spreads: Map<string, number> } {
  const ast = parseFile(path.join(API_DIR, "client.ts"));
  const imports = relativeImports(ast, API_DIR);
  const methods = new Map<string, string>();
  const spreads = new Map<string, number>();

  const collect = (obj: TSESTree.ObjectExpression): void => {
    for (const prop of obj.properties) {
      if (prop.type === "SpreadElement") {
        if (prop.argument.type !== "Identifier") throw new Error("api spreads a non-identifier");
        const name = prop.argument.name;
        const target = imports.get(name);
        if (!target) throw new Error(`api spreads ${name}, which is not a relative import of client.ts`);
        const [file, exported] = target;
        const spread = objectLiteral(parseFile(file), exported);
        if (!spread) throw new Error(`${path.basename(file)} has no object literal named ${exported}`);
        const before = methods.size;
        collect(spread);
        spreads.set(name, methods.size - before);
        continue;
      }
      if (prop.key.type !== "Identifier") continue;
      methods.set(prop.key.name, verbOf(prop.value));
    }
  };

  const api = objectLiteral(ast, "api");
  if (!api) throw new Error("client.ts has no `api` object literal");
  collect(api);
  return { methods, spreads };
}

const { methods, spreads } = apiMethods();
const writes = [...methods].filter(([, verb]) => verb !== "GET").map(([name]) => name);
const classified = Object.keys(API_WRITE_COVERAGE);

it("reads a real api surface", () => {
  // Without this, a parser that found nothing would make every check below vacuous.
  expect(methods.size).toBeGreaterThan(100);
  expect(writes.length).toBeGreaterThan(50);
});

it("follows every module spread into api", () => {
  // A spread read as empty would hide that module's writes as surely as skipping it.
  expect(spreads.size).toBeGreaterThan(0);
  const empty = [...spreads].filter(([, count]) => count === 0).map(([name]) => name);
  expect(empty).toEqual([]);
  // One known write from a spread module, so "followed" means "read its verbs" too.
  expect(methods.get("addDependency")).toBe("POST");
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
