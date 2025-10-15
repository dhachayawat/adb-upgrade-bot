import os, time, json, base64
from flask import Flask, request, jsonify, Response
import cv2

from app import config as C
from app import cv_utils as CV
from app import controller as CTRL

app = Flask(__name__)

HTML = r"""
<!doctype html>
<html lang="th">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>ADB Upgrade Bot - Control & Config</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<style>
 body{background:#0f1115;color:#fff}
 .navbar{background:#1b1f2a}
 .card{background:#151922;border-color:#242a38;color:#fff}
 .card .card-header{color:#fff}
 pre{background:#0b0d12;color:#fff;border:1px solid #242a38;border-radius:.5rem;padding:.75rem;max-height:320px;overflow:auto}
 canvas{border:1px solid #242a38;border-radius:.5rem}
 .pill{border-radius:999px}
 .form-control, .form-select{background:#0b0d12;border-color:#242a38;color:#fff}
 .form-text{color:#cfd6df}
 .btn-outline-light{color:#fff;border-color:#adb5bd}
 .badge{color:#fff}
 .text-secondary{color:#d0d6df!important}
 .accordion-button{background:#0b0d12;color:#fff}
 .accordion-body{background:#0b0d12;color:#fff}
</style>
</head>
<body>
<nav class="navbar navbar-expand-lg navbar-dark mb-3">
  <div class="container-fluid">
    <span class="navbar-brand">ADB Upgrade Bot</span>
    <div class="d-flex gap-2">
      <button class="btn btn-success btn-sm" onclick="ctrl('start')">▶ Start</button>
      <button class="btn btn-danger btn-sm" onclick="ctrl('stop')">■ Stop</button>
      <button class="btn btn-secondary btn-sm" onclick="doSnap()">📸 Screenshot</button>
      <button class="btn btn-secondary btn-sm" onclick="doPreview()">🖼 Preview</button>
    </div>
  </div>
</nav>

<div class="container-fluid">
  <div class="row g-3">
    <div class="col-xl-8">
      <div class="card">
        <div class="card-header d-flex align-items-center justify-content-between">
          <div class="d-flex align-items-center gap-2">
            <span class="badge bg-info text-dark pill" id="runstate">state: unknown</span>
            <span class="badge bg-secondary pill" id="phase">phase: -</span>
            <span class="badge bg-secondary pill" id="batch">batch: -</span>
            <span class="badge bg-secondary pill" id="item">item: -</span>
          </div>
          <div class="d-flex gap-2">
            <button class="btn btn-outline-warning btn-sm" onclick="undo()">↶ Undo</button>
            <button class="btn btn-outline-danger btn-sm" onclick="clearAll()">Clear</button>
            <button class="btn btn-primary btn-sm" onclick="apply()">✅ Apply (ENV)</button>
            <button class="btn btn-outline-primary btn-sm" onclick="saveCfg()">💾 Save</button>
            <button class="btn btn-outline-primary btn-sm" onclick="loadCfg()">📂 Load</button>
          </div>
        </div>

        <div class="card-body">
          <!-- โหมดปรับค่า (ลบ Insert Btn ออกแล้ว) -->
          <div class="mb-2">
            <div class="btn-group" role="group">
              <button class="btn btn-outline-light btn-sm" onclick="mode='slot';setInfo('Slot Center & ROI (drag)')">Slot</button>
              <button class="btn btn-outline-light btn-sm" onclick="mode='overlay';setInfo('Overlay ROI (drag)')">Overlay</button>
              <button class="btn btn-outline-light btn-sm" onclick="mode='insert_roi';setInfo('Insert ROI (drag)')">Insert ROI</button>
              <button class="btn btn-outline-light btn-sm" onclick="mode='upgrade';setInfo('Upgrade Button (click)')">Upgrade Btn</button>
              <button class="btn btn-outline-light btn-sm" onclick="mode='items';setInfo('Items (click 6)')">Items</button>
              <button class="btn btn-outline-light btn-sm" onclick="mode='swipe';setInfo('Swipe (drag line)')">Swipe</button>
            </div>
            <span class="ms-2 text-info" id="info">Ready</span>
          </div>

          <!-- แผง Screenshot + Refresh + ปรับดีเลย์การหา/ใส่ -->
          <div class="d-flex justify-content-between align-items-center mb-2">
            <div class="d-flex gap-2">
              <div class="input-group input-group-sm" style="width: 240px;">
                <span class="input-group-text">Insert pre delay</span>
                <input id="inp_pre" type="number" min="0" step="0.05" class="form-control">
                <span class="input-group-text">s</span>
              </div>
              <div class="input-group input-group-sm" style="width: 240px;">
                <span class="input-group-text">Find delay</span>
                <input id="inp_find" type="number" min="0" step="0.05" class="form-control">
                <span class="input-group-text">s</span>
              </div>
              <div class="input-group input-group-sm" style="width: 240px;">
                <span class="input-group-text">Find timeout</span>
                <input id="inp_timeout" type="number" min="0.5" step="0.1" class="form-control">
                <span class="input-group-text">s</span>
              </div>
            </div>
            <button class="btn btn-outline-light btn-sm" onclick="reloadShot()">🔄 Refresh Screenshot</button>
          </div>

          <canvas id="cv" width="1280" height="720"></canvas>
          <div class="form-text">Drag = กรอบ/เส้น | Click = จุด | Undo/Clear | Save/Load = ไฟล์คอนฟิก</div>
        </div>
      </div>

      <!-- Result JSON: แบบพับได้ (เริ่มต้นหุบ) -->
      <div class="accordion mt-3" id="jsonAcc">
        <div class="accordion-item">
          <h2 class="accordion-header" id="jsonHead">
            <button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#jsonBody" aria-expanded="false" aria-controls="jsonBody">
              Result JSON (click to expand)
            </button>
          </h2>
          <div id="jsonBody" class="accordion-collapse collapse" aria-labelledby="jsonHead" data-bs-parent="#jsonAcc">
            <div class="accordion-body">
              <pre id="json"></pre>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Status / Logs -->
    <div class="col-xl-4">
      <div class="card">
        <div class="card-header">Live Status</div>
        <div class="card-body">
          <div class="row g-2 mb-2">
            <div class="col-6">
              <div class="card text-bg-dark">
                <div class="card-body py-2">
                  <div class="small text-secondary">Processed</div>
                  <div class="fs-4" id="stat_processed">0</div>
                </div>
              </div>
            </div>
            <div class="col-6">
              <div class="card text-bg-dark">
                <div class="card-body py-2">
                  <div class="small text-secondary">+5</div>
                  <div class="fs-4 text-success" id="stat_plus5">0</div>
                </div>
              </div>
            </div>
            <div class="col-4">
              <div class="card text-bg-dark">
                <div class="card-body py-2">
                  <div class="small text-secondary">Broken</div>
                  <div class="fs-5 text-warning" id="stat_broken">0</div>
                </div>
              </div>
            </div>
            <div class="col-4">
              <div class="card text-bg-dark">
                <div class="card-body py-2">
                  <div class="small text-secondary">Skipped</div>
                  <div class="fs-5 text-info" id="stat_skipped">0</div>
                </div>
              </div>
            </div>
            <div class="col-4">
              <div class="card text-bg-dark">
                <div class="card-body py-2">
                  <div class="small text-secondary">Errors</div>
                  <div class="fs-5 text-danger" id="stat_errors">0</div>
                </div>
              </div>
            </div>
          </div>
          <div class="small text-secondary">Last message</div>
          <div class="mb-2" id="lastmsg">-</div>
          <button class="btn btn-sm btn-outline-light" onclick="fetchStatus(true)">Refresh status</button>
        </div>
      </div>

      <div class="card mt-3">
        <div class="card-header d-flex justify-content-between">
          <span>Logs</span>
          <button class="btn btn-sm btn-outline-warning" onclick="clearLogBox()">Clear view</button>
        </div>
        <div class="card-body">
          <div id="log" style="height:420px; overflow:auto; white-space:pre-wrap; font-family: ui-monospace, monospace; color:#fff;"></div>
        </div>
      </div>
    </div>

  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
let img = new Image();
let cvs = document.getElementById('cv');
let ctx = cvs.getContext('2d');
let W=1280, H=720;
let mode='slot';
let history=[];

let state = {
  slot:        { center:[%(scx)d,%(scy)d], roi:[%(srw)d,%(srh)d] },
  overlay_abs: { x1:%(ox1)d, y1:%(oy1)d, w:%(ow)d, h:%(oh)d },
  insert_roi:  { x1:%(irx1)d, y1:%(iry1)d, w:%(irw)d, h:%(irh)d },
  // ไม่มี insert.base แล้ว เพราะใช้ CV เท่านั้น
  insert:      { pre_delay:%(pre)0.2f, find_delay:%(find)0.2f, find_timeout:%(ftime)0.2f },
  upgrade_btn: [%(ux)d,%(uy)d],
  items: %(items)s,
  swipe: { x:%(sx)d, y:%(sy)d, dy:%(sdy)d, ms:%(sms)d }
};

let dragStart=null, lastShot=null;

function bindDelayInputs(){
  document.getElementById('inp_pre').value     = state.insert.pre_delay ?? 0.8;
  document.getElementById('inp_find').value    = state.insert.find_delay ?? 0.25;
  document.getElementById('inp_timeout').value = state.insert.find_timeout ?? 5.0;
  document.getElementById('inp_pre').addEventListener('change', e=>{ state.insert.pre_delay = parseFloat(e.target.value)||0; showJSON(); });
  document.getElementById('inp_find').addEventListener('change', e=>{ state.insert.find_delay = parseFloat(e.target.value)||0; showJSON(); });
  document.getElementById('inp_timeout').addEventListener('change', e=>{ state.insert.find_timeout = parseFloat(e.target.value)||0; showJSON(); });
}

function pushHist(){ history.push(JSON.stringify(state)); if(history.length>50) history.shift(); }
function undo(){ if(history.length===0){ return; } state = JSON.parse(history.pop()); draw(); showJSON(); bindDelayInputs(); }
function clearAll(){
  pushHist();
  state.items=[];
  state.overlay_abs={x1: Math.floor(W*0.5-200), y1: Math.floor(H*0.3-100), w:400, h:200};
  state.insert_roi={x1: %(irx1)d, y1: %(iry1)d, w: %(irw)d, h: %(irh)d};
  draw(); showJSON(); bindDelayInputs();
}
function setInfo(t){ /* optional toast */ }

async function reloadShot(){
  const r = await fetch('/api/shot'); const j = await r.json();
  lastShot = j; img.src = 'data:image/png;base64,'+j.base64; img.onload = ()=>{ draw(); };
}

async function ctrl(action){ const r = await fetch('/api/'+action, {method:'POST'}); await r.json(); fetchStatus(true); }
async function doSnap(){ await fetch('/api/snap', {method:'POST'}); }
async function doPreview(){ await fetch('/api/preview', {method:'POST'}); }

function draw(){
  ctx.clearRect(0,0,W,H); if(img) ctx.drawImage(img,0,0,W,H);
  for(let i=0;i<state.items.length;i++){ const [x,y]=state.items[i]; cross(x,y,'#08f'); text(`#${i+1}(${x},${y})`, x+6, y-8, '#fff'); }
  const [cx,cy]=state.slot.center, [rw,rh]=state.slot.roi; rect(cx-rw/2, cy-rh/2, rw, rh, '#ffc107'); cross(cx,cy,'#ffc107'); text(`slot_center=(${cx},${cy}) roi=${rw}x${rh}`, cx-rw/2, cy-rh/2-6, '#000', '#ffc107');
  const o=state.overlay_abs; rect(o.x1,o.y1,o.w,o.h,'#0dcaf0'); text(`overlay_abs=(${o.x1},${o.y1},${o.w}x${o.h})`, o.x1, o.y1-6, '#000', '#0dcaf0');
  const ir=state.insert_roi; rect(ir.x1,ir.y1,ir.w,ir.h,'#d63384'); text(`insert_roi=(${ir.x1},${ir.y1},${ir.w}x${ir.h})`, ir.x1, ir.y1-6, '#fff', '#d63384');
  cross(state.upgrade_btn[0], state.upgrade_btn[1], '#fd7e14'); text(`upgrade=${state.upgrade_btn}`, state.upgrade_btn[0]+8, state.upgrade_btn[1]-8, '#fff');
  ctx.strokeStyle='#20c997'; ctx.lineWidth=3; ctx.beginPath(); ctx.moveTo(state.swipe.x, state.swipe.y); ctx.lineTo(state.swipe.x, state.swipe.y+state.swipe.dy); ctx.stroke();
  text(`swipe (${state.swipe.x},${state.swipe.y})->(${state.swipe.x},${state.swipe.y+state.swipe.dy}) ${state.swipe.ms}ms`, Math.min(state.swipe.x,state.swipe.x)+6, Math.min(state.swipe.y,state.swipe.y+state.swipe.dy)-8, '#000', '#20c997');
  showJSON();
}
function showJSON(){
  const obj = {
    screen: { size: (lastShot? `${lastShot.w}x${lastShot.h}` : "1280x720") },
    slot: state.slot,
    insert_roi: state.insert_roi,
    insert: state.insert,     // มีเฉพาะค่า delay/timeout
    upgrade_btn: state.upgrade_btn,
    items: state.items.slice(0,6),
    tray_swipe: state.swipe,
    overlay_roi_abs: state.overlay_abs
  };
  document.getElementById('json').textContent = JSON.stringify(obj, null, 2);
}
function cross(x,y,color){ ctx.strokeStyle=color; ctx.lineWidth=2; ctx.beginPath(); ctx.moveTo(x-8,y); ctx.lineTo(x+8,y); ctx.moveTo(x,y-8); ctx.lineTo(x,y+8); ctx.stroke(); }
function rect(x,y,w,h,color){ ctx.strokeStyle=color; ctx.lineWidth=2; ctx.strokeRect(x,y,w,h); }
function text(t,x,y,fg='#fff',bg=null){ ctx.font='12px system-ui, Segoe UI, Arial'; if(bg){ const m=ctx.measureText(t); ctx.fillStyle=bg; ctx.fillRect(x-2,y-12,m.width+6,14); } ctx.fillStyle=fg; ctx.fillText(t,x,y); }

cvs.addEventListener('mousedown', (e)=>{ const r=cvs.getBoundingClientRect(); const x=Math.round(e.clientX-r.left), y=Math.round(e.clientY-r.top); if(['slot','overlay','swipe','insert_roi'].includes(mode)){ pushHist(); dragStart=[x,y]; }});
cvs.addEventListener('mouseup',   (e)=>{
  const r=cvs.getBoundingClientRect(); const x=Math.round(e.clientX-r.left), y=Math.round(e.clientY-r.top);
  if(mode==='slot' && dragStart){ const [sx,sy]=dragStart; const w=Math.abs(x-sx), h=Math.abs(y-sy); const x1=Math.min(sx,x), y1=Math.min(sy,y); const cx=x1+Math.round(w/2), cy=y1+Math.round(h/2); state.slot.center=[cx,cy]; state.slot.roi=[w,h]; dragStart=null; draw(); }
  else if(mode==='overlay' && dragStart){ const [sx,sy]=dragStart; const w=Math.abs(x-sx), h=Math.abs(y-sy); state.overlay_abs={x1:Math.min(sx,x), y1:Math.min(sy,y), w:w, h:h}; dragStart=null; draw(); }
  else if(mode==='insert_roi' && dragStart){ const [sx,sy]=dragStart; const w=Math.abs(x-sx), h=Math.abs(y-sy); state.insert_roi={x1:Math.min(sx,x), y1:Math.min(sy,y), w:w, h:h}; dragStart=null; draw(); }
  else if(mode==='swipe' && dragStart){ const [sx,sy]=dragStart; state.swipe.x=sx; state.swipe.y=sy; state.swipe.dy=(y-sy); dragStart=null; draw(); }
});
cvs.addEventListener('click', (e)=>{
  const r=cvs.getBoundingClientRect(); const x=Math.round(e.clientX-r.left), y=Math.round(e.clientY-r.top);
  if(mode==='upgrade'){ pushHist(); state.upgrade_btn=[x,y]; draw(); }
  else if(mode==='items'){ if(state.items.length<6){ pushHist(); state.items.push([x,y]); draw(); } }
});

async function apply(){
  const payload = payloadFromState();
  const r = await fetch('/api/apply', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  await r.json();
}
async function saveCfg(){
  const r = await fetch('/api/save_config', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payloadFromState())});
  await r.json();
}
async function loadCfg(){
  const r = await fetch('/api/load_config'); const j = await r.json();
  if(j.ok){ state = j.state; draw(); showJSON(); bindDelayInputs(); }
}

function payloadFromState(){
  return {
    slot_center: state.slot.center,
    slot_roi: state.slot.roi,
    overlay_abs: state.overlay_abs,
    insert_roi: state.insert_roi,
    // ไม่ส่ง insert_base แล้ว
    insert: { pre_delay: state.insert.pre_delay||0, find_delay: state.insert.find_delay||0, find_timeout: state.insert.find_timeout||0 },
    upgrade_btn: state.upgrade_btn,
    items: state.items.slice(0,6),
    swipe: state.swipe
  };
}

async function fetchStatus(force=false){
  const r = await fetch('/api/status'); const j = await r.json();
  document.getElementById('runstate').textContent = 'state: ' + (j.state.running ? 'running' : 'stopped');
  document.getElementById('phase').textContent    = 'phase: ' + j.state.phase;
  document.getElementById('batch').textContent    = 'batch: ' + j.state.batch;
  document.getElementById('item').textContent     = 'item: ' + j.state.current_item_idx;
  document.getElementById('stat_processed').textContent = j.state.counts.processed;
  document.getElementById('stat_plus5').textContent     = j.state.counts["+5"];
  document.getElementById('stat_broken').textContent    = j.state.counts.broken;
  document.getElementById('stat_skipped').textContent   = j.state.counts.skipped;
  document.getElementById('stat_errors').textContent    = j.state.counts.errors;
  document.getElementById('lastmsg').textContent = j.state.last_message || '-';

  const logEl = document.getElementById('log');
  if(force || logEl.dataset.len != j.logs.length){
    logEl.textContent = j.logs.join("\n");
    logEl.dataset.len = j.logs.length;
    logEl.scrollTop = logEl.scrollHeight;
  }
}

function clearLogBox(){
  const logEl = document.getElementById('log');
  logEl.textContent = '';
  logEl.dataset.len = 0;
}

async function reloadShot(){
  const r = await fetch('/api/shot'); const j = await r.json();
  lastShot = j; img.src = 'data:image/png;base64,'+j.base64; img.onload = ()=>{ draw(); };
}

function cross(x,y,color){ ctx.strokeStyle=color; ctx.lineWidth=2; ctx.beginPath(); ctx.moveTo(x-8,y); ctx.lineTo(x+8,y); ctx.moveTo(x,y-8); ctx.lineTo(x,y+8); ctx.stroke(); }
function rect(x,y,w,h,color){ ctx.strokeStyle=color; ctx.lineWidth=2; ctx.strokeRect(x,y,w,h); }
function text(t,x,y,fg='#fff',bg=null){ ctx.font='12px system-ui, Segoe UI, Arial'; if(bg){ const m=ctx.measureText(t); ctx.fillStyle=bg; ctx.fillRect(x-2,y-12,m.width+6,14); } ctx.fillStyle=fg; ctx.fillText(t,x,y); }

async function init(){
  bindDelayInputs();
  await reloadShot();
  await fetchStatus(true);
  setInterval(fetchStatus, 1000);
}
init();
</script>
</body>
</html>
"""

def bgr_to_base64(img):
    ok, buf = cv2.imencode(".png", img)
    if not ok: raise RuntimeError("cv2.imencode failed")
    return base64.b64encode(buf.tobytes()).decode("ascii")

def _current_state_from_C():
    return {
        "slot":        {"center":[C.SLOT_CENTER_X, C.SLOT_CENTER_Y], "roi":[C.SLOT_ROI_W, C.SLOT_ROI_H]},
        "overlay_abs": {"x1":C.OVERLAY_X1, "y1":C.OVERLAY_Y1, "w":C.OVERLAY_W, "h":C.OVERLAY_H},
        "insert_roi":  {"x1":C.INSERT_ROI_X1, "y1":C.INSERT_ROI_Y1, "w":C.INSERT_ROI_W, "h":C.INSERT_ROI_H},
        "insert":      {"pre_delay":C.PRE_INSERT_DELAY, "find_delay":C.INSERT_FIND_DELAY_SEC, "find_timeout":C.INSERT_FIND_TIMEOUT_SEC},
        "upgrade_btn": [C.UPGRADE_BTN_X, C.UPGRADE_BTN_Y],
        "items":       C.ITEM_POSITIONS,
        "swipe":       {"x":C.TRAY_SWIPE_X, "y":C.TRAY_SWIPE_Y, "dy":C.TRAY_SWIPE_DY, "ms":C.TRAY_SWIPE_MS},
    }

@app.get("/")
def index():
    s = _current_state_from_C()
    html = HTML % dict(
        scx=s["slot"]["center"][0], scy=s["slot"]["center"][1], srw=s["slot"]["roi"][0], srh=s["slot"]["roi"][1],
        ox1=s["overlay_abs"]["x1"], oy1=s["overlay_abs"]["y1"], ow=s["overlay_abs"]["w"], oh=s["overlay_abs"]["h"],
        irx1=s["insert_roi"]["x1"], iry1=s["insert_roi"]["y1"], irw=s["insert_roi"]["w"], irh=s["insert_roi"]["h"],
        pre=s["insert"]["pre_delay"], find=s["insert"]["find_delay"], ftime=s["insert"]["find_timeout"],
        ux=s["upgrade_btn"][0], uy=s["upgrade_btn"][1],
        items=json.dumps(s["items"]),
        sx=s["swipe"]["x"], sy=s["swipe"]["y"], sdy=s["swipe"]["dy"], sms=s["swipe"]["ms"],
    )
    return Response(html, mimetype="text/html")

@app.get("/api/state")
def api_state():
    return jsonify({"running": CTRL.is_running()})

@app.get("/api/status")
def api_status():
    return jsonify(CTRL.get_status())

@app.post("/api/start")
def api_start(): ok, msg = CTRL.start(); return jsonify({"ok": ok, "message": msg})

@app.post("/api/stop")
def api_stop():  ok, msg = CTRL.stop();  return jsonify({"ok": ok, "message": msg})

@app.post("/api/snap")
def api_snap():
    path, size = CTRL.snapshot("web")
    return jsonify({"ok": True, "path": path, "size": size})

@app.post("/api/preview")
def api_preview():
    path = CTRL.preview()
    return jsonify({"ok": True, "path": path})

@app.get("/api/shot")
def api_shot():
    img = CV.screencap_bgr(save_tag="config")
    h, w = img.shape[:2]
    ok, buf = cv2.imencode(".png", img)
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")
    path = time.strftime(f"{C.CACHE_DIR}/debug/shot_%Y%m%d-%H%M%S.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imwrite(path, img)
    return jsonify({"w": w, "h": h, "base64": b64, "path": path})

@app.get("/api/load_config")
def api_load_config():
    path = C.CONFIG_FILE
    if not os.path.exists(path):
        return jsonify({"ok": False, "message": f"No config file: {path}"})
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        state = {
            "slot": {"center": d.get("slot_center",[C.SLOT_CENTER_X,C.SLOT_CENTER_Y]),
                     "roi":    d.get("slot_roi",[C.SLOT_ROI_W,C.SLOT_ROI_H])},
            "overlay_abs": d.get("overlay_abs", {"x1":C.OVERLAY_X1,"y1":C.OVERLAY_Y1,"w":C.OVERLAY_W,"h":C.OVERLAY_H}),
            "insert_roi":  d.get("insert_roi",  {"x1":C.INSERT_ROI_X1,"y1":C.INSERT_ROI_Y1,"w":C.INSERT_ROI_W,"h":C.INSERT_ROI_H}),
            "insert":      {
                "pre_delay": d.get("insert",{}).get("pre_delay", C.PRE_INSERT_DELAY),
                "find_delay": d.get("insert",{}).get("find_delay", C.INSERT_FIND_DELAY_SEC),
                "find_timeout": d.get("insert",{}).get("find_timeout", C.INSERT_FIND_TIMEOUT_SEC),
            },
            "upgrade_btn": d.get("upgrade_btn",[C.UPGRADE_BTN_X, C.UPGRADE_BTN_Y]),
            "items":       d.get("items", C.ITEM_POSITIONS),
            "swipe":       d.get("swipe", {"x":C.TRAY_SWIPE_X,"y":C.TRAY_SWIPE_Y,"dy":C.TRAY_SWIPE_DY,"ms":C.TRAY_SWIPE_MS}),
        }
        return jsonify({"ok": True, "state": state, "config_file": path})
    except Exception as e:
        return jsonify({"ok": False, "message": f"load failed: {e}"})

@app.post("/api/save_config")
def api_save_config():
    d = request.get_json(force=True)
    path = C.CONFIG_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        return jsonify({"ok": True, "message": f"saved to {path}"})
    except Exception as e:
        return jsonify({"ok": False, "message": f"save failed: {e}"})

@app.post("/api/apply")
def api_apply():
    d = request.get_json(force=True)
    out_dir = os.path.join(C.CACHE_DIR, "debug")
    os.makedirs(out_dir, exist_ok=True)
    out_json = os.path.join(out_dir, "config_result.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return jsonify({"ok": True, "message": f"Saved: {out_json}"})

def run(host="0.0.0.0", port=8765):
    print(f"[config-ui] http://{host}:{port}")
    app.run(host=host, port=port, debug=False)

if __name__ == "__main__":
    run()
