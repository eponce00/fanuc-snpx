# FANUC EtherNet/IP Reference (MAROU91EN02171E Rev D)

Distilled notes from the R-30iB Plus EtherNet/IP Setup and Operations Manual
for controller software V9.10 and later.

Full PDF and extract:

- `docs/manuals/MAROU91EN02171E_R30iB_Plus_EtherNetIP.pdf`
- `docs/manuals/extracts/MAROU91EN02171E_R30iB_Plus_EtherNetIP.txt`

## Coverage

- Ethernet connection and IP address assignment
- Adapter and scanner configuration
- EtherNet/IP-to-DeviceNet routing
- Configuration mapping, backup, and restore
- Explicit messaging client setup
- FANUC vendor-specific register, position, status, motion, system,
  application, recovery, and communication objects
- Network design, performance, diagnostics, and troubleshooting
- Enhanced Access to controller data
- CIP Safety setup and status

## Adapter vs scanner

- Adapter mode exposes robot I/O assemblies to an external scanner, commonly a
  PLC. This is the Amperesand baseline architecture.
- Scanner mode lets the robot initiate implicit connections to adapter devices.
- Connection points, assembly sizes, RPI, and electronic keying must match at
  both ends.

## Explicit messaging

The manual documents CIP objects for numeric registers, string registers,
position registers, current positions, recent alarm/status information, and
other controller data. Explicit messaging is separate from cyclic implicit I/O
and should not be used as a safety channel.

## CIP Safety

The appendix covers robot IP/Ethernet setup, CIP Safety parameters, connection
confirmation, operation, and status screens. For this repo, the authoritative
project configuration remains:

- EtherNet/IP adapter settings: `SYSEIP.SV`
- CIP Safety settings: `SYSCIPS.SV`
- DCS safety I/O settings: `DCSIOC.SV`
- Required safety payload: 4 bytes input and 4 bytes output

Use `docs/DCS_REFERENCE.md` for DCS validation and Safe I/O Connect guidance.

## Version boundary

This manual targets V9.10 and later. Confirm controller software and installed
options before applying any screen sequence or configuration detail to V8.30
or older controllers.

