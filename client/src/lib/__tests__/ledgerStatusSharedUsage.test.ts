import fs from "fs";
import path from "path";

// Requirement 1 / 8's non-behavioral acceptance criterion — "the shared
// constant is imported, not redefined" — isn't observable through rendered
// output, so it's checked the same way the ticket's own oxlint/`as any` gates
// are: by reading the changed source files directly.

function readSource(relativePath: string): string {
  return fs.readFileSync(path.resolve(__dirname, "../../..", relativePath), "utf8");
}

describe("ACTIVE_LEDGER_STATUSES is imported, not redefined, at each call site", () => {
  it("RunLedgerPanel.tsx imports the shared constant", () => {
    const source = readSource("src/components/RunLedgerPanel.tsx");
    expect(source).toMatch(/import\s*\{[^}]*ACTIVE_LEDGER_STATUSES[^}]*\}\s*from\s*["'].*ledgerStatus["']/);
  });

  it("RunLedgerPanel.tsx no longer defines its own literal active-status set", () => {
    const source = readSource("src/components/RunLedgerPanel.tsx");
    expect(source).not.toMatch(/new Set\(\s*\[\s*["']running["']/);
  });

  it("LogsPanel.tsx imports the shared constant for its lane derivation", () => {
    const source = readSource("src/components/LogsPanel.tsx");
    expect(source).toMatch(/import\s*\{[^}]*ACTIVE_LEDGER_STATUSES[^}]*\}\s*from\s*["'].*ledgerStatus["']/);
  });

  it("LogsPanel.tsx does not redefine its own literal active-status set", () => {
    const source = readSource("src/components/LogsPanel.tsx");
    expect(source).not.toMatch(/new Set\(\s*\[\s*["']running["']/);
  });

  it("RunLogModal.tsx keeps its own deliberately-divergent ACTIVE_STATUSES (no queued) — this ticket does not unify it", () => {
    const source = readSource("src/components/RunLogModal.tsx");
    expect(source).toMatch(/ACTIVE_STATUSES\s*=\s*new Set\(\s*\[\s*["']running["']\s*,\s*["']awaiting_permission["']\s*\]\s*\)/);
    expect(source).not.toMatch(/import\s*\{[^}]*ACTIVE_LEDGER_STATUSES[^}]*\}\s*from\s*["'].*ledgerStatus["']/);
  });
});

describe("RunningLaneTabs.tsx uses the plan-stage-corrected CSS classes, not the page-level tab bar", () => {
  it("does not use .tab-bar / .tab-btn as the strip's base class", () => {
    const source = readSource("src/components/logs/RunningLaneTabs.tsx");
    expect(source).not.toMatch(/className=["'`][^"'`]*\btab-bar\b(?!-scroll)/);
    expect(source).not.toMatch(/className=["'`][^"'`]*\btab-btn\b/);
  });

  it("uses .studio-subtabs / .studio-subtab per the plan-stage correction", () => {
    const source = readSource("src/components/logs/RunningLaneTabs.tsx");
    expect(source).toMatch(/studio-subtabs/);
    expect(source).toMatch(/studio-subtab/);
  });
});
