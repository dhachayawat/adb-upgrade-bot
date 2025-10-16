# app/config_ui.py
from flask import Flask, Response
from app.config_api import bp_api
from app.ui.components import render_page

def create_app():
    app = Flask(__name__)
    app.register_blueprint(bp_api)

    @app.get("/")
    def index():
        html = render_page()
        return Response(html, mimetype="text/html; charset=utf-8")

    return app

def run(host: str = "0.0.0.0", port: int = 8765, **kwargs):
    """
    รัน Flask UI ในเธรดแยก
    - รองรับ **kwargs เพื่อกันกรณีมีการส่ง debug/use_reloader/extra flags มา
    - บังคับ use_reloader=False เมื่อรันในเธรด (กัน spawn process)
    """
    # กลืนคีย์เวิร์ดที่ไม่รองรับ (กัน error TypeError)
    kwargs.pop("debug", None)
    kwargs.pop("reloader", None)
    kwargs.pop("use_debugger", None)

    # ให้ตั้งค่า threaded=True / use_reloader=False เพื่อความเสถียรในเธรด
    threaded = kwargs.pop("threaded", True)
    use_reloader = kwargs.pop("use_reloader", False)

    app = create_app()
    app.run(host=host, port=port, threaded=threaded, use_reloader=use_reloader)
