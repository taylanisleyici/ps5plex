# Plex with rclone living inside the same container.
#
# Why not a separate rclone container? Sharing a FUSE mount between containers
# needs `rshared` bind propagation, which Docker Desktop for macOS cannot provide
# — and it downgrades the propagation silently rather than erroring, so Plex just
# sees an empty directory. One container, one mount namespace, no propagation.
FROM lscr.io/linuxserver/plex:latest

ARG TARGETARCH
ARG RCLONE_VERSION=v1.75.1

RUN apt-get update \
 && apt-get install -y --no-install-recommends fuse3 unzip curl ca-certificates python3 \
 && curl -fsSL "https://downloads.rclone.org/${RCLONE_VERSION}/rclone-${RCLONE_VERSION}-linux-${TARGETARCH}.zip" -o /tmp/rclone.zip \
 && unzip -j /tmp/rclone.zip '*/rclone' -d /usr/local/bin \
 && chmod +x /usr/local/bin/rclone \
 && echo user_allow_other >> /etc/fuse.conf \
 && mkdir -p /etc/rclone /media \
 && rm -rf /tmp/rclone.zip /var/lib/apt/lists/*

COPY plex-prefs.sh    /custom-cont-init.d/05-plex-prefs
COPY rclone-mount.sh  /custom-cont-init.d/10-rclone-mount
RUN chmod +x /custom-cont-init.d/05-plex-prefs /custom-cont-init.d/10-rclone-mount
