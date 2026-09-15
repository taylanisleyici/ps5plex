"""Watch the Plex Watchlist and fetch what appears on it.

Add a title to your Watchlist — from the PS5 Plex app, your phone, anywhere —
and this puts a good release into Real-Debrid. zurg exposes it, the librarian
files it, Plex shows it. Couch to playable without touching the Mac.

Plex's Watchlist *RSS feed* is a Plex Pass feature, but the underlying
discover.provider.plex.tv API works for any signed-in account, which is the one
this reads.

Runs only while the stack is up, and dies with it like everything else here.
"""
import os
import sys
import time
import xml.etree.ElementTree as ET

import requests

from rank import score

PLEX_TOKEN = os.environ.get("PLEX_TOKEN", "").strip()
RD_TOKEN = os.environ.get("RD_TOKEN", "").strip()
# Any Stremio scraper addon: Torrentio, Comet, MediaFusion. They share the
# /stream/{type}/{imdb}.json shape, so paste whichever you already use.
SCRAPER = os.environ.get("SCRAPER_URL", "").strip().rstrip("/")
INTERVAL = int(os.environ.get("WATCHLIST_INTERVAL", "120"))

DISCOVER = "https://discover.provider.plex.tv"
RD = "https://api.real-debrid.com/rest/1.0"
HEADERS = {"Accept": "application/xml", "X-Plex-Product": "ps5plex",
           "X-Plex-Client-Identifier": "ps5plex-watchlist"}


def watchlist():
    """Every item currently on the Plex Watchlist, as (type, title, ratingKey)."""
    r = requests.get(f"{DISCOVER}/library/sections/watchlist/all",
                     params={"X-Plex-Token": PLEX_TOKEN}, headers=HEADERS, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    out = []
    for el in list(root.findall(".//Video")) + list(root.findall(".//Directory")):
        key = (el.get("ratingKey") or "").rsplit("/", 1)[-1]
        if key:
            out.append((el.get("type", "movie"), el.get("title", "?"), key))
    return out


def imdb_id(rating_key):
    """Plex guids are opaque; the metadata record carries the IMDb id."""
    r = requests.get(f"{DISCOVER}/library/metadata/{rating_key}",
                     params={"X-Plex-Token": PLEX_TOKEN}, headers=HEADERS, timeout=30)
    if r.status_code != 200:
        return None
    for g in ET.fromstring(r.text).iter("Guid"):
        gid = g.get("id", "")
        if gid.startswith("imdb://"):
            return gid.split("://", 1)[1]
    return None


def rd_existing_names():
    r = requests.get(f"{RD}/torrents", params={"limit": 200},
                     headers={"Authorization": f"Bearer {RD_TOKEN}"}, timeout=30)
    r.raise_for_status()
    return {t.get("filename", "").lower() for t in r.json()}


def best_release(kind, imdb):
    """Ask the scraper what exists and pick the release the PS5 plays best."""
    url = f"{SCRAPER}/stream/{'series' if kind == 'show' else 'movie'}/{imdb}.json"
    r = requests.get(url, timeout=90, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    best = None
    for s in r.json().get("streams", []):
        hints = s.get("behaviorHints") or {}
        # Comet puts the real release name and the info hash in behaviorHints;
        # `name` is only a badge like "[RD⚡] Comet 2160p", which tells you
        # nothing about codec or source.
        release = hints.get("filename") or ""
        if not release:
            first = (s.get("description") or "").split("\n")[0]
            release = first.lstrip("📄 ").strip()
        # bingeGroup looks like "comet|realdebrid|<infohash>"
        info_hash = ""
        for part in str(hints.get("bingeGroup", "")).split("|"):
            if len(part) == 40 and all(c in "0123456789abcdefABCDEF" for c in part):
                info_hash = part.lower()
        if not (release and info_hash):
            continue
        points = score(release, hints.get("videoSize") or 0)
        if points is None:
            continue          # CAM/TS/screener — never selected
        # Already cached at Real-Debrid means it plays immediately instead of
        # waiting on a download.
        if "⚡" in (s.get("name") or "") or "[RD+]" in (s.get("name") or ""):
            points += 40
        if best is None or points > best[0]:
            best = (points, release, info_hash)
    return best


def add_to_rd(info_hash):
    magnet = f"magnet:?xt=urn:btih:{info_hash}"
    r = requests.post(f"{RD}/torrents/addMagnet",
                      headers={"Authorization": f"Bearer {RD_TOKEN}"},
                      data={"magnet": magnet}, timeout=30)
    r.raise_for_status()
    tid = r.json()["id"]
    # Selecting all files is what actually starts the caching.
    requests.post(f"{RD}/torrents/selectFiles/{tid}",
                  headers={"Authorization": f"Bearer {RD_TOKEN}"},
                  data={"files": "all"}, timeout=30).raise_for_status()
    return tid


def pass_once(seen):
    existing = rd_existing_names()
    for kind, title, key in watchlist():
        if key in seen:
            continue
        imdb = imdb_id(key)
        if not imdb:
            print(f"[watchlist] {title}: no IMDb id, skipping")
            seen.add(key)
            continue
        if any(title.lower() in name for name in existing):
            seen.add(key)
            continue
        found = best_release(kind, imdb)
        if not found:
            print(f"[watchlist] {title}: nothing usable found (all junk or no results)")
            continue
        points, name, info_hash = found
        add_to_rd(info_hash)
        seen.add(key)
        print(f"[watchlist] added {title} -> {name[:70]} (score {points:.0f})")


def main():
    missing = [n for n, v in (("PLEX_TOKEN", PLEX_TOKEN), ("RD_TOKEN", RD_TOKEN),
                              ("SCRAPER_URL", SCRAPER)) if not v]
    if missing:
        sys.exit(f"[watchlist] not configured: {', '.join(missing)} — see .env.example")
    print(f"[watchlist] watching your Plex Watchlist every {INTERVAL}s")
    seen = set()
    while True:
        try:
            pass_once(seen)
        except Exception as e:                       # a flaky scraper must not kill the loop
            print(f"[watchlist] pass failed: {type(e).__name__}: {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
