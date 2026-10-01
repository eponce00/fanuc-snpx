# Controller file formats (research notes, 2026-10-01)

Written by a research sub-agent from public controller files; the implementation is
`src/fanuc_snpx/parsers.py` and the samples are in `vendor-docs/public-samples/` (sources and licenses in
`SOURCES.tsv` there). **Re-check every format against the test robot's own files** (TRACKER Phase 2 step 8).

Sources: real controllers V9.40P/60 (R-30iB Plus, SR-6iA SCARA) and V8.20P/A2 (R-30iB, 4 motion groups);
ROBOGUIDE V7.70P/36 (R-30iA) and V8.30P/48; onerobotics/go-fanuc test data (MIT, HTTP variant). No real
R-30iA numreg/posreg/sysframe/strreg was found.

## General

- Line endings: CRLF in `.va`/`.ls` straight from a controller backup; `.dg` were LF. Accept both.
- Encoding: plain ASCII, no BOM. Trailing spaces are common and significant for nothing; don't rely on them.
- Over HTTP (`/md/...`) files are wrapped in `<HTML>...<PRE>` (errall.ls also `<XMP>`); strip the tags.
- Variable header: `[*<PROG>*]$NAME  Storage: <SHADOW|CMOS|...>  Access: <RW|RO|FP>  : <TYPE>` optionally
  followed by `= value`; each block ends with a blank line.
- Uninitialized values are the bare word `Uninitialized`.

## numreg.va

```
[*NUMREG*]$NUMREG  Storage: SHADOW  Access: RW  : ARRAY[200] OF Numeric Reg
  [1] = 1  'Chip 1' 
  [51] = -2.700000  'X STEP' 
[*NUMREG*]$MAXREGNUM  Storage: CMOS  Access: RW  : INTEGER = 200
```
Integers bare; reals fixed with 6 decimals (no exponent). Whether |x| < 1 prints with a leading zero is not
seen in samples (parser accepts both). Comments in single quotes, inner padding preserved.

## posreg.va

```
[*POSREG*]$POSREG  Storage: SHADOW  Access: RW  : ARRAY[4,100] OF Position Reg
    [1,1] =   'HOME'   Group: 1
  J1 =    60.000 deg   J2 =   -50.000 deg   J3 =    10.000 deg 
  J4 =   -10.000 deg   J5 =   -95.000 deg   J6 =   -50.000 deg 

    [1,2] =   '' Uninitialized
    [1,10] =   '' 
  Group: 1   Config: N U T, 0, 0, 0
  X:  -820.507   Y:  2309.503   Z:   945.836
  W:    90.594   P:    -1.149   R:  -160.248
  EXT1:  ******** mm  
```
- Joint PRs: `Group:` on the element line, values 3 per line, `deg` or `mm`, then a blank line.
- Cartesian PRs: `Group: g   Config: <cfg>, t4, t5, t6`, X/Y/Z line, W/P/R line, optional `EXT1:` lines;
  no blank line after. No UF/UT in the file.
- Numbers `%9.3f`-like with the leading zero dropped for |x| < 1 (`.800`, `-.364`); `********` for
  overflow/uninitialized axes.
- Config strings seen: `N U T`, `N D B`, `F U T`, SCARA `R` (e.g. `R, 0, 0, -1`).

## sysframe.va

`$CELL_FLOOR`, `$CELL_GRP` (struct array with `Field:` lines), `$MNUFRAME`, `$MNUFRAMENUM`, `$MNUTOOL`,
`$MNUTOOLNUM`. Elements `    [g,i] = ` (no comment) followed by the Cartesian block. Non-robot groups print
`Group: 2   Config: ` (empty config).

## system.va (`$SNPX_ASG`, `$SNPX_PARAM`)

There is no `syssnpx.va`; these variables are in `system.va` (no file header; starts with the first block).

```
[*SYSTEM*]$SNPX_ASG  Storage: CMOS  Access: RW  : ARRAY[80] OF SNPX_ASG_T
     Field: $SNPX_ASG[1].$ADDRESS Access: RW: SHORT = 1
     Field: $SNPX_ASG[1].$SIZE Access: RW: SHORT = 10000
     Field: $SNPX_ASG[1].$VAR_NAME Access: RW: STRING[37] = 'R[1]@1.1'
     Field: $SNPX_ASG[1].$MULTIPLY Access: RW: REAL = 1.000000e+00

[*SYSTEM*]$SNPX_PARAM  Storage: CMOS  Access: FP  : SNPX_PARAM_T = 
   Field: $SNPX_PARAM.$TIMEOUT Access: RW: INTEGER = 5000
   Field: $SNPX_PARAM.$NUM_CIMP Access: RW: INTEGER = 0
   Field: $SNPX_PARAM.$NUM_FRIF Access: RW: INTEGER = 4
   Field: $SNPX_PARAM.$VERSION Access: RO: INTEGER = 2
```
- `Field: <full path> Access: <RW|RO>: <TYPE> = <value>`; reals in `%e` form here; BOOLEAN `TRUE`/`FALSE`;
  strings single-quoted, `''` empty, `Uninitialized` unset. Array fields print as
  `Field: $A[1].$B  ARRAY[2] OF REAL` followed by `[1] = ...` lines.
- V8.20 `$SNPX_PARAM` ends at `$MODBUS_PORT` (no `$CMD_ENDIAN`, `$COMP_FLAG`, `$DUMMY13/14`).
- All three public samples hold the factory table (only slot 1 = `R[1]@1.1`) and `$NUM_CIMP = 0`.

## strreg.va

Only one public example, with every register empty:
```
[*STRREG*]$STRREG  Storage: SHADOW  Access: RW  : ARRAY[25] OF String Reg
  [1] =   '' 
```
Whether the quoted field is the value or the comment, and how a stored value prints, is unknown → no parser
until the robot's file is available.

## curpos.dg (same layout as section 7 of summary.dg)

```
F Number: E210325  
VERSION : LR HandlingTool      
$VERSION: V9.40373      5/31/2024
DATE:     04-JUL-28 16:04 

CURRENT ROBOT POSITION::
Group #:  1
 
CURRENT JOINT POSITION:
Joint   1:     19.62
...
Frame #:   8  Tool #:   8
CURRENT USER FRAME POSITION:
CFG: R, 0, 0, 0
X:    281.58
...
Tool #:   8
CURRENT WORLD POSITION:
CFG: R, 0, 0, 0
X:    281.58
```
Two decimals, leading zero dropped for small values. V7.70 prints `CFG:` after the R line and narrower
`Frame #:` fields; match with flexible whitespace.

## errall.ls

```
ERRALL.LS      Robot Name ROBOT 04-JUL-28 16:04:36  

47125" 04-JUL-28 16:02:52 " SRVO-003 Deadman switch released                  " " SERVO                         00110110" act"
```
Split on `"`: sequence, time, message (padded to 50), cause (single space if empty), severity padded to 30
followed by an 8-bit mask, `act` or spaces. V7.70 timestamps have no seconds. Newest first.

## iostate.dg

Same 4-line header as curpos.dg, then `IO STATUS::` and lines such as `DIN[  81]  ON  Hold`,
`GIN[   1]    0  RecipeReadData`, `UI[   1] OFF  *IMSTP`; `FLG[...]` lines come in two columns.
