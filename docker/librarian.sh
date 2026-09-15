#!/usr/bin/with-contenv bash
# Keeps /library in sync with the Real-Debrid mount.
#
# Runs on a loop rather than once, so a torrent added to Real-Debrid shows up in
# Plex a minute later without restarting anything. It dies with the container
# like everything else here — nothing is left running after Ctrl+C.
set -u
INTERVAL="${LIBRARIAN_INTERVAL:-120}"
mkdir -p /library

(
  # Wait for the mount to carry content; zurg needs a moment to list the account.
  for _ in $(seq 1 30); do
    [ -d /media/__all__ ] && [ -n "$(ls -A /media/__all__ 2>/dev/null)" ] && break
    sleep 2
  done

  # React as soon as the picker records something, rather than waiting out a
  # timer. managed.json changing is the signal; the interval is just a backstop
  # for torrents that only become playable later.
  last=""
  while true; do
    now=$(stat -c %Y /state/managed.json 2>/dev/null || echo 0)
    if [ "$now" != "$last" ]; then
      sleep 2                       # let the write settle
      python3 /opt/librarian/librarian.py 2>&1 || echo "[librarian] pass failed"
      last="$now"
      # Tell Plex to look, so it appears without waiting for a scheduled scan.
      # Section ids are discovered, not hardcoded: they change if a library is
      # ever deleted and recreated.
      for s in $(curl -sS --max-time 15 "http://localhost:32400/library/sections" 2>/dev/null \
                 | grep -oE 'key="[0-9]+"' | grep -oE '[0-9]+'); do
        curl -sS -o /dev/null --max-time 20 \
          "http://localhost:32400/library/sections/$s/refresh" 2>/dev/null || true
      done
    fi
    sleep 5
    tick=$((${tick:-0} + 5))
    if [ "$tick" -ge "$INTERVAL" ]; then
      python3 /opt/librarian/librarian.py 2>&1 || true
      tick=0
    fi
  done
) &

echo "[ps5plex] librarian watching /state/managed.json (backstop every ${INTERVAL}s)"
