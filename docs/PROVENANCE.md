# Provenance

No vendor binary was decompiled, patched or redistributed. No source code was copied from the projects
below; the implementation is written from the protocol facts they document. Facts learned from each source:

| Source | License | Used for |
|---|---|---|
| [valstad-shipworks/fanuc_ucl](https://github.com/valstad-shipworks/fanuc_ucl) (`src/hmi/proto/wire.rs`, `ports.rs`, `asg.rs`, `mod.rs`) | Apache-2.0 | Header field layout, `D4`/`94`/`D1` message types, `4F` session control, segment selectors, I/O offsets, `SETASG`/`CLRASG` on %G, 50-word position layout, string size, observation that UCL sends `CLRASG` on connect (to port 60008) |
| [Booozie-Z/Fanuc_GESRTP_Driver](https://github.com/Booozie-Z/Fanuc_GESRTP_Driver) (`docs/srtp packets.txt`, `srtp_message.py`) | MIT | Wire captures of Kepware and a test client talking to a FANUC controller on 18245: request/reply bytes, byte 9/17/30 values, extended-write layout, bit-write packing, inline vs extended replies, a `D1` error frame. Used as golden fixtures in `tests/golden.py` |
| [Palatis/packet-ge-srtp](https://github.com/Palatis/packet-ge-srtp) | BSD-3-Clause | Field names and offsets of the 56-byte header |
| FANUC B-82604EN/01 "CIMPLICITY HMI for Robots" Operator's Manual, chapter 6 (public PDF) | FANUC copyright; facts only | `$SNPX_ASG` fields and limits, `$MULTIPLY` semantics, `@a.b` slices, element sizes, I/O mapping, %G commands, multiplexed tables, notes on conversion cost |
| GE GFK-0582D "Series 90 PLC Serial Communications User's Manual" (provided by the user, not in this repo) | GE copyright; facts only | Background on SNP/SNP-X memory types |
| Kepware GE Ethernet driver manual | Kepware copyright; facts only | Request size range 32-2048 bytes |
| UnderAutomation public documentation | facts only (feature list) | API scope comparison; no code, no wire format |
| Other public SRTP clients surveyed (TheMadHatt3r/ge-ethernet-SRTP MIT, hoilung/go-gesrtp MIT, kkuba91/uGESRTP MIT, praetorian-inc/nerva Apache-2.0) | as listed | Cross-checks of the init packet, status bytes, selectors |
| Palatis/Fanuc.RobotInterface, BiasedControls/snpx-client | no license file | **Not used for code.** Mentioned only where they agree with licensed sources |

## Controller file formats (for `parsers.py`)

Layouts of `numreg.va`, `posreg.va`, `sysframe.va`, `system.va` (`Field:` lines, `$SNPX_*`),
`curpos.dg`, `summary.dg`, `errall.ls` and `iostate.dg` were learned from public files of real and
ROBOGUIDE controllers (V7.70 to V9.40): onerobotics/go-fanuc `testdata/md` (MIT),
KonstantynBely/karelCalibration (MIT), and controller backups published without a license in
slsdetectorgroup/pickingtools, JuniorNatalin/VASC, Apllepie/draw_portret and ricardocastr0/MFI_DEMO (format
facts only; nothing copied). The test fixtures in `tests/fixtures/` were written for this project with
invented values in the same layout.

Vendor manuals and notes the owner supplied, text extracts of the manuals above, the MIT wire captures and the public controller-file samples are in `vendor-docs/` (committed on the owner's decision; private; not packaged; see `vendor-docs/README.md`). Remove that folder from the history before any public release.
