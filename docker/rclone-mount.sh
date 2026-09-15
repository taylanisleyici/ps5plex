#!/usr/bin/with-contenv bash
# Mounts zurg's WebDAV at /media before Plex starts.
#
# vfs-cache-mode is OFF on purpose. In `full` mode rclone downloads each file in
# its ENTIRETY the first time anything reads a single byte of it — so merely
# scanning the library drags it all onto local disk. Off means pure passthrough
# streaming with HTTP range requests, which is what a debrid mount wants.
# LinuxServer runs /custom-cont-init.d/* to completion first, so this backgrounds
# rclone and then waits for the mountpoint to actually appear.
set -u

MOUNT=/media
mkdir -p "$MOUNT"

if mountpoint -q "$MOUNT"; then
  echo "[ps5plex] $MOUNT is already mounted"
  exit 0
fi

rclone mount zurg: "$MOUNT" \
  --config /etc/rclone/rclone.conf \
  --allow-other \
  --uid "${PUID:-911}" --gid "${PGID:-911}" --umask 002 \
  --dir-cache-time 10s \
  --poll-interval 0 \
  --read-only \
  --vfs-cache-mode off \
  --vfs-read-chunk-size 32M \
  --vfs-read-chunk-size-limit 128M \
  --buffer-size 32M \
  --log-file /config/rclone.log \
  --log-level INFO &

for _ in $(seq 1 30); do
  if mountpoint -q "$MOUNT"; then
    echo "[ps5plex] rclone mounted $MOUNT"
    exit 0
  fi
  sleep 1
done

# Don't wedge the container — let Plex start so the log is reachable.
echo "[ps5plex] ERROR: rclone did not mount $MOUNT within 30s. See /config/rclone.log" >&2
