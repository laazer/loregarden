/**
 * AST helpers shared by the frontend gates that read TS/TSX: the theme gate
 * (colours in style objects) and the type gate (font sizes in style objects).
 */

const { parse } = require("./gate_client_modules.cjs");

function parseFile(content) {
  try {
    return parse(content, { jsx: true, loc: true, range: false, comment: false });
  } catch {
    // silent-ok: tsc and oxlint both report syntax errors, and far better
    return null;
  }
}

function walk(node, visit) {
  if (!node || typeof node.type !== "string") return;
  visit(node);
  for (const key of Object.keys(node)) {
    if (key === "parent" || key === "loc") continue;
    const value = node[key];
    if (Array.isArray(value)) {
      for (const child of value) walk(child, visit);
    } else if (value && typeof value.type === "string") {
      walk(value, visit);
    }
  }
}

function attributeName(attr) {
  return attr.type === "JSXAttribute" && attr.name && attr.name.type === "JSXIdentifier"
    ? attr.name.name
    : "";
}

/** The literal text of a string or template literal; null for anything computed. */
function literalText(node) {
  if (!node) return null;
  if (node.type === "Literal" && typeof node.value === "string") return node.value;
  if (node.type === "TemplateLiteral") return node.quasis.map((q) => q.value.cooked).join(" ");
  if (node.type === "JSXExpressionContainer") return literalText(node.expression);
  return null;
}

function propertyKey(prop) {
  if (prop.key.type === "Identifier") return prop.key.name;
  if (prop.key.type === "Literal") return String(prop.key.value);
  return "";
}

module.exports = { parseFile, walk, attributeName, literalText, propertyKey };
