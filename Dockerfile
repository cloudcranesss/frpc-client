FROM python:3.12-slim

ARG FRP_VERSION=0.68.0
ARG TARGETARCH

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    FRP_PANEL_SECURE_COOKIE=false \
    FRP_PANEL_PORT=8000

WORKDIR /app

# System dependencies for runtime + healthcheck
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tar \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# App source
COPY app ./app
COPY web ./web
COPY scripts ./scripts
COPY run.py ./run.py

# Built-in frpc binary (arch-aware)
RUN mkdir -p /app/bin/linux /app/data/configs \
    && if [ "${TARGETARCH}" = "arm64" ]; then FRP_ARCH=arm64; else FRP_ARCH=amd64; fi \
    && curl -fsSL "https://github.com/fatedier/frp/releases/download/v${FRP_VERSION}/frp_${FRP_VERSION}_linux_${FRP_ARCH}.tar.gz" -o /tmp/frp.tar.gz \
    && tar -xzf /tmp/frp.tar.gz -C /tmp \
    && cp "/tmp/frp_${FRP_VERSION}_linux_${FRP_ARCH}/frpc" /app/bin/linux/frpc \
    && chmod +x /app/bin/linux/frpc \
    && rm -rf /tmp/frp*

EXPOSE 8000
VOLUME ["/app/data"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
