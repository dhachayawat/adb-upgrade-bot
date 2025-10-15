# app/tools/screendump.py
from app import cv_utils as CV

def main():
    img = CV.screencap_bgr(save_tag="manual")
    h,w = img.shape[:2]
    print(f"[screendump] captured {w}x{h}. See /app/cache/*.png")

if __name__ == "__main__":
    main()
