# Examples

| File | What it shows |
|---|---|
| `adapter.py` | An application adapter: named operations (registers, PRs, frames/tools, current pose with joints, group I/O, comments) on top of `SnpxClient`, plus a `write_guard` that vetoes large frame/tool changes unless the caller says they are expected |
| `demo_fake.py` | Runs the adapter against `fanuc_snpx.testing.FakeSrtpServer`: `python examples/demo_fake.py` (no robot needed; also run by `tests/test_examples.py`) |
| `asg-map.example.json` | An assignment map (the controller's `$SNPX_ASG` entries the client reads through). Slot 1 is left to the factory default (`R[1]@1.1` over %R1..%R10000), so the example starts at %R10001. Produce your own with `fanuc-snpx plan-asg` |
| `robot-policy.example.json` | An access policy with generic values. Copy it to `robot-policy.local.json` (git-ignored), bind it to your controller and list only the scratch targets agreed with the cell owner |

Using the adapter against a real controller (read-only unless a policy is given):

```python
from adapter import RobotAdapter
from fanuc_snpx import AssignmentTable

table = AssignmentTable.load("my-asg-map.json")
with RobotAdapter("192.0.2.10", table) as robot:
    print(robot.read_numerics(1, 10))
    print(robot.read_current_world_position_with_joints())
```
