# Access policy file (schema version 1)

The policy is a local JSON file next to your application, by default `./robot-policy.local.json`, or the
path in `$FANUC_SNPX_POLICY`, or a path given to `SnpxClient(policy=...)` / `--policy`. It is yours and
must not be committed (`*.local.json` is git-ignored).

Without a policy, every write is refused. With one, a write needs: not `protected`, inside `writable`,
within `limits`, the right `controller.host`, and a reason. See [SAFETY.md](SAFETY.md).

## Example (generic values; replace with your own)

```json
{
  "version": 1,
  "description": "Example cell - scratch targets agreed with the cell owner on 2026-10-01",
  "controller": { "host": "192.0.2.10" },
  "protected": {
    "numeric_registers": [[1, 99]],
    "position_registers": [[1, 100]],
    "system_variables": ["$SNPX_*", "$MNUFRAME[1,1]"],
    "digital_outputs": "*",
    "group_outputs": "*",
    "digital_inputs": "*",
    "group_inputs": "*"
  },
  "writable": {
    "numeric_registers": [[190, 199]],
    "position_registers": [[490, 499]],
    "system_variables": ["$MNUFRAME[1,9]", "$MNUTOOL[1,9]"],
    "comments.numeric_registers": [[190, 199]],
    "snpx_assignments": [[5001, 6000]]
  },
  "limits": {
    "max_numeric_register": 200,
    "max_position_register": 500,
    "max_string_register": 25
  },
  "assignments": { "r_block": [5001, 6000] }
}
```

## Fields

| Field | Meaning |
|---|---|
| `version` | must be `1` |
| `description` | free text |
| `controller.host` | optional; the policy is refused for any other host |
| `protected.<kind>` | `"*"` or a list of `N` / `[first, last]` (inclusive); never writable |
| `writable.<kind>` | list of ranges; `"*"` is not allowed here |
| `limits.<key>` | highest valid index; enforced on reads and writes |
| `assignments.r_block` | `[first, last]` %R addresses the assignment planner may use |

Kinds with integer indexes: `numeric_registers`, `position_registers`, `string_registers`, `flags`,
`digital_inputs`, `digital_outputs`, `robot_inputs`, `robot_outputs`, `uop_inputs`, `uop_outputs`,
`sop_inputs`, `sop_outputs`, `weld_inputs`, `weld_outputs`, `wire_stick_inputs`, `wire_stick_outputs`,
`group_inputs`, `group_outputs`, `analog_inputs`, `analog_outputs`, `snpx_assignments` (%R addresses), and
`comments.<kind>` for comments of any of those.

Kind with names: `system_variables`. Entries are full names (`$MNUFRAME[1,2]`) or patterns where only `*`
and `?` are wildcards; brackets match literally; case is ignored.

Limit keys: `max_numeric_register`, `max_position_register`, `max_string_register`, `max_flag`,
`max_digital_input`, `max_digital_output`, `max_group_input`, `max_group_output`.

## Advice

- Protect every signal a PLC or the robot program owns. Writable I/O should be signals nobody else drives.
- Keep writable ranges small and named in `description` with the date and who agreed them.
- Bind the policy to the controller with `controller.host`.
