#!/bin/sh
# Lightweight local embeddings for the Lantern units: nomic-embed-text-v1.5 on
# the existing llama.cpp build, no ollama daemon.
#
#   sh install-embed-service.sh
set -e

MODEL=/opt/sage-lantern/models/nomic-embed-text-v1.5.Q8_0.gguf
URL=https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF/resolve/main/nomic-embed-text-v1.5.Q8_0.gguf
UNIT=/etc/systemd/system/sage-lantern-embed.service

[ "$(id -u)" = 0 ] || { echo "root required" >&2; exit 1; }

if [ ! -f "$MODEL" ]; then
    echo "fetching nomic-embed-text-v1.5 (about 146 MB)"
    curl -fsSL --retry 3 -o "$MODEL.part" "$URL"
    chmod 0644 "$MODEL.part"
    mv "$MODEL.part" "$MODEL"
fi
sha256sum "$MODEL"

cat > "$UNIT" <<'EOF'
[Unit]
Description=SAGE Lantern local embeddings (nomic-embed-text-v1.5, CPU)
After=local-fs.target

[Service]
Type=simple
User=sage-lantern
Group=sage-lantern
Environment=LD_LIBRARY_PATH=/opt/sage-lantern/llama.cpp
ExecStart=/opt/sage-lantern/llama.cpp/llama-server --model /opt/sage-lantern/models/nomic-embed-text-v1.5.Q8_0.gguf --embedding --pooling mean --host 127.0.0.1 --port 8083 --ctx-size 512 --threads 2
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now sage-lantern-embed.service
sleep 4
systemctl is-active sage-lantern-embed.service
curl -s --max-time 20 http://127.0.0.1:8083/v1/embeddings \
    -H 'Content-Type: application/json' \
    -d '{"model":"nomic-embed-text-v1.5","input":"search_query: how far is Ipoh from Kuala Lumpur"}' \
    | /opt/sage-lantern/venv/bin/python -c 'import json,sys; d=json.load(sys.stdin); v=d["data"][0]["embedding"]; print("dimension", len(v), "first", round(v[0], 4))'
