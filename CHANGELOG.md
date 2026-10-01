# Changelog

All notable changes to this project. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/) once 0.1.0 is released. Before 1.0 the API may
still change between minor versions; changes are listed here.

## [Unreleased]

Nothing has been validated against a robot yet (see docs/VALIDATION_LOG.md). The first release (0.1.0)
is planned after the Phase 3 read-only validation (docs/TRACKER.md).

### Added
- SRTP session (`SrtpSession`): init packet, `0x4F` session control, `C0` reads, `80` writes, strict reply
  validation, transparent read chunking, per-operation deadlines, rolling rate limit, close-on-error with a
  fresh session on the next request, open hooks; no automatic retries.
- Memory helpers: segment selectors, the single 1-based → 0-based conversion, word/int/float32/string/bit
  packing, chunk planning.
- Access policy (`AccessPolicy`): local JSON file with protected/writable ranges, limits, controller host
  binding and an assignment block; writes need `allow_writes=True`, a policy, a reason, and pass an
  optional `write_guard`; every write reads back and compares at float32 precision.
- `SnpxClient` typed API: numeric/position/string registers, comments, system variables, user
  frames/tools, current position (Cartesian + joints from one read), robot I/O, controller information
  services, alarm screens (`ALM[]`), program status (`PRG[]`).
- `$SNPX_ASG` model: `$VAR_NAME` parser and validator, element layouts and `@` slices, slot precedence,
  planner (`plan_assignments`, `free_slots`, `request_words`), session-scoped `AssignmentManager` that
  refuses `CLRASG` unless multiplexing is confirmed.
- Read-only FTP oracle (`ControllerFiles`) and parsers for `numreg.va`, `posreg.va`, `sysframe.va`,
  `system.va` (`$SNPX_*`), `curpos.dg`/`summary.dg`, `errall.ls`, `iostate.dg`.
- SRTP-vs-file comparison (`compare.py`).
- CLI `fanuc-snpx`: `probe`, `survey`, `read-raw`, `read-io`, `read-reg`, `read-sysvar`, `read-pos`,
  `ftp-get`, `parse`, `compare`, `plan-asg`, guarded `write-reg` / `write-io`.
- `fanuc_snpx.testing`: `FakeSrtpServer` / `FakeController` with fault injection.
- GE SNP status-code descriptions as hints in `SrtpServiceError`.
- Examples: application adapter, demo against the fake controller, example policy and assignment map.
