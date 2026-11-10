"""Local teacher console. Listens on 127.0.0.1 only."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .grid import (
    follow,
    free_inside,
    inflate,
    load_ros_map,
    nearest_free,
    plan,
    point_in_polygon,
    save_ros_map,
    stamp_keepouts,
    to_png_bytes,
)
from .mission import parse_mission
from .ros_bridge import RosBridge

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
DATA = ROOT / "data"
MAP_DIR = DATA / "map"
SEMANTIC = DATA / "semantic.json"
EXAMPLE = ROOT / "examples" / "office"

MAX_SPEED = 0.30
NAV_SPEED = 0.22
INFLATE_M = 0.22


class Console:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.ros = RosBridge()
        self.estop = False
        self.teleop = False
        self.tele_twist = (0.0, 0.0, 0.0)
        self.tele_speed = 0.20
        self.places: list[dict] = []
        self.saved_grid = load_ros_map(self._map_dir())
        self.nav = {
            "active": False,
            "goal": None,
            "place": None,
            "path": [],
            "status": "空闲",
        }
        self.mission = {
            "active": False,
            "text": "",
            "steps": [],
            "index": 0,
            "status": "空闲",
            "log": [],
        }
        self._png = b""
        self._png_seq = 0
        self._token = None
        self._load_semantic()

    def start(self) -> None:
        DATA.mkdir(parents=True, exist_ok=True)
        self.ros.start()
        threading.Thread(target=self._loop, name="go2-console-loop", daemon=True).start()

    def _map_dir(self) -> Path:
        if (MAP_DIR / "map.yaml").is_file():
            return MAP_DIR
        return EXAMPLE

    def _semantic_path(self) -> Path:
        if SEMANTIC.is_file():
            return SEMANTIC
        return EXAMPLE / "semantic.json"

    def _load_semantic(self) -> None:
        path = self._semantic_path()
        if path.is_file():
            try:
                self.places = json.loads(path.read_text(encoding="utf-8")).get("places", [])
            except json.JSONDecodeError:
                self.places = []

    def _save_semantic(self) -> None:
        DATA.mkdir(parents=True, exist_ok=True)
        SEMANTIC.write_text(
            json.dumps({"places": self.places}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _grid(self):
        live, pose = self.ros.snapshot()
        return live or self.saved_grid, pose, live is not None

    def _loop(self) -> None:
        while True:
            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001
                with self.lock:
                    self.nav["status"] = f"控制循环出错：{exc}"
            time.sleep(0.1)

    def _tick(self) -> None:
        with self.lock:
            estop = self.estop
            teleop = self.teleop
            tele = self.tele_twist
            nav_on = self.nav["active"]
            mission_on = self.mission["active"]
            places = list(self.places)
        grid, pose, _live = self._grid()
        if estop or grid is None:
            self.ros.set_twist(0.0, 0.0, 0.0, False)
            return
        if teleop:
            vx, vy, wz = tele
            moving = abs(vx) + abs(vy) + abs(wz) > 1e-4
            self.ros.set_twist(vx, vy, wz, moving)
            return
        if not nav_on:
            self.ros.set_twist(0.0, 0.0, 0.0, False)
            return
        if pose is None:
            self.ros.set_twist(0.0, 0.0, 0.0, False)
            with self.lock:
                self.nav["status"] = "还没有 map→base_footprint，导航停着"
            return
        with self.lock:
            goal = self.nav["goal"]
            path = list(self.nav["path"])
        if goal is None:
            self.ros.stop()
            return
        if not path or int(time.time() * 10) % 10 == 0:
            path = self._plan_to(grid, pose, goal, places) or []
            with self.lock:
                self.nav["path"] = path
        if not path:
            self.ros.stop()
            with self.lock:
                self.nav["status"] = "规划失败：目标被挡住，或起点在障碍里"
                self.nav["active"] = False
                if mission_on:
                    self.mission["status"] = self.nav["status"]
                    self.mission["active"] = False
            return
        vx, wz, arrived = follow(pose, path, NAV_SPEED)
        if arrived:
            self.ros.stop()
            self._arrived()
            return
        self.ros.set_twist(vx, 0.0, wz, True)
        with self.lock:
            self.nav["status"] = "正在前往目标"

    def _arrived(self) -> None:
        with self.lock:
            place = self.nav.get("place")
            self.nav["active"] = False
            self.nav["status"] = "已到达" + (place or "目标点")
            if not self.mission["active"]:
                return
            step = self.mission["steps"][self.mission["index"]] if self.mission["index"] < len(self.mission["steps"]) else None
            self.mission["log"].append(self.nav["status"])
            if step and step["op"] == "goto":
                self.mission["index"] += 1
            self._advance_locked()

    def _advance_locked(self) -> None:
        steps = self.mission["steps"]
        while self.mission["index"] < len(steps):
            step = steps[self.mission["index"]]
            if step["op"] == "wait":
                self.mission["status"] = step.get("prompt") or "等待继续"
                self.nav["active"] = False
                return
            if step["op"] == "goto":
                goal = self._goal_for_place_locked(step["place"])
                if goal is None:
                    self.mission["status"] = f"找不到地点「{step['place']}」里的可走点"
                    self.mission["active"] = False
                    return
                self.nav.update(active=True, goal=goal, place=step["place"], path=[], status=f"前往{step['place']}")
                self.mission["status"] = f"前往{step['place']}"
                return
            self.mission["index"] += 1
        self.mission["status"] = "任务完成"
        self.mission["active"] = False
        self.mission["log"].append("任务完成")

    def _goal_for_place_locked(self, name: str):
        for place in self.places:
            if place.get("name") == name and place.get("kind") != "keepout":
                return place.get("goal")
        return None

    def _plan_to(self, grid, pose, goal, places):
        work = inflate(grid, INFLATE_M)
        keepouts = []
        for place in places:
            if place.get("kind") == "keepout":
                keepouts.append([(p[0], p[1]) for p in place.get("points", [])])
        stamp_keepouts(work, keepouts)
        return plan(work, (pose[0], pose[1]), (goal[0], goal[1]))

    def _token_of(self, grid) -> tuple:
        acc = 2166136261
        for value in grid.data:
            acc = ((acc ^ (value & 0xFF)) * 16777619) & 0xFFFFFFFF
        return (
            grid.width,
            grid.height,
            round(grid.origin_x, 2),
            round(grid.origin_y, 2),
            round(grid.resolution, 4),
            acc,
        )

    def _sync_map(self, grid) -> int:
        token = None if grid is None else self._token_of(grid)
        with self.lock:
            if token != self._token:
                self._token = token
                self._png_seq += 1
                self._png = b""
            return self._png_seq

    def state(self) -> dict:
        grid, pose, live = self._grid()
        seq = self._sync_map(grid)
        with self.lock:
            return {
                "ros": self.ros.ok,
                "ros_detail": self.ros.detail,
                "estop": self.estop,
                "teleop": self.teleop,
                "tele_speed": self.tele_speed,
                "twist": list(self.tele_twist),
                "live_map": live,
                "map": None
                if grid is None
                else {
                    "width": grid.width,
                    "height": grid.height,
                    "resolution": grid.resolution,
                    "origin_x": grid.origin_x,
                    "origin_y": grid.origin_y,
                    "seq": seq,
                },
                "pose": None if pose is None else {"x": pose[0], "y": pose[1], "yaw": pose[2]},
                "places": self.places,
                "nav": self.nav,
                "mission": self.mission,
            }

    def png(self) -> bytes:
        grid, _, _ = self._grid()
        if grid is None:
            return b""
        seq = self._sync_map(grid)
        with self.lock:
            cached = self._png
        if cached:
            return cached
        blob = to_png_bytes(grid)
        with self.lock:
            if self._png_seq == seq:
                self._png = blob
        return blob

    def set_teleop(self, on: bool) -> None:
        with self.lock:
            if on and (self.nav["active"] or self.mission["active"]):
                return
            self.teleop = on
            if not on:
                self.tele_twist = (0.0, 0.0, 0.0)
        if not on:
            self.ros.stop()

    def set_keys(self, keys: dict) -> None:
        with self.lock:
            if not self.teleop or self.estop:
                self.tele_twist = (0.0, 0.0, 0.0)
                return
            speed = self.tele_speed
            vx = (1 if keys.get("w") else 0) - (1 if keys.get("s") else 0)
            vy = (1 if keys.get("a") else 0) - (1 if keys.get("d") else 0)
            wz = (1 if keys.get("q") else 0) - (1 if keys.get("e") else 0)
            self.tele_twist = (vx * speed, vy * speed, wz * 0.6)

    def save_map(self) -> dict:
        grid, _, live = self._grid()
        if grid is None:
            return {"ok": False, "error": "还没有地图。先启动在线建图。"}
        save_ros_map(grid, MAP_DIR)
        self.saved_grid = grid.copy()
        self._save_semantic()
        return {"ok": True, "live": live, "width": grid.width, "height": grid.height}

    def set_places(self, places: list) -> dict:
        clean = []
        for item in places:
            name = str(item.get("name", "")).strip()
            points = item.get("points") or []
            if not name or len(points) < 3:
                continue
            kind = "keepout" if item.get("kind") == "keepout" else "place"
            poly = [(float(p[0]), float(p[1])) for p in points]
            goal = None
            if kind == "place":
                grid, _, _ = self._grid()
                if grid is not None:
                    hit = free_inside(grid, poly)
                    goal = list(hit) if hit else None
            clean.append({"name": name, "kind": kind, "points": poly, "goal": goal})
        with self.lock:
            self.places = clean
        self._save_semantic()
        return {"ok": True, "places": clean}

    def preview_goal(self, body: dict) -> dict:
        grid, pose, _ = self._grid()
        if grid is None:
            return {"ok": False, "error": "还没有地图"}
        if pose is None:
            return {"ok": False, "error": "还没有定位，只能先看地图，不能规划"}
        goal = self._resolve_goal(body, grid)
        if goal is None:
            return {"ok": False, "error": "目标不在可走区域里"}
        with self.lock:
            places = list(self.places)
        path = self._plan_to(grid, pose, goal, places)
        if not path:
            return {"ok": False, "error": "没有找到路"}
        return {"ok": True, "goal": list(goal), "path": path}

    def start_nav(self, body: dict) -> dict:
        with self.lock:
            if self.estop:
                return {"ok": False, "error": "急停中"}
            if self.mission["active"]:
                return {"ok": False, "error": "任务还在执行"}
            self.teleop = False
            self.tele_twist = (0.0, 0.0, 0.0)
        grid, pose, _ = self._grid()
        if grid is None or pose is None:
            return {"ok": False, "error": "需要地图和定位。建图程序要在跑。"}
        goal = self._resolve_goal(body, grid)
        if goal is None:
            return {"ok": False, "error": "目标不在可走区域里"}
        place = body.get("place")
        with self.lock:
            self.nav = {"active": True, "goal": list(goal), "place": place, "path": [], "status": "规划中"}
        return {"ok": True}

    def stop_motion(self) -> None:
        with self.lock:
            self.nav["active"] = False
            self.nav["status"] = "已停止"
            self.mission["active"] = False
            self.mission["status"] = "已停止"
            self.teleop = False
            self.tele_twist = (0.0, 0.0, 0.0)
        self.ros.stop()

    def start_mission(self, text: str) -> dict:
        with self.lock:
            if self.estop:
                return {"ok": False, "error": "急停中"}
            names = [p["name"] for p in self.places if p.get("kind") != "keepout"]
        parsed = parse_mission(text, names)
        if not parsed.get("ok"):
            return parsed
        with self.lock:
            self.teleop = False
            self.mission = {
                "active": True,
                "text": text,
                "steps": parsed["steps"],
                "index": 0,
                "status": "开始",
                "log": ["开始：" + text],
            }
            self._advance_locked()
        return {"ok": True, "steps": parsed["steps"]}

    def mission_continue(self, note: str = "") -> dict:
        with self.lock:
            if not self.mission["active"]:
                return {"ok": False, "error": "没有正在等待的任务"}
            steps = self.mission["steps"]
            idx = self.mission["index"]
            if idx >= len(steps) or steps[idx]["op"] != "wait":
                return {"ok": False, "error": "当前不是等待步骤"}
            if note:
                self.mission["log"].append("交互：" + note[:80])
            self.mission["index"] += 1
            self._advance_locked()
        return {"ok": True}

    def _resolve_goal(self, body: dict, grid):
        if body.get("place"):
            with self.lock:
                for place in self.places:
                    if place.get("name") == body["place"] and place.get("kind") != "keepout":
                        poly = [(p[0], p[1]) for p in place["points"]]
                        if place.get("goal"):
                            return tuple(place["goal"])
                        return free_inside(grid, poly)
            return None
        x, y = float(body["x"]), float(body["y"])
        return nearest_free(grid, x, y, 0.8)


CONSOLE = Console()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def _send(self, code: int, body: bytes, content: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _read(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        return json.loads(self.rfile.read(n).decode())

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/app.css":
            self._send(200, (STATIC / "app.css").read_bytes(), "text/css; charset=utf-8")
            return
        if path == "/app.js":
            self._send(200, (STATIC / "app.js").read_bytes(), "text/javascript; charset=utf-8")
            return
        if path == "/api/state":
            self._json(200, CONSOLE.state())
            return
        if path == "/map.png":
            blob = CONSOLE.png()
            if not blob:
                self._send(404, b"", "image/png")
                return
            self._send(200, blob, "image/png")
            return
        if path == "/cloud.bin":
            blob = CONSOLE.ros.cloud_blob()
            self._send(200, blob, "application/octet-stream")
            return
        self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            body = self._read()
        except json.JSONDecodeError:
            self._json(400, {"ok": False, "error": "请求不是 JSON"})
            return
        if path == "/api/estop":
            CONSOLE.estop = bool(body.get("on", True))
            if CONSOLE.estop:
                CONSOLE.stop_motion()
            self._json(200, {"ok": True, "estop": CONSOLE.estop})
            return
        if path == "/api/teleop":
            CONSOLE.set_teleop(bool(body.get("on")))
            if "speed" in body:
                CONSOLE.tele_speed = max(0.05, min(MAX_SPEED, float(body["speed"])))
            self._json(200, {"ok": True})
            return
        if path == "/api/keys":
            CONSOLE.set_keys(body.get("keys") or {})
            self._json(200, {"ok": True})
            return
        if path == "/api/map/save":
            self._json(200, CONSOLE.save_map())
            return
        if path == "/api/places":
            self._json(200, CONSOLE.set_places(body.get("places") or []))
            return
        if path == "/api/plan":
            self._json(200, CONSOLE.preview_goal(body))
            return
        if path == "/api/nav/start":
            self._json(200, CONSOLE.start_nav(body))
            return
        if path == "/api/nav/stop":
            CONSOLE.stop_motion()
            self._json(200, {"ok": True})
            return
        if path == "/api/mission/start":
            self._json(200, CONSOLE.start_mission(str(body.get("text") or "")))
            return
        if path == "/api/mission/continue":
            self._json(200, CONSOLE.mission_continue(str(body.get("note") or "")))
            return
        self._json(404, {"ok": False, "error": "没有这个接口"})


def main() -> None:
    CONSOLE.start()
    server = ThreadingHTTPServer(("127.0.0.1", 8770), Handler)
    print("Go2 控制台 http://127.0.0.1:8770", flush=True)
    server.serve_forever()
