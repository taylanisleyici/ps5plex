"""Watch the Plex Watchlist and fetch what appears on it.

Add a title to your Watchlist — from the PS5 Plex app, your phone, anywhere —
and this puts a good release into Real-Debrid. zurg exposes it, the librarian
files it, Plex shows it. Couch to playable without touching the Mac.

Only what you ask for ends up in Plex. A Real-Debrid account accumulates years of
already-watched torrents, and mirroring all of it into a library is both useless
and a good way to get rate-limited. Everything this fetches is recorded in
state/managed.json, and the librarian links nothing else.

Plex's Watchlist *RSS feed* is a Plex Pass feature, but the underlying
discover.provider.plex.tv API works for any signed-in account, which is the one
this reads.

Runs only while the stack is up, and dies with it like everything else here.
"""
import os
import pathlib
import re
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

STATE = os.environ.get("PS5PLEX_STATE", "/state/managed.json")

CINEMETA = "https://v3-cinemeta.strem.io"
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


def load_managed():
    """Which Real-Debrid folders this project put there, and for what."""
    import json
    try:
        return json.loads(pathlib.Path(STATE).read_text())
    except (OSError, ValueError):
        return {}


def save_managed(managed):
    import json
    path = pathlib.Path(STATE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(managed, indent=2, ensure_ascii=False))


def rd_torrents():
    """id, filename and hash for everything in the account."""
    r = requests.get(f"{RD}/torrents", params={"limit": 200},
                     headers={"Authorization": f"Bearer {RD_TOKEN}"}, timeout=30)
    r.raise_for_status()
    return r.json()


def rd_existing_names():
    r = requests.get(f"{RD}/torrents", params={"limit": 200},
                     headers={"Authorization": f"Bearer {RD_TOKEN}"}, timeout=30)
    r.raise_for_status()
    return {t.get("filename", "").lower() for t in r.json()}


def aired_seasons(imdb):
    """Seasons of a series that have actually aired, from Stremio's metadata.

    Season 0 is specials and is skipped.
    """
    r = requests.get(f"{CINEMETA}/meta/series/{imdb}.json", timeout=30)
    r.raise_for_status()
    today = time.strftime("%Y-%m-%d")
    seasons = set()
    for v in r.json().get("meta", {}).get("videos", []):
        season = v.get("season")
        if not season:
            continue
        released = (v.get("released") or v.get("firstAired") or "")[:10]
        if released and released > today:
            continue                      # not out yet
        seasons.add(int(season))
    return sorted(seasons)


def best_series_release(imdb, season):
    """Pick one torrent for a season.

    We ask the scraper for episode 1. Comet resolves season packs down to the
    individual episode file, but the hash it reports is the *torrent's* hash —
    so adding it pulls in whatever that torrent holds, usually the whole season.
    One request and one add per season instead of one per episode, which matters
    when the debrid API is rate-limited.
    """
    url = f"{SCRAPER}/stream/series/{imdb}:{season}:1.json"
    r = requests.get(url, timeout=90, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    best = None
    for s in r.json().get("streams", []):
        release, info_hash, size, cached = _stream_fields(s)
        if not (release and info_hash):
            continue
        points = score(release, size)
        if points is None:
            continue
        if cached:
            points += 40
        if best is None or points > best[0]:
            best = (points, release, info_hash)
    return best


def _stream_fields(s):
    """Comet 2.x hides the useful parts in behaviorHints."""
    hints = s.get("behaviorHints") or {}
    release = hints.get("filename") or ""
    if not release:
        release = (s.get("description") or "").split("\n")[0].lstrip("📄 ").strip()
    info_hash = ""
    for part in str(hints.get("bingeGroup", "")).split("|"):
        if len(part) == 40 and all(c in "0123456789abcdefABCDEF" for c in part):
            info_hash = part.lower()
    badge = s.get("name") or ""
    return release, info_hash, hints.get("videoSize") or 0, ("⚡" in badge or "[RD+]" in badge)


def best_release(kind, imdb):
    """Ask the scraper what exists and pick the release the PS5 plays best."""
    url = f"{SCRAPER}/stream/{'series' if kind == 'show' else 'movie'}/{imdb}.json"
    r = requests.get(url, timeout=90, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    best = None
    for s in r.json().get("streams", []):
        release, info_hash, size, cached = _stream_fields(s)
        if not (release and info_hash):
            continue
        points = score(release, size)
        if points is None:
            continue          # CAM/TS/screener — never selected
        if cached:
            points += 40      # already at Real-Debrid: plays immediately
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


def pass_once():
    """One sweep: anything on the watchlist we have not already handled."""
    managed = load_managed()
    handled = {v.get("ratingKey") for v in managed.values()}
    torrents = None

    for kind, title, key in watchlist():
        if key in handled:
            continue

        imdb = imdb_id(key)
        if not imdb:
            print(f"[watchlist] {title}: no IMDb id, skipping")
            continue

        if torrents is None:
            torrents = rd_torrents()

        if kind == "show":
            _fetch_series(title, imdb, key, managed, torrents)
        else:
            _fetch_movie(title, imdb, key, managed, torrents)


def _adopt(title, imdb, key, managed, torrents, season=None):
    """If it is already in the account, use that instead of fetching it twice."""
    needle = title.lower()
    for t in torrents:
        name = (t.get("filename") or "").lower()
        if needle not in name:
            continue
        if season is not None and not re.search(rf"s0?{season}\b", name):
            continue
        managed[t["filename"]] = {"title": title, "imdb": imdb, "kind": "show" if season
                                  else "movie", "ratingKey": key, "season": season}
        save_managed(managed)
        which = f"{title} season {season}" if season else title
        print(f"[watchlist] {which}: already in Real-Debrid, added to your library")
        return True
    return False


def _record(folder, title, imdb, key, kind, managed, season=None):
    managed[folder] = {"title": title, "imdb": imdb, "kind": kind,
                       "ratingKey": key, "season": season}
    save_managed(managed)


def _add_and_record(found, title, imdb, key, kind, managed, season=None):
    points, name, info_hash = found
    tid = add_to_rd(info_hash)
    info = requests.get(f"{RD}/torrents/info/{tid}",
                        headers={"Authorization": f"Bearer {RD_TOKEN}"}, timeout=30).json()
    _record(info.get("filename") or name, title, imdb, key, kind, managed, season)
    label = f"{title} S{season:02d}" if season else title
    print(f"[watchlist] added {label} -> {name[:62]} (score {points:.0f})")


def _fetch_movie(title, imdb, key, managed, torrents):
    if _adopt(title, imdb, key, managed, torrents):
        return
    found = best_release("movie", imdb)
    if not found:
        print(f"[watchlist] {title}: nothing usable found (all junk, or no results)")
        return
    _add_and_record(found, title, imdb, key, "movie", managed)


def _fetch_series(title, imdb, key, managed, torrents):
    """One season pack per aired season, rather than one torrent per episode."""
    seasons = aired_seasons(imdb)
    if not seasons:
        print(f"[watchlist] {title}: no aired seasons found")
        return
    for season in seasons:
        if any(v.get("imdb") == imdb and v.get("season") == season
               for v in managed.values()):
            continue
        if _adopt(title, imdb, key, managed, torrents, season):
            continue
        found = best_series_release(imdb, season)
        if not found:
            print(f"[watchlist] {title} S{season:02d}: nothing usable found")
            continue
        _add_and_record(found, title, imdb, key, "show", managed, season)


def main():
    missing = [n for n, v in (("PLEX_TOKEN", PLEX_TOKEN), ("RD_TOKEN", RD_TOKEN),
                              ("SCRAPER_URL", SCRAPER)) if not v]
    if missing:
        sys.exit(f"[watchlist] not configured: {', '.join(missing)} — see .env.example")
    print(f"[watchlist] watching your Plex Watchlist every {INTERVAL}s")
    while True:
        try:
            pass_once()
        except Exception as e:                       # a flaky scraper must not kill the loop
            print(f"[watchlist] pass failed: {type(e).__name__}: {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
