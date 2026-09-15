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
# Fetching is manual by default — use the picker at :8081. Automatic fetching
# once pulled 17 torrents for a single show without showing any of them.
AUTO_FETCH = os.environ.get("AUTO_FETCH", "0") == "1"
# Even in automatic mode, never pull a back catalogue off one watchlist entry.
MAX_PER_TITLE = int(os.environ.get("MAX_PER_TITLE", "3"))

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


def _runtime_minutes(text):
    """Cinemeta writes runtimes as "157 min", "2h 37min" or "45 min"."""
    if not text:
        return None
    h = re.search(r"(\d+)\s*h", text)
    m = re.search(r"(\d+)\s*min", text)
    mins = (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)
    return mins or None


def title_info(kind, imdb):
    """Origin language and runtime from Stremio's metadata.

    Runtime matters as much as language here: with the file size from the
    scraper it gives the bitrate a release will demand, which on a Wi-Fi link
    decides whether it plays or buffers.
    """
    try:
        r = requests.get(f"{CINEMETA}/meta/{'series' if kind == 'show' else 'movie'}/{imdb}.json",
                         timeout=30)
        r.raise_for_status()
        meta = r.json().get("meta", {})
        return {"origin": origin_language(meta.get("country")),
                "runtime_min": _runtime_minutes(meta.get("runtime"))}
    except Exception:
        return {"origin": None, "runtime_min": None}


def title_origin(kind, imdb):
    """The language a title was made in, so a dub away from it can be avoided."""
    return title_info(kind, imdb)["origin"]


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
    """Add a magnet. Does not wait for Real-Debrid to finish with it.

    An uncached torrent sits in `magnet_conversion` while Real-Debrid fetches its
    metadata from the swarm, which can take minutes. Blocking on that made the
    picker time out and leave half-added torrents behind, so we add, give it a
    few seconds in case it is already cached, and let reconcile() finish the job.
    """
    head = {"Authorization": f"Bearer {RD_TOKEN}"}
    r = requests.post(f"{RD}/torrents/addMagnet", headers=head,
                      data={"magnet": f"magnet:?xt=urn:btih:{info_hash}"}, timeout=30)
    r.raise_for_status()
    tid = r.json()["id"]
    for _ in range(4):                    # a cached release is ready almost at once
        if _select_if_ready(tid, head):
            break
        time.sleep(1.5)
    return tid


def _select_if_ready(tid, head=None):
    """Select all files once Real-Debrid will accept it. True when nothing is left to do."""
    head = head or {"Authorization": f"Bearer {RD_TOKEN}"}
    try:
        info = requests.get(f"{RD}/torrents/info/{tid}", headers=head, timeout=30).json()
    except Exception:
        return False
    status = info.get("status", "")
    if status == "waiting_files_selection":
        try:
            requests.post(f"{RD}/torrents/selectFiles/{tid}", headers=head,
                          data={"files": "all"}, timeout=30).raise_for_status()
            return True
        except Exception:
            return False
    return status in ("downloaded", "downloading", "queued", "compressing", "uploading")


def reconcile():
    """Finish off torrents Real-Debrid was still converting when they were added,
    and drop ones it could not resolve at all."""
    head = {"Authorization": f"Bearer {RD_TOKEN}"}
    try:
        torrents = requests.get(f"{RD}/torrents", params={"limit": 200},
                                headers=head, timeout=30).json()
    except Exception:
        return
    for t in torrents:
        status = t.get("status", "")
        if status == "waiting_files_selection":
            if _select_if_ready(t["id"], head):
                print(f"[picker] selected files for {t.get('filename','?')[:50]}", flush=True)
        elif status in ("magnet_error", "error", "virus", "dead"):
            requests.delete(f"{RD}/torrents/delete/{t['id']}", headers=head, timeout=30)
            print(f"[picker] removed unusable torrent ({status})", flush=True)


def prune(managed, on_watchlist):
    """Forget titles no longer on the watchlist.

    Only the library forgets them — the torrents stay in your Real-Debrid
    account. The librarian drops their symlinks on its next pass.
    """
    gone = [f for f, v in managed.items()
            if v.get("imdb") and v["imdb"] not in on_watchlist]
    for f in gone:
        print(f"[watchlist] {managed[f]['title']}: off your watchlist, removing from library")
        managed.pop(f)
    if gone:
        save_managed(managed)


def pass_once():
    """One sweep: anything on the watchlist we have not already handled."""
    managed = load_managed()
    handled = {v.get("ratingKey") for v in managed.values()}
    torrents = None

    items = watchlist()
    prune(managed, {imdb_id(k) for _, _, k in items} - {None})
    if not AUTO_FETCH:
        return              # the picker does the fetching; see picker.py

    for kind, title, key in items:
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


def _fetch_series(title, imdb, key, managed, torrents, budget=None):
    """Fill in every aired episode, using as few torrents as possible.

    Searching for episode 1 often returns a torrent holding the whole season, so
    we add that first and only then fetch whatever it turned out to be missing.
    `budget` caps how many adds happen per pass so a long-running series does not
    fire off fifty API calls at once; the next pass continues where this stopped.
    """
    budget = MAX_PER_TITLE if budget is None else budget
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
