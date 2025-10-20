FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    android-tools-adb \
    libglib2.0-0 libgl1 ca-certificates \
    tesseract-ocr tesseract-ocr-eng tesseract-ocr-tha tesseract-ocr-osd libtesseract-dev libthai0 \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir pytesseract

COPY app ./app
COPY templates ./templates

# เฉพาะ env คงที่ที่ปลอดภัยจะใส่ใน image
ENV PYTHONUNBUFFERED=1 \
    OPENCV_LOG_LEVEL=ERROR

RUN printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'mkdir -p /app/cache/debug /app/data /app/logs || true' \
  'adb start-server >/dev/null 2>&1 || true' \
  'adb devices || true' \
  'echo "[entrypoint] ready. launching python ..."' \
  'exec python -u -m app.main' \
  > /entrypoint.sh && chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
