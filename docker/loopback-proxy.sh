#!/usr/bin/with-contenv bash
# Make every client look local to Plex.
#
# Plex decides "local" one way: is the client on the same subnet as one of my
# interfaces? Inside Docker Desktop its interface is 172.x and every outside
# client arrives NAT'd from 192.168.65.1 — a different subnet — so Plex labels
# the PS5 "(WAN)" and, since April 2026, refuses to play without a paid Remote
# Watch Pass. There is no setting for this; the old "LAN Networks" preference
# was removed from the server entirely.
#
# So the published port lands on socat, which relays it to Plex over loopback.
# Plex sees 127.0.0.1, logs "(Loopback)", and treats it as local. Layer 4 only —
# TLS still terminates in Plex, so the plex.direct certificate keeps working.
#
# The relay port must not be 32401: Plex binds 127.0.0.1:32401 itself for an
# internal service, and a relay sitting on 0.0.0.0:32401 makes every Plex start
# die with "Error binding acceptor: Address in use" (the log never names the port).
set -u
socat TCP-LISTEN:33400,fork,reuseaddr TCP:127.0.0.1:32400 &
echo "[ps5plex] loopback relay :33400 -> 127.0.0.1:32400"
