/**
 * 555 — "the New view modal's name field is unstyled against the dark surface."
 *
 * The root cause was not a wrong value; it was a *missing rule*. The markup
 * asked for `class="input"` and `class="field-label"`, neither class was
 * defined anywhere in the client, and because there is no element-level
 * `input { }` rule to fall back on either, the browser default painted a white
 * box on a #0b0f16 dialog.
 *
 * That is why the assertion below is about the stylesheet rather than about a
 * computed colour. **jsdom does not load the app's CSS** — every element in it
 * reports transparent, at 0px, in every test in this repo — so an assertion
 * like `expect(input).toHaveStyle({background: "#18202e"})` could only pass by
 * asserting an inline style, which is precisely the per-dialog bespoke styling
 * this ticket forbids. The honest, and stronger, check is: *does every class
 * this dialog names resolve to a rule that actually exists?* That test fails on
 * the shipped bug and passes on the fix, which is the whole job.
 *
 * It is deliberately generic over the dialog rather than pinned to the name
 * field: the ticket asked for the rest of the dialog to be audited too, and a
 * check that only knew about `.input` would not have noticed the next
 * `class="whatever"` that matches nothing.
 */

import fs from "fs";
import path from "path";

const SRC = path.resolve(__dirname, "../..");

/** Every class selector defined anywhere in the client's stylesheets. */
function definedClassNames(): Set<string> {
  const defined = new Set<string>();
  const walk = (dir: string) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name !== "node_modules") walk(full);
      } else if (entry.name.endsWith(".css")) {
        const source = fs.readFileSync(full, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
        for (const [, name] of source.matchAll(/\.(-?[_a-zA-Z][\w-]*)/g)) defined.add(name);
      }
    }
  };
  walk(SRC);
  return defined;
}

/** The static `className="..."` literals in a component, template parts included. */
function classNamesUsedIn(file: string): string[] {
  const source = fs.readFileSync(path.resolve(SRC, file), "utf8");
  const used: string[] = [];
  for (const [, value] of source.matchAll(/className=\{?["'`]([^"'`]*)["'`]/g)) {
    for (const name of value.split(/\s+/)) {
      // Template holes leave fragments like `${` — only whole literal names.
      if (name !== "" && /^[_a-zA-Z][\w-]*$/.test(name)) used.push(name);
    }
  }
  return used;
}

describe("555 — the New view dialog names no class that does not exist", () => {
  it("resolves every class the dialog uses to a real rule", () => {
    const defined = definedClassNames();
    const used = classNamesUsedIn("components/NewViewModal.tsx");

    // A guard on the guard: if the extraction ever stops finding classes, the
    // loop below would pass vacuously and vouch for nothing.
    expect(used.length).toBeGreaterThan(5);

    for (const name of used) {
      expect({ name, defined: defined.has(name) }).toEqual({ name, defined: true });
    }
  });

  it("defines the shared field treatment the dialog reaches for", () => {
    // Named explicitly as well, because the sweep above would go quiet if the
    // dialog were ever "fixed" by dropping the class instead of defining it.
    const defined = definedClassNames();
    expect({
      input: defined.has("input"),
      label: defined.has("field-label"),
    }).toEqual({ input: true, label: true });
  });

  it("styles the shared field from the app's tokens, not from literal colours", () => {
    // AC1: "using shared tokens rather than values invented for this dialog."
    const css = fs.readFileSync(path.resolve(SRC, "index.css"), "utf8");
    const rule = css.match(/\.input,\n\.studio-input,\n\.studio-textarea,\n\.studio-select\s*\{([^}]*)\}/);
    expect(rule).not.toBeNull();

    const body = (rule as RegExpMatchArray)[1];
    // Every colour in the shared rule is a var(), so the treatment moves with
    // the palette instead of pinning a hex this dialog chose for itself.
    expect(body).toMatch(/background:\s*var\(--bg2\)/);
    expect(body).toMatch(/color:\s*var\(--tx\)/);
    expect(body).toMatch(/border:\s*1px solid var\(--bd\)/);
    expect(body).not.toMatch(/#[0-9a-fA-F]{3,8}/);
  });

  it("shares one declaration with the studio field rather than adding a seventh copy", () => {
    // The ticket's real instruction: do not invent a new input style for this
    // one dialog. `.input` and `.studio-input` selecting the same rule is the
    // observable form of that.
    const css = fs.readFileSync(path.resolve(SRC, "index.css"), "utf8");
    expect(css).toMatch(/\.input,\n\.studio-input,\n\.studio-textarea,\n\.studio-select\s*\{/);
  });
});
