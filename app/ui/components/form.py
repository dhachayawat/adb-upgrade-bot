# app/ui/components/form.py
def render_form() -> str:
    return """
<div class="card mb-3">
  <div class="card-header">General</div>
  <div class="card-body">
    <div class="mb-2">
      <label class="form-label">ADB Host</label>
      <input id="adbHost" type="text" class="form-control form-control-sm" placeholder="host.docker.internal">
    </div>
    <div class="mb-3">
      <label class="form-label">ADB Port</label>
      <input id="adbPort" type="number" class="form-control form-control-sm" placeholder="5605">
    </div>

    <div class="d-flex gap-2 mb-3">
      <button type="button" class="btn btn-primary btn-sm" onclick="saveCfg()">Save Config</button>
      <button type="button" class="btn btn-outline-light btn-sm" onclick="undoCfg()">Undo</button>
      <button type="button" class="btn btn-outline-danger btn-sm" onclick="clearCfg()">Clear</button>
    </div>

    <div class="form-text text-secondary small">
      Config file: <span id="cfgpath" class="mono"></span>
    </div>
  </div>
</div>

<!-- Advanced (collapsed by default) -->
<div class="card">
  <div class="card-header d-flex align-items-center justify-content-between">
    <span>Advanced</span>
    <button type="button" class="btn btn-outline-secondary btn-sm"
            data-bs-toggle="collapse" data-bs-target="#advWrap"
            aria-expanded="false" aria-controls="advWrap">
      Show / Hide
    </button>
  </div>
  <div id="advWrap" class="collapse">
    <div class="card-body">

      <!-- Slot & Status -->
      <div class="mb-3">
        <h6 class="mb-2">Slot & Status</h6>
        <div class="row g-2">
          <div class="col-6">
            <label class="form-label">slot_center X</label>
            <input id="slotcx" type="number" class="form-control form-control-sm" value="0">
          </div>
          <div class="col-6">
            <label class="form-label">slot_center Y</label>
            <input id="slotcy" type="number" class="form-control form-control-sm" value="0">
          </div>
          <div class="col-6">
            <label class="form-label">slot_roi W</label>
            <input id="slotw" type="number" class="form-control form-control-sm" value="0">
          </div>
          <div class="col-6">
            <label class="form-label">slot_roi H</label>
            <input id="sloth" type="number" class="form-control form-control-sm" value="0">
          </div>
          <div class="col-6">
            <label class="form-label">slot_status X</label>
            <input id="slotsx" type="number" class="form-control form-control-sm" value="0">
          </div>
          <div class="col-6">
            <label class="form-label">slot_status Y</label>
            <input id="slotsy" type="number" class="form-control form-control-sm" value="0">
          </div>
          <div class="col-6">
            <label class="form-label">slot_status ROI W</label>
            <input id="statusw" type="number" class="form-control form-control-sm" value="80" oninput="window.STATUS_W=parseInt(this.value||80)">
          </div>
          <div class="col-6">
            <label class="form-label">slot_status ROI H</label>
            <input id="statush" type="number" class="form-control form-control-sm" value="80" oninput="window.STATUS_H=parseInt(this.value||80)">
          </div>
        </div>
        <div class="form-text text-secondary small">* ค่าเหล่านี้มักตั้งค่าผ่านการลากกรอบบนภาพด้านขวา</div>
      </div>

      <!-- Swipe -->
      <div class="mb-3">
        <h6 class="mb-2">Swipe</h6>
        <div class="row g-2">
          <div class="col-6">
            <label class="form-label">x</label>
            <input id="swx" type="number" class="form-control form-control-sm" value="0">
          </div>
          <div class="col-6">
            <label class="form-label">y</label>
            <input id="swy" type="number" class="form-control form-control-sm" value="0">
          </div>
          <div class="col-6">
            <label class="form-label">dy</label>
            <input id="swdy" type="number" class="form-control form-control-sm" value="0">
          </div>
          <div class="col-6">
            <label class="form-label">ms</label>
            <input id="swms" type="number" class="form-control form-control-sm" value="800">
          </div>
        </div>
      </div>

      <!-- Items (6 slots) -->
      <div class="mb-2">
        <h6 class="mb-2">Items</h6>
        <div id="items" class="row"></div>
        <div class="form-text text-secondary small">* ปกติใช้ลากจุดบนภาพเพื่อกำหนดตำแหน่งไอเทม</div>
      </div>

      <div class="d-flex align-items-center gap-2">
        <div class="form-check form-switch">
          <input id="dragToggle" class="form-check-input" type="checkbox" checked>
          <label for="dragToggle" class="form-check-label">Enable drag/resize on image</label>
        </div>
        <button type="button" class="btn btn-outline-light btn-sm" onclick="placeAll()">Preview Overlays</button>
      </div>

    </div>
  </div>
</div>
"""
