# Amperesand Enclosure Dispense & FANUC Robot Documentation Archive

**Created:** October 1, 2026  
**Repositories Included:** `Enclosure-Dispense-Robot` & `Fanuc-DevAmp`  
**Robot Target:** FANUC LR Mate 200iD/7L, Controller R-30iB (F546175, IP `10.50.160.51`)  
**Dispenser:** Graco EFR (Profinet CGM) | **Vision:** Lucid TRI081S-MC & Edge Trace  
**Pedestal Calibration Sensor:** CAPTRON OGLW2-70T4-2PS6 dual laser barrier  

---

## Folder Organization

### `01_TCP_Teach_and_Calibration/`
Authoritative documentation for the CAPTRON laser pedestal calibration, robot TCP teach, and camera hand-eye frames:
- **`TCP_BOOT_V2_2026-09-30.md`**: Complete operational procedure, 8-orientation measurement sequence, register map (R151..R179, PR360..PR370), fault codes, quality gates, and operator instructions.
- **`captron-tcp-check.md`**: Hardware specification, wiring, pinout, alignment, and teach procedure for the CAPTRON OGLW2-70T4-2PS6 dual laser barrier.
- **`HAND_EYE_NEST_UFRAME_WORKFLOW.md`**: Hand-eye solving and UFRAME calculation workflow.
- **`CAMERA_NORMALITY_WORKFLOW.md` & `CAMERA_NORMALITY_APPROVAL.template.json`**: Camera normality inspection and sign-off criteria.
- **`HAND_EYE_POSE_ERROR_FINDINGS_2026-09-29.md`**: Physical findings regarding arm posture deflection and joint correction model.
- **`CAMERA_CALIBRATION_ROADMAP.md`**: Multi-phase vision, lens distortion, and homography calibration roadmap.
- **`TCP_AXIS_CALIBRATION_2026-09-01.md` / `TCP_AXIS_REFERENCE_2026-09-02.md` / `TCP_GOLDEN_REFERENCE_2026-09-01.md` / `TCP_ROUTE_2026-09-02.md`**: Historical baseline references and route benchmarks.
- **`STATION_TABLE_AND_PURGE_TEACH_2026-08-26.md` & `STATION_TABLE_FRAME_RESERVATION.md`**: Coordinate reservations and purge pose teaching.
- **`UFRAME5_STATION_TABLE_TEACH_TARGET_LETTER.pdf` / `.svg`**: Printable letter-sized teach target for UFRAME 5 station table.

### `02_FANUC_SNPX_and_Ethernet_Control_Interface/`
Complete documentation on FANUC Option R553 (HMI Device SNPX), EtherNet/IP adapter/scanner, and PLC motion control:
- **`FANUC_CONTROLLER_SNPX_AND_WEB_INTERFACE_GUIDE.md`**: Comprehensive architectural guide covering Option R553, GE SRTP protocol framing on port 18245, Modbus TCP on port 502, `$SNPX_ASG` register mapping, and controller web tools.
- **`SNPX_OWN_CLIENT.md`**: Amperesand's design and specification for replacing third-party commercial SDKs with our zero-dependency pure-Python SRTP client.
- **`Fanuc_robot_to_KEPServerEX_SNPX_Guide.pdf`**: Complete guide detailing Option R553 installation, PAC code authorization, Controlled Start procedure, `$SNPX_ASG` setup, and tag mapping.
- **`APNT1195_GPro_EX_to_Fanuc_Robot_R30iA_via_Ethernet_SNPX.pdf`**: Pro-face application note on connecting external touch screens to FANUC controllers over Ethernet using Series 90 SNPX.
- **`GFK-0582D_Series_90_SNP_Communications_Protocol.pdf`**: Official GE Fanuc manual detailing the SNP/SNPX protocol framing, message structures, memory type codes (%R, %I, %Q, %M), and service request codes.
- **`MAROU91EN02171E_R30iB_Plus_EtherNetIP.pdf`**: Official FANUC EtherNet/IP Setup and Operations Manual covering adapter/scanner configuration, explicit messaging, CIP objects, and CIP Safety.
- **`B-84214EN_01_PLC_Motion_IO.pdf`**: Official FANUC PLC Motion and I/O Control Manual.
- **`ETHERNETIP_REFERENCE.md` & `PLC_MOTION_IO_REFERENCE.md`**: Distilled engineering reference summaries.
- **`controller_live_web_and_diagnostics/`**: Live web pages and diagnostic dumps captured directly from the robot controller (`default.stm`, `enet.stm`, `summary.dg`, `version.dg`, `iostate.dg`, `prgstate.dg`, `curpos.dg`, etc.).

### `03_Station_Architecture_and_PLC_Integration/`
Station-level integration between the FANUC robot, Siemens S7-1500 PLC, and Graco EFR dispense system:
- **`CORE_PLC_RUNTIME_CONTRACT.md`**: Master authority for robot-PLC state machine, UOP handshakes, task selection (PNS0101), registers, and alarms.
- **`ENCLOSURE_DISPENSE_STATION_OVERLAY.md`**: Enclosure dispense cell specifics.
- **`VISION_CYCLE_HANDSHAKE.md`**: Handshake codes (11 for dispense inspection, 21 for holding/bead start, 6 for purge, 8 for barcode, 2 for complete).
- **`cell-cycle-architecture.md` & `operator-enclosure-dispense-design.md`**: Station sequence design and operator interaction patterns.
- **`station-permissives.md`**: Permissive interlock definitions for robot and dispenser operations.
- **`vision-camera-robot-coordinates.md`**: Camera coordinate systems and transformations.
- **`NEST_ORCHESTRATOR_INTEGRATION.md`**: Orchestrator state machine, sensor truth tables, and batch flow.
- **`GRACO_EFR_INTEGRATION.md` & `Graco_EFR_PROFINET_Setup_Instructions.pdf`**: Graco EFR meter-mix dispense integration and Profinet setup.
- **`EFR_LIVE_TEST_REPORT_2026-09-01.md`**: Detailed commissioning test results and findings.
- **`EFR_DISPENSER_SETUP_INSTRUCTIONS.md`**: Hardware and software setup steps for the Graco EFR dispenser.
- **`COMPUTATIONAL_CAPTURE.md`, `PATH_SAFETY_ROADMAP.md`, `PR_STREAM_PROTOCOL.md`**: Vision capture and path streaming protocols.
- **`plan-b-fixed-path.md` & `commissioning-test-notes.md`**: Fixed path execution fallback and test records.
- **`FB35_EnclosureDispenseRobotControl_NETWORK_MAP.md` & `FB36_EnclosureDispenseRobotSequence_GRAPH_MAP.md`**: Comprehensive network and GRAPH step maps for Siemens PLC FB35 and FB36.
- **`MES_CHECK_GRAPH_BRANCH.md` & `STATION_ORCHESTRATION_CROSSWALK.md`**: MES transaction branches and station signal crosswalks.
- **`FANUC_FTP_TRANSFER.md` & `FANUC_INITIAL_PACKAGE_INSTALLATION.md`**: Controller package management and FTP deployment.
- **`LS_LINTER.md`**: FANUC TP/LS listing linter constraints and syntax rules.
- **`siemens-function-blocks.mdc` & `wincc-unified-sr1000-screen.mdc`**: Amperesand Siemens FB and WinCC Unified screen standards.

### `04_FANUC_Official_Manuals_PDFs/`
Unmodified official FANUC controller, mechanical, software, and application manual PDFs:
- `MAROUHT9307191E_REV_D_V930_HandlingTool.pdf` (2,390 pages)
- `MARRUEROR02171E_R30iB_Error_Code_Manual.pdf` (1,955 pages)
- `B-83284EN_09_Controller_Operator_Basic_Function.pdf` (994 pages)
- `B-83195EN_15_Maintenance.pdf` (592 pages)
- `B-83144EN-1_02_KAREL_Reference.pdf` (536 pages)
- `B-83184EN_13_02_DCS.pdf` (414 pages)
- `MARA3ERCD11001E_RJ3_Error_Code_Manual.pdf` (367 pages)
- `B-82864EN_11_PROFINET.pdf` (184 pages)
- `B-83144EN_02_KAREL_Function.pdf` (166 pages)
- `B-84074EN_08_M20iD_Mechanical.pdf` (168 pages)
- `B-83754EN_04_02_M20iB_Mechanical.pdf` (152 pages)
- `B-83064EN_01_PickTool.pdf` (142 pages)
- `B-83924EN_06_iRPickTool.pdf` (iRPickTool Manual)
- `B-83914EN-6_05_iRVision_Bin_Picking.pdf` (iRVision Bin Picking Manual)
- `MARGUM10A04171E_ARCMate_M10_M20_Startup_Guide.pdf` (2 pages)
- `MARGUM2IB04171E_M20iB_Startup_Guide.pdf` (2 pages)

### `05_FANUC_Manual_Markdown_Conversions_and_References/`
Fully searchable, text-extract and Mistral OCR-converted Markdown versions of the FANUC manuals:
- High-level reference summaries: `DCS_REFERENCE.md`, `ERROR_CODE_REFERENCE.md`, `KAREL_REFERENCE.md`, `MAINTENANCE_REFERENCE.md`, `MECHANICAL_UNIT_REFERENCE.md`, `PICKTOOL_REFERENCE.md`, `PROFINET_REFERENCE.md`, `STARTUP_GUIDE_REFERENCE.md`, `VISION_APPLICATIONS_REFERENCE.md`.
- Full searchable text/markdown extracts in `extracts/` for every official manual including figure descriptions and tables.
