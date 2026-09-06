# ReelVault — single container, CUDA-enabled
# Build:  docker build -t reelvault .
# Run:    docker run -d --gpus all -p 8756:8756 \
#           -v D:/reelvault/models:/data/models \
#           -v D:/reelvault/data:/data/appdata \
#           -v D:/reelvault/media:/data/media \
#           -e RV_BACKUP_PASSPHRASE=change-me reelvault
#
# The image compiles llama.cpp (CUDA) and installs whisper; models & data are
# VOLUMES so backups/upgrades never touch them.

FROM nvidia/cuda:12.4.1-devel-ubuntu22.04 AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git curl libcurl4-openssl-dev && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git clone --depth 1 https://github.com/ggml-org/llama.cpp . && \
    cmake -B build -DGGML_CUDA=ON -DLLAMA_CURL=OFF && \
    cmake --build build --config Release -j"$(nproc)" --target llama-server

FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HOME=/data/cache/hf
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.11 python3-pip python3.11-venv ffmpeg tini curl && \
    rm -rf /var/lib/apt/lists/* && \
    ln -sf /usr/bin/python3.11 /usr/bin/python3 && \
    curl -fsSL "https://github.com/caddyserver/caddy/releases/download/v2.8.4/caddy_2.8.4_linux_amd64.tar.gz" \
      | tar xz -C /usr/local/bin caddy && chmod +x /usr/local/bin/caddy

COPY --from=builder /src/build/bin/llama-server /usr/local/bin/
COPY requirements-docker.txt /tmp/
RUN pip3 install --break-system-packages -r /tmp/requirements-docker.txt && \
    pip3 install --break-system-packages openai-whisper pywebpush cryptography

WORKDIR /app
COPY app ./app
COPY scripts ./scripts

# Caddy: automatic HTTPS for LAN via internal CA; replace Caddyfile when using
# a domain + Cloudflare/Tailscale.
COPY <<'CADDY' /etc/caddy/Caddyfile
{
    auto_https off
}
:8443 {
    tls internal
    reverse_proxy 127.0.0.1:8756
}
CADDY

ENV RV_DATA_DIR=/data/appdata \
    RV_MODELS_DIR=/data/models \
    RV_MEDIA_DIR=/data/media \
    RV_DB_PATH=/data/appdata/reelvault.db \
    RV_LLM_SERVER_URL=http://127.0.0.1:8091/v1

EXPOSE 8756 8443
VOLUME ["/data"]

HEALTHCHECK --interval=60s --timeout=5s --start-period=180s \
  CMD curl -fsS http://127.0.0.1:8756/healthz || exit 1

# supervisord-lite via tini + shell: start llama-server, then app + caddy
CMD ["/usr/bin/tini", "--", "/bin/sh", "-c", "\
  llama-server -m /data/models/qwen2.5-3b-instruct-q4_k_m.gguf \
    --host 127.0.0.1 --port 8091 -c 8192 -ngl 99 --jinja & \
  python3 -m uvicorn app.api.main:app --host 0.0.0.0 --port 8756 & \
  caddy run --config /etc/caddy/Caddyfile & \
  wait -n"]
