# app/ui/components/summary.py
# หน้า Summary สรุปแตก 7 วันย้อนหลัง (รวม + ต่ออุปกรณ์)

def render_summary() -> str:
    return """
<div class="d-flex align-items-center justify-content-between mb-3">
  <div>
    <a href="/" class="btn btn-link p-0">&larr; Back</a>
    <h3 class="m-0 mt-2">Summary — Breaks by Level (7 days)</h3>
    <div class="text-muted small">Rolling 7 days • Timezone: Asia/Bangkok</div>
  </div>
  <div class="d-flex gap-2">
    <button class="btn btn-outline-secondary btn-sm" onclick="refresh()">Refresh</button>
  </div>
</div>

<div id="days"></div>

<script>
async function api(path, opt){ const r = await fetch(path, Object.assign({headers:{'Content-Type':'application/json'}}, opt||{})); return r.json(); }

function pills(h){
  const ks = Object.keys(h||{}).sort((a,b)=>parseInt(a)-parseInt(b));
  if(ks.length===0) return '<span class="text-muted small">No breaks</span>';
  return ks.map(k=>`<span class="badge text-bg-dark me-1 mb-1">+${k}: ${h[k]}</span>`).join(' ');
}

function deviceBlock(dev){
  const rows = Object.keys(dev||{}).sort().map(id=>{
    const hist = dev[id];
    return `<tr><td class="text-monospace">${id}</td><td>${pills(hist)}</td></tr>`;
  }).join('');
  if(!rows) return '<div class="text-muted small">No device data</div>';
  return `<div class="table-responsive"><table class="table table-sm align-middle">
    <thead><tr><th>Device</th><th>Breaks (by level)</th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

function dayCard(d){
  return `
  <div class="card shadow-sm mb-3">
    <div class="card-body">
      <div class="d-flex justify-content-between align-items-center">
        <h5 class="mb-0">${d.date}</h5>
        <div>${pills(d.totals||{})}</div>
      </div>
      <div class="mt-3">${deviceBlock(d.devices||{})}</div>
    </div>
  </div>`;
}

async function load(){
  const data = await api('/api/summary?days=7');
  const days = data.days || [];
  const root = document.getElementById('days');
  if(days.length===0){ root.innerHTML = '<div class="text-muted">No data</div>'; return; }
  root.innerHTML = days.map(dayCard).join('');
}

async function refresh(){ await load(); }
document.addEventListener('DOMContentLoaded', load);
</script>
"""
