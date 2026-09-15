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

from guessit import guessit
from rank import origin_language, score

PLEX_TOKEN = os.environ.get("PLEX_TOKEN", "").strip()
RD_TOKEN = os.environ.get("RD_TOKEN", "").strip()
# Any Stremio scraper addon: Torrentio, Comet, MediaFusion. They share the
# /stream/{type}/{imdb}.json shape, so paste whichever you already use.
SCRAPER = os.environ.get("SCRAPER_URL", "").strip().rstrip("/")
INTERVAL = int(os.environ.get("WATCHLIST_INTERVAL", "120"))

STATE = os.environ.get("PS5PLEX_STATE", "/state/managed.json")

CINEMETA = "https://v3-cinemeta.strem.io"
DISCOVER = "https://discover.provider.plex.tv"
# api-1/api-2/api-6 are CNAMEs of api.real-debrid.com and resolve to the same
# IPs, but some networks drop TLS handshakes whose SNI is exactly
# "api.real-debrid.com" while letting the aliases through. Same server, same
# API — so pick whichever actually answers.
RD_HOSTS = ("api.real-debrid.com", "api-1.real-debrid.com",
            "api-2.real-debrid.com", "api-6.real-debrid.com")


def _pick_rd_host():
    for host in RD_HOSTS:
        try:
            requests.get(f"https://{host}/rest/1.0/time", timeout=8).raise_for_status()
            return host
        except Exception:
            continue
    return RD_HOSTS[0]


RD = f"https://{_pick_rd_host()}/rest/1.0"
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


def title_origin(kind, imdb):
    """The language a title was made in, so a dub away from it can be avoided."""
    try:
        r = requests.get(f"{CINEMETA}/meta/{'series' if kind == 'show' else 'movie'}/{imdb}.json",
                         timeout=30)
        r.raise_for_status()
        return origin_language(r.json().get("meta", {}).get("country"))
    except Exception:
        return None


def aired_episodes(imdb):
    """{season: [episode, ...]} for everything that has actually aired.

    Season 0 is specials and is skipped, as is anything dated in the future.
    """
    r = requests.get(f"{CINEMETA}/meta/series/{imdb}.json", timeout=30)
    r.raise_for_status()
    today = time.strftime("%Y-%m-%d")
    out = {}
    for v in r.json().get("meta", {}).get("videos", []):
        season, episode = v.get("season"), v.get("episode")
        if not season or not episode:
            continue
        released = (v.get("released") or v.get("firstAired") or "")[:10]
        if released and released > today:
            continue
        out.setdefault(int(season), []).append(int(episode))
    return {s: sorted(set(eps)) for s, eps in sorted(out.items())}


def covered_episodes(files):
    """Which episode numbers a Real-Debrid torrent actually contains.

    A torrent found by searching for episode 1 may hold the whole season, five
    episodes, or just the one — there is no way to tell before adding it.
    """
    found = set()
    for f in files or []:
        if not f.get("selected"):
            continue
        path = f.get("path", "")
        if not path.lower().endswith((".mkv", ".mp4", ".avi", ".m4v")):
            continue
        ep = guessit(path.rsplit("/", 1)[-1], {"type": "episode"}).get("episode")
        for e in (ep if isinstance(ep, list) else [ep]):
            if e:
                found.add(int(e))
    return found


def best_series_release(imdb, season, episode=1, origin=None):
    """Pick one torrent for a season.

    We ask the scraper for episode 1. Comet resolves season packs down to the
    individual episode file, but the hash it reports is the *torrent's* hash —
    so adding it pulls in whatever that torrent holds, usually the whole season.
    One request and one add per season instead of one per episode, which matters
    when the debrid API is rate-limited.
    """
    url = f"{SCRAPER}/stream/series/{imdb}:{season}:{episode}.json"
    r = requests.get(url, timeout=90, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    best = None
    for s in r.json().get("streams", []):
        release, info_hash, size, cached, context = _stream_fields(s)
        if not (release and info_hash):
            continue
        points = score(release, size, origin, context)
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
    # The description carries the torrent's own name and Comet's language flags,
    # which is where a dub shows up when the inner file is named innocuously.
    context = (s.get("description") or "").replace("\n", " ")
    return (release, info_hash, hints.get("videoSize") or 0,
            ("⚡" in badge or "[RD+]" in badge), context)


def best_release(kind, imdb, origin=None):
    """Ask the scraper what exists and pick the release the PS5 plays best."""
    url = f"{SCRAPER}/stream/{'series' if kind == 'show' else 'movie'}/{imdb}.json"
    r = requests.get(url, timeout=90, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    best = None
    for s in r.json().get("streams", []):
        release, info_hash, size, cached, context = _stream_fields(s)
        if not (release and info_hash):
            continue
        points = score(release, size, origin, context)
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
        # A movie is done once fetched. A series never is: new episodes air, and
        # the torrent we picked may only have covered part of a season, so it is
        # re-examined every pass (per-episode gaps are checked in _fetch_series).
        if kind != "show" and key in handled:
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


def _add_and_record(found, title, imdb, key, kind, managed, season=None):
    """Add to Real-Debrid and record it. Returns the episodes it turned out to hold."""
    points, name, info_hash = found
    tid = add_to_rd(info_hash)
    info = requests.get(f"{RD}/torrents/info/{tid}",
                        headers={"Authorization": f"Bearer {RD_TOKEN}"}, timeout=30).json()
    episodes = covered_episodes(info.get("files")) if season else set()
    folder = info.get("filename") or name
    managed[folder] = {"title": title, "imdb": imdb, "kind": kind, "ratingKey": key,
                       "season": season, "episodes": sorted(episodes) or None}
    save_managed(managed)
    label = f"{title} S{season:02d}" if season else title
    extra = f" [{len(episodes)} episodes]" if len(episodes) > 1 else ""
    print(f"[watchlist] added {label} -> {name[:58]}{extra} (score {points:.0f})")
    return episodes


def _fetch_movie(title, imdb, key, managed, torrents):
    if _adopt(title, imdb, key, managed, torrents):
        return
    found = best_release("movie", imdb, title_origin("movie", imdb))
    if not found:
        print(f"[watchlist] {title}: nothing usable found (all junk, or no results)")
        return
    _add_and_record(found, title, imdb, key, "movie", managed)


def _fetch_series(title, imdb, key, managed, torrents, budget=8):
    """Fill in every aired episode, using as few torrents as possible.

    Searching for episode 1 often returns a torrent holding the whole season, so
    we add that first and only then fetch whatever it turned out to be missing.
    `budget` caps how many adds happen per pass so a long-running series does not
    fire off fifty API calls at once; the next pass continues where this stopped.
    """
    origin = title_origin("show", imdb)
    schedule = aired_episodes(imdb)
    if not schedule:
        print(f"[watchlist] {title}: no aired episodes found")
        return

    for season, episodes in schedule.items():
        have = set()
        for folder, meta in managed.items():
            if meta.get("imdb") == imdb and meta.get("season") == season:
                have |= set(meta.get("episodes") or [])

        for episode in episodes:
            if episode in have:
                continue
            if budget <= 0:
                print(f"[watchlist] {title}: pausing, more to fetch next pass")
                return
            found = best_series_release(imdb, season, episode, origin)
            if not found:
                print(f"[watchlist] {title} S{season:02d}E{episode:02d}: nothing usable")
                have.add(episode)          # don't retry it every 2 minutes
                continue
            got = _add_and_record(found, title, imdb, key, "show", managed, season)
            budget -= 1
            # The torrent may well have brought its whole season with it.
            have |= got or {episode}


def main():
    missing = [n for n, v in (("PLEX_TOKEN", PLEX_TOKEN), ("RD_TOKEN", RD_TOKEN),
                              ("SCRAPER_URL", SCRAPER)) if not v]
    if missing:
        sys.exit(f"[watchlist] not configured: {', '.join(missing)} — see .env.example")
    print(f"[watchlist] watching your Plex Watchlist every {INTERVAL}s via {RD}")
    while True:
        try:
            pass_once()
        except Exception as e:            # a flaky scraper must not kill the loop
            print(f"[watchlist] pass failed: {type(e).__name__}: {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
