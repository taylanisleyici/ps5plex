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

  while true; do
    python3 /opt/librarian/librarian.py 2>&1 || echo "[librarian] pass failed, retrying"
    sleep "$INTERVAL"
  done
) &

echo "[ps5plex] librarian started (every ${INTERVAL}s)"
