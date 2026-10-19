#!/bin/sh
# Point the personal SAGE node at the local nomic shim (idempotent).
set -e

CONFIG=/var/lib/sage-lantern/sage-personal/config.yaml
[ "$(id -u)" = 0 ] || { echo "root required" >&2; exit 1; }

if grep -q "provider: ollama" "$CONFIG" && grep -q "base_url: http://127.0.0.1:8084" "$CONFIG"; then
    echo "config already points at the shim"
    exit 0
fi

cp -p "$CONFIG" "$CONFIG.before-nomic-$(date +%Y%m%d-%H%M%S)"
/opt/sage-lantern/venv/bin/python - "$CONFIG" <<'PY'
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
text = path.read_text()
if not re.search(r'(?m)^embedding:\n', text):
    raise SystemExit('embedding section missing')
text = re.sub(r'(?m)^embedding:\n(?:[ \t]+.*\n)*',
              'embedding:\n'
              '    provider: ollama\n'
              '    dimension: 768\n'
              '    model: nomic-embed-text-v1.5\n'
              '    base_url: http://127.0.0.1:8084\n'
              '    api_key: ""\n',
              text, count=1)
path.write_text(text)
print('config updated')
PY
