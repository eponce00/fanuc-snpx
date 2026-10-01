"""Run the example adapter against the fake controller (no robot needed).

python examples/demo_fake.py
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from adapter import RobotAdapter, summarize
from fanuc_snpx import AccessPolicy, AssignmentTable, Cartesian
from fanuc_snpx.errors import WriteVetoed
from fanuc_snpx.srtp import RateLimiter, SrtpSession
from fanuc_snpx.testing import (
    FakeAssignment,
    FakeController,
    FakeSrtpServer,
    position_image,
)

HERE = Path(__file__).resolve().parent


def seeded_controller(table: AssignmentTable) -> FakeController:
    """A fake controller whose $SNPX_ASG matches the example map, with some data in it."""
    ctl = FakeController()
    for e in table:
        assert e.slot is not None
        ctl.assign(e.slot, FakeAssignment(e.address, e.size, str(e.var), e.multiply))
    ctl.numeric_registers.update({1: 3, 2: 12.5})
    ctl.position_registers[1] = position_image(
        xyzwpr=(400.0, 0.0, 300.0, 180.0, 0.0, 0.0), joints=(0.0, 10.0, -20.0, 0.0, -70.0, 0.0)
    )
    ctl.sysvars["$MNUFRAME[1,1]"] = position_image(xyzwpr=(500.0, -200.0, 0.0, 0.0, 0.0, 90.0))
    ctl.sysvars["$MNUTOOL[1,1]"] = position_image(xyzwpr=(0.0, 0.0, 120.0, 0.0, 0.0, 0.0))
    ctl.current_position[(1, 0)] = position_image(
        xyzwpr=(410.0, 5.0, 295.0, 180.0, 0.0, 1.0), joints=(1.0, 11.0, -19.0, 0.0, -71.0, 1.0)
    )
    ctl.words[0x0A][0:2] = (7).to_bytes(2, "little")  # %AI1 = GO[1] = 7
    return ctl


def main() -> dict[str, object]:
    table = AssignmentTable.load(HERE / "asg-map.example.json")
    policy = AccessPolicy.load(HERE / "robot-policy.example.json")
    out: dict[str, object] = {}
    with FakeSrtpServer(seeded_controller(table)) as srv:
        # The example policy is bound to a documentation address; rebind it to the fake.
        policy = dataclasses.replace(policy, controller_host=srv.host)
        session = SrtpSession(srv.host, srv.port, rate_limiter=RateLimiter(1000))
        with RobotAdapter(srv.host, table, policy=policy, session=session) as adapter:
            out["R[1..2]"] = adapter.read_numerics(1, 2)
            out["PR[1..2]"] = summarize(adapter.read_positions(1, 2))
            pose = adapter.read_current_world_position_with_joints()
            out["pose"] = {"xyzwpr": pose.xyzwpr, "joints": pose.joints}
            out["GO[1]"] = adapter.read_group_output(1)
            out["UFRAME 1"] = adapter.read_user_frame(1).xyzwpr
            # A small, intended frame correction passes the guard ...
            small = Cartesian(501.0, -200.0, 0.0, 0.0, 0.0, 90.0, None)
            adapter.write_user_frame(1, small, reason="demo: 1 mm correction")
            out["UFRAME 1 after"] = adapter.read_user_frame(1).xyzwpr
            # ... a large one is vetoed unless the caller says it is expected.
            try:
                big = Cartesian(600.0, -200.0, 0.0, 0.0, 0.0, 90.0, None)
                adapter.write_user_frame(1, big, reason="demo: 99 mm jump")
                out["large change"] = "written"
            except WriteVetoed as exc:
                out["large change"] = f"vetoed: {exc}"
            out["R[20] old"] = adapter.write_numeric(20, 1.5, reason="demo scratch register")
            out["R[20]"] = adapter.read_numeric(20)
    return out


if __name__ == "__main__":
    print(json.dumps(main(), indent=2))
