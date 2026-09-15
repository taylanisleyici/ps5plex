#!/usr/bin/with-contenv bash
# Seeds the Plex preferences that a Dockerised server cannot discover for itself.
#
# Without these, Plex sees every connection arriving from Docker's NAT gateway
# (192.168.65.1 on Docker Desktop) and treats it as public internet — so even the
# browser on this very machine gets "Not authorized", and PS5 playback would be
# billed as *remote*, which now requires a paid Plex Pass.
set -u
PREFS="/config/Library/Application Support/Plex Media Server/Preferences.xml"
mkdir -p "$(dirname "$PREFS")"

PLEX_ADVERTISE_URL="${PLEX_ADVERTISE_URL:-}" python3 - "$PREFS" <<'PY'
import os, sys, xml.etree.ElementTree as ET, pathlib

prefs = pathlib.Path(sys.argv[1])
wanted = {
    # RFC1918 space counts as local — unblocks setup and keeps playback "local".
    "allowedNetworks": "192.168.0.0/16,10.0.0.0/8,172.16.0.0/12",
    "LanNetworksBandwidth": "192.168.0.0/16,10.0.0.0/8,172.16.0.0/12",
    "TranscoderTempDirectory": "/transcode",
    # Without this Plex calls the PS5 "WAN" — it arrives via Docker's gateway,
    # not the LAN — and applies remote quality limits, forcing a transcode of a
    # file the console could have played untouched.
    "LanNetworksBandwidth": "192.168.0.0/16,10.0.0.0/8,172.16.0.0/12",
    # These each read whole files off the Real-Debrid mount. Never do that.
    "GenerateBIFBehavior": "never",
    "GenerateChapterThumbBehavior": "never",
    "LoudnessAnalysisBehavior": "never",
    "ButlerTaskDeepMediaAnalysis": "0",
    "ButlerTaskGenerateBIFFrames": "0",
    "ButlerTaskGenerateChapterThumbs": "0",
    "ButlerTaskAnalyzeLoudness": "0",
}
# Inside the container Plex only knows its 172.x bridge address, which no other
# device on the LAN can reach. Advertise the Mac's real address if we were told it.
url = os.environ.get("PLEX_ADVERTISE_URL", "").strip()
if url:
    wanted["customConnections"] = url

if prefs.exists():
    root = ET.parse(prefs).getroot()
else:
    root = ET.Element("Preferences")

changed = [k for k, v in wanted.items() if root.get(k) != v]
for k, v in wanted.items():
    root.set(k, v)

if changed:
    ET.ElementTree(root).write(prefs, encoding="utf-8", xml_declaration=True)
    print(f"[ps5plex] seeded {len(changed)} Plex preference(s): {', '.join(changed)}")
else:
    print("[ps5plex] Plex preferences already correct")
PY
