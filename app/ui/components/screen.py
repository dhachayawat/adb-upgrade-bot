# app/ui/components/screen.py
def render_screen() -> str:
    return """
<div class="card mb-3">
  <div class="card-header d-flex align-items-center justify-content-between">
    <span>Screenshot & Config Overlays</span>
    <div class="d-flex gap-2">
      <button class="btn btn-secondary btn-sm" onclick="refreshShot()">Refresh</button>
      <button class="btn btn-outline-secondary btn-sm" onclick="snapAndRefresh()">Screenshot</button>
    </div>
  </div>
  <div class="card-body">
    <div id="stage" class="img-stage">
      <img id="shotimg" class="img-fluid" />
      <!-- overlay_abs -->
      <div id="roi-overlay" class="roi" style="border-color:#ff7eb6">
        <div class="label">overlay_abs</div>
        <div class="handle"></div>
      </div>
      <!-- insert_roi -->
      <div id="roi-insert"  class="roi" style="border-color:#ffb347">
        <div class="label">insert_roi</div>
        <div class="handle"></div>
      </div>
      <!-- slot roi (center + size) -->
      <div id="roi-slot" class="roi" style="border-color:#34d399">
        <div class="label">slot_roi</div>
        <div class="handle"></div>
      </div>
      <!-- slot_status ROI (resizable & persisted) -->
      <div id="roi-slot-status" class="roi" style="border-color:#39c0ff">
        <div class="label">slot_status ROI</div>
        <div class="handle"></div>
      </div>
      <!-- points -->
      <div id="pt-slot-center" class="pt yellow" title="slot_center"></div>
      <div id="pt-slot-status" class="pt cyan"   title="slot_status"></div>
      <div id="pt-upg" class="pt red" title="upgrade_btn"></div>
      <div id="pt-item-1" class="pt" title="item #1"></div>
      <div id="pt-item-2" class="pt" title="item #2"></div>
      <div id="pt-item-3" class="pt" title="item #3"></div>
      <div id="pt-item-4" class="pt" title="item #4"></div>
      <div id="pt-item-5" class="pt" title="item #5"></div>
      <div id="pt-item-6" class="pt" title="item #6"></div>
      <!-- swipe visual -->
      <div id="swipe-line" class="sw-line"></div>
      <div id="pt-swipe" class="pt yellow" title="Swipe Start"></div>
      <div id="pt-swipe-handle" class="sw-handle" title="Swipe Handle (adjust dY)"></div>
    </div>
    <div class="small text-secondary mt-2">
      ป้ายชื่อ: device(host:port), slot_center, slot_status, slot_roi, <b>slot_status ROI</b>, overlay_abs, insert_roi, upgrade_btn, items[1..6], swipe
    </div>
  </div>
</div>

<div class="card">
  <div class="card-header">Raw Config (JSON)</div>
  <div class="card-body">
    <details>
      <summary class="text-white">Show/Hide</summary>
      <pre id="dump"></pre>
    </details>
  </div>
</div>

<script>
let CFG = null, lastCfg = null;
let shotMeta = {w:1280, h:720};
const $ = sel => document.querySelector(sel);

// slot_status ROI size (persisted)
let STATUS_W = 80, STATUS_H = 80;

// ---------- items grid ----------
function fillItemsGrid(items){
  const wrap = $('#items'); if(!wrap) return; wrap.innerHTML = '';
  for(let i=0;i<6;i++){
    const [x,y] = items[i] || [0,0];
    const div = document.createElement('div'); div.className='col-6';
    div.innerHTML = `
      <div class="input-group input-group-sm mb-1">
        <span class="input-group-text">#${i+1} X</span>
        <input id="itemx${i+1}" type="number" class="form-control" value="${x}">
        <span class="input-group-text">Y</span>
        <input id="itemy${i+1}" type="number" class="form-control" value="${y}">
      </div>`;
    wrap.appendChild(div);
  }
}
function getItemsFromForm(){
  const arr=[]; for(let i=1;i<=6;i++){
    const xEl = $('#itemx'+i), yEl = $('#itemy'+i);
    if(!xEl || !yEl){ arr.push([0,0]); continue; }
    const x = parseInt(xEl.value), y = parseInt(yEl.value);
    arr.push([x,y]);
  } return arr;
}

// ---------- device (host:port) ----------
function setDeviceInputs(dev){
  const hostEl = $('#adbHost'), portEl = $('#adbPort');
  const [host, port] = (dev||'').split(':');
  if(hostEl) hostEl.value = host || '';
  if(portEl) portEl.value = port || '';
}
function getDeviceString(){
  const host = ($('#adbHost')?.value || 'host.docker.internal').trim();
  const port = ($('#adbPort')?.value || '5605').trim();
  return `${host}:${port}`;
}

// ---------- form fill / collect ----------
function fillForm(cfg){
  lastCfg = JSON.parse(JSON.stringify(cfg));
  const cfgpath = $('#cfgpath'); if(cfgpath) cfgpath.textContent = (cfg.paths && cfg.paths.config_file) || '';

  setDeviceInputs(cfg.device);

  // slot & status
  const slotcx = $('#slotcx'), slotcy = $('#slotcy'), slotw = $('#slotw'), sloth = $('#sloth');
  const slotsx = $('#slotsx'), slotsy = $('#slotsy');
  if(slotcx) slotcx.value = cfg.slot_center[0];
  if(slotcy) slotcy.value = cfg.slot_center[1];
  if(slotw)  slotw.value  = cfg.slot_roi[0];
  if(sloth)  sloth.value  = cfg.slot_roi[1];
  if(slotsx) slotsx.value = cfg.slot_status[0];
  if(slotsy) slotsy.value = cfg.slot_status[1];

  STATUS_W = (cfg.slot_status_roi && cfg.slot_status_roi[0]) ? cfg.slot_status_roi[0] : 80;
  STATUS_H = (cfg.slot_status_roi && cfg.slot_status_roi[1]) ? cfg.slot_status_roi[1] : 80;

  // swipe
  const swx = $('#swx'), swy = $('#swy'), swdy = $('#swdy'), swms = $('#swms');
  if(swx)  swx.value  = cfg.swipe.x;
  if(swy)  swy.value  = cfg.swipe.y;
  if(swdy) swdy.value = cfg.swipe.dy;
  if(swms) swms.value = cfg.swipe.ms;

  // items
  fillItemsGrid(cfg.items || [[0,0],[0,0],[0,0],[0,0],[0,0],[0,0]]);
  const dump = $('#dump');
  if(dump) dump.innerText = JSON.stringify(cfg, null, 2);

  requestAnimationFrame(placeAll);
}

function collectForm(){
  const slotcx = parseInt($('#slotcx')?.value || CFG.slot_center[0]);
  const slotcy = parseInt($('#slotcy')?.value || CFG.slot_center[1]);
  const slotw  = parseInt($('#slotw') ?.value || CFG.slot_roi[0]);
  const sloth  = parseInt($('#sloth')?.value || CFG.slot_roi[1]);
  const slotsx = parseInt($('#slotsx')?.value || CFG.slot_status[0]);
  const slotsy = parseInt($('#slotsy')?.value || CFG.slot_status[1]);

  CFG.slot_center = [ slotcx, slotcy ];
  CFG.slot_roi    = [ slotw,  sloth ];
  CFG.slot_status = [ slotsx, slotsy ];
  CFG.slot_status_roi = [ STATUS_W, STATUS_H ];
  CFG.swipe = {
    x:  parseInt($('#swx') ?.value || CFG.swipe.x),
    y:  parseInt($('#swy') ?.value || CFG.swipe.y),
    dy: parseInt($('#swdy')?.value || CFG.swipe.dy),
    ms: parseInt($('#swms')?.value || CFG.swipe.ms),
  };

  return {
    device: getDeviceString(),
    slot_center: CFG.slot_center,
    slot_status: CFG.slot_status,
    slot_roi:    CFG.slot_roi,
    slot_status_roi: CFG.slot_status_roi,
    overlay_abs: CFG.overlay_abs,
    insert_roi:  CFG.insert_roi,
    upgrade_btn: CFG.upgrade_btn,
    items: getItemsFromForm(),
    swipe: CFG.swipe
  };
}

// ---------- scaling / placement ----------
function scaleInfo(){
  const img = $('#shotimg');
  const natW = shotMeta.w || img.naturalWidth || 1280;
  const natH = shotMeta.h || img.naturalHeight || 720;
  const rW = img.clientWidth;
  const rH = img.clientHeight;
  const sx = rW / natW;
  const sy = rH / natH;
  const off = img.getBoundingClientRect();
  return {sx, sy, offLeft: off.left + window.scrollX, offTop: off.top + window.scrollY};
}
function px2scr(px, py){
  const r = scaleInfo();
  return [Math.round(px / r.sx), Math.round(py / r.sy)];
}

function placeAll(){
  const r = scaleInfo();
  const cfg = CFG;
  const placeRect = (id, x1,y1,w,h)=>{
    const el = document.querySelector(id);
    if(!el) return;
    el.style.left = (x1 * r.sx) + 'px';
    el.style.top  = (y1 * r.sy) + 'px';
    el.style.width  = (w  * r.sx) + 'px';
    el.style.height = (h  * r.sy) + 'px';
  };
  // overlay, insert
  placeRect('#roi-overlay', cfg.overlay_abs.x1, cfg.overlay_abs.y1, cfg.overlay_abs.w, cfg.overlay_abs.h);
  placeRect('#roi-insert',  cfg.insert_roi.x1,  cfg.insert_roi.y1,  cfg.insert_roi.w,  cfg.insert_roi.h );
  // slot roi from center/size
  const [cx,cy] = cfg.slot_center; const [sw,sh] = cfg.slot_roi;
  placeRect('#roi-slot', cx - Math.floor(sw/2), cy - Math.floor(sh/2), sw, sh);
  // slot_status ROI
  const [sx,sy] = cfg.slot_status;
  placeRect('#roi-slot-status', sx - Math.floor(STATUS_W/2), sy - Math.floor(STATUS_H/2), STATUS_W, STATUS_H);

  const placePt = (id, x,y)=>{
    const el = document.querySelector(id);
    if(!el) return;
    el.style.left = (x * r.sx) + 'px';
    el.style.top  = (y * r.sy) + 'px';
  };
  // points
  placePt('#pt-slot-center', cfg.slot_center[0], cfg.slot_center[1]);
  placePt('#pt-slot-status', cfg.slot_status[0], cfg.slot_status[1]);
  placePt('#pt-upg', cfg.upgrade_btn[0], cfg.upgrade_btn[1]);
  for(let i=1;i<=6;i++){
    const [x,y] = cfg.items[i-1] || [0,0];
    placePt('#pt-item-'+i, x,y);
  }

  // swipe visuals
  const swStart = {x: cfg.swipe.x, y: cfg.swipe.y};
  const swEnd   = {x: cfg.swipe.x, y: cfg.swipe.y + cfg.swipe.dy};
  const line = $('#swipe-line');
  if(line){
    line.style.left   = (swStart.x * r.sx) + 'px';
    line.style.top    = (Math.min(swStart.y, swEnd.y) * r.sy) + 'px';
    line.style.height = (Math.abs(cfg.swipe.dy) * r.sy) + 'px';
  }

  placePt('#pt-swipe', swStart.x, swStart.y);
  const h = $('#pt-swipe-handle');
  if(h){
    h.style.left = (swEnd.x * r.sx) + 'px';
    h.style.top  = (swEnd.y * r.sy) + 'px';
  }

  const dump = document.getElementById('dump');
  if(dump){
    const preview = collectForm();
    dump.innerText = JSON.stringify(preview, null, 2);
  }
}

// ---------- drag / resize ----------
function enableDragResize(){
  const enable = document.getElementById('dragToggle')?.checked ?? true;
  const stage = document.getElementById('stage');
  if(!stage) return;
  if(!enable){ stage.onmousedown = null; document.onmousemove=null; document.onmouseup=null; return; }

  const r = ()=>scaleInfo();

  function makeRectDrag(sel, getter, setter){
    const el = document.querySelector(sel);
    if(!el) return;
    const h  = el.querySelector('.handle');
    let mode=null, s={};

    function down(ev, isResize){
      ev.preventDefault();
      if(isResize) ev.stopPropagation(); // << สำคัญ: กัน parent move กิน event
      mode = isResize ? 'resize' : 'move';
      const sc = r();
      const rect = el.getBoundingClientRect();
      s = {
        startX: ev.clientX, startY: ev.clientY,
        offLeft: sc.offLeft, offTop: sc.offTop,
        x: rect.left - sc.offLeft, y: rect.top - sc.offTop,
        w: rect.width, h: rect.height
      };
      document.onmousemove = move; document.onmouseup = up;
    }
    function move(ev){
      const dx = (ev.clientX - s.startX), dy = (ev.clientY - s.startY);
      let nx = s.x, ny = s.y, nw = s.w, nh = s.h;
      if(mode==='move'){ nx += dx; ny += dy; } else { nw = Math.max(10, s.w + dx); nh = Math.max(10, s.h + dy); }
      el.style.left = nx + 'px'; el.style.top = ny + 'px';
      el.style.width = nw + 'px'; el.style.height = nh + 'px';
    }
    function up(){
      document.onmousemove=null; document.onmouseup=null;
      const rect = el.getBoundingClientRect(); const sc = r();
      const rx = rect.left - sc.offLeft, ry = rect.top - sc.offTop;
      const [x1,y1] = px2scr(rx, ry);
      const [w,h]   = px2scr(rect.width, rect.height);
      const v = getter(); v.x1=x1; v.y1=y1; v.w=w; v.h=h; setter(v); placeAll();
    }

    // move when dragging box
    el.onmousedown = (e)=>down(e, false);
    // resize only when dragging handle; stopPropagation กัน move
    if(h) h.onmousedown = (e)=>down(e, true);
  }

  // rectangles (persisted)
  makeRectDrag('#roi-overlay', ()=>CFG.overlay_abs, v=>CFG.overlay_abs=v);
  makeRectDrag('#roi-insert',  ()=>CFG.insert_roi,  v=>CFG.insert_roi=v);

  // slot roi: move & resize (persisted) — ใช้ makeRectDrag แทนเวอร์ชันแยกเดิม
  (function(){
    const getter = ()=> {
      const [cx,cy]=CFG.slot_center, [sw,sh]=CFG.slot_roi;
      return {x1: cx-Math.floor(sw/2), y1: cy-Math.floor(sh/2), w: sw, h: sh};
    };
    const setter = (v)=> {
      CFG.slot_center = [ v.x1 + Math.floor(v.w/2), v.y1 + Math.floor(v.h/2) ];
      CFG.slot_roi    = [ v.w, v.h ];
      const a=$('#slotcx'), b=$('#slotcy'), c=$('#slotw'), d=$('#sloth');
      if(a) a.value = CFG.slot_center[0];
      if(b) b.value = CFG.slot_center[1];
      if(c) c.value = CFG.slot_roi[0];
      if(d) d.value = CFG.slot_roi[1];
    };
    // สร้างกล่องชั่วคราวให้ makeRectDrag ใช้ตำแหน่งปัจจุบัน
    const el = document.getElementById('roi-slot');
    if(el){
      makeRectDrag('#roi-slot', getter, setter);
    }
  })();

  // slot_status ROI (move & resize, persisted in CFG.slot_status_roi)
  (function(){
    const el = document.getElementById('roi-slot-status');
    if(!el) return;
    const h  = el.querySelector('.handle');

    function boxInfo(){
      const [sx,sy]=CFG.slot_status; const w=STATUS_W, h=STATUS_H;
      return {x1:sx-Math.floor(w/2), y1:sy-Math.floor(h/2), w, h};
    }
    function applyBox(v){
      STATUS_W=v.w; STATUS_H=v.h;
      CFG.slot_status = [ v.x1 + Math.floor(v.w/2), v.y1 + Math.floor(v.h/2) ];
      CFG.slot_status_roi = [STATUS_W, STATUS_H];
      const a=$('#slotsx'), b=$('#slotsy'); if(a)a.value=CFG.slot_status[0]; if(b)b.value=CFG.slot_status[1];
    }

    // move / resize with stopPropagation on handle
    let mode=null, s={};
    el.onmousedown = (ev)=>{
      ev.preventDefault();
      mode='move';
      const sc = scaleInfo(); const rect = el.getBoundingClientRect();
      s = {startX:ev.clientX,startY:ev.clientY,offLeft:sc.offLeft,offTop:sc.offTop,x:rect.left-sc.offLeft,y:rect.top-sc.offTop,w:rect.width,h:rect.height};
      document.onmousemove = (e)=>{
        const nx = s.x + (e.clientX - s.startX);
        const ny = s.y + (e.clientY - s.startY);
        el.style.left = nx + 'px'; el.style.top = ny + 'px';
      };
      document.onmouseup = ()=>{
        document.onmousemove=null; document.onmouseup=null;
        const rect2 = el.getBoundingClientRect(); const sc2 = scaleInfo();
        const rx = rect2.left - sc2.offLeft, ry = rect2.top - sc2.offTop;
        const [x1,y1] = px2scr(rx, ry);
        const [w,h]   = px2scr(rect2.width, rect2.height);
        applyBox({x1,y1,w,h}); placeAll();
      };
    };
    if(h) h.onmousedown = (ev)=>{
      ev.preventDefault(); ev.stopPropagation();
      const sc = scaleInfo(); const rect = el.getBoundingClientRect();
      s = {startX:ev.clientX,startY:ev.clientY, w:rect.width,h:rect.height, offLeft:sc.offLeft, offTop:sc.offTop, left:rect.left, top:rect.top};
      document.onmousemove = (e)=>{
        const nw = Math.max(10, s.w + (e.clientX - s.startX));
        const nh = Math.max(10, s.h + (e.clientY - s.startY));
        el.style.width = nw + 'px'; el.style.height = nh + 'px';
      };
      document.onmouseup = ()=>{
        document.onmousemove=null; document.onmouseup=null;
        const rect2 = el.getBoundingClientRect(); const sc2 = scaleInfo();
        const rx = rect2.left - sc2.offLeft, ry = rect2.top - sc2.offTop;
        const [x1,y1] = px2scr(rx, ry);
        const [w,h]   = px2scr(rect2.width, rect2.height);
        applyBox({x1,y1,w,h}); placeAll();
      };
    };
  })();
}

// ---------- API ----------
async function fetchCfg(){
  const r = await fetch('/api/config'); const j = await r.json();
  CFG = j; lastCfg = JSON.parse(JSON.stringify(j));
  fillForm(j);
  await renderShot();
}
async function saveCfg(){
  const payload = collectForm();
  const r = await fetch('/api/save-config', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
  const j = await r.json();
  alert(j.ok ? 'Saved' : ('Error: ' + (j.error||'')));
  if(j.ok) await fetchCfg();
}
function undoCfg(){ if(lastCfg){ CFG = JSON.parse(JSON.stringify(lastCfg)); fillForm(CFG); placeAll(); } }
async function clearCfg(){
  if(!confirm('Clear config file?')) return;
  const r = await fetch('/api/clear-config', {method:'POST'});
  const j = await r.json(); alert(j.ok ? 'Cleared' : ('Error: '+(j.error||'')));
  await fetchCfg();
}
async function refreshShot(){ await renderShot(true); }
async function snap(){ const r = await fetch('/api/snap', {method:'POST'}); return await r.json(); }
async function snapAndRefresh(){
  const j = await snap();
  if(!j.ok){ alert('Error: '+(j.error||'')); return; }
  await renderShot(true);
}
async function renderShot(force=false){
  const r = await fetch('/api/shot' + (force ? '?t=' + Date.now() : '')); const j = await r.json();
  const img = document.getElementById('shotimg');
  img.onload = ()=>{ shotMeta.w=j.w; shotMeta.h=j.h; placeAll(); enableDragResize(); };
  img.src = 'data:image/png;base64,'+j.image_b64;
}
document.addEventListener('DOMContentLoaded', fetchCfg);
document.addEventListener('DOMContentLoaded', ()=> {
  const dragToggle = document.getElementById('dragToggle');
  if(dragToggle) dragToggle.addEventListener('change', enableDragResize);
});
</script>
"""
