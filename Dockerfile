# syntax=docker/dockerfile:1.7

FROM python:3.11-slim-bookworm

ARG GIT_COMMIT=dev
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DISPLAY=:99 \
    DATA_DIR=/data \
    GS_ACCOUNTS=/data/accounts.json \
    GS_PORT=8899 \
    GS_BIND_HOST=0.0.0.0 \
    GIT_COMMIT=$GIT_COMMIT

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       chromium \
       xvfb \
       dumb-init \
       ca-certificates \
       fonts-noto-cjk \
       fonts-liberation \
       fonts-dejavu-core \
       libnss3 \
       libatk-bridge2.0-0 \
       libgtk-3-0 \
       libgbm1 \
       libasound2 \
       libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY web ./web
COPY main.py gs_login.py gs_register.py gs_reg_driver.py gs_export_template.py ./
COPY accounts.example.json ./
COPY LICENSE DISCLAIMER.md ./
COPY docker/entrypoint.sh /usr/local/bin/ciallo-entrypoint

RUN chmod +x /usr/local/bin/ciallo-entrypoint \
    && mkdir -p /data /app/data \
    && printf '%s\n' "$GIT_COMMIT" > /app/BUILD_ID

VOLUME ["/data"]
EXPOSE 8899

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import json,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8899/health', timeout=4); d=json.load(r); raise SystemExit(0 if d.get('ok') else 1)"

ENTRYPOINT ["dumb-init", "--", "/usr/local/bin/ciallo-entrypoint"]
