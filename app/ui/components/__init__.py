# app/ui/components/__init__.py
from .header import render_header
from .form import render_form
from .screen import render_screen
from .log import render_log

def render_page() -> str:
    return f"""
<!doctype html>
<html lang="th" data-bs-theme="dark">
<head>
<meta charset="utf-8">
<title>ADB Upgrade Bot — Config</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
<style>
  html,body{{height:100%}}
  body{{background:#0f1115;color:#fff; user-select:none; -webkit-user-select:none; -ms-user-select:none}}
  .card{{background:#151922;border:1px solid #22283a}}
  .form-label{{color:#cfd8ef}}
  .btn{{border-radius:.6rem}}
  pre{{white-space:pre-wrap;background:#0b0e14;color:#e9eefb;border:1px solid #22283a;padding:.75rem;border-radius:.5rem}}
  .img-stage{{position:relative;display:inline-block;max-width:100%}}
  .img-stage img{{display:block;}}
  /* --- overlays & controls layering --- */
  .roi{{position:absolute;border:2px solid #fff; box-sizing:border-box; z-index:5; pointer-events:auto}}
  .label{{position:absolute;left:0;top:-18px;background:rgba(0,0,0,.6);padding:0 6px;border-radius:6px;font-size:12px; z-index:6; pointer-events:none}}
  .handle{{position:absolute;width:12px;height:12px;background:#fff;border-radius:50%;right:-6px;bottom:-6px;cursor:nwse-resize; z-index:10}}
  .pt{{position:absolute;width:12px;height:12px;border-radius:50%;background:#8ab4ff;border:2px solid #fff;transform:translate(-50%,-50%);cursor:move; z-index:8}}
  .pt.red{{background:#ff6b6b}}
  .pt.yellow{{background:#ffd166}}
  .pt.cyan{{background:#39c0ff}}
  .sw-line{{position:absolute;width:2px;background:#ffd166;opacity:.9; z-index:4}}
  .sw-handle{{position:absolute;width:12px;height:12px;border-radius:50%;background:#ffd166;border:2px solid #fff;transform:translate(-50%,-50%);cursor:ns-resize; z-index:9}}
  .small{{font-size:.85rem}}
  .mono{{font-family:ui-monospace,Consolas,monospace}}
</style>
</head>
<body>
<div class="container py-3">
  {render_header()}
  <div class="row g-3">
    <div class="col-lg-4">
      {render_form()}
      {render_log()}
    </div>
    <div class="col-lg-8">
      {render_screen()}
    </div>
  </div>
</div>
</body>
</html>
"""
