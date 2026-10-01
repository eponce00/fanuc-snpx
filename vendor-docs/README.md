# vendor-docs: private reference material

Committed on the owner's decision (2026-10-01) so any agent working on this repository has the same
references. **This folder is not part of the library** (it is not packaged) and it contains third-party
copyrighted documents and the owner's own notes. The repository is private. If the library is ever
published, this folder must be removed from the repository *and its history*
(e.g. `git filter-repo --path vendor-docs --invert-paths`), or moved to a separate private repository first.

Files are stored byte-exact (`.gitattributes`: `vendor-docs/** -text`).

## Most useful first

| File | Why |
|---|---|
| `research/cimplicity_b82604.txt` | FANUC B-82604EN/01 "CIMPLICITY HMI for Robots", chapter 6 (lines ~986-2470): the best available SNPX reference (`$SNPX_ASG`, `$MULTIPLY`, `@a.b`, element layouts, I/O mapping, %G commands, multiplexed tables). There is no R553 manual in this repo. |
| `public-captures/Booozie-Z_Fanuc_GESRTP_Driver/srtp_packets.txt` | Wire captures of Kepware and a test client talking to a FANUC controller on 18245 (MIT, see `LICENSE` there). Source of the golden frames in `tests/golden.py`. |
| `public-samples/` + `SOURCES.tsv` | Public controller files (numreg/posreg/sysframe/system.va `$SNPX_*`/curpos/errall/iostate/version) from V7.70-V9.40 controllers; formats behind `src/fanuc_snpx/parsers.py`. Most come from repositories without a license: reference only, never copy into tests. |
| `02_FANUC_SNPX_and_Ethernet_Control_Interface/SNPX_OWN_CLIENT.md` | The owner's plan for replacing the commercial SDK: the operations their application needs (see docs/TRACKER.md §8). Application-specific: do not put its register numbers into the library. |
| `02_.../FANUC_CONTROLLER_SNPX_AND_WEB_INTERFACE_GUIDE.md` | The owner's notes on the test robot's SNPX/web setup, including the `$SNPX_PARAM`/`$SNPX_ASG` values (source not recorded; it omits `$NUM_CIMP`). |
| `research/GFK-0582D_*.txt`, `02_.../GFK-0582D_*.pdf` | GE Series 90 SNP manual: memory types and the error-code tables used in `status.py` (chapter 6, around line 4234 of the text). |
| `research/kepware_ge_ethernet.txt` | Kepware GE Ethernet driver manual (request size range 32-2048 bytes). |
| `05_.../extracts/MARRUEROR02171E_R30iB_Error_Code_Manual.md` | R-30iB alarm codes; SNPX alarms are PRIO-090 (communication error), PRIO-096 (connection full), PRIO-097 (timeout), around line 48906. |

## Everything else

| Path | Content |
|---|---|
| `README_DOCUMENTATION_INDEX.md` | The owner's index of the full documentation zip (most of the zip is about their cell and is not included here) |
| `02_FANUC_SNPX_and_Ethernet_Control_Interface/*.pdf` | KEPServerEX SNPX setup guide, Pro-face APNT1195 SNPX app note, GE GFK-0582D, R-30iB Plus EtherNet/IP manual, PLC Motion I/O manual |
| `02_.../ETHERNETIP_REFERENCE.md`, `PLC_MOTION_IO_REFERENCE.md` | The owner's short notes |
| `02_.../controller_live_web_and_diagnostics/` | Pages saved from the test robot's web server. **Caution:** the `.dg` files there are copies of the robot's home page, not real diagnostic files |
| `03_Station_Architecture_and_PLC_Integration/FANUC_FTP_TRANSFER.md` | The owner's notes on FTP access to the controller |
| `05_FANUC_Manual_Markdown_Conversions_and_References/extracts/` | Markdown conversions of the R-30iB operator manual (B-83284EN), HandlingTool V9.30 manual, and R-30iB error-code manual |
| `research/` | Text extracts made during Phase 0 (`pdftotext -layout`) and the first research report (also in docs/research/PUBLIC_SOURCES.md) |

Source of the owner's files: `E:\ErnestoProfile\Desktop\Enclosure_Robot_and_Fanuc_Documentation.zip`
(349 MB). Only the SNPX-related subset is here.
