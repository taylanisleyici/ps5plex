#!/bin/bash
# Copies the Plex server's own account token into .env, so the watchlist robot
# can read your Watchlist. Only works once the server is claimed.
set -euo pipefail
cd "$(dirname "$0")/.."

PREFS="/config/Library/Application Support/Plex Media Server/Preferences.xml"
TOKEN=$(docker compose exec -T plex python3 -c \
  "import xml.etree.ElementTree as ET; print(ET.parse('$PREFS').getroot().get('PlexOnlineToken') or '')" \
  2>/dev/null | tr -d '\r\n')

if [ -z "$TOKEN" ]; then
  echo "No token yet. Sign in at http://localhost:32400/web, then run this again."
  exit 1
fi

python3 - "$TOKEN" <<'PY'
import pathlib, sys
token = sys.argv[1]
p = pathlib.Path(".env")
lines = p.read_text().splitlines()
out = [f"PLEX_TOKEN={token}" if l.startswith("PLEX_TOKEN=") else l for l in lines]
if not any(l.startswith("PLEX_TOKEN=") for l in out):
    out.append(f"PLEX_TOKEN={token}")
p.write_text("\n".join(out) + "\n")
print(f"PLEX_TOKEN written to .env ({len(token)} chars)")
PY
