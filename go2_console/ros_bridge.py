"""Optional ROS 2 side: /map, map->base_footprint, cmd_vel, and the lidar cloud.

Publishes a twist only while teleop or navigation is asking for one.
Idle does not publish, so the sport bridge is not kept in BalanceStand.
"""

from __future__ import annotations

import math
import struct
import threading
from array import array

from .grid import Grid, yaw_of

_VOXEL = 0.06
_MAX_VOXELS = 60000


class RosBridge:
    def __init__(self) -> None:
        self.ok = False
        self.detail = "尚未连接 ROS"
        self._lock = threading.Lock()
        self._grid: Grid | None = None
        self._pose: tuple[float, float, float] | None = None
        self._twist = (0.0, 0.0, 0.0)
        self._publish = False
        self._node = None
        self._thread: threading.Thread | None = None
        self._voxels: dict[tuple[int, int, int], tuple[float, float, float]] = {}
        self._live: list[tuple[float, float, float]] = []
        self._odom_pose = (0.0, 0.0, 0.0, 0.0)
        self._cloud_seq = 0

    def start(self) -> None:
        oper = "/sys/class/net/enp7s0/operstate"
        try:
            state = open(oper, encoding="utf-8").read().strip()
        except OSError:
            state = ""
        if state != "up":
            self.detail = "Go2 网线没通。页面可以先用；接上狗后重新打开控制台，才能建图和导航。"
            return
        # rclpy.init() calls exit() when Cyclone cannot be loaded. Check first.
        try:
            import ctypes

            ctypes.CDLL("librmw_cyclonedds_cpp.so")
        except OSError as exc:
            self.detail = f"未接入 ROS（Cyclone 库没加载）：{exc}"
            return
        try:
            import rclpy
            from geometry_msgs.msg import Twist
            from nav_msgs.msg import OccupancyGrid
            from rclpy.node import Node
            from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
            from sensor_msgs.msg import PointCloud2
            from tf2_ros import Buffer, TransformListener
        except Exception as exc:  # noqa: BLE001
            self.detail = f"ROS 不可用：{exc}"
            return
        try:
            if not rclpy.ok():
                rclpy.init()
        except Exception as exc:  # noqa: BLE001
            self.detail = f"ROS 初始化失败：{exc}"
            return

        bridge = self

        class _Node(Node):
            def __init__(self) -> None:
                super().__init__("go2_console")
                self.pub = self.create_publisher(Twist, "cmd_vel", 10)
                self.create_subscription(OccupancyGrid, "map", self.on_map, 1)
                cloud_qos = QoSProfile(
                    reliability=ReliabilityPolicy.BEST_EFFORT,
                    history=HistoryPolicy.KEEP_LAST,
                    depth=5,
                )
                self.create_subscription(PointCloud2, "/utlidar/cloud_deskewed", self.on_cloud, cloud_qos)
                self.buf = Buffer()
                self.listener = TransformListener(self.buf, self)
                self.create_timer(0.1, self.on_tick)

            def on_map(self, msg: OccupancyGrid) -> None:
                if len(msg.data) != int(msg.info.width) * int(msg.info.height):
                    return
                data = array("b", msg.data)
                grid = Grid(
                    int(msg.info.width),
                    int(msg.info.height),
                    float(msg.info.resolution),
                    float(msg.info.origin.position.x),
                    float(msg.info.origin.position.y),
                    data,
                )
                with bridge._lock:
                    bridge._grid = grid

            def on_cloud(self, msg: PointCloud2) -> None:
                points = _read_xyz(msg)
                if not points:
                    return
                stride = max(1, len(points) // 6000)
                live = points[::stride][:5000]
                with bridge._lock:
                    voxels = bridge._voxels
                    for x, y, z in points[::stride]:
                        key = (int(math.floor(x / _VOXEL)), int(math.floor(y / _VOXEL)), int(math.floor(z / _VOXEL)))
                        if key in voxels or len(voxels) < _MAX_VOXELS:
                            voxels[key] = (x, y, z)
                    bridge._live = live
                    bridge._cloud_seq += 1

            def on_tick(self) -> None:
                try:
                    tf = self.buf.lookup_transform("map", "base_footprint", rclpy.time.Time())
                    t = tf.transform.translation
                    q = tf.transform.rotation
                    pose = (t.x, t.y, yaw_of(q.x, q.y, q.z, q.w))
                    with bridge._lock:
                        bridge._pose = pose
                except Exception:
                    pass
                try:
                    tf = self.buf.lookup_transform("odom", "base_footprint", rclpy.time.Time())
                    t = tf.transform.translation
                    q = tf.transform.rotation
                    with bridge._lock:
                        bridge._odom_pose = (t.x, t.y, t.z, yaw_of(q.x, q.y, q.z, q.w))
                except Exception:
                    pass
                with bridge._lock:
                    publish = bridge._publish
                    vx, vy, wz = bridge._twist
                if not publish:
                    return
                msg = Twist()
                msg.linear.x = float(vx)
                msg.linear.y = float(vy)
                msg.angular.z = float(wz)
                self.pub.publish(msg)

        try:
            self._node = _Node()
        except Exception as exc:  # noqa: BLE001
            self.detail = f"节点创建失败：{exc}"
            return
        self.ok = True
        self.detail = "已接入 /map、点云、TF 和 cmd_vel"

        def spin() -> None:
            rclpy.spin(self._node)

        self._thread = threading.Thread(target=spin, name="go2-console-ros", daemon=True)
        self._thread.start()

    def snapshot(self) -> tuple[Grid | None, tuple[float, float, float] | None]:
        with self._lock:
            grid = self._grid.copy() if self._grid is not None else None
            return grid, self._pose

    def set_twist(self, vx: float, vy: float, wz: float, publish: bool) -> None:
        with self._lock:
            self._twist = (vx, vy, wz)
            self._publish = publish

    def stop(self) -> None:
        self.set_twist(0.0, 0.0, 0.0, False)

    def cloud_blob(self) -> bytes:
        with self._lock:
            acc = list(self._voxels.values())
            live = list(self._live)
            pose = self._odom_pose
            seq = self._cloud_seq
        if len(acc) > 50000:
            acc = acc[:: max(1, len(acc) // 50000)]
        flat_acc = array("f", [v for p in acc for v in p])
        flat_live = array("f", [v for p in live for v in p])
        header = struct.pack("<III4f", seq, len(acc), len(live), *pose)
        return header + flat_acc.tobytes() + flat_live.tobytes()


def _read_xyz(msg) -> list[tuple[float, float, float]]:
    offsets = {}
    for field in msg.fields:
        if field.name in ("x", "y", "z") and field.datatype == 7:
            offsets[field.name] = field.offset
    if len(offsets) < 3:
        return []
    step = int(msg.point_step)
    count = int(msg.width) * int(msg.height)
    raw = memoryview(msg.data)
    ox, oy, oz = offsets["x"], offsets["y"], offsets["z"]
    out = []
    for i in range(count):
        base = i * step
        x = struct.unpack_from("<f", raw, base + ox)[0]
        y = struct.unpack_from("<f", raw, base + oy)[0]
        z = struct.unpack_from("<f", raw, base + oz)[0]
        if x != x or y != y or z != z:
            continue
        if z < -1.0 or z > 2.8 or abs(x) > 40 or abs(y) > 40:
            continue
        out.append((x, y, z))
    return out
