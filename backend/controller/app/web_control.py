#!/usr/bin/env python3
"""Minimal Flask web UI for supervisor control."""

import json
import time

from flask import Flask, Response, jsonify, request

from config.settings import load_config
from motor.motion_controller import get_controller, DIRECTION_MAP
from logutil.logger import get_logger

CONFIG = load_config()

app = Flask(__name__)
mc = get_controller()
log = get_logger("app.web_control")


# ---------- helpers ----------
def ok(data=None):
    return jsonify({"ok": True, **(data or {})})


def err(msg):
    return jsonify({"ok": False, "error": msg}), 400


# ---------- routes ----------
@app.get("/status")
def status():
    try:
        return jsonify(mc.get_status())
    except Exception as exc:
        log.error(f"/status error: {exc}")
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/events")
def events():
    def stream():
        last_sent = 0.0
        while True:
            try:
                data = mc.get_status()
                payload = json.dumps(data)
                yield f"data: {payload}\n\n"
                last_sent = time.time()
            except Exception as exc:
                log.error(f"/events error: {exc}")
                yield f"data: {json.dumps({'ok': False, 'error': str(exc)})}\n\n"
            time.sleep(0.02)
    return Response(stream(), mimetype="text/event-stream")


@app.get("/")
def index():
    return (
        """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Supervisor Control</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body { padding: 1.5rem; }
    .card + .card { margin-top: 1rem; }
    .btn-group .btn { min-width: 90px; }
    pre { background: #111; color: #0f0; padding: 0.75rem; border-radius: 6px; }
    .invert-active .btn-check:checked + .btn,
    .invert-active .btn.active {
      background-color: var(--bs-btn-active-bg);
      border-color: var(--bs-btn-active-border-color);
      color: var(--bs-btn-active-color);
    }
    .invert-active .btn-check:focus + .btn,
    .invert-active .btn:focus {
      box-shadow: 0 0 0 0.2rem rgba(0,0,0,0.25);
    }
    label.disabled { opacity: 0.5; }
  </style>
</head>
<body>
  <div class="container-fluid">
    <h3 class="mb-3">Supervisor Control</h3>
    <div class="mb-3">
      <a class="btn btn-outline-primary btn-sm" href="/dashboard">Open Hall Dashboard</a>
    </div>
    <div class="row g-3">
      <div class="col-lg-4">
        <div class="card shadow-sm">
          <div class="card-header">Mode</div>
          <div class="card-body d-grid gap-2">
            <div class="btn-group invert-active" role="group" aria-label="Mode toggle">
              <input type="radio" class="btn-check" name="mode" id="mode-idle" autocomplete="off" onclick="post('/mode/idle')">
              <label class="btn btn-outline-secondary" for="mode-idle">IDLE</label>

              <input type="radio" class="btn-check" name="mode" id="mode-setup" autocomplete="off" onclick="setMode('setup')">
              <label class="btn btn-outline-primary" for="mode-setup">SETUP</label>

              <input type="radio" class="btn-check" name="mode" id="mode-test" autocomplete="off" onclick="post('/mode/test')">
              <label class="btn btn-outline-success" for="mode-test">TEST</label>
            </div>
            <button class="btn btn-outline-warning" onclick="post('/fault/clear')">Clear Fault</button>
          </div>
        </div>
        <div class="card shadow-sm">
          <div class="card-header">Stop</div>
          <div class="card-body d-grid gap-2">
            <button class="btn btn-danger" onclick="post('/stop/all', {reason:'manual emergency'})">EMERGENCY STOP</button>
            <button class="btn btn-outline-danger" onclick="post('/stop', {reason:'manual stop'})">Soft Stop (no fault)</button>
          </div>
        </div>
      </div>
      <div class="col-lg-4">
        <div class="card shadow-sm">
          <div class="card-header">Setup Hall Run (requires SETUP mode)</div>
          <div class="card-body">
            <div class="row g-2 mb-2">
              <div class="col-12">
                <label class="form-label disabled">Max Seconds (disabled)</label>
                <input id="setup-sec" type="number" step="0.1" class="form-control" value="0" disabled>
              </div>
            </div>
            <div class="btn-group w-100 mb-2 invert-active" role="group" aria-label="Setup direction">
              <input type="radio" class="btn-check" name="setup-dir" id="setup-dir-fwd" autocomplete="off" checked>
              <label class="btn btn-outline-dark" for="setup-dir-fwd">Forward</label>
              <input type="radio" class="btn-check" name="setup-dir" id="setup-dir-rev" autocomplete="off">
              <label class="btn btn-outline-dark" for="setup-dir-rev">Reverse</label>
            </div>
            <button class="btn btn-primary w-100" onclick="post('/setup/hall', {direction:getSetupDir()})">Run Hall</button>
            <button class="btn btn-outline-secondary w-100 mt-2" onclick="post('/job/cancel', {reason:'ui cancel'})">Cancel Job</button>
          </div>
        </div>
        <div class="card shadow-sm">
          <div class="card-header">UP Test (requires TEST mode)</div>
          <div class="card-body">
            <div class="row g-2 mb-2">
              <div class="col-6">
                <label class="form-label">RPM</label>
                <input id="up-rpm" type="number" class="form-control" value="350">
              </div>
              <div class="col-6">
                <label class="form-label">Seconds</label>
                <input id="up-sec" type="number" step="0.1" class="form-control" value="10">
              </div>
            </div>
            <button id="up-test-btn" class="btn btn-success w-100" onclick="post('/test/up', {rpm:getVal('up-rpm'), seconds:getVal('up-sec')})">Start UP Test</button>
          </div>
        </div>
      </div>
      <div class="col-lg-4">
        <div class="card shadow-sm">
          <div class="card-header">Directional Tests (TEST mode)</div>
          <div class="card-body">
            <div class="row g-2 mb-2">
              <div class="col-6">
                <label class="form-label">RPM</label>
                <input id="dir-rpm" type="number" class="form-control" value="350">
              </div>
              <div class="col-6">
                <label class="form-label">Seconds</label>
                <input id="dir-sec" type="number" step="0.1" class="form-control" value="6">
              </div>
            </div>
            <div class="btn-group w-100 invert-active" role="group" aria-label="Directional test row 1">
              <input type="radio" class="btn-check" name="dir-test" id="dir-forward" autocomplete="off" onclick="dir('forward')">
              <label class="btn btn-outline-dark" for="dir-forward">Forward</label>
              <input type="radio" class="btn-check" name="dir-test" id="dir-back" autocomplete="off" onclick="dir('back')">
              <label class="btn btn-outline-dark" for="dir-back">Back</label>
            </div>
            <div class="btn-group w-100 mt-2 invert-active" role="group" aria-label="Directional test row 2">
              <input type="radio" class="btn-check" name="dir-test" id="dir-left" autocomplete="off" onclick="dir('left')">
              <label class="btn btn-outline-dark" for="dir-left">Left</label>
              <input type="radio" class="btn-check" name="dir-test" id="dir-right" autocomplete="off" onclick="dir('right')">
              <label class="btn btn-outline-dark" for="dir-right">Right</label>
            </div>
            <div class="btn-group w-100 mt-2 invert-active" role="group" aria-label="Directional test row 3">
              <input type="radio" class="btn-check" name="dir-test" id="dir-up" autocomplete="off" onclick="dir('up')">
              <label class="btn btn-outline-dark" for="dir-up">Up</label>
              <input type="radio" class="btn-check" name="dir-test" id="dir-down" autocomplete="off" onclick="dir('down')">
              <label class="btn btn-outline-dark" for="dir-down">Down</label>
            </div>
          </div>
        </div>
        <div class="card shadow-sm mt-3">
          <div class="card-header">Status</div>
          <div class="card-body">
            <div id="status-text" class="mb-2 small text-muted">Loading…</div>
            <div id="action-result" class="mb-2 small"></div>
            <div id="halls-table" class="mb-2"></div>
            <div id="power-table" class="mb-2"></div>
            <div id="bundle-table" class="mb-2"></div>
            <div id="imu-box" class="mb-2"></div>
            <pre id="status-json" class="small">{}</pre>
            <button class="btn btn-outline-secondary w-100" onclick="refreshStatus()">Refresh</button>
          </div>
        </div>
      </div>
    </div>
  </div>

<script>
async function post(url, data) {
  const opts = {method:'POST', headers:{'Content-Type':'application/json'}};
  if (data !== undefined) opts.body = JSON.stringify(data);
  const res = await fetch(url, opts);
  const js = await res.json().catch(()=>({ok:false,error:'bad json'}));
  const ar = document.getElementById('action-result');
  if (ar) {
    if (js.ok) {
      ar.textContent = `Last action OK: ${url}`;
      ar.className = 'mb-2 small text-success';
    } else {
      ar.textContent = `Last action ERROR: ${js.error || 'unknown'}`;
      ar.className = 'mb-2 small text-danger';
    }
  }
  document.getElementById('status-json').textContent = JSON.stringify(js, null, 2);
  refreshStatus();
}
async function refreshStatus(){
  const res = await fetch('/status');
  const js = await res.json().catch(()=>({ok:false,error:'bad json'}));
  renderStatus(js);
}
function getVal(id){ return parseFloat(document.getElementById(id).value) || 0; }
function dir(name){ post(`/test/dir/${name}`, {rpm:getVal('dir-rpm'), seconds:getVal('dir-sec')}); }
function getSetupDir(){ return document.getElementById('setup-dir-rev').checked ? 'reverse' : 'forward'; }
async function setMode(mode){
  await post(`/mode/${mode}`);
}
function renderStatus(js){
  document.getElementById('status-json').textContent = JSON.stringify(js, null, 2);
  if (js.ok === false) {
    document.getElementById('status-text').textContent = `Status error: ${js.error || 'unknown'}`;
    return;
  }
  document.getElementById('status-text').textContent = `Mode: ${js.mode} | Fault: ${js.fault || 'none'} | Last update: ${fmtTime(js.last_update)}`;
  const lc = js.last_command;
  if (lc) {
    document.getElementById('status-text').textContent += ` | Last cmd: ${lc.action} (${lc.detail}) @ ${fmtTime(parseFloat(lc.ts))}`;
  }
  if (js.job_label) {
    document.getElementById('status-text').textContent += ` | Job: ${js.job_label} running=${js.job_running} gen=${js.job_gen}`;
  }
  if (js.poll_seq !== undefined) {
    document.getElementById('status-text').textContent += ` | Poll: ${js.poll_seq}`;
  }
  if (js.max_hall_seen) {
    const mh = js.max_hall_seen;
    document.getElementById('status-text').textContent += ` | MaxHall: 1=${mh['1'] ?? '?'} 2=${mh['2'] ?? '?'} 3=${mh['3'] ?? '?'} 4=${mh['4'] ?? '?'}`;
  }

  document.getElementById('mode-idle').checked = (js.mode === 'IDLE');
  document.getElementById('mode-setup').checked = (js.mode === 'SETUP');
  document.getElementById('mode-test').checked = (js.mode === 'TEST');
  const upBtn = document.getElementById('up-test-btn');
  if (upBtn) upBtn.disabled = false;

  const halls = js.halls || {};
  let hallsHtml = '<table class="table table-sm table-bordered"><thead><tr><th>Winch</th><th>Hall</th></tr></thead><tbody>';
  for (const [k,v] of Object.entries(halls)) { hallsHtml += `<tr><td>${k}</td><td>${v}</td></tr>`; }
  hallsHtml += '</tbody></table>';
  document.getElementById('halls-table').innerHTML = '<strong>Hall</strong>' + hallsHtml;

  const power = js.power || {};
  let pHtml = '<table class="table table-sm table-bordered"><thead><tr><th>Winch</th><th>Bus (mV)</th><th>Current (mA)</th><th>Power (mW)</th></tr></thead><tbody>';
  for (const [k,v] of Object.entries(power)) { pHtml += `<tr><td>${k}</td><td>${v.bus_mv}</td><td>${v.current_ma}</td><td>${v.power_mw}</td></tr>`; }
  pHtml += '</tbody></table>';
  document.getElementById('power-table').innerHTML = '<strong>Power</strong>' + pHtml;

  const bundles = js.bundles || {};
  let bHtml = '<table class="table table-sm table-bordered"><thead><tr><th>Winch</th><th>Total</th><th>Delta</th><th>Hall</th><th>Dist(mm)</th><th>Strength</th><th>TempRaw</th><th>Age(ms)</th></tr></thead><tbody>';
  for (const [k,v] of Object.entries(bundles)) {
    bHtml += `<tr><td>${k}</td><td>${v.total_count ?? ''}</td><td>${v.delta_count ?? ''}</td><td>${v.hall_raw ?? ''}</td><td>${v.dist_mm ?? ''}</td><td>${v.strength ?? ''}</td><td>${v.temp_raw ?? ''}</td><td>${v.age_ms ?? ''}</td></tr>`;
  }
  bHtml += '</tbody></table>';
  document.getElementById('bundle-table').innerHTML = '<strong>Winch Sensors</strong>' + bHtml;

  if (js.imu) {
    const i = js.imu;
    const imuTxt = `Gyro: (${i.gyro[0].toFixed(2)}, ${i.gyro[1].toFixed(2)}, ${i.gyro[2].toFixed(2)}) | ` +
      `Accel: (${i.accel[0].toFixed(2)}, ${i.accel[1].toFixed(2)}, ${i.accel[2].toFixed(2)}) | ` +
      `Pitch: ${i.pitch.toFixed(2)} Roll: ${i.roll.toFixed(2)} Yaw: ${i.yaw.toFixed(2)} | Temp: ${i.temp_c.toFixed(1)}C`;
    document.getElementById('imu-box').innerHTML = '<strong>IMU</strong><div class="small">' + imuTxt + '</div>';
  } else {
    document.getElementById('imu-box').innerHTML = '<strong>IMU</strong><div class="small text-muted">(no data yet)</div>';
  }
}

function connectSSE(){
  const es = new EventSource('/events');
  es.onmessage = (evt) => {
    try {
      const js = JSON.parse(evt.data);
      renderStatus(js);
    } catch(e) {}
  };
  es.onerror = () => {
    document.getElementById('status-text').textContent = 'Status stream disconnected';
  };
}
function fmtTime(t){ if(!t) return 'n/a'; const d=new Date(t*1000); return d.toLocaleTimeString(); }
connectSSE();
</script>

</body>
</html>
        """
    )


@app.get("/dashboard")
def dashboard():
    return (
        """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Hall Dashboard</title>
  <style>
    :root {
      --bg-0: #0f131a;
      --bg-1: #141a23;
      --fg-0: #eef2f6;
      --fg-1: #aab6c5;
      --accent: #35c0c5;
      --grid: rgba(255,255,255,0.08);
    }
    body {
      margin: 0;
      font-family: "SF Mono", "Fira Code", Consolas, "Liberation Mono", Menlo, monospace;
      color: var(--fg-0);
      background:
        radial-gradient(1200px 600px at 10% 20%, #1a2330 0%, var(--bg-0) 55%),
        radial-gradient(900px 500px at 90% 10%, #192431 0%, var(--bg-1) 60%);
      min-height: 100vh;
    }
    header {
      padding: 1.25rem 1.5rem 0.25rem;
    }
    h1 {
      margin: 0 0 0.25rem;
      font-size: 1.4rem;
      letter-spacing: 0.02em;
    }
    .meta {
      color: var(--fg-1);
      font-size: 0.85rem;
    }
    .wrap {
      padding: 0.75rem 1.5rem 2rem;
      display: grid;
      grid-template-columns: 1fr;
      gap: 1rem;
    }
    .panel {
      background: rgba(17, 23, 33, 0.85);
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 14px;
      padding: 1rem;
      box-shadow: 0 20px 50px rgba(0,0,0,0.35);
    }
    .legend {
      display: flex;
      gap: 0.75rem;
      flex-wrap: wrap;
      margin-top: 0.6rem;
      font-size: 0.85rem;
      color: var(--fg-1);
    }
    .dot {
      width: 10px;
      height: 10px;
      border-radius: 999px;
      display: inline-block;
      margin-right: 6px;
    }
    .row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1rem;
    }
    .stat {
      font-size: 0.9rem;
      color: var(--fg-1);
    }
    canvas {
      width: 100%;
      height: 360px;
      background: linear-gradient(180deg, rgba(255,255,255,0.02), rgba(255,255,255,0.00));
      border-radius: 12px;
      display: block;
    }
    @media (max-width: 900px) {
      .row { grid-template-columns: 1fr; }
      canvas { height: 280px; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Hall Sensor Dashboard</h1>
    <div class="meta">
      Live stream via SSE · <a href="/" style="color:var(--accent)">Back to Control</a>
    </div>
  </header>
  <div class="wrap">
    <div class="panel">
      <div class="row">
        <div class="stat" id="status-text">Connecting…</div>
        <div class="stat" id="threshold-text"></div>
      </div>
      <canvas id="hall-canvas"></canvas>
      <div class="legend" id="legend"></div>
    </div>
  </div>

<script>
const MAX_POINTS = 360;
const colors = ["#35c0c5", "#f9a03f", "#6ee7b7", "#f472b6", "#60a5fa", "#facc15"];
const series = new Map(); // winchId -> {color, points: [{t,v}]}
let threshold = null;

const canvas = document.getElementById('hall-canvas');
const ctx = canvas.getContext('2d');

function resizeCanvas(){
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.floor(rect.width * dpr);
  canvas.height = Math.floor(rect.height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  draw();
}
window.addEventListener('resize', resizeCanvas);

function updateLegend(){
  const legend = document.getElementById('legend');
  legend.innerHTML = "";
  for (const [winch, data] of series.entries()) {
    const item = document.createElement('div');
    item.innerHTML = `<span class="dot" style="background:${data.color}"></span>Winch ${winch}`;
    legend.appendChild(item);
  }
}

function pushPoint(winch, value, ts){
  if (!series.has(winch)) {
    series.set(winch, {color: colors[series.size % colors.length], points: []});
    updateLegend();
  }
  const s = series.get(winch);
  s.points.push({t: ts, v: value});
  if (s.points.length > MAX_POINTS) s.points.shift();
}

function getYRange(){
  let min = Infinity, max = -Infinity;
  for (const s of series.values()) {
    for (const p of s.points) {
      if (p.v < min) min = p.v;
      if (p.v > max) max = p.v;
    }
  }
  if (!isFinite(min) || !isFinite(max)) return {min: 0, max: 1};
  if (min === max) return {min: min - 1, max: max + 1};
  return {min, max};
}

function drawGrid(w, h){
  ctx.strokeStyle = 'rgba(255,255,255,0.06)';
  ctx.lineWidth = 1;
  const stepX = w / 12;
  const stepY = h / 6;
  for (let x = 0; x <= w; x += stepX) {
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, h);
    ctx.stroke();
  }
  for (let y = 0; y <= h; y += stepY) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(w, y);
    ctx.stroke();
  }
}

function draw(){
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  ctx.clearRect(0, 0, w, h);
  drawGrid(w, h);

  const {min, max} = getYRange();
  const pad = 8;
  const scaleY = (v) => h - pad - ((v - min) / (max - min)) * (h - 2 * pad);
  const now = Date.now() / 1000;
  const span = 8.0;
  const scaleX = (t) => w - pad - ((now - t) / span) * (w - 2 * pad);

  if (threshold !== null) {
    const y = scaleY(threshold);
    ctx.strokeStyle = 'rgba(255,72,72,0.75)';
    ctx.setLineDash([6, 6]);
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(w, y);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  for (const [winch, s] of series.entries()) {
    ctx.strokeStyle = s.color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    let started = false;
    for (const p of s.points) {
      const x = scaleX(p.t);
      const y = scaleY(p.v);
      if (!started) {
        ctx.moveTo(x, y);
        started = true;
      } else {
        ctx.lineTo(x, y);
      }
    }
    ctx.stroke();
  }
}

function handleStatus(js){
  const status = document.getElementById('status-text');
  if (js.ok === false) {
    status.textContent = `Status error: ${js.error || 'unknown'}`;
    return;
  }
  const now = Date.now() / 1000;
  const halls = js.halls || {};
  for (const [k, v] of Object.entries(halls)) {
    pushPoint(k, Number(v), now);
  }
  if (typeof js.threshold === 'number') {
    threshold = js.threshold;
    document.getElementById('threshold-text').textContent = `Hall threshold: ${threshold}`;
  }
  status.textContent = `Mode: ${js.mode} | Fault: ${js.fault || 'none'} | Last update: ${fmtTime(js.last_update)}`;
  draw();
}

function connectSSE(){
  const es = new EventSource('/events');
  es.onmessage = (evt) => {
    try {
      const js = JSON.parse(evt.data);
      handleStatus(js);
    } catch(e) {}
  };
  es.onerror = () => {
    document.getElementById('status-text').textContent = 'Status stream disconnected';
  };
}

function fmtTime(t){
  if (!t) return 'n/a';
  const d = new Date(t * 1000);
  return d.toLocaleTimeString();
}

resizeCanvas();
connectSSE();
</script>
</body>
</html>
        """
    )


@app.post("/mode/idle")
def mode_idle():
    try:
        log.info("UI: mode idle")
        mc.record_command("mode", "idle")
        mc.set_mode("IDLE")
        return ok()
    except Exception as exc:
        log.error(f"UI: mode idle failed: {exc}")
        return err(str(exc))


@app.post("/mode/setup")
def mode_setup():
    try:
        log.info("UI: mode setup")
        mc.record_command("mode", "setup")
        mc.set_mode("SETUP")
        return ok()
    except Exception as exc:
        log.error(f"UI: mode setup failed: {exc}")
        return err(str(exc))


@app.post("/mode/test")
def mode_test():
    try:
        log.info("UI: mode test")
        mc.record_command("mode", "test")
        mc.set_mode("TEST")
        return ok()
    except Exception as exc:
        log.error(f"UI: mode test failed: {exc}")
        return err(str(exc))


@app.post("/fault/clear")
def clear_fault():
    try:
        log.info("UI: clear fault")
        mc.record_command("fault", "clear")
        mc.clear_fault()
        return ok()
    except Exception as exc:
        return err(str(exc))


@app.post("/stop")
def stop():
    reason = request.json.get("reason", "user stop") if request.is_json else "user stop"
    log.warning(f"UI: stop ({reason})")
    mc.record_command("stop", reason)
    mc.stop_all(reason)
    return ok({"stopped": True, "reason": reason})


@app.post("/stop/all")
def stop_all_fault():
    reason = request.json.get("reason", "emergency stop") if request.is_json else "emergency stop"
    log.warning(f"UI: emergency stop ({reason})")
    mc.record_command("stop_all", reason)
    mc.emergency_stop(reason)
    return ok({"stopped": True, "fault": True, "reason": reason})


@app.post("/job/cancel")
def cancel_job():
    reason = request.json.get("reason", "user cancel") if request.is_json else "user cancel"
    log.warning(f"UI: cancel job ({reason})")
    mc.record_command("cancel_job", reason)
    mc.cancel_job(reason)
    return ok({"cancelled": True, "reason": reason})


@app.post("/setup/jog")
def setup_jog():
    payload = request.get_json(force=True, silent=True) or {}
    rpm = int(payload.get("rpm", 200))
    seconds = float(payload.get("seconds", 1.0))
    try:
        log.info(f"UI: setup jog rpm={rpm} sec={seconds}")
        mc.record_command("setup_jog", f"rpm={rpm} sec={seconds}")
        label = mc.setup_jog(rpm=rpm, seconds=seconds)
        return ok({"job": label})
    except Exception as exc:
        return err(str(exc))


@app.post("/setup/hall")
def setup_hall():
    payload = request.get_json(force=True, silent=True) or {}
    rpm = int(payload.get("rpm", 200))
    seconds = float(payload.get("seconds", 0.0))
    direction = str(payload.get("direction", "forward"))
    try:
        log.info(f"UI: setup hall rpm={rpm} dir={direction}")
        mc.record_command("setup_hall", f"rpm={rpm} dir={direction}")
        label = mc.setup_hall_run(rpm=rpm, seconds=seconds, direction=direction)
        log.info(f"UI: setup hall started job={label}")
        return ok({"job": label, "status": mc.get_status()})
    except Exception as exc:
        log.error(f"UI: setup hall failed: {exc}")
        return err(str(exc))


@app.post("/test/up")
def test_up():
    payload = request.get_json(force=True, silent=True) or {}
    rpm = int(payload.get("rpm", 350))
    seconds = float(payload.get("seconds", 10.0))
    try:
        log.info(f"UI: test up rpm={rpm} sec={seconds}")
        mc.record_command("test_up", f"rpm={rpm} sec={seconds}")
        label = mc.test_up(rpm=rpm, seconds=seconds)
        return ok({"job": label})
    except Exception as exc:
        return err(str(exc))


@app.post("/test/dir/<name>")
def test_dir(name):
    if name not in DIRECTION_MAP:
        return err("invalid direction")
    payload = request.get_json(force=True, silent=True) or {}
    rpm = int(payload.get("rpm", 350))
    seconds = float(payload.get("seconds", 6.0))
    try:
        log.info(f"UI: test dir={name} rpm={rpm} sec={seconds}")
        mc.record_command("test_dir", f"dir={name} rpm={rpm} sec={seconds}")
        label = mc.test_direction(name=name, rpm=rpm, seconds=seconds)
        return ok({"job": label})
    except Exception as exc:
        return err(str(exc))


def main():
    app.run(
        host=CONFIG["web"]["host"],
        port=CONFIG["web"]["port"],
        threaded=True,
    )


if __name__ == "__main__":
    main()
