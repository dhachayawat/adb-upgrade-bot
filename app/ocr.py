import cv2
import numpy as np
import pytesseract

def ocr_plus_n(bgr) -> tuple[int | None, float]:
    """
    อ่านข้อความในภาพเล็กๆ (ตรา +N) คืน (N, conf)
    - N คือจำนวนหลังเครื่องหมาย + (int) หรือ None ถ้าอ่านไม่ได้
    - conf คือความมั่นใจเฉลี่ย 0..100
    """
    if bgr is None or bgr.size == 0:
        return None, 0.0

    # ยืดให้ใหญ่ขึ้นเพื่อช่วย OCR
    H, W = bgr.shape[:2]
    scale = 3 if max(H, W) < 60 else 2
    up = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    gray = cv2.cvtColor(up, cv2.COLOR_BGR2GRAY)
    # เน้นตัวหนังสือ: adaptive threshold + invert
    thr = cv2.adaptiveThreshold(gray, 255,
                                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                cv2.THRESH_BINARY, 31, 9)
    inv = 255 - thr

    # รักษาคอนทัวร์จี๊ดๆ
    kernel = np.ones((1,1), np.uint8)
    proc = cv2.morphologyEx(inv, cv2.MORPH_OPEN, kernel)

    # OCR: อ่านเฉพาะ + และตัวเลข
    cfg = r'--oem 1 --psm 7 -c tessedit_char_whitelist=+0123456789'
    data = pytesseract.image_to_data(proc, lang='eng', config=cfg, output_type=pytesseract.Output.DICT)

    texts = []
    confs = []
    for txt, conf in zip(data.get('text', []), data.get('conf', [])):
        if txt and conf != '-1':
            texts.append(txt.strip())
            try:
                confs.append(float(conf))
            except:
                pass

    text = ''.join(texts)
    avg_conf = float(np.mean(confs)) if confs else 0.0

    # ทำความสะอาด
    text = text.replace(' ', '').replace('\n', '')

    # รูปแบบที่ยอมรับ: +5, +6, +12 ฯลฯ
    if len(text) >= 2 and text[0] == '+':
        num_part = ''.join(ch for ch in text[1:] if ch.isdigit())
        if num_part.isdigit():
            return int(num_part), avg_conf

    # บางที OCR อาจหายเครื่องหมาย + ให้ลองดักกรณี "5" ล้วน หากความเชื่อมั่นสูง
    if text.isdigit():
        return int(text), avg_conf

    return None, avg_conf
