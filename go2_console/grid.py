"""Occupancy grid, region geometry, and grid path planning."""

from __future__ import annotations

import heapq
import math
from array import array
from dataclasses import dataclass


@dataclass
class Grid:
    width: int
    height: int
    resolution: float
    origin_x: float
    origin_y: float
    data: array  # signed bytes, ROS occupancy: -1 unknown, 0 free, 100 occupied

    def copy(self) -> "Grid":
        return Grid(
            self.width,
            self.height,
            self.resolution,
            self.origin_x,
            self.origin_y,
            array("b", self.data),
        )


def world_to_cell(grid: Grid, x: float, y: float) -> tuple[int, int]:
    col = int((x - grid.origin_x) / grid.resolution)
    row = int((y - grid.origin_y) / grid.resolution)
    return col, row


def cell_to_world(grid: Grid, col: int, row: int) -> tuple[float, float]:
    x = grid.origin_x + (col + 0.5) * grid.resolution
    y = grid.origin_y + (row + 0.5) * grid.resolution
    return x, y


def in_bounds(grid: Grid, col: int, row: int) -> bool:
    return 0 <= col < grid.width and 0 <= row < grid.height


def cell_blocked(grid: Grid, col: int, row: int) -> bool:
    if not in_bounds(grid, col, row):
        return True
    return grid.data[row * grid.width + col] != 0


def inflate(grid: Grid, radius_m: float) -> Grid:
    cells = max(0, int(round(radius_m / grid.resolution)))
    if cells == 0:
        return grid.copy()
    src = grid.data
    out = array("b", src)
    w, h = grid.width, grid.height
    blocked = []
    for row in range(h):
        base = row * w
        for col in range(w):
            if src[base + col] != 0:
                blocked.append((col, row))
    for col, row in blocked:
        r0 = max(0, row - cells)
        r1 = min(h - 1, row + cells)
        c0 = max(0, col - cells)
        c1 = min(w - 1, col + cells)
        for rr in range(r0, r1 + 1):
            base = rr * w
            for cc in range(c0, c1 + 1):
                if out[base + cc] == 0:
                    out[base + cc] = 100
    return Grid(w, h, grid.resolution, grid.origin_x, grid.origin_y, out)


def stamp_keepouts(grid: Grid, polygons: list[list[tuple[float, float]]]) -> None:
    if not polygons:
        return
    for row in range(grid.height):
        y = grid.origin_y + (row + 0.5) * grid.resolution
        base = row * grid.width
        for col in range(grid.width):
            if grid.data[base + col] != 0:
                continue
            x = grid.origin_x + (col + 0.5) * grid.resolution
            for poly in polygons:
                if point_in_polygon(x, y, poly):
                    grid.data[base + col] = 100
                    break


def point_in_polygon(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    if len(poly) < 3:
        return False
    inside = False
    j = len(poly) - 1
    for i, (xi, yi) in enumerate(poly):
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def polygon_centroid(poly: list[tuple[float, float]]) -> tuple[float, float]:
    if not poly:
        return 0.0, 0.0
    return sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly)


def nearest_free(grid: Grid, x: float, y: float, max_m: float = 1.2) -> tuple[float, float] | None:
    col, row = world_to_cell(grid, x, y)
    if in_bounds(grid, col, row) and not cell_blocked(grid, col, row):
        return cell_to_world(grid, col, row)
    reach = int(math.ceil(max_m / grid.resolution))
    best = None
    best_d = 1e9
    for rr in range(row - reach, row + reach + 1):
        for cc in range(col - reach, col + reach + 1):
            if cell_blocked(grid, cc, rr):
                continue
            wx, wy = cell_to_world(grid, cc, rr)
            d = (wx - x) ** 2 + (wy - y) ** 2
            if d < best_d:
                best_d = d
                best = (wx, wy)
    return best


def free_inside(grid: Grid, poly: list[tuple[float, float]]) -> tuple[float, float] | None:
    cx, cy = polygon_centroid(poly)
    col, row = world_to_cell(grid, cx, cy)
    if in_bounds(grid, col, row) and not cell_blocked(grid, col, row) and point_in_polygon(
        *cell_to_world(grid, col, row), poly
    ):
        return cell_to_world(grid, col, row)
    best = None
    best_d = 1e9
    # Search a box around the centroid, keep the closest free cell still inside the polygon.
    reach = max(8, int(4.0 / grid.resolution))
    for rr in range(max(0, row - reach), min(grid.height, row + reach + 1)):
        y = grid.origin_y + (rr + 0.5) * grid.resolution
        for cc in range(max(0, col - reach), min(grid.width, col + reach + 1)):
            if cell_blocked(grid, cc, rr):
                continue
            x = grid.origin_x + (cc + 0.5) * grid.resolution
            if not point_in_polygon(x, y, poly):
                continue
            d = (x - cx) ** 2 + (y - cy) ** 2
            if d < best_d:
                best_d = d
                best = (x, y)
    return best


def plan(grid: Grid, start: tuple[float, float], goal: tuple[float, float]) -> list[tuple[float, float]] | None:
    sc, sr = world_to_cell(grid, start[0], start[1])
    gc, gr = world_to_cell(grid, goal[0], goal[1])
    if cell_blocked(grid, sc, sr):
        snapped = nearest_free(grid, start[0], start[1], 0.6)
        if snapped is None:
            return None
        sc, sr = world_to_cell(grid, snapped[0], snapped[1])
    if cell_blocked(grid, gc, gr):
        snapped = nearest_free(grid, goal[0], goal[1], 0.8)
        if snapped is None:
            return None
        gc, gr = world_to_cell(grid, snapped[0], snapped[1])
    w = grid.width
    start_i = sr * w + sc
    goal_i = gr * w + gc
    if start_i == goal_i:
        return [cell_to_world(grid, gc, gr)]

    open_heap = [(0.0, start_i)]
    came: dict[int, int] = {}
    cost = {start_i: 0.0}
    seen = set()
    while open_heap:
        _, cur = heapq.heappop(open_heap)
        if cur in seen:
            continue
        seen.add(cur)
        if cur == goal_i:
            break
        col, row = cur % w, cur // w
        for dc, dr, step in (
            (1, 0, 1.0),
            (-1, 0, 1.0),
            (0, 1, 1.0),
            (0, -1, 1.0),
            (1, 1, 1.414),
            (1, -1, 1.414),
            (-1, 1, 1.414),
            (-1, -1, 1.414),
        ):
            nc, nr = col + dc, row + dr
            if cell_blocked(grid, nc, nr):
                continue
            if dc and dr and (cell_blocked(grid, col + dc, row) or cell_blocked(grid, col, row + dr)):
                continue
            nxt = nr * w + nc
            ncost = cost[cur] + step
            if ncost < cost.get(nxt, 1e18):
                cost[nxt] = ncost
                came[nxt] = cur
                heur = math.hypot(nc - gc, nr - gr)
                heapq.heappush(open_heap, (ncost + heur, nxt))
    if goal_i not in came and start_i != goal_i:
        return None
    cells = [goal_i]
    while cells[-1] != start_i:
        cells.append(came[cells[-1]])
    cells.reverse()
    pts = [cell_to_world(grid, i % w, i // w) for i in cells]
    return _simplify(pts)


def _simplify(pts: list[tuple[float, float]], tol: float = 0.08) -> list[tuple[float, float]]:
    if len(pts) < 3:
        return pts
    return _rdp(pts, tol)


def _rdp(pts: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    if len(pts) < 3:
        return pts
    ax, ay = pts[0]
    bx, by = pts[-1]
    span = math.hypot(bx - ax, by - ay) or 1.0
    worst_i = 1
    worst = -1.0
    for i in range(1, len(pts) - 1):
        px, py = pts[i]
        dist = abs((px - ax) * (by - ay) - (py - ay) * (bx - ax)) / span
        if dist > worst:
            worst = dist
            worst_i = i
    if worst <= tol:
        return [pts[0], pts[-1]]
    return _rdp(pts[: worst_i + 1], tol)[:-1] + _rdp(pts[worst_i:], tol)


def follow(pose: tuple[float, float, float], path: list[tuple[float, float]], speed: float) -> tuple[float, float, bool]:
    """Return vx, wz, arrived. Body frame: +x forward."""
    if not path:
        return 0.0, 0.0, True
    x, y, yaw = pose
    gx, gy = path[-1]
    if math.hypot(gx - x, gy - y) < 0.35:
        return 0.0, 0.0, True
    # Look ahead about 0.6 m along the polyline.
    target = path[-1]
    remain = 0.6
    prev = (x, y)
    for pt in path:
        seg = math.hypot(pt[0] - prev[0], pt[1] - prev[1])
        if seg >= remain:
            if seg < 1e-6:
                target = pt
            else:
                t = remain / seg
                target = (prev[0] + t * (pt[0] - prev[0]), prev[1] + t * (pt[1] - prev[1]))
            break
        remain -= seg
        prev = pt
        target = pt
    err = _wrap(math.atan2(target[1] - y, target[0] - x) - yaw)
    if abs(err) > 0.7:
        return 0.0, max(-0.5, min(0.5, 0.9 * err)), False
    vx = speed * max(0.25, math.cos(err))
    wz = max(-0.6, min(0.6, 1.2 * err))
    return vx, wz, False


def _wrap(a: float) -> float:
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a


def to_png_bytes(grid: Grid) -> bytes:
    # Local import so planner tests do not need Pillow until a map is shown.
    from PIL import Image

    img = Image.new("RGB", (grid.width, grid.height))
    pix = img.load()
    w = grid.width
    free = (244, 239, 228)
    occupied = (47, 58, 72)
    unknown = (196, 204, 212)
    for row in range(grid.height):
        # Image row 0 is the top; ROS row 0 is the bottom (origin).
        src = grid.height - 1 - row
        base = src * w
        for col in range(w):
            v = grid.data[base + col]
            if v < 0:
                pix[col, row] = unknown
            elif v >= 50:
                pix[col, row] = occupied
            else:
                pix[col, row] = free
    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def save_ros_map(grid: Grid, directory, stem: str = "map") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    pgm = directory / f"{stem}.pgm"
    # map_server: occupied -> 0, free -> 254, unknown -> 205, negate 0.
    raw = bytearray(grid.width * grid.height)
    for row in range(grid.height):
        src = grid.height - 1 - row
        for col in range(grid.width):
            v = grid.data[src * grid.width + col]
            if v < 0:
                raw[row * grid.width + col] = 205
            else:
                raw[row * grid.width + col] = int(round((100 - max(0, min(100, v))) * 2.54))
    header = f"P5\n{grid.width} {grid.height}\n255\n".encode()
    pgm.write_bytes(header + bytes(raw))
    yaml = directory / f"{stem}.yaml"
    yaml.write_text(
        "\n".join(
            [
                f"image: {stem}.pgm",
                f"resolution: {grid.resolution:.6f}",
                f"origin: [{grid.origin_x:.6f}, {grid.origin_y:.6f}, 0.0]",
                "negate: 0",
                "occupied_thresh: 0.65",
                "free_thresh: 0.25",
                "",
            ]
        ),
        encoding="utf-8",
    )


def load_ros_map(directory, stem: str = "map") -> Grid | None:
    yaml_path = directory / f"{stem}.yaml"
    pgm = directory / f"{stem}.pgm"
    if not yaml_path.is_file() or not pgm.is_file():
        return None
    meta = {}
    for line in yaml_path.read_text(encoding="utf-8").splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip()
    resolution = float(meta["resolution"])
    origin = meta["origin"].strip("[]")
    ox, oy, _ = [float(p) for p in origin.split(",")]
    blob = pgm.read_bytes()
    if not blob.startswith(b"P5"):
        return None
    # Skip header lines.
    parts = blob.split(b"\n", 3)
    if len(parts) < 4:
        return None
    wh = parts[1].split()
    width, height = int(wh[0]), int(wh[1])
    raw = parts[3]
    if len(raw) < width * height:
        return None
    data = array("b", [0]) * (width * height)
    for row in range(height):
        src = height - 1 - row
        for col in range(width):
            pix = raw[row * width + col]
            dst = src * width + col
            if pix >= 250:
                data[dst] = 0
            elif pix <= 40:
                data[dst] = 100
            else:
                data[dst] = -1
    return Grid(width, height, resolution, ox, oy, data)


def yaw_of(qx: float, qy: float, qz: float, qw: float) -> float:
    siny = 2.0 * (qw * qz + qx * qy)
    cosy = 1.0 - 2.0 * (qy * qy + qz * qz)
    return math.atan2(siny, cosy)
