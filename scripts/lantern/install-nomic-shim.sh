#!/bin/sh
# Ollama-compatible shim so the SAGE node can use the nomic embeddings served by
# llama.cpp on :8083. No ollama daemon, no second copy of the model.
set -e

DIR=/opt/sage-lantern/nomic-shim
UNIT=/etc/systemd/system/sage-lantern-embed-shim.service

[ "$(id -u)" = 0 ] || { echo "root required" >&2; exit 1; }

mkdir -p "$DIR"
install -o root -g root -m 0755 /tmp/nomic-shim.py "$DIR/nomic-shim.py"

cat > "$UNIT" <<'EOF'
[Unit]
Description=SAGE Lantern Ollama-compatible shim for local nomic embeddings
After=sage-lantern-embed.service
Wants=sage-lantern-embed.service

[Service]
Type=simple
User=sage-lantern
Group=sage-lantern
ExecStart=/usr/bin/python3 /opt/sage-lantern/nomic-shim/nomic-shim.py --upstream http://127.0.0.1:8083 --port 8084
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now sage-lantern-embed-shim.service
sleep 2
systemctl is-active sage-lantern-embed-shim.service
curl -s --max-time 20 http://127.0.0.1:8084/api/embeddings -H 'Content-Type: application/json' \
    -d '{"model":"nomic-embed-text-v1.5","prompt":"how far is Ipoh from Kuala Lumpur"}' \
    | /opt/sage-lantern/venv/bin/python -c 'import json,sys; v=json.load(sys.stdin)["embedding"]; print("shim dimension", len(v))'
