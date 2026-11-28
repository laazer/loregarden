"""What a symbol LOOKED like, as a content hash nobody types (368, R1).

The anchor on a claim is the slug — `qualified_name`, which an LLM mints and
must be able to reproduce. This module is the other half: the fingerprint, a
digest computed server-side from the map at write time and never typed by
anyone, so no non-deterministic producer can mint one.

The input is `ast.unparse(node)` and never raw source bytes or a line range:

* comments are not in the AST, so a comment edit cannot move the digest;
* formatting is re-rendered canonically, so a reflow cannot either;
* no line number is in the input, so motion within a file is invisible;
* the symbol's own NAME is in the input, because a renamed symbol is a
  different unit rather than the same one moved (D2);
* the PATH is not, because a file move must read as MOVED and not as CHANGED.

The scheme prefix is both hashed in and stored, so a deliberate scheme bump
makes an old corpus UNVERIFIABLE — regroundable — rather than reporting false
drift on every claim in it.

`hashlib` and the constant below, and nothing else: no I/O and nothing from
the rest of this package. A pure module is what lets the golden-fingerprint
test mean what it says.
"""

from __future__ import annotations

import hashlib

#: The scheme this build computes. Bump it only deliberately: every stored
#: fingerprint carrying another prefix reads UNVERIFIABLE from that moment.
FINGERPRINT_SCHEME = "sym1"

#: Half a sha256. 128 bits is far past the point where a collision inside one
#: checkout's partition is a real risk, and the stored column stays narrow.
_DIGEST_CHARS = 32


def fingerprint_for(node_source: str) -> str:
    """`sym1:<32 hex>` over *node_source*, which is `ast.unparse(node)`."""
    digest = hashlib.sha256((FINGERPRINT_SCHEME + "\n" + node_source).encode("utf-8")).hexdigest()
    return f"{FINGERPRINT_SCHEME}:{digest[:_DIGEST_CHARS]}"
