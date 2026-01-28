#!/usr/bin/env python3
"""Minimal EVB web dashboard with live graphs."""

from __future__ import annotations

import json
import threading
import time

from flask import Flask, Response, jsonify

from config.settings import load_config
from drivers.evb_driver import EVBDriver
from logutil.logger import get_logger

CONFIG = load_config()
log = get_logger("app.web_dashboard")

app = Flask(__name__)

_lock = threading.Lock()
_state = {
    "last_update": None,
    "halls": {},
    "power": {},
    "distance": None,
    "imu": None,
    "error": None,
    "distance_error": None,
    "imu_error": None,
}

STALE_MS = CONFIG.get("ui", {}).get("stale_threshold_ms", 1000)

def _poll_loop():
    host = CONFIG["evb"]["host"]
    port = CONFIG["evb"]["port"]
    timeout = CONFIG["evb"]["timeout"]
    interval = CONFIG["motion"]["poll_interval"]
    winch_ids = CONFIG["motion"]["winch_ids"]
    while True:
        try:
            with EVBDriver(host, port, timeout) as evb:
                while True:
                    halls = {}
                    power = {}
                    for w in winch_ids:
                        b = evb.bundle(w)
                        halls[w] = b.hall_raw
                        power[w] = {"bus_mv": b.bus_mv, "current_ma": b.current_ma, "power_mw": b.power_mw}
                    try:
                        distance = evb.distance()
                    except Exception as exc:
                        distance = None
                        distance_error = str(exc)
                    else:
                        distance_error = None
                    try:
                        imu = evb.imu()
                    except Exception as exc:
                        imu = None
                        imu_error = str(exc)
                    else:
                        imu_error = None
                    with _lock:
                        _state["halls"] = halls
                        _state["power"] = power
                        _state["distance"] = distance
                        _state["imu"] = None if imu is None else imu.__dict__
                        _state["last_update"] = time.time()
                        _state["error"] = None
                        _state["distance_error"] = distance_error
                        _state["imu_error"] = imu_error
                    time.sleep(max(0.0, float(interval)))
        except Exception as exc:
            log.error(f"EVB poll error: {exc}")
            with _lock:
                _state["error"] = str(exc)
            time.sleep(0.5)


@app.get("/data")
def data():
    with _lock:
        return jsonify({**_state, "stale_threshold_ms": STALE_MS})


@app.get("/events")
def events():
    def stream():
        last_sent = 0.0
        last_payload = None
        while True:
            with _lock:
                payload = json.dumps({**_state, "stale_threshold_ms": STALE_MS}, sort_keys=True)
            now = time.time()
            if payload != last_payload or (now - last_sent) > 1.0:
                yield f"data: {payload}\n\n"
                last_payload = payload
                last_sent = now
            time.sleep(0.1)
    return Response(stream(), mimetype="text/event-stream")


@app.get("/")
def index():
    return """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>EVB Dashboard</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body { padding: 16px; }
    canvas { width: 100%; height: 80px; border: 1px solid #eee; border-radius: 6px; }
    .hall-row { display: grid; grid-template-columns: 40px 1fr 80px; gap: 8px; align-items: center; }
    .hall-val { font-variant-numeric: tabular-nums; text-align: right; }
    pre { background: #111; color: #0f0; padding: 8px; border-radius: 6px; }
    .metric-table td, .metric-table th { padding: 4px 8px; font-size: 0.9rem; }
    .badge-stale { background: #d9534f; }
  </style>
</head>
<body>
  <h2>EVB Dashboard <span id="staleBadge" class="badge text-bg-success">fresh</span></h2>
  <div class="row g-3">
    <div class="col-lg-6">
      <div class="card shadow-sm">
        <div class="card-body">
          <h5 class="card-title">Hall (per winch)</h5>
          <div class="d-grid gap-2" id="hallRows">
            <div class="hall-row">
              <div class="fw-semibold">W1</div>
              <canvas id="hallChart1" width="480" height="80"></canvas>
              <div class="hall-val" id="hallVal1">0</div>
            </div>
            <div class="hall-row">
              <div class="fw-semibold">W2</div>
              <canvas id="hallChart2" width="480" height="80"></canvas>
              <div class="hall-val" id="hallVal2">0</div>
            </div>
            <div class="hall-row">
              <div class="fw-semibold">W3</div>
              <canvas id="hallChart3" width="480" height="80"></canvas>
              <div class="hall-val" id="hallVal3">0</div>
            </div>
            <div class="hall-row">
              <div class="fw-semibold">W4</div>
              <canvas id="hallChart4" width="480" height="80"></canvas>
              <div class="hall-val" id="hallVal4">0</div>
            </div>
          </div>
        </div>
      </div>
    </div>
    <div class="col-lg-6">
      <div class="card shadow-sm">
        <div class="card-body">
          <h5 class="card-title">Power (mW)</h5>
          <canvas id="powerChart" width="600" height="200"></canvas>
          <table class="table table-sm table-bordered mt-2 metric-table">
            <thead><tr><th>Winch</th><th>Bus (mV)</th><th>Current (mA)</th><th>Power (mW)</th></tr></thead>
            <tbody id="powerTable"></tbody>
          </table>
        </div>
      </div>
    </div>
    <div class="col-lg-6">
      <div class="card shadow-sm">
        <div class="card-body">
          <h5 class="card-title">IMU</h5>
          <pre id="imuBox">no data</pre>
        </div>
      </div>
    </div>
    <div class="col-lg-6">
      <div class="card shadow-sm">
        <div class="card-body">
          <h5 class="card-title">Distance</h5>
          <pre id="distBox">no data</pre>
        </div>
      </div>
    </div>
  </div>

<script>
const hallHistory = [[],[],[],[]];
const powerHistory = [[],[],[],[]];
const maxPoints = 200;

function pushPoint(arr, v){
  arr.push(v);
  if (arr.length > maxPoints) arr.shift();
}

function drawChart(canvas, series, colors, minOverride=null, maxOverride=null){
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0,0,w,h);
  let all = series.flat();
  if (all.length === 0) return;
  let min = (minOverride !== null) ? minOverride : Math.min(...all);
  let max = (maxOverride !== null) ? maxOverride : Math.max(...all);
  if (min === max) { min -= 1; max += 1; }
  // Axes
  ctx.strokeStyle = '#e5e7eb';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, h-1);
  ctx.lineTo(w, h-1);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(0, 1);
  ctx.lineTo(w, 1);
  ctx.stroke();

  series.forEach((s, i) => {
    ctx.strokeStyle = colors[i];
    ctx.lineWidth = 2;
    ctx.beginPath();
    s.forEach((v, idx) => {
      const x = (idx / (maxPoints - 1)) * w;
      const y = h - ((v - min) / (max - min)) * h;
      if (idx === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });
}

function updateUI(js){
  const halls = js.halls || {};
  const power = js.power || {};
  let powerRows = '';
  for (let i=1;i<=4;i++){
    const hallVal = halls[i] ?? 0;
    const p = power[i] || {};
    pushPoint(hallHistory[i-1], hallVal);
    pushPoint(powerHistory[i-1], p.power_mw ?? 0);
    powerRows += `<tr><td>${i}</td><td>${p.bus_mv ?? ''}</td><td>${p.current_ma ?? ''}</td><td>${p.power_mw ?? ''}</td></tr>`;
    const hallCanvas = document.getElementById(`hallChart${i}`);
    drawChart(hallCanvas, [hallHistory[i-1]], ['#1a7'], 700, 2800);
    const hallValEl = document.getElementById(`hallVal${i}`);
    if (hallValEl) hallValEl.textContent = hallVal.toString().padStart(4, ' ');
  }
  drawChart(document.getElementById('powerChart'), powerHistory, ['#444','#666','#888','#aaa']);
  document.getElementById('powerTable').innerHTML = powerRows;
  const imuText = js.imu ? JSON.stringify(js.imu, null, 2) : `no data${js.imu_error ? ' | ' + js.imu_error : ''}`;
  const distText = js.distance ? JSON.stringify(js.distance, null, 2) : `no data${js.distance_error ? ' | ' + js.distance_error : ''}`;
  document.getElementById('imuBox').textContent = imuText;
  document.getElementById('distBox').textContent = distText;

  const staleThreshold = js.stale_threshold_ms || 1000;
  const ages = [];
  for (let i=1;i<=4;i++){
    const p = power[i] || {};
    if (p.cache_age_ms !== undefined && p.cache_age_ms !== null) ages.push(p.cache_age_ms);
  }
  if (js.distance && js.distance.cache_age_ms !== undefined) ages.push(js.distance.cache_age_ms);
  if (js.imu && js.imu.cache_age_ms !== undefined) ages.push(js.imu.cache_age_ms);
  const maxAge = ages.length ? Math.max(...ages) : null;
  const badge = document.getElementById('staleBadge');
  if (maxAge !== null && maxAge > staleThreshold) {
    badge.textContent = `stale (${maxAge}ms)`;
    badge.className = 'badge text-bg-danger';
  } else if (maxAge !== null) {
    badge.textContent = `fresh (${maxAge}ms)`;
    badge.className = 'badge text-bg-success';
  } else {
    badge.textContent = 'unknown';
    badge.className = 'badge text-bg-secondary';
  }
}

const es = new EventSource('/events');
es.onmessage = (evt) => {
  try { updateUI(JSON.parse(evt.data)); } catch(e) {}
};
</script>
</body>
</html>
"""


def main():
    t = threading.Thread(target=_poll_loop, daemon=True)
    t.start()
    app.run(host=CONFIG["web"]["host"], port=CONFIG["web"]["port"], threaded=True)


if __name__ == "__main__":
    main()
