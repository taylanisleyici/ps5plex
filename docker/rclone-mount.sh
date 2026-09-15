#!/usr/bin/with-contenv bash
# Mounts rdserve's HTTP tree at /media before Plex starts.
#
# The read buffer is large on purpose. The Mac reaches Real-Debrid over Wi-Fi at
# ~64 Mbps with dips, and the PS5's own playback buffer is tiny: any stall in the
# feed makes its app abandon the stream and fall back to a transcode, which then
# stalls the same way. 256 MB in memory per open file rides out those dips, and
# letting chunks grow without limit turns a file into one long-lived request
# instead of a fresh HTTP round-trip every 128 MB.
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

rclone mount rd: "$MOUNT" \
  --config /etc/rclone/rclone.conf \
  --allow-other \
  --uid "${PUID:-911}" --gid "${PGID:-911}" --umask 002 \
  --dir-cache-time 10s \
  --poll-interval 0 \
  --read-only \
  --vfs-cache-mode off \
  --vfs-read-chunk-size 64M \
  --vfs-read-chunk-size-limit off \
  --buffer-size 256M \
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
