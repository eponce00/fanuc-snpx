# Architecture

```
SnpxClient (client.py)            typed API, 1-based FANUC indexes, guarded writes
 ├─ AssignmentTable / Manager     $VAR_NAME parsing, element layout, slot precedence, CLRASG/SETASG
 │   (assignments.py)
 ├─ AccessPolicy (policy.py)      local JSON policy → WriteAuthorization
 ├─ types.py                      Position, Cartesian, Joints, IoFamily, status replies
 └─ SrtpSession (srtp.py)         frames, handshake, one request at a time, chunking, rate limit,
     │                            close-on-error, open hooks (re-create session assignments)
     ├─ memory.py                 segments, srtp_index(), packing, chunk planning
     └─ EvidenceLog (evidence.py) optional JSONL of every frame and write decision

ControllerFiles (oracle.py)       read-only FTP (list / download) for validation
cli.py                            fanuc-snpx command
testing.py                        FakeSrtpServer + FakeController for tests and for users' own tests
```

## Data flow of a read

`robot.position_registers.read_block(101, 250)`

1. Policy limits checked (if a policy is loaded).
2. `AssignmentTable.plan_runs("PR", 101, 250)` finds the slot(s) covering PR[101..350], checks that no
   lower slot shadows them, and merges consecutive elements into runs.
3. Each run is one ranged %R read; `SrtpSession.read_words` splits it into requests of at most
   `max_read_bytes` and concatenates the replies (any failure raises; no partial list).
4. Each element's bytes are decoded according to the assignment (`$MULTIPLY`, `@` slice).

## Data flow of a write

`robot.numeric_registers.write(195, 1.5, reason="...")`

1. `_authorize`: `allow_writes`, policy present, host, limits, protected, writable, reason.
2. Locate `R[195]` in the table; encode for its assignment; predict the read-back value.
3. Read old value → `write_guard` → `write_words` with the `WriteAuthorization` → read back → compare.

## Sessions

`SrtpSession` opens lazily. Any exception during a request closes the socket. The next request opens a new
connection, repeats the handshake and runs the open hooks (the assignment manager uses one to re-send
`CLRASG`/`SETASG` for session-scoped tables). Nothing is retried automatically.

## Design choices

- Standard library only at runtime; `py.typed`; `mypy --strict` clean.
- Blocking sockets with per-operation deadlines; no threads in the client.
- The fake controller encodes data with its own code, so client decoding is tested against a second
  implementation of the same hypothesis. Golden bytes come from third-party captures.
- No raw writes in the public API.
