# fanuc-snpx

A pure-Python client for FANUC robot controllers with the **HMI Device (SNPX)** option (R553). It speaks
GE SRTP on TCP 18245. It has no runtime dependencies (standard library only), runs on Windows, Linux and
macOS, and is **read-only by default**.

> **Status: pre-alpha.** The frame format is taken from published wire captures and open-source clients and
> is tested against a fake controller. It has **not** been validated against a robot yet. See
> [docs/PROTOCOL.md](docs/PROTOCOL.md) for the verification status of every field and
> [docs/VALIDATION_LOG.md](docs/VALIDATION_LOG.md) for hardware evidence.

## What it does

- Numeric, position and string registers; comments; system variables (int, real, string, position);
  user frames/tools; current position (Cartesian and joints from one read); robot I/O (DI/DO, RI/RO,
  UI/UO, SI/SO, WI/WO, WSI/WSO, GI/GO, AI/AO); controller short status.
- Reads are resolved through the controller's `$SNPX_ASG` assignment table, so the client works with an
  existing table (read-only) or with session-scoped assignments it creates (a controller change, gated by
  your policy).
- Block reads are merged into as few requests as possible and chunked transparently.
- Optional evidence log: every frame as JSON lines.
- Read-only FTP helper to fetch the controller's own files for validation.

What it deliberately does **not** do: motion, program control, alarm reset, run/stop, forcing I/O,
program loading, or the port-60008 PC-interface protocol.

## Quick start (read only)

```python
from fanuc_snpx import SnpxClient, AssignmentTable

# Describe the controller's current assignment table. The factory default maps
# %R1..%R10000 to R[1..10000] as 16-bit integers.
table = AssignmentTable.factory_default()

with SnpxClient("192.0.2.10", assignments=table) as robot:
    print(robot.controller.short_status())
    print(robot.io.read("DO", 1, 8))          # [True, False, ...]
    print(robot.io.read("GO", 1))             # [560]
    print(robot.numeric_registers.read_block(1, 5))
```

With an assignment table that maps PRs, frames and the current position (for example
`AssignmentTable.load("my-cell-asg.json")`):

```python
pr = robot.position_registers.read(7)
print(pr.cartesian().xyzwpr, pr.cartesian().configuration, pr.user_frame)
print(robot.current_position.world().joints().j1_j6)
print(robot.frames.read_user_frame(group=1, number=2))
```

## Writes

Writes need `allow_writes=True`, a local policy file that lists the target as writable and not protected,
and a reason. Every write reads the old value, can be vetoed by your `write_guard`, and is read back and
compared (reals at float32 precision).

```python
from fanuc_snpx import SnpxClient

with SnpxClient("192.0.2.10", assignments=table, allow_writes=True,
                policy="robot-policy.local.json") as robot:
    old = robot.numeric_registers.write(195, 1.5, reason="scratch test agreed with cell owner")
```

See [docs/SAFETY.md](docs/SAFETY.md) and [docs/POLICY.md](docs/POLICY.md).

## Command line

```bash
fanuc-snpx probe 192.0.2.10 --controller-type
fanuc-snpx read-io 192.0.2.10 DO 1 16
fanuc-snpx read-reg 192.0.2.10 R 1 5 --factory-default
fanuc-snpx ftp-get 192.0.2.10 numreg.va posreg.va --dest controller-dumps
fanuc-snpx write-reg 192.0.2.10 R 195 1.5 --map asg.json --policy robot-policy.local.json --reason "..."
```

## Testing your own code

`fanuc_snpx.testing.FakeSrtpServer` is a local fake controller with a scriptable memory image, a model
of `$SNPX_ASG`, and fault injection (error replies, timeouts, split and stale replies, dropped connections).

## Documentation

[PROTOCOL](docs/PROTOCOL.md) · [ARCHITECTURE](docs/ARCHITECTURE.md) · [SAFETY](docs/SAFETY.md) ·
[POLICY](docs/POLICY.md) · [RUNBOOK](docs/RUNBOOK.md) · [PLAN](docs/PLAN.md) ·
[PROVENANCE](docs/PROVENANCE.md) · [VALIDATION_LOG](docs/VALIDATION_LOG.md) · [ASG_MAP](docs/ASG_MAP.md)

## License

Apache-2.0. FANUC and GE are trademarks of their owners; this project is not affiliated with them.
