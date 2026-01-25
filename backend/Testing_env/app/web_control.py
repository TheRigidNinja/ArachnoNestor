#!/usr/bin/env python3
"""Minimal Flask web UI for supervisor control."""

from flask import Flask, jsonify, request

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
  </style>
</head>
<body>
  <div class="container-fluid">
    <h3 class="mb-3">Supervisor Control</h3>
    <div class="row g-3">
      <div class="col-lg-4">
        <div class="card shadow-sm">
          <div class="card-header">Mode</div>
          <div class="card-body d-grid gap-2">
            <div class="btn-group" role="group" aria-label="Mode toggle">
              <input type="radio" class="btn-check" name="mode" id="mode-idle" autocomplete="off" onclick="post('/mode/idle')">
              <label class="btn btn-outline-secondary" for="mode-idle">IDLE</label>

              <input type="radio" class="btn-check" name="mode" id="mode-setup" autocomplete="off" onclick="post('/mode/setup')">
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
          <div class="card-header">Setup Jog (requires SETUP mode)</div>
          <div class="card-body">
            <div class="row g-2 mb-2">
              <div class="col-6">
                <label class="form-label">RPM</label>
                <input id="jog-rpm" type="number" class="form-control" value="200">
              </div>
              <div class="col-6">
                <label class="form-label">Seconds</label>
                <input id="jog-sec" type="number" step="0.1" class="form-control" value="1.0">
              </div>
            </div>
            <button class="btn btn-primary w-100" onclick="post('/setup/jog', {rpm:getVal('jog-rpm'), seconds:getVal('jog-sec')})">Run Jog</button>
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
            <button class="btn btn-success w-100" onclick="post('/test/up', {rpm:getVal('up-rpm'), seconds:getVal('up-sec')})">Start UP Test</button>
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
            <div class="btn-group w-100" role="group">
              <button class="btn btn-outline-dark" onclick="dir('forward')">Forward</button>
              <button class="btn btn-outline-dark" onclick="dir('back')">Back</button>
            </div>
            <div class="btn-group w-100 mt-2" role="group">
              <button class="btn btn-outline-dark" onclick="dir('left')">Left</button>
              <button class="btn btn-outline-dark" onclick="dir('right')">Right</button>
            </div>
            <div class="btn-group w-100 mt-2" role="group">
              <button class="btn btn-outline-dark" onclick="dir('up')">Up</button>
              <button class="btn btn-outline-dark" onclick="dir('down')">Down</button>
            </div>
          </div>
        </div>
        <div class="card shadow-sm mt-3">
          <div class="card-header">Status</div>
          <div class="card-body">
            <div id="status-text" class="mb-2 small text-muted">Loading…</div>
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
  document.getElementById('status-json').textContent = JSON.stringify(js, null, 2);
  refreshStatus();
}
function getVal(id){ return parseFloat(document.getElementById(id).value) || 0; }
function dir(name){ post(`/test/dir/${name}`, {rpm:getVal('dir-rpm'), seconds:getVal('dir-sec')}); }
async function refreshStatus(){
  try {
    const res = await fetch('/status');
    const js = await res.json();
    document.getElementById('status-json').textContent = JSON.stringify(js, null, 2);
    if (js.ok === false) {
      document.getElementById('status-text').textContent = `Status error: ${js.error || 'unknown'}`;
      return;
    }
    document.getElementById('status-text').textContent = `Mode: ${js.mode} | Fault: ${js.fault || 'none'} | Last update: ${fmtTime(js.last_update)}`;

    document.getElementById('mode-idle').checked = (js.mode === 'IDLE');
    document.getElementById('mode-setup').checked = (js.mode === 'SETUP');
    document.getElementById('mode-test').checked = (js.mode === 'TEST');

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
  } catch(e){
    document.getElementById('status-text').textContent = 'Status fetch failed';
  }
}
function fmtTime(t){ if(!t) return 'n/a'; const d=new Date(t*1000); return d.toLocaleTimeString(); }
refreshStatus();
setInterval(refreshStatus, 1500);
</script>

</body>
</html>
        """
    )


@app.post("/mode/idle")
def mode_idle():
    try:
        log.info("UI: mode idle")
        mc.set_mode("IDLE")
        return ok()
    except Exception as exc:
        return err(str(exc))


@app.post("/mode/setup")
def mode_setup():
    try:
        log.info("UI: mode setup")
        mc.set_mode("SETUP")
        return ok()
    except Exception as exc:
        return err(str(exc))


@app.post("/mode/test")
def mode_test():
    try:
        log.info("UI: mode test")
        mc.set_mode("TEST")
        return ok()
    except Exception as exc:
        return err(str(exc))


@app.post("/fault/clear")
def clear_fault():
    try:
        log.info("UI: clear fault")
        mc.clear_fault()
        return ok()
    except Exception as exc:
        return err(str(exc))


@app.post("/stop")
def stop():
    reason = request.json.get("reason", "user stop") if request.is_json else "user stop"
    log.warning(f"UI: stop ({reason})")
    mc.stop_all(reason)
    return ok({"stopped": True, "reason": reason})


@app.post("/stop/all")
def stop_all_fault():
    reason = request.json.get("reason", "emergency stop") if request.is_json else "emergency stop"
    log.warning(f"UI: emergency stop ({reason})")
    mc.emergency_stop(reason)
    return ok({"stopped": True, "fault": True, "reason": reason})


@app.post("/setup/jog")
def setup_jog():
    payload = request.get_json(force=True, silent=True) or {}
    rpm = int(payload.get("rpm", 200))
    seconds = float(payload.get("seconds", 1.0))
    try:
        log.info(f"UI: setup jog rpm={rpm} sec={seconds}")
        label = mc.setup_jog(rpm=rpm, seconds=seconds)
        return ok({"job": label})
    except Exception as exc:
        return err(str(exc))


@app.post("/test/up")
def test_up():
    payload = request.get_json(force=True, silent=True) or {}
    rpm = int(payload.get("rpm", 350))
    seconds = float(payload.get("seconds", 10.0))
    try:
        log.info(f"UI: test up rpm={rpm} sec={seconds}")
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
