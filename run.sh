#!/usr/bin/env sh
# Start the server, preferring the local .venv (which has the GRIB decoders).
cd "$(dirname "$0")"
SKYGLOW_PORT="${1:-8770}"

if [ -x ".venv/bin/python" ]; then
  exec .venv/bin/python server.py "$SKYGLOW_PORT"
fi

echo "[!] .venv not found - running with the system Python."
echo "    Direct archive mode (ECMWF / GFS / ICON) needs eccodes; run ./setup.sh once."
echo
exec python3 server.py "$SKYGLOW_PORT"
