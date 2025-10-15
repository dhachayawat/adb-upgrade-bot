import os
from app.cv_utils import read_tpl

# เก็บ template แบบ lazy-load
TPL = {
    "plus5": None,
    "slot_empty": None,
    "btn_upgrade": None,
    "btn_insert_list": None,
    "fail": None,
    "success": None,
}

def ensure(templates_dir: str):
    # จำเป็น
    if TPL["plus5"] is None:
        TPL["plus5"] = read_tpl("plus5.png")
    if TPL["slot_empty"] is None:
        TPL["slot_empty"] = read_tpl("slot_empty.png")

    # ทางเลือก (มีไฟล์ค่อยโหลด)
    if TPL["btn_upgrade"] is None:
        p = os.path.join(templates_dir, "btn_upgrade.png")
        if os.path.exists(p):
            TPL["btn_upgrade"] = read_tpl("btn_upgrade.png")

    if TPL["btn_insert_list"] is None:
        names = ["btn_insert.png"]  # เติมชื่อไฟล์อื่นได้
        TPL["btn_insert_list"] = []
        for n in names:
            p = os.path.join(templates_dir, n)
            if os.path.exists(p):
                TPL["btn_insert_list"].append(read_tpl(n))

    # overlays
    if TPL["fail"] is None:
        p = os.path.join(templates_dir, "fail.png")
        if os.path.exists(p):
            TPL["fail"] = read_tpl("fail.png")

    if TPL["success"] is None:
        p = os.path.join(templates_dir, "success.png")
        if os.path.exists(p):
            TPL["success"] = read_tpl("success.png")
