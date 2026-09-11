#!/bin/sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 -m unittest discover -s "$HERE/tests" -p 'test_*.py' -v

echo "Static artifact checks passed. Run nginx -t on the target after modules and certificates are installed."
