# Safety model

The library will be pointed at production robots. These properties hold by construction, and tests check
them.

## What the code base cannot do

There is no code for run/stop, program select/run/pause/abort, alarm reset (`CLRALM`), clearing faults,
forcing or toggling force, loading programs, setting the controller clock, privilege changes, motion, or
the port-60008 protocol. The SRTP layer only knows services `00`, `03`, `04`, `07`, `38`, `43`, `4F`;
`SrtpSession.service()` refuses anything but the four read-only information services.

There are no raw-memory write methods in the public API. Writes go through typed APIs so the policy can
name the target (`R[12]`, `DO[3]`, `$MNUFRAME[1,2]`).

## Read-only by default

A write happens only if **all** of these hold, checked before any byte is sent:

1. The client was created with `allow_writes=True`.
2. An access policy is loaded (explicit path, `$FANUC_SNPX_POLICY`, or `./robot-policy.local.json`).
   No policy → every write raises `WriteNotAllowed`.
3. The policy's `controller.host` (if set) equals the client's host → otherwise `PolicyViolation`.
4. Every target index is within the policy `limits` → otherwise `PolicyViolation` (also checked on reads).
5. No target is in `protected` → otherwise `PolicyViolation`, even with `allow_writes=True`.
6. Every target is inside `writable` → otherwise `WriteNotAllowed`. `"*"` is not accepted in `writable`.
7. A non-empty `reason` → otherwise `WriteNotAllowed`.

Then the client reads the old value, calls `write_guard(target, old, new, reason)` (return `False` or raise
to veto → `WriteVetoed`), writes, reads back and compares. Reals are compared after rounding both sides to
float32 (plus `readback_tolerance`, default 0). A mismatch raises `ReadBackMismatch`. Old value, new value,
reason and the read-back go to the log and, when enabled, to the evidence file.

The session layer refuses any write that does not carry a `WriteAuthorization` produced by the policy.
(Python cannot stop deliberate misuse; this prevents accidental writes.)

## Specific hazards and how they are handled

| Hazard | Handling |
|---|---|
| `CLRASG` erases the shared `$SNPX_ASG` on controllers without multiplexed assignments | Never sent implicitly. `AssignmentManager.apply_session` needs `multiplex_confirmed=True`, a policy that lists the %R block as writable for `snpx_assignments`, and a reason. Confirm `$SNPX_PARAM` from the controller's files first. |
| Persistent assignment changes | Persistent mode is not implemented. |
| PR user frame / tool numbers cannot be restored from the pendant | PR writes touch words 1-26 or 27-45 only, never UF/UT (46-47). |
| Writing robot inputs (DI, AI) causes a momentary false value | Only possible if the policy lists them as writable; document PLC-owned signals as protected. |
| Ambiguous system-variable names (`$MNUFRAME[2]` accepted but writes nothing) | Known arrays must carry every index; blanks rejected; every write is read back. |
| A failed batch leaves the session desynchronised | Any error closes the socket; the next request opens a new session; nothing is retried automatically. |
| Partial results | A block read either returns every element or raises. Split writes are refused. |
| Hammering the controller | Rate limit: at most 5 requests in any rolling second by default (configurable, never unlimited). |
| Using one robot's policy on another | `controller.host` binding in the policy. |
| FTP oracle | `ReadOnlyFtp` refuses STOR/DELE/RNFR/MKD/SITE/APPE/... before sending them. |

## Operating rules

- Do not commit policy files, evidence logs, controller dumps or vendor manuals (`.gitignore` covers them).
- Hardware tests are read-only and run only with `FANUC_SNPX_HARDWARE=1`.
- Writes on a real robot only on targets the robot owner named, with someone at the pendant.
