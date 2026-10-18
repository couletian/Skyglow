#!/usr/bin/env sh
# One-time setup: create .venv here and install the optional decoders.
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "[1/3] Creating .venv ..."
  python3 -m venv .venv
else
  echo "[1/3] Reusing existing .venv"
fi

echo "[2/3] Installing requirements ..."
.venv/bin/python -m pip install --upgrade pip --quiet
.venv/bin/python -m pip install -r requirements.txt

echo "[3/3] Verifying ..."
.venv/bin/python - <<'PY'
import eccodes, numpy, cryptography
print("  eccodes ok / numpy", numpy.__version__, "/ cryptography ok")
PY

echo
echo "Done. Start the app with:  ./run.sh"
