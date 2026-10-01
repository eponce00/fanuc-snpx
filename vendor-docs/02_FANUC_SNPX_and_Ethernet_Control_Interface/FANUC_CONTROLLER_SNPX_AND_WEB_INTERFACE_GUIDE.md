# FANUC Controller SNPX & Web Interface Reference Guide

**Robot Controller:** FANUC R-30iB (F546175)  
**Software Version:** V9.30P/17 (V9.30146)  
**Controller IP:** 10.50.160.51  
**Captured Date:** October 2026  

---

## 1. What is FANUC Option R553: "HMI Device (SNPX)"?

- **FANUC Option Code:** `R553` (part number `RTL-R553-HT`)
- **Controller Name:** `HMI Device (SNPX)` / `SNPX basic` (`snpxba`)
- **Protocol:** GE Series 90 **SNP / SNPX** (Serial Network Protocol eXtended) encapsulated over Ethernet as **GE SRTP** (Service Request Transport Protocol) on TCP port **18245**.
- **Alternative / Modbus:** Option R553 also provides Modbus TCP slave on port **502**.
- **Official Documentation Code:** `MARUIBHMI03031E` (*HMI Device Setup and Operations Manual* / *HMI User's Manual*), provided as a supplement to the HandlingTool manual set.

### How SNPX Works on the Controller
Unlike HTTP or browser pages, SNPX is a binary request-response protocol designed for PLCs, HMIs (like Pro-face, QuickPanel, PanelView with Kepware, or Ignition), and SCADA drivers.
The robot acts as a GE Series 90 / PACSystems PLC emulator. It maps controller internal data structures (Numeric Registers `R[]`, Position Registers `PR[]`, System Variables, DI/DO, String Registers `SR[]`) to standard GE PLC memory types (`%R`, `%I`, `%Q`, `%M`, `%AI`, `%AQ`).

---

## 2. Live Controller SNPX System Variables (F546175)

The mapping between GE `%R` addresses and robot registers is configured in `$SNPX_ASG`.
The controller has 80 configurable assignment slots.

### Live Mapping Configuration on F546175:

```text
$SNPX_PARAM:
  $TIMEOUT     : 5000 ms
  $SNP_ID      : Uninitialized
  $NUM_ASG     : 80
  $NUM_FRIF    : 4
  $VERSION     : 2
  $STATUS      : 0
  $DISP_INFO   : 0
  $MODBUS_ADR  : 1
  $NUM_MODBUS  : 0
  $MODBUS_PORT : 502
  $CMD_ENDIAN  : 1
  $COMP_FLAG   : 0

$SNPX_ASG[1]:
  $ADDRESS     : 1        (Starting %R address in the GE protocol)
  $SIZE        : 10000    (Number of consecutive registers)
  $VAR_NAME    : 'R[1]@1.1' (Maps %R1..%R10000 to Robot Numeric Registers R[1]..R[10000])
  $MULTIPLY    : 1.000000e+00

$SNPX_ASG[2..80]:
  Unassigned ($ADDRESS = 0, $SIZE = 0, $VAR_NAME = '')
```

### Adding Position Registers, Frames, or I/O to `$SNPX_ASG`:
To read or write Position Registers, String Registers, or User Frames via SNPX:
- **Position Registers:** Assign e.g. `$SNPX_ASG[2].$ADDRESS = 10001`, `$SIZE = 500`, `$VAR_NAME = 'PR[1]'`.
- **String Registers:** Assign e.g. `$SNPX_ASG[3].$ADDRESS = 11001`, `$SIZE = 64`, `$VAR_NAME = 'SR[1]'`.
- **User Frames:** System variables such as `$MNUFRAME[1,2]` or `$MNUTOOL[1,3]` can be assigned by variable name.
- **Digital I/O:** Can be mapped through `%I` / `%Q` or by assigning `DI[1]` / `DO[1]` to `%R` holding registers.

---

## 3. Controller Web Server & Web Help

The controller runs a lightweight HTTP server on port 80:
- **Default Homepage:** `http://10.50.160.51/` (serves `frs/default.stm`)
- **EtherNet/IP Diagnostics & Monitoring:** `http://10.50.160.51/frs/enet.stm` (allows starting, stopping, and viewing Ethernet statistics)
- **Robot State & Diagnostics:**
  - `curpos.dg` - Live Cartesian and joint positions
  - `summary.dg` - Controller software options, hardware configuration, and summary
  - `version.dg` - Full software build, options, and PAC status
  - `iostate.dg` - Real-time I/O state
  - `prgstate.dg` - Task and program execution status
  - `errall.ls` - Full system error history
- **Robot Comment Tool:** Accessible via `MENU -> Setup -> Host Comm -> HTTP` or browser at `http://10.50.160.51/` under Robot Tools.

---

## 4. Key Reference Documents in this Archive

1. **`Fanuc_robot_to_KEPServerEX_SNPX_Guide.pdf`**  
   Complete walkthrough for FANUC R-30iA/B Option R553 HMI Device (SNPX) setup, PAC authorization code entry in Controlled Start, `$SNPX_ASG` configuration, and mapping `%R` tags.
2. **`APNT1195_GPro_EX_to_Fanuc_Robot_R30iA_via_Ethernet_SNPX.pdf`**  
   Application note on connecting external touch screens / HMIs to FANUC controllers over Ethernet using Series 90 SNPX.
3. **`GFK-0582D_Series_90_SNP_Communications_Protocol.pdf`**  
   Official GE Fanuc manual detailing the SNP/SNPX protocol framing, message structures, memory type codes (`%R`, `%I`, etc.), and service request codes.
4. **`MAROU91EN02171E_R30iB_Plus_EtherNetIP.pdf`**  
   Official FANUC manual for EtherNet/IP adapter, scanner, explicit messaging, CIP objects, and CIP Safety.
5. **`B-84214EN_01_PLC_Motion_IO.pdf`**  
   Official FANUC manual for PLC motion and I/O integration.
6. **`SNPX_OWN_CLIENT.md`**  
   Amperesand's architectural blueprint and protocol analysis for our zero-dependency pure-Python SRTP client (replacing commercial SDKs).
