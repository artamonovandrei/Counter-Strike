# shared/

`protocol.ts` and `backend/app/protocol.py` describe the same wire format. They are kept as
two files rather than one generated artefact so that neither side needs a build step or a
codegen toolchain.

The rule: **the Python file is authoritative**. If they disagree, the Python file is right
and the TypeScript file needs updating.

`make check-parity` (see the root `Makefile`) extracts the constants from both files and
fails if `PROTOCOL_VERSION`, the key bitmask or the entity flag bits differ. It does not
compare interface shapes — TypeScript interfaces are erased at build time, so a mismatch
there shows up immediately as `undefined` in the client rather than as a silent bug.

Tuning values (movement constants, weapon tables, map geometry) are deliberately *not*
duplicated here. The server sends them in the `welcome` message and the client uses
whatever it is given, so there is exactly one source of truth at runtime:
`backend/app/config.py`.
