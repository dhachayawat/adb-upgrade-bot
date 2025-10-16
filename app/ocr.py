# app/ocr.py
import cv2
import numpy as np
import pytesseract

def ocr_plus_n(bgr):
    """
    อ่านตรา +N จากภาพเล็กๆ
    คืน (N | None, avg_conf)
    """
    if bgr is None or bgr.size == 0:
        return None, 0.0

    H, W = bgr.shape[:2]
    scale = 3 if max(H, W) < 60 else 2
    up = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    gray = cv2.cvtColor(up, cv2.COLOR_BGR2GRAY)
    thr = cv2.adaptiveThreshold(gray, 255,
                                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                cv2.THRESH_BINARY, 31, 9)
    inv = 255 - thr
    proc = inv  # คมพอแล้ว

    cfg = r'--oem 1 --psm 7 -c tessedit_char_whitelist=+0123456789'
    data = pytesseract.image_to_data(proc, lang='eng', config=cfg, output_type=pytesseract.Output.DICT)

    texts, confs = [], []
    for txt, conf in zip(data.get('text', []), data.get('conf', [])):
        txt = (txt or "").strip()
        if txt and str(conf) != '-1':
            texts.append(txt)
            try:
                confs.append(float(conf))
            except:
                pass

    text = ''.join(texts).replace(' ', '')
    avg_conf = float(np.mean(confs)) if confs else 0.0

    if len(text) >= 2 and text[0] == '+':
        num = ''.join(ch for ch in text[1:] if ch.isdigit())
        if num.isdigit():
            return int(num), avg_conf

    if text.isdigit():
        return int(text), avg_conf

    return None, avg_conf
