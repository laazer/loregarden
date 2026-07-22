import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { createOfficeplaceOpenWalkGrid } from "../layouts/walkGrid";
import {
  OFFICEPLACE_DESKS,
  OFFICEPLACE_ERRANDS,
  OFFICEPLACE_MAP,
  OFFICEPLACE_MDR_STAFF,
  OFFICEPLACE_RECEPTIONIST,
  OFFICEPLACE_STATIONS,
  OFFICEPLACE_WAITING,
  OFFICEPLACE_ZONES,
} from "../layouts/officeplaceLayout";

const OUT = process.env.CALIB_OUT ?? "/tmp/officeplace-calibration.svg";
const BG = resolve(__dirname, "../../../assets/hive/officeplace/floor-bg.png");

type Dot = { x: number; y: number; label: string; fill: string };

// Emits an SVG overlay identical to the in-app debug overlay, so we can eyeball
// the walk grid + hand-placed points against the real background outside the app.
// Dev tool, not an assertion: runs only when CALIB_OUT is set, skipped otherwise.
const maybe = process.env.CALIB_OUT ? test : test.skip;
maybe("render officeplace calibration overlay", () => {
  const grid = createOfficeplaceOpenWalkGrid();
  const { width, height } = OFFICEPLACE_MAP;
  const bg = readFileSync(BG).toString("base64");

  // Flood-fill from a bullpen desk; report any target the agents can't reach.
  const seed: { x: number; y: number } = { ...OFFICEPLACE_DESKS[0]! };
  const reachable = new Set<string>();
  const stack: Array<{ x: number; y: number }> = [seed];
  reachable.add(`${seed.x},${seed.y}`);
  while (stack.length) {
    const c = stack.pop()!;
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]] as const) {
      const nx = c.x + dx;
      const ny = c.y + dy;
      const k = `${nx},${ny}`;
      if (nx < 0 || ny < 0 || nx >= width || ny >= height) continue;
      if (reachable.has(k) || !grid.isWalkable(nx, ny)) continue;
      reachable.add(k);
      stack.push({ x: nx, y: ny });
    }
  }
  const targets: Array<{ x: number; y: number; label: string }> = [
    ...Object.entries(OFFICEPLACE_STATIONS).map(([label, p]) => ({ ...p, label })),
    ...OFFICEPLACE_DESKS.map((p, i) => ({ ...p, label: `desk${i}` })),
    ...OFFICEPLACE_ERRANDS.map((e) => ({ ...e.stand, label: e.id })),
    { ...OFFICEPLACE_WAITING, label: "waiting" },
    { x: OFFICEPLACE_RECEPTIONIST.x, y: OFFICEPLACE_RECEPTIONIST.y, label: "reception" },
  ];
  const stranded = targets.filter((t) => !reachable.has(`${t.x},${t.y}`));
  // eslint-disable-next-line no-console
  console.log(
    stranded.length
      ? `UNREACHABLE: ${stranded.map((t) => `${t.label}(${t.x},${t.y})`).join(", ")}`
      : "reachability OK: all targets reachable from bullpen",
  );

  const cells: string[] = [];
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const fill = grid.isWalkable(x, y) ? "#40d078" : "#e04040";
      const op = grid.isWalkable(x, y) ? 0.32 : 0.24;
      cells.push(`<rect x="${x}" y="${y}" width="1" height="1" fill="${fill}" fill-opacity="${op}"/>`);
    }
  }

  const dots: Dot[] = [
    ...Object.entries(OFFICEPLACE_STATIONS).map(([label, p]) => ({ ...p, label, fill: "#4b9bff" })),
    ...OFFICEPLACE_DESKS.map((p, i) => ({ ...p, label: `desk${i}`, fill: "#ff9d3b" })),
    ...OFFICEPLACE_ERRANDS.map((e) => ({ ...e.stand, label: e.id, fill: "#c77dff" })),
    { ...OFFICEPLACE_WAITING, label: "waiting", fill: "#ffd84b" },
    { x: OFFICEPLACE_RECEPTIONIST.x, y: OFFICEPLACE_RECEPTIONIST.y, label: "reception", fill: "#ff6fae" },
    ...OFFICEPLACE_MDR_STAFF.map((s) => ({ x: s.x, y: s.y, label: s.label, fill: "#3bd8c8" })),
    ...OFFICEPLACE_ZONES.map((z) => ({ x: z.x, y: z.y, label: z.label, fill: "#ffffff" })),
  ];

  const markers = dots
    .map(
      (d) =>
        `<circle cx="${d.x + 0.5}" cy="${d.y + 0.5}" r="0.5" fill="${d.fill}" stroke="#000" stroke-width="0.12"/>` +
        `<text x="${d.x + 1}" y="${d.y + 0.9}" font-size="1.2" fill="#fff" stroke="#000" stroke-width="0.15" paint-order="stroke">${d.label}</text>`,
    )
    .join("");

  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" width="${width * 16}" height="${height * 16}">` +
    `<image href="data:image/png;base64,${bg}" x="0" y="0" width="${width}" height="${height}" preserveAspectRatio="none"/>` +
    cells.join("") +
    markers +
    `</svg>`;

  writeFileSync(OUT, svg);
  expect(svg.length).toBeGreaterThan(0);

  // Ruler: bare image + numbered tile grid, for reading true room rectangles.
  const lines: string[] = [];
  for (let x = 0; x <= width; x++) {
    const major = x % 5 === 0;
    lines.push(
      `<line x1="${x}" y1="0" x2="${x}" y2="${height}" stroke="#00e5ff" stroke-width="${major ? 0.08 : 0.03}" stroke-opacity="${major ? 0.75 : 0.35}"/>`,
    );
    if (major) lines.push(`<text x="${x + 0.1}" y="1.2" font-size="1.1" fill="#00e5ff">${x}</text>`);
  }
  for (let y = 0; y <= height; y++) {
    const major = y % 5 === 0;
    lines.push(
      `<line x1="0" y1="${y}" x2="${width}" y2="${y}" stroke="#00e5ff" stroke-width="${major ? 0.08 : 0.03}" stroke-opacity="${major ? 0.75 : 0.35}"/>`,
    );
    if (major) lines.push(`<text x="0.1" y="${y - 0.15}" font-size="1.1" fill="#00e5ff">${y}</text>`);
  }
  const ruler =
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" width="${width * 20}" height="${height * 20}">` +
    `<image href="data:image/png;base64,${bg}" x="0" y="0" width="${width}" height="${height}" preserveAspectRatio="none"/>` +
    lines.join("") +
    `</svg>`;
  writeFileSync(OUT.replace(/\.svg$/, "-ruler.svg"), ruler);
});
