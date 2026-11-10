const canvas = document.getElementById("map");
const ctx = canvas.getContext("2d");
const keys = {};
let state = null;
let tab = "mapping";
let draft = [];
let clickGoal = null;
let preview = null;
let shown = new Image();
let shownReady = false;
let imgSeq = -1;
let loadingSeq = -1;
let holdMap = null;
let zoom = 1;
let panX = 0;
let panY = 0;

function $(id) { return document.getElementById(id); }

document.querySelectorAll("nav button").forEach((btn) => {
  btn.addEventListener("click", () => {
    tab = btn.dataset.tab;
    document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b === btn));
    ["mapping", "annotate", "nav", "mission"].forEach((name) => {
      $("panel-" + name).classList.toggle("hidden", name !== tab);
    });
    draft = [];
    document.body.classList.toggle("view-3d", tab === "mapping");
    if (tab === "annotate" && state && state.map && shownReady) {
      holdMap = { meta: state.map, image: shown };
    } else {
      holdMap = null;
    }
    draw();
  });
});

async function post(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return res.json();
}

$("estop").onclick = () => post("/api/estop", { on: true });
$("clear-estop").onclick = () => post("/api/estop", { on: false });
$("arm-teleop").onclick = () => post("/api/teleop", { on: true, speed: Number($("speed").value) });
$("disarm-teleop").onclick = () => post("/api/teleop", { on: false });
$("speed").oninput = () => {
  $("speed-label").textContent = Number($("speed").value).toFixed(2);
  post("/api/teleop", { on: !!(state && state.teleop), speed: Number($("speed").value) });
};
$("save-map").onclick = async () => {
  const r = await post("/api/map/save", {});
  $("map-status").textContent = r.ok ? `地图已保存 ${r.width}×${r.height}` : r.error;
};
$("undo-pt").onclick = () => { draft.pop(); draw(); };
$("close-poly").onclick = () => {
  if (draft.length >= 3) draft.closed = true;
  draw();
};
$("add-place").onclick = async () => {
  if (!draft.closed || draft.length < 3) {
    $("map-status").textContent = "先点出至少三个点，再闭合。";
    return;
  }
  const name = $("place-name").value.trim();
  if (!name) return;
  const places = (state.places || []).filter((p) => p.name !== name);
  places.push({ name, kind: $("place-kind").value, points: draft.map((p) => [p.x, p.y]) });
  const r = await post("/api/places", { places });
  if (r.ok) {
    draft = [];
    state.places = r.places;
  }
  $("map-status").textContent = r.ok ? "区域已保存" : (r.error || "保存失败");
};
$("save-places").onclick = async () => {
  const r = await post("/api/places", { places: state.places || [] });
  $("map-status").textContent = r.ok ? "标注已写入" : "写入失败";
};
$("preview-path").onclick = async () => { preview = await goalRequest("/api/plan"); draw(); };
$("start-nav").onclick = async () => { preview = await goalRequest("/api/nav/start"); };
$("stop-nav").onclick = () => post("/api/nav/stop", {});
$("start-mission").onclick = () => post("/api/mission/start", { text: $("mission-text").value });
$("continue-mission").onclick = () => post("/api/mission/continue", { note: $("mission-note").value });
$("stop-mission").onclick = () => post("/api/nav/stop", {});

async function goalRequest(url) {
  const place = $("nav-place").value;
  const body = place ? { place } : clickGoal;
  if (!body) {
    $("nav-status").textContent = "先在地图上点一个位置，或选择地点。";
    return null;
  }
  const r = await post(url, body);
  $("nav-status").textContent = r.ok ? (r.path ? `路径 ${r.path.length} 段` : "已开始") : r.error;
  return r.ok ? r : null;
}

function setKey(code, down) {
  const map = { KeyQ: "q", KeyW: "w", KeyE: "e", KeyA: "a", KeyS: "s", KeyD: "d" };
  const k = map[code];
  if (!k || tab !== "mapping") return;
  keys[k] = down;
  document.querySelectorAll("#pad kbd").forEach((el) => el.classList.toggle("on", !!keys[el.dataset.key]));
  post("/api/keys", { keys });
}
window.addEventListener("keydown", (ev) => {
  if (ev.repeat) return;
  if (["KeyQ", "KeyW", "KeyE", "KeyA", "KeyS", "KeyD"].includes(ev.code) && tab === "mapping") ev.preventDefault();
  setKey(ev.code, true);
});
window.addEventListener("keyup", (ev) => setKey(ev.code, false));
window.addEventListener("blur", () => {
  Object.keys(keys).forEach((k) => { keys[k] = false; });
  post("/api/keys", { keys: {} });
});

function mapOnScreen() {
  if (holdMap) return holdMap.meta;
  return state && state.map;
}

function imageOnScreen() {
  if (holdMap) return holdMap.image;
  return shownReady ? shown : null;
}

function worldFromEvent(ev) {
  const map = mapOnScreen();
  if (!map) return null;
  const rect = canvas.getBoundingClientRect();
  const px = (ev.clientX - rect.left) / rect.width * canvas.width;
  const py = (ev.clientY - rect.top) / rect.height * canvas.height;
  const view = viewOf(map);
  const col = (px - view.ox) / view.scale;
  const row = (py - view.oy) / view.scale;
  const x = map.origin_x + col * map.resolution;
  const y = map.origin_y + (map.height - row) * map.resolution;
  return { x, y };
}

canvas.addEventListener("click", (ev) => {
  const p = worldFromEvent(ev);
  if (!p || !state || !state.map) return;
  if (tab === "annotate") {
    draft.push(p);
    draft.closed = false;
    draw();
    return;
  }
  if (tab === "nav") {
    clickGoal = p;
    $("nav-place").value = "";
    draw();
  }
});

function viewOf(map) {
  const pad = 16;
  const fit = Math.min((canvas.width - pad * 2) / map.width, (canvas.height - pad * 2) / map.height);
  const scale = fit * zoom;
  const ox = (canvas.width - map.width * scale) / 2 + panX;
  const oy = (canvas.height - map.height * scale) / 2 + panY;
  return { scale, ox, oy, fit };
}

function setZoom(next, px, py) {
  const map = mapOnScreen();
  if (!map) return;
  const view = viewOf(map);
  const col = px == null ? map.width / 2 : (px - view.ox) / view.scale;
  const row = py == null ? map.height / 2 : (py - view.oy) / view.scale;
  zoom = Math.min(10, Math.max(0.6, next));
  const scale = view.fit * zoom;
  const ax = px == null ? canvas.width / 2 : px;
  const ay = py == null ? canvas.height / 2 : py;
  panX = ax - col * scale - (canvas.width - map.width * scale) / 2;
  panY = ay - row * scale - (canvas.height - map.height * scale) / 2;
  draw();
}

$("zoom-in").onclick = () => setZoom(zoom * 1.25);
$("zoom-out").onclick = () => setZoom(zoom / 1.25);
$("zoom-reset").onclick = () => { zoom = 1; panX = 0; panY = 0; draw(); };

canvas.addEventListener("wheel", (ev) => {
  ev.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const px = (ev.clientX - rect.left) / rect.width * canvas.width;
  const py = (ev.clientY - rect.top) / rect.height * canvas.height;
  setZoom(zoom * (ev.deltaY < 0 ? 1.12 : 1 / 1.12), px, py);
}, { passive: false });

let panning = false;
let panLast = null;
canvas.addEventListener("contextmenu", (ev) => ev.preventDefault());
canvas.addEventListener("pointerdown", (ev) => {
  if (ev.button !== 2 && ev.button !== 1) return;
  panning = true;
  panLast = [ev.clientX, ev.clientY];
  canvas.setPointerCapture(ev.pointerId);
});
canvas.addEventListener("pointermove", (ev) => {
  if (!panning || !panLast) return;
  const rect = canvas.getBoundingClientRect();
  panX += (ev.clientX - panLast[0]) * canvas.width / rect.width;
  panY += (ev.clientY - panLast[1]) * canvas.height / rect.height;
  panLast = [ev.clientX, ev.clientY];
  draw();
});
canvas.addEventListener("pointerup", () => { panning = false; panLast = null; });

function toCanvas(map, x, y) {
  const view = viewOf(map);
  const col = (x - map.origin_x) / map.resolution;
  const row = map.height - (y - map.origin_y) / map.resolution;
  return [view.ox + col * view.scale, view.oy + row * view.scale];
}

function draw() {
  const map = mapOnScreen();
  const image = imageOnScreen();
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#c5ced6";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  if (!map) {
    ctx.fillStyle = "#24143f";
    ctx.font = "16px sans-serif";
    ctx.fillText("还没有地图。先在终端启动建图，或保存过一张图。", 24, 40);
    return;
  }
  const view = viewOf(map);
  ctx.imageSmoothingEnabled = false;
  if (image && image.complete && image.naturalWidth) {
    ctx.drawImage(image, view.ox, view.oy, map.width * view.scale, map.height * view.scale);
  }
  const places = (state && state.places) || [];
  for (const place of places) drawPoly(map, place.points, place.kind === "keepout" ? "rgba(198,40,40,0.35)" : "rgba(75,45,134,0.28)", place.name);
  if (draft.length) drawPoly(map, draft.map((p) => [p.x, p.y]), "rgba(245,193,74,0.45)", draft.closed ? "未命名" : "绘制中", !draft.closed);
  const path = (preview && preview.path) || (state && state.nav && state.nav.path) || [];
  if (path.length) {
    ctx.beginPath();
    path.forEach((p, i) => {
      const [x, y] = toCanvas(map, p[0], p[1]);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = "#1565c0";
    ctx.lineWidth = 3;
    ctx.stroke();
  }
  if (clickGoal && tab === "nav") {
    const [x, y] = toCanvas(map, clickGoal.x, clickGoal.y);
    ctx.fillStyle = "#e85aa8";
    ctx.beginPath();
    ctx.arc(x, y, 6, 0, Math.PI * 2);
    ctx.fill();
  }
  if (state && state.pose) drawGo2(map, state.pose);
}

function drawPoly(map, points, fill, name, open) {
  if (!points.length) return;
  ctx.beginPath();
  points.forEach((p, i) => {
    const [x, y] = toCanvas(map, p[0], p[1]);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  if (!open) ctx.closePath();
  ctx.fillStyle = fill;
  if (!open) ctx.fill();
  ctx.strokeStyle = "#2c1658";
  ctx.lineWidth = 2;
  ctx.stroke();
  const [x, y] = toCanvas(map, points[0][0], points[0][1]);
  drawLabel(x, y, name);
}

function drawLabel(x, y, text) {
  ctx.font = "600 14px sans-serif";
  const width = ctx.measureText(text).width;
  const boxW = width + 12;
  const boxH = 22;
  const left = x + 8;
  const top = y - boxH - 6;
  ctx.beginPath();
  ctx.roundRect(left, top, boxW, boxH, 6);
  ctx.fillStyle = "rgba(255, 250, 240, 0.95)";
  ctx.fill();
  ctx.lineWidth = 1.5;
  ctx.strokeStyle = "#2c2416";
  ctx.stroke();
  ctx.fillStyle = "#1c1408";
  ctx.fillText(text, left + 6, top + 15);
}

function drawGo2(map, pose) {
  const view = viewOf(map);
  const ppm = view.scale / map.resolution;
  const [sx, sy] = toCanvas(map, pose.x, pose.y);
  ctx.save();
  ctx.translate(sx, sy);
  ctx.rotate(-pose.yaw);
  ctx.scale(ppm, ppm);
  ctx.lineJoin = "round";
  ctx.lineWidth = 0.015;
  ctx.strokeStyle = "#2a2724";
  ctx.fillStyle = "#6d645b";
  // Thighs sit outside the trunk. Overall about 0.70 m long and 0.31 m wide.
  [[0.20, 0.115], [0.20, -0.115], [-0.18, 0.115], [-0.18, -0.115]].forEach(([x, y]) => {
    ctx.beginPath();
    ctx.ellipse(x, -y, 0.075, 0.042, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  });
  ctx.fillStyle = "#f7f4ee";
  ctx.beginPath();
  ctx.roundRect(-0.30, -0.10, 0.58, 0.20, 0.07);
  ctx.fill();
  ctx.stroke();
  ctx.beginPath();
  ctx.ellipse(0.30, 0, 0.09, 0.075, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = "#c4552a";
  ctx.beginPath();
  ctx.moveTo(0.40, 0);
  ctx.lineTo(0.33, 0.035);
  ctx.lineTo(0.33, -0.035);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

function renderSide() {
  if (!state) return;
  const pose = state.pose ? `定位 ${state.pose.x.toFixed(2)}, ${state.pose.y.toFixed(2)}` : "还没有定位";
  const map = state.map ? `地图 ${state.map.width}×${state.map.height}${state.live_map ? "（实时）" : "（已保存）"}` : "无地图";
  $("map-status").textContent = `${state.ros_detail} · ${map} · ${pose}${state.estop ? " · 急停" : ""}${state.teleop ? " · 遥控中" : ""}`;
  const list = $("place-list");
  list.innerHTML = "";
  const select = $("nav-place");
  const current = select.value;
  select.innerHTML = '<option value="">用地图上点的位置</option>';
  (state.places || []).forEach((p) => {
    const li = document.createElement("li");
    li.textContent = `${p.name} · ${p.kind === "keepout" ? "禁行区" : "地点"} `;
    const del = document.createElement("button");
    del.className = "ghost";
    del.textContent = "删除";
    del.onclick = async () => {
      const places = state.places.filter((item) => item.name !== p.name);
      const r = await post("/api/places", { places });
      if (r.ok) state.places = r.places;
    };
    li.appendChild(del);
    list.appendChild(li);
    if (p.kind !== "keepout") {
      const opt = document.createElement("option");
      opt.value = p.name;
      opt.textContent = p.name;
      select.appendChild(opt);
    }
  });
  select.value = current;
  $("nav-status").textContent = state.nav ? state.nav.status : "";
  $("mission-status").textContent = state.mission ? state.mission.status : "";
  $("mission-log").textContent = ((state.mission && state.mission.log) || []).join("\n");
}

function loadMap(seq) {
  if (holdMap || seq === imgSeq || seq === loadingSeq) return;
  loadingSeq = seq;
  const next = new Image();
  next.onload = () => {
    if (seq !== loadingSeq) return;
    shown = next;
    shownReady = true;
    imgSeq = seq;
    loadingSeq = -1;
    draw();
  };
  next.onerror = () => {
    if (seq === loadingSeq) loadingSeq = -1;
  };
  next.src = "/map.png?seq=" + seq;
}

async function poll() {
  try {
    const res = await fetch("/api/state");
    state = await res.json();
    if (state.map && !holdMap) loadMap(state.map.seq);
    renderSide();
    draw();
  } catch (err) {
    $("map-status").textContent = "控制台连接中断";
  }
}
setInterval(poll, 500);
poll();
document.body.classList.add("view-3d");

const cloudCanvas = document.getElementById("cloud");
const gl = cloudCanvas.getContext("webgl", { antialias: false, preserveDrawingBuffer: false });
const cloudCam = { yaw: 0.9, pitch: 0.55, dist: 8 };
let cloudTarget = [0, 0, 0];
let cloudSeq = -1;
let accCount = 0;
let liveCount = 0;

function matMul(a, b) {
  const o = new Float32Array(16);
  for (let c = 0; c < 4; c++) {
    for (let r = 0; r < 4; r++) {
      o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
    }
  }
  return o;
}

function perspective(fovy, aspect, near, far) {
  const f = 1 / Math.tan(fovy / 2);
  const m = new Float32Array(16);
  m[0] = f / aspect;
  m[5] = f;
  m[10] = (far + near) / (near - far);
  m[11] = -1;
  m[14] = (2 * far * near) / (near - far);
  return m;
}

function lookAt(eye, target, up) {
  const zx = eye[0] - target[0], zy = eye[1] - target[1], zz = eye[2] - target[2];
  let zl = Math.hypot(zx, zy, zz) || 1;
  const fz = [zx / zl, zy / zl, zz / zl];
  const rx = up[1] * fz[2] - up[2] * fz[1];
  const ry = up[2] * fz[0] - up[0] * fz[2];
  const rz = up[0] * fz[1] - up[1] * fz[0];
  let rl = Math.hypot(rx, ry, rz) || 1;
  const fx = [rx / rl, ry / rl, rz / rl];
  const ux = [fz[1] * fx[2] - fz[2] * fx[1], fz[2] * fx[0] - fz[0] * fx[2], fz[0] * fx[1] - fz[1] * fx[0]];
  const m = new Float32Array(16);
  m[0] = fx[0]; m[1] = ux[0]; m[2] = fz[0];
  m[4] = fx[1]; m[5] = ux[1]; m[6] = fz[1];
  m[8] = fx[2]; m[9] = ux[2]; m[10] = fz[2];
  m[12] = -(fx[0] * eye[0] + fx[1] * eye[1] + fx[2] * eye[2]);
  m[13] = -(ux[0] * eye[0] + ux[1] * eye[1] + ux[2] * eye[2]);
  m[14] = -(fz[0] * eye[0] + fz[1] * eye[1] + fz[2] * eye[2]);
  m[15] = 1;
  return m;
}

let cloudProg = null;
let accBuf = null;
let liveBuf = null;
if (gl) {
  const vs = `attribute vec3 aPos; uniform mat4 uMvp; uniform float uSize; varying float vH;
    void main() { gl_Position = uMvp * vec4(aPos, 1.0); gl_PointSize = uSize; vH = aPos.y; }`;
  const fs = `precision mediump float; varying float vH; uniform vec3 uTint; uniform float uRainbow;
    void main() {
      vec2 p = gl_PointCoord * 2.0 - 1.0;
      if (dot(p, p) > 1.0) discard;
      float h = clamp((vH + 0.2) / 1.6, 0.0, 1.0);
      vec3 low = vec3(0.22, 0.38, 0.95);
      vec3 mid = vec3(0.25, 0.82, 0.55);
      vec3 high = vec3(0.98, 0.72, 0.22);
      vec3 rainbow = h < 0.5 ? mix(low, mid, h * 2.0) : mix(mid, high, (h - 0.5) * 2.0);
      gl_FragColor = vec4(mix(uTint, rainbow, uRainbow), 1.0);
    }`;
  function shader(type, src) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    return s;
  }
  cloudProg = gl.createProgram();
  gl.attachShader(cloudProg, shader(gl.VERTEX_SHADER, vs));
  gl.attachShader(cloudProg, shader(gl.FRAGMENT_SHADER, fs));
  gl.linkProgram(cloudProg);
  accBuf = gl.createBuffer();
  liveBuf = gl.createBuffer();
}

function worldToGl(x, y, z) {
  return [x, z, -y];
}

function paintCloud(acc, live) {
  if (!gl || !cloudProg) return;
  const eyeYaw = cloudCam.yaw;
  const eyePitch = Math.max(0.12, Math.min(1.35, cloudCam.pitch));
  cloudCam.pitch = eyePitch;
  const dist = cloudCam.dist;
  const eye = [
    cloudTarget[0] + dist * Math.cos(eyePitch) * Math.cos(eyeYaw),
    cloudTarget[1] + dist * Math.sin(eyePitch),
    cloudTarget[2] + dist * Math.cos(eyePitch) * Math.sin(eyeYaw),
  ];
  const view = lookAt(eye, cloudTarget, [0, 1, 0]);
  const proj = perspective(0.9, cloudCanvas.width / cloudCanvas.height, 0.05, 80);
  const mvp = matMul(proj, view);
  gl.viewport(0, 0, cloudCanvas.width, cloudCanvas.height);
  gl.clearColor(0.09, 0.12, 0.16, 1);
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  gl.enable(gl.DEPTH_TEST);
  gl.useProgram(cloudProg);
  gl.uniformMatrix4fv(gl.getUniformLocation(cloudProg, "uMvp"), false, mvp);
  const posLoc = gl.getAttribLocation(cloudProg, "aPos");
  gl.enableVertexAttribArray(posLoc);
  function drawBuf(buf, data, count, tint, size) {
    if (!count) return;
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.DYNAMIC_DRAW);
    gl.vertexAttribPointer(posLoc, 3, gl.FLOAT, false, 0, 0);
    gl.uniform3fv(gl.getUniformLocation(cloudProg, "uTint"), tint);
    gl.uniform1f(gl.getUniformLocation(cloudProg, "uSize"), size);
    gl.uniform1f(gl.getUniformLocation(cloudProg, "uRainbow"), tint[0] > 0.9 ? 0.0 : 1.0);
    gl.drawArrays(gl.POINTS, 0, count);
  }
  drawBuf(accBuf, acc, accCount, [0.45, 0.78, 0.95], 2.2);
  drawBuf(liveBuf, live, liveCount, [1.0, 0.86, 0.35], 3.0);
}

function recolor(src, count) {
  const out = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    const x = src[i * 3], y = src[i * 3 + 1], z = src[i * 3 + 2];
    const g = worldToGl(x, y, z);
    out[i * 3] = g[0];
    out[i * 3 + 1] = g[1];
    out[i * 3 + 2] = g[2];
  }
  return out;
}

async function pollCloud() {
  if (tab !== "mapping" || !gl) return;
  try {
    const res = await fetch("/cloud.bin");
    const buf = await res.arrayBuffer();
    if (buf.byteLength < 28) return;
    const head = new DataView(buf);
    const seq = head.getUint32(0, true);
    const nAcc = head.getUint32(4, true);
    const nLive = head.getUint32(8, true);
    const rx = head.getFloat32(12, true);
    const ry = head.getFloat32(16, true);
    const rz = head.getFloat32(20, true);
    cloudTarget = worldToGl(rx, ry, rz);
    const need = 28 + (nAcc + nLive) * 12;
    if (buf.byteLength < need) return;
    if (seq !== cloudSeq) {
      cloudSeq = seq;
      const accBytes = nAcc * 12;
      const accSrc = new Float32Array(buf, 28, nAcc * 3);
      const liveSrc = new Float32Array(buf, 28 + accBytes, nLive * 3);
      accCount = nAcc;
      liveCount = nLive;
      paintCloud(recolor(accSrc, nAcc), recolor(liveSrc, nLive));
      if (!nAcc && !nLive) {
        gl.clearColor(0.09, 0.12, 0.16, 1);
        gl.clear(gl.COLOR_BUFFER_BIT);
      }
    } else {
      paintCloud(lastAcc, lastLive);
    }
  } catch (err) {
    /* mapping view can wait until the cloud arrives */
  }
}

let lastAcc = new Float32Array(0);
let lastLive = new Float32Array(0);
const _paint = paintCloud;
paintCloud = function (acc, live) {
  lastAcc = acc;
  lastLive = live;
  _paint(acc, live);
};

let cloudDrag = null;
cloudCanvas.addEventListener("pointerdown", (ev) => {
  if (ev.button !== 0) return;
  cloudDrag = [ev.clientX, ev.clientY, cloudCam.yaw, cloudCam.pitch];
  cloudCanvas.setPointerCapture(ev.pointerId);
});
cloudCanvas.addEventListener("pointermove", (ev) => {
  if (!cloudDrag) return;
  cloudCam.yaw = cloudDrag[2] - (ev.clientX - cloudDrag[0]) * 0.008;
  cloudCam.pitch = cloudDrag[3] + (ev.clientY - cloudDrag[1]) * 0.006;
  paintCloud(lastAcc, lastLive);
});
cloudCanvas.addEventListener("pointerup", () => { cloudDrag = null; });
cloudCanvas.addEventListener("wheel", (ev) => {
  ev.preventDefault();
  cloudCam.dist = Math.min(30, Math.max(1.2, cloudCam.dist * (ev.deltaY < 0 ? 0.9 : 1.1)));
  paintCloud(lastAcc, lastLive);
}, { passive: false });
setInterval(pollCloud, 250);
pollCloud();
