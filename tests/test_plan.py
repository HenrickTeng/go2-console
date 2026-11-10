"""Planner and mission parser checks. No robot, no ROS."""

from array import array

from go2_console.grid import Grid, follow, plan
from go2_console.mission import parse_mission


def grid_with_wall() -> Grid:
    w, h = 40, 20
    data = array("b", [0]) * (w * h)
    for row in range(4, 16):
        data[row * w + 18] = 100
    return Grid(w, h, 0.1, 0.0, 0.0, data)


def test_plan_goes_around_wall():
    path = plan(grid_with_wall(), (0.5, 1.0), (3.0, 1.0))
    assert path, "expected a path"
    assert any(p[1] < 0.5 or p[1] > 1.6 for p in path)


def test_follow_stops_at_goal():
    vx, wz, done = follow((0.0, 0.0, 0.0), [(0.2, 0.0)], 0.2)
    assert done and vx == 0 and wz == 0


def test_mission_wait_between_places():
    parsed = parse_mission("先去饭堂，等人，再去教室", ["饭堂", "教室"])
    assert parsed["ok"]
    ops = [s["op"] for s in parsed["steps"]]
    assert ops == ["goto", "wait", "goto"]
    assert parsed["steps"][0]["place"] == "饭堂"
    assert parsed["steps"][2]["place"] == "教室"


def test_mission_without_places():
    parsed = parse_mission("先去饭堂", [])
    assert not parsed["ok"]


if __name__ == "__main__":
    test_plan_goes_around_wall()
    test_follow_stops_at_goal()
    test_mission_wait_between_places()
    test_mission_without_places()
    print("ok")
