/**
 * Where a toast message may be broken across lines.
 *
 * A long absolute path is the one action a handoff refusal leads to, and it was
 * being cut mid-identifier — the operator was shown a path-shaped string that
 * is not a path. The mechanism is an ordinary **hyphen soft-wrap opportunity**,
 * not `overflow-wrap` splitting a token in the remaining space of a line: `-`
 * is a break opportunity in normal CSS line breaking and every worktree
 * directory name is hyphen-separated. Measured in Chromium at the shipped
 * geometry, no combination of `word-break` and `overflow-wrap` changes it.
 *
 * So the fix is not a break rule. Path segments are made unbreakable and
 * nothing else is: each `/`-separated segment of a *path token* gets
 * `white-space: nowrap` — the one declaration that suppresses the hyphen break
 * — and the breaker is given an opportunity between segments instead. A *path
 * token* is a whitespace-delimited token containing `/`, so prose keeps
 * wrapping at its own spaces and the common case is untouched.
 *
 * This module decides that split and nothing about how it is rendered. The
 * break opportunity the component inserts between segments is a `<wbr/>`
 * element, never a zero-width space: `<wbr>` contributes no text, so
 * `textContent`, the `aria-live` announcement and the clipboard payload all
 * stay byte-identical to the message the store holds. A U+200B would put
 * invisible characters inside a path the operator pastes into a shell.
 *
 * Which makes join-identity the property that matters: the parts, concatenated,
 * are the original message byte for byte. Hence a hand-written scan rather than
 * a regex replace, whose replacement string would interpret `$&` and friends
 * inside a path that legitimately contains them.
 */

/** One run of the message, and whether it must be laid out unbreakably. */
export interface WrapPart {
  text: string;
  nowrap: boolean;
}

/** Split one token after every `/`; the separator stays with its segment. */
function segmentsOf(token: string): string[] {
  const segments: string[] = [];
  let start = 0;
  for (let i = 0; i < token.length; i += 1) {
    if (token[i] === "/") {
      segments.push(token.slice(start, i + 1));
      start = i + 1;
    }
  }
  if (start < token.length) segments.push(token.slice(start));
  return segments;
}

/**
 * Split `message` into the runs a toast renders.
 *
 * `nowrap` is true for exactly the `/`-segments of a whitespace-delimited token
 * that contains `/`, and false for everything else — prose, whitespace, and a
 * token with no separator in it. Every nowrap part but the last of its own token
 * ends with `/`, no part is empty, and `parts.map((p) => p.text).join("")` is
 * `message` for every input.
 */
export function splitMessageForWrap(message: string): WrapPart[] {
  const parts: WrapPart[] = [];
  let prose = "";

  const flushProse = () => {
    if (prose === "") return;
    parts.push({ text: prose, nowrap: false });
    prose = "";
  };

  let i = 0;
  while (i < message.length) {
    // Whitespace is a token boundary, so it can never land inside a segment.
    if (/\s/.test(message[i])) {
      prose += message[i];
      i += 1;
      continue;
    }

    let end = i;
    while (end < message.length && !/\s/.test(message[end])) end += 1;
    const token = message.slice(i, end);
    i = end;

    if (!token.includes("/")) {
      prose += token;
      continue;
    }

    flushProse();
    for (const segment of segmentsOf(token)) {
      parts.push({ text: segment, nowrap: true });
    }
  }

  flushProse();
  return parts;
}
