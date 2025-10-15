FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    android-tools-adb libglib2.0-0 libgl1 ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY templates ./templates

ENV DEVICE=host.docker.internal:5605

RUN printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'mkdir -p /app/cache/debug || true' \
  'adb start-server >/dev/null 2>&1 || true' \
  'for i in $(seq 1 20); do adb connect "${DEVICE}" >/dev/null 2>&1 && break || sleep 1; done' \
  'adb devices' \
  'echo "[entrypoint] ready. launching python (waiting for 1/0/q) ..."' \
  'exec python -u -m app.main' \
  > /entrypoint.sh && chmod +x /entrypoint.sh

RUN apt-get update && apt-get install -y --no-install-recommends \
      tesseract-ocr tesseract-ocr-eng tesseract-ocr-osd libtesseract-dev \
      libthai0 \
    && rm -rf /var/lib/apt/lists/*

# Python libs
RUN pip install --no-cache-dir pytesseract

ENTRYPOINT ["/entrypoint.sh"]
