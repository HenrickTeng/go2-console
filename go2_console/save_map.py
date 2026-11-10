"""Save the current /map occupancy grid. No Nav2 required."""

from array import array

import rclpy
from nav_msgs.msg import OccupancyGrid
from rclpy.node import Node

from .grid import Grid, save_ros_map
from pathlib import Path

OUT = Path("/home/henrick/go2_slam_ws/go2_console/data/map")


class Once(Node):
    def __init__(self) -> None:
        super().__init__("go2_save_map")
        self.msg = None
        self.create_subscription(OccupancyGrid, "map", self.on_map, 1)

    def on_map(self, msg: OccupancyGrid) -> None:
        self.msg = msg


def main() -> None:
    rclpy.init()
    node = Once()
    for _ in range(50):
        rclpy.spin_once(node, timeout_sec=0.2)
        if node.msg is not None:
            break
    if node.msg is None:
        raise SystemExit("没有收到 /map，建图进程还在吗？")
    msg = node.msg
    grid = Grid(
        int(msg.info.width),
        int(msg.info.height),
        float(msg.info.resolution),
        float(msg.info.origin.position.x),
        float(msg.info.origin.position.y),
        array("b", msg.data),
    )
    save_ros_map(grid, OUT)
    print(f"已保存 {OUT}/map.pgm 和 map.yaml，大小 {grid.width}×{grid.height}")
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
