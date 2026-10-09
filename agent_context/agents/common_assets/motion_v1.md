# Motion v1

How a surface moves. It applies to anyone who decides, builds or checks motion in an app UI:
`ui-design` before the work, the frontend implementer during it, `visual_qa` after it.

## The one rule

**Motion shows a change of state; it never decorates.** Something appeared, left, moved,
opened, closed, succeeded or failed. If a proposed animation cannot name the state change it
shows, cut it. This is an operator's tool that people keep open all day; motion that was
charming the first time is noise the hundredth.

Before adding any motion, make a **restraint pass** over the change: list each animation and the
state change it shows, and delete the ones with no answer. A surface with no motion is an
acceptable outcome.

## Timing

Use the tokens, never a literal. In CSS they are `var(--t-*)` and `var(--ease-*)` on `:root`
in `client/src/index.css`. In JS (Motion) they are `DURATION` and `EASE_OUT` from
`client/src/lib/motionTokens.ts`. Both mirror `@lore-eden/ui` `tokens/specs.ts`.

| Job | Token | Value |
|---|---|---|
| Hover, press, tooltip | `--t-fast` | 120ms |
| Toggle, dropdown, toast entering | `--t-med` | 200ms |
| Modal or panel entering | `--t-slow` | 300ms; go no slower |
| Any exit | one step faster than its entrance | a modal leaves at `--t-med`, a toast at `--t-fast` |
| List stagger | `--t-stagger` per item | 40ms; the whole sequence finishes within about 500ms |

- `--ease-out` for entrances and most state changes. `--ease-spring` overshoots slightly; keep
  it for one or two moments in the whole app, not a default.
- Entrances at 600–800ms belong to landing pages. Here they read as lag.
- A loop (pulse, shimmer, spinner) is allowed only while something is actually in progress, and
  stops when it ends. Never an ambient loop for atmosphere.

## What may move

- Animate `transform` and `opacity` only, or the individual `translate`/`scale`/`rotate`
  properties when an element already uses `transform` for positioning (`.modal-panel` does).
- Never a layout property (`width`, `height`, `top`, `left`, `margin`, `padding`, …): each
  frame relayouts the page. A progress bar uses `scaleX` on a fixed track, not `width`.
- Never `transition: all`; name the properties.
- Never use motion to cover a slow load. Waiting is the loading state's job: a skeleton where
  the shape is known.
- Nothing rests at `opacity: 0` waiting for an observer. Content is visible without motion.

## Reduced motion

A reader who turned on "reduce motion" gets a short fade or a held frame, and loops stop.

- **CSS:** covered app-wide by the `@media (prefers-reduced-motion: reduce)` rule in
  `index.css`. Do not add per-file copies.
- **Motion (`motion/react`):** covered by `<MotionConfig reducedMotion="user">` at the root
  (`main.tsx`). Do not override it per component.
- **Anything else in JS** (`requestAnimationFrame`, canvas, timers driving movement) is not
  covered by either. It must check `matchMedia("(prefers-reduced-motion: reduce)")` itself and
  render a still frame.

## Building it

- **Enter only:** a CSS `@keyframes` on the element, with token durations.
- **Exit, or anything that must animate before it unmounts:** `m.*` from `motion/react` inside
  `AnimatePresence`. The root uses `LazyMotion strict`, so `motion.*` components throw; use
  `m.*`.
- **A leaving element must not take input.** Set `pointerEvents: "none"` in its `exit`, so a
  second click cannot land on something that is already going.
- Dialogs: until the shared modal shell exists (lg-ux-enforcement-912), dialogs get the CSS
  entrance from `.modal-panel` and no exit. Do not hand-roll an exit inside one dialog.

The `ts-motion` gate rejects layout-property animation, `transition: all` and hardcoded UI
durations on changed lines; waive with `motion-ok:` plus a reason a reviewer can check.

## Checking it (`visual_qa`)

For each surface the change touches, walk its state changes: load, hover, focus, open, close,
empty to filled, success, error. For each one, check that:

1. the motion it has shows that state change, and anything that only decorates is reported;
2. its duration matches the table, and its exit is faster than its entrance;
3. nothing jumps, because nothing animates a layout property;
4. with reduced motion emulated, it settles to a still end state and every loop stops;
5. a closing element does not take a click, and focus does not stay inside it.

Report a finding against the acceptance criterion it breaks. Motion that is merely not to your
taste is not a finding.

## Other workspaces

These tokens and files are loregarden's. In another workspace, use that workspace's own motion
tokens if it has them. The rules above (state over decoration, the timing ratios,
transform/opacity only, reduced motion) still hold.
