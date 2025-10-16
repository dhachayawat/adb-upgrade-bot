# app/ui/components/header.py
def render_header() -> str:
    return """
<div class="d-flex align-items-center justify-content-between mb-3">
  <h3 class="m-0">ADB Upgrade Bot — Config</h3>
  <div class="d-flex align-items-center gap-2">
    <span id="state" class="badge text-bg-secondary">stopped</span>
    <button class="btn btn-success btn-sm" onclick="botStart()">Start</button>
    <button class="btn btn-warning btn-sm" onclick="botPause()">Pause/Resume</button>
    <button class="btn btn-danger btn-sm" onclick="botStop()">Stop</button>
  </div>
</div>
<script>
async function _status(){
  const r = await fetch('/api/status'); const j = await r.json();
  const el = document.getElementById('state');
  if(!el) return;
  const s = j.state + (j.paused ? ' (paused)' : '');
  el.textContent = s;
  el.className = 'badge ' + (j.state==='running' ? (j.paused?'text-bg-warning':'text-bg-success') : 'text-bg-secondary');
}
async function botStart(){ const r = await fetch('/api/start', {method:'POST'}); await _status(); }
async function botStop(){ const r = await fetch('/api/stop',  {method:'POST'}); await _status(); }
async function botPause(){ const r = await fetch('/api/pause', {method:'POST'}); await _status(); }
document.addEventListener('DOMContentLoaded', ()=>{
  _status();
  setInterval(_status, 1500);
});
</script>
"""
