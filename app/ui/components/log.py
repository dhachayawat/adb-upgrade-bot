# app/ui/components/log.py
def render_log() -> str:
    return """
<div class="card">
  <div class="card-header d-flex align-items-center justify-content-between">
    <span>Runtime Logs</span>
    <div class="d-flex gap-2">
      <button class="btn btn-outline-light btn-sm" onclick="refreshLogs()">Refresh</button>
      <button class="btn btn-outline-secondary btn-sm" onclick="toggleAutoLog()">Auto</button>
    </div>
  </div>
  <div class="card-body">
    <pre id="logbox" style="max-height:260px;overflow:auto"></pre>
    <div class="form-text text-secondary small">* โชว์สถานะหลัง “ใส่ลง”, OCR เลเวล, ผลสำเร็จ/ล้มเหลว, และการอัปเกรด</div>
  </div>
</div>
<script>
let autoLog = true, _logTimer=null;
async function refreshLogs(){
  try{
    const r = await fetch('/api/logs');
    const j = await r.json();
    const el = document.getElementById('logbox');
    el.textContent = (j && j.text) ? j.text : '(empty)';
    el.scrollTop = el.scrollHeight;
  }catch(e){}
}
function toggleAutoLog(){
  autoLog = !autoLog;
  if(autoLog){ startAuto(); } else { if(_logTimer) clearInterval(_logTimer); }
}
function startAuto(){
  if(_logTimer) clearInterval(_logTimer);
  _logTimer = setInterval(refreshLogs, 1000);
}
document.addEventListener('DOMContentLoaded', ()=>{ startAuto(); refreshLogs(); });
</script>
"""
