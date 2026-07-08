/**
 * Export munder-difflin procedural office cast sprites into Hive officeplace agents.
 *
 *   MUNDER_DIFFLIN_ROOT=/path/to/munder-difflin npx tsx scripts/hive/export_officeplace_cast.ts
 *
 * Characters are custom-drawn in munder-difflin portraitArt.ts (MIT source), not LimeZu sheets.
 */
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync, unlinkSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const __dir = dirname(fileURLToPath(import.meta.url));
const LG_ROOT = join(__dir, "../..");
const MD = process.env.MUNDER_DIFFLIN_ROOT;

if (!MD) {
  console.error("Set MUNDER_DIFFLIN_ROOT to a munder-difflin checkout");
  process.exit(1);
}

const OUT_AGENTS = join(LG_ROOT, "client/src/assets/hive/officeplace/agents");
mkdirSync(OUT_AGENTS, { recursive: true });

async function main(): Promise<void> {
  const portrait = await import(
    pathToFileURL(join(MD!, "src/renderer/src/scene/office/portraitArt.ts")).href
  );

  const CAST: Record<string, string> = {
    worker: "pam",
    planner: "michael",
    implementer: "jim",
    tester: "dwight",
    reviewer: "toby",
  };

  const { SCENE_W, SCENE_H, sceneFrameBufs } = portrait as {
    SCENE_W: number;
    SCENE_H: number;
    sceneFrameBufs: (name: string) => { front: Uint8ClampedArray[] };
  };

  const OUT_SIZE = 96;

  for (const [cast, name] of Object.entries(CAST)) {
    const { front } = sceneFrameBufs(name);
    const buf = front[0];
    const raw = join(OUT_AGENTS, `${cast}.raw`);
    writeFileSync(raw, Buffer.from(buf));
    const png = join(OUT_AGENTS, `${cast}.png`);
    execFileSync(
      "magick",
      [
        "-size",
        `${SCENE_W}x${SCENE_H}`,
        "-depth",
        "8",
        `rgba:${raw}`,
        "-alpha",
        "on",
        "-filter",
        "point",
        "-resize",
        `${OUT_SIZE}x${OUT_SIZE}`,
        "-background",
        "none",
        "-gravity",
        "center",
        "-extent",
        `${OUT_SIZE}x${OUT_SIZE}`,
        png,
      ],
      { stdio: "inherit" },
    );
    unlinkSync(raw);
    console.log(`  agents/${cast}.png (${name})`);
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
