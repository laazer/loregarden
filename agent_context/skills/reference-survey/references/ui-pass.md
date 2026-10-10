# The UI pass

A UI finding is not a screenshot impression. If you cannot run the thing, say so
in the append, then read the interface source — component files carry the states
a screenshot cannot show.

## What to read for

**States, not layouts.** The questions worth answering are the ones our own
surfaces get wrong:

- Does an unknown value render differently from a zero? A `-1` drawn in a muted
  colour with a tooltip explaining which of two unknowns it is beats a hidden
  element every time.
- Does a `switch` over a status have a `default` arm that is neither pass nor
  fail? A fallback that lands on success is how an unmodelled state reads as green.
- Are there two empty states — nothing yet, and nothing matching the filter — or
  one? Only the second can tell the user what to change.
- Does a disabled control explain why it is disabled?
- Can every dialog be dismissed from the keyboard, and is that in the component
  or in each caller's memory?

**Where the state lives.** A pattern enforced by the base component is worth more
than the same pattern repeated correctly in thirty files, because the thirty-first
will be wrong.

**Copy.** Error and empty-state text is product copy. Look for messages that name
a recovery with a real value substituted, and for messages that assert a
consequence the system cannot verify.

## Count before you claim

Grep the numbers rather than asserting a vibe: how many components carry an
accessibility annotation, how many reference reduced motion, how many colour
literals exist against how many tokens. A survey that says "good accessibility"
is worth nothing; one that says "127 `aria-` attributes across 52 of 97
components" is a finding, and so is the inverse.

## Where UI findings land

Interface work spreads across several workspaces: a shared primitive belongs in
`lore-eden`, a token decision in the client's own quality milestone, a surface
behaviour on the feature that owns that surface. One UI survey usually produces
findings for three or four tickets, not one.
