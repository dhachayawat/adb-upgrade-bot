# app/ui/components/device_detail.py
# หน้าแสดงรายละเอียดต่ออุปกรณ์ + ปุ่มควบคุม + Snap หน้าจอ
import html

def render_device_detail(device_id: str) -> str:
    safe_id = html.escape(device_id, quote=True)

    html_str = """
<div class="d-flex align-items-center justify-content-between mb-3">
  <div>
    <a href="/" class="btn btn-link p-0">&larr; Back</a>
    <h3 class="m-0 mt-2">Device — <span id="dev-name">...</span> <span class="badge text-bg-secondary">__DEV_ID__</span></h3>
    <div class="text-muted small" id="dev-addr">...</div>
  </div>
  <div class="d-flex gap-2">
    <a href="/summary" class="btn btn-outline-secondary btn-sm">Summary (7d)</a>
    <button class="btn btn-outline-secondary btn-sm" onclick="refresh()">Refresh</button>
  </div>
</div>

<div class="row g-3">
  <div class="col-12 col-lg-6">
    <div class="card shadow-sm">
      <div class="card-body">
        <div class="d-flex justify-content-between align-items-start">
          <div>
            <div class="small text-muted">State</div>
            <div id="badge-state">...</div>
          </div>
          <div class="d-flex gap-2">
            <button class="btn btn-success btn-sm"   id="btn-start"  onclick="act('start')">Start</button>
            <button class="btn btn-warning btn-sm"   id="btn-pause"  onclick="act('pause')">Pause</button>
            <button class="btn btn-warning btn-sm"   id="btn-resume" onclick="act('resume')">Resume</button>
            <button class="btn btn-danger btn-sm"    id="btn-stop"   onclick="act('stop')">Stop</button>
          </div>
        </div>

        <div class="row mt-3 g-2">
          <div class="col-4">
            <div class="small text-muted">Uptime</div>
            <div class="fw-bold"><span id="uptime">0</span> min</div>
          </div>
          <div class="col-4">
            <div class="small text-muted">Items Done</div>
            <div class="fw-bold" id="items-done">0</div>
          </div>
          <div class="col-4">
            <div class="small text-muted">Target</div>
            <div class="d-flex align-items-center gap-2">
              <input type="number" min="1" max="10" class="form-control form-control-sm w-50" id="tl" value="5">
              <button class="btn btn-sm btn-outline-primary" onclick="setTarget(false)">Apply</button>
              <button class="btn btn-sm btn-primary" onclick="setTarget(true)" title="Apply & Save to config">Apply+Save</button>
            </div>
          </div>
        </div>

        <div class="mt-3">
          <div class="small text-muted">Breaks by Level (today)</div>
          <div id="hist">-</div>
        </div>

        <div class="mt-3 row g-2">
          <div class="col-4">
            <div class="small text-muted">Success clicks</div>
            <div class="fw-bold" id="succ">0</div>
          </div>
          <div class="col-4">
            <div class="small text-muted">Fail non-break</div>
            <div class="fw-bold" id="fnb">0</div>
          </div>
          <div class="col-4">
            <div class="small text-muted">Upgrade clicks</div>
            <div class="fw-bold" id="uc">0</div>
          </div>
        </div>

      </div>
    </div>
  </div>

  <div class="col-12 col-lg-6">
    <div class="card shadow-sm">
      <div class="card-body">
        <div class="d-flex justify-content-between align-items-center">
          <h5 class="card-title mb-0">Screen</h5>
          <div class="d-flex gap-2">
            <button class="btn btn-outline-primary btn-sm" onclick="snap()">Snap</button>
          </div>
        </div>
        <div class="mt-2">
          <img id="shot" src="" alt="screenshot" style="max-width:100%; border:1px solid #eee; border-radius:.5rem;">
        </div>
      </div>
    </div>
  </div>
</div>

<script>
const DEV_ID = "__DEV_ID__";

async function api(path, opt){
  const r = await fetch(path, Object.assign({ headers: { 'Content-Type':'application/json' } }, opt || {}));
  // ถ้าเป็นรูปภาพจะไม่เรียกใช้ฟังก์ชันนี้
  return r.json();
}

function stateBadge(state){
  const map = { running:'success', paused:'warning', idle:'secondary', error:'danger', stopping:'secondary' };
  const cls = map[state] || 'secondary';
  return `<span class="badge text-bg-${cls} text-uppercase">${state}</span>`;
}

function histHtml(h){
  const ks = Object.keys(h || {}).sort((a,b)=>parseInt(a)-parseInt(b));
  if(ks.length === 0) return '<div class="text-muted small">No breaks</div>';
  return ks.map(k => `<span class="badge text-bg-dark me-1">+${k}: ${h[k]}</span>`).join(' ');
}

function esc(s){
  return (''+s).replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

async function load(){
  const d = await api(`/api/devices/${DEV_ID}/status`);
  document.getElementById('dev-name').innerText = d.name || '';
  document.getElementById('dev-addr').innerText = d.device || '';
  document.getElementById('badge-state').innerHTML = stateBadge(d.state);
  const up = (typeof d.uptime_min === 'number') ? d.uptime_min.toFixed(2) : (d.uptime_min || 0);
  document.getElementById('uptime').innerText = up;
  document.getElementById('items-done').innerText = d.items_upgraded_done ?? 0;
  document.getElementById('tl').value = d.target_level ?? 5;
  document.getElementById('hist').innerHTML = histHtml(d.break_histogram);
  document.getElementById('succ').innerText = d.successes ?? 0;
  document.getElementById('fnb').innerText = d.fail_nonbreak ?? 0;
  document.getElementById('uc').innerText = d.upgrade_clicks ?? 0;

  // ปุ่มตามสถานะ
  document.getElementById('btn-start').style.display  = (d.state==='idle' || d.state==='error') ? 'inline-block' : 'none';
  document.getElementById('btn-pause').style.display  = (d.state==='running') ? 'inline-block' : 'none';
  document.getElementById('btn-resume').style.display = (d.state==='paused')  ? 'inline-block' : 'none';
  document.getElementById('btn-stop').style.display   = (d.state==='running' || d.state==='paused') ? 'inline-block' : 'none';
}

async function act(cmd){
  await api(`/api/devices/${DEV_ID}/` + cmd, { method:'POST' });
  refresh();
}

async function setTarget(persist){
  const v = parseInt(document.getElementById('tl').value, 10);
  if (isNaN(v)) return;
  await api(`/api/devices/${DEV_ID}/target-level`, { method:'PUT', body: JSON.stringify({ target_level: v, persist: !!persist }) });
  refresh();
}

async function snap(){
  // รูปต้องโหลดเป็น binary จึงไม่ใช้ api()
  const img = document.getElementById('shot');
  img.src = `/api/devices/${DEV_ID}/shot?ts=` + Date.now();
}

async function refresh(){ await load(); }
document.addEventListener('DOMContentLoaded', ()=>{ load(); snap(); });
</script>
"""
    return html_str.replace("__DEV_ID__", safe_id)
