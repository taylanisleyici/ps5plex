"""Build a Plex-shaped symlink tree over the flat Real-Debrid mount.

Only what the watchlist robot fetched is linked. A Real-Debrid account is full of
things you watched years ago; a library of all of it is noise, and walking it
costs API calls. state/managed.json is the list, written by watchlist/poller.py.

rdserve exposes one folder per torrent, named after the release. Plex copes badly
with that: its movie scanner treats every video file in a folder as a separate
movie (so a 120 MB featurette can win over the feature), and its TV scanner wants
Show/Season NN/... rather than a flat pile of episode files.

So we read the mount, classify each release, and lay out symlinks:

    /library/movies/La La Land (2016)/La La Land (2016).mkv
    /library/shows/The Boys/Season 05/The Boys - S05E01.mkv

Symlinks cost nothing and download nothing — they point back into the FUSE mount,
which streams on demand. Plex only ever sees clean, unambiguous names.
"""
import json
import os
import pathlib
import sys

from classify import VIDEO_EXTS, classify, _first
from guessit import guessit
from rank import score

SOURCE = pathlib.Path(os.environ.get("LIBRARIAN_SOURCE", "/media"))
TARGET = pathlib.Path(os.environ.get("LIBRARIAN_TARGET", "/library"))
OVERRIDES_FILE = pathlib.Path(os.environ.get("LIBRARIAN_OVERRIDES", "/etc/ps5plex/overrides.yml"))
STATE = pathlib.Path(os.environ.get("PS5PLEX_STATE", "/state/managed.json"))
SUBS_DIR = pathlib.Path(os.environ.get("SUBS_DIR", "/state/subs"))


def load_managed():
    """Folder names the watchlist robot asked for. Nothing else gets linked."""
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def load_overrides():
    """Tiny key: value YAML. Avoids a PyYAML dependency for ~3 lines of config."""
    out = {}
    if not OVERRIDES_FILE.exists():
        return out
    for line in OVERRIDES_FILE.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().strip('"\''), value.strip().strip('"\'')
        if value in ("movie", "series"):
            out[key] = value
    return out


def video_files(folder):
    """Every video file in a torrent folder, biggest first."""
    found = []
    for path in folder.rglob("*"):
        try:
            if path.is_file() and path.suffix.lower() in VIDEO_EXTS:
                found.append((path.stat().st_size, path))
        except OSError:
            continue        # a broken debrid link — skip rather than abort
    return [p for _, p in sorted(found, key=lambda x: -x[0])]


def safe(name):
    """Strip characters that break on filesystems or confuse Plex's parser."""
    cleaned = "".join(c for c in name if c not in '/\\:*?"<>|').strip().rstrip(".")
    return cleaned or "Unknown"


def link(src, dst):
    """Point dst at src, replacing whatever was there. Returns True if changed."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_symlink():
        if os.readlink(dst) == str(src):
            return False
        dst.unlink()
    elif dst.exists():
        return False
    dst.symlink_to(src)
    return True


def place_subtitles(imdb, dst, season=None, episode=None):
    """Copy any downloaded subtitles next to the video file. Returns their paths.

    Plex reads "Name.eng.srt" sitting beside "Name.mkv" as a selectable English
    track, and "Name.2.eng.srt" as a second one. A sidecar file is far more
    reliable than whatever a release embedded, and the PS5 client handles it
    better than image-based subtitles.

    Source names are "<imdb>[.sXXeYY][.<n>].<lang>.srt"; the optional middle
    part is kept so several per language can coexist.
    """
    placed = set()
    if not SUBS_DIR.exists():
        return placed
    tag = f".s{int(season):02d}e{int(episode):02d}" if season and episode else ""
    prefix = f"{imdb}{tag}."
    base = str(dst)[:-len(dst.suffix)]           # not with_suffix: "Mr. Robot" has a dot
    for src in SUBS_DIR.glob(f"{prefix}*.srt"):
        rest = src.name[len(prefix):-len(".srt")]        # "<n>.<lang>" or "<lang>"
        if tag == "" and rest.startswith("s") and "e" in rest.split(".")[0]:
            continue                                     # an episode file, not this movie's
        target = pathlib.Path(f"{base}.{rest}.srt")
        try:
            if not target.exists() or target.read_bytes() != src.read_bytes():
                target.write_bytes(src.read_bytes())
            placed.add(target)
        except OSError:
            continue
    return placed


def plan_movie(info, files):
    """One movie per torrent: the largest file. Extras are simply not linked."""
    if not files:
        return []
    biggest = files[0]
    name = safe(info["title"])
    if info.get("year"):
        name = f"{name} ({info['year']})"
    return [(biggest, TARGET / "movies" / name / f"{name}{biggest.suffix}")]


def plan_series(info, files):
    """One symlink per episode file, foldered by season."""
    show = safe(info["title"])
    out = []
    for path in files:
        per_file = guessit(path.name)
        season = _first(per_file.get("season")) or info.get("season") or 1
        episode = _first(per_file.get("episode")) or info.get("episode")
        if episode is None:
            continue         # can't place it without an episode number
        if season > 100:     # same year-as-season trap as in classify
            season = info.get("season") or 1
        tag = f"S{int(season):02d}E{int(episode):02d}"
        out.append((path, TARGET / "shows" / show / f"Season {int(season):02d}"
                    / f"{show} - {tag}{path.suffix}"))
    return out


def main():
    overrides = load_overrides()
    managed = load_managed()
    if not SOURCE.exists():
        sys.exit(f"[librarian] source {SOURCE} is missing — is the mount up?")
    if not managed:
        print("[librarian] nothing requested yet — add something to your Plex Watchlist")

    wanted, created = set(), 0
    counts = {"movie": 0, "series": 0, "skipped": 0, "junk": 0, "losers": 0}
    movies = {}          # (title, year) -> best candidate so far

    for folder in sorted(SOURCE.iterdir()):
        if not folder.is_dir() or folder.name not in managed:
            continue
        kind, info = classify(folder.name, overrides)
        files = video_files(folder)
        if not files:
            counts["skipped"] += 1
            continue

        if kind == "series":
            pairs = plan_series(info, files)
            if not pairs:
                counts["skipped"] += 1
                continue
            counts["series"] += 1
            imdb = managed[folder.name].get("imdb")
            for src, dst in pairs:
                wanted.add(dst)
                created += link(src, dst)
                if imdb:
                    parsed = guessit(dst.name, {"type": "episode"})
                    wanted |= place_subtitles(imdb, dst, _first(parsed.get("season")),
                                              _first(parsed.get("episode")))
            continue

        # Movies compete: several torrents can be the same film in different
        # quality, and a camera rip must never win over a real release.
        try:
            size = files[0].stat().st_size
        except OSError:
            size = 0
        points = score(folder.name, size)
        if points is None:
            counts["junk"] += 1          # CAM/TS/screener — never link it
            continue
        key = (info["title"].lower(), info.get("year"))
        best = movies.get(key)
        if best is None or points > best[0]:
            if best is not None:
                counts["losers"] += 1
            movies[key] = (points, info, files, managed[folder.name].get("imdb"))
        else:
            counts["losers"] += 1

    subs_placed = 0
    for points, info, files, imdb in movies.values():
        pairs = plan_movie(info, files)
        if not pairs:
            counts["skipped"] += 1
            continue
        counts["movie"] += 1
        for src, dst in pairs:
            wanted.add(dst)
            created += link(src, dst)
            if imdb:
                placed = place_subtitles(imdb, dst)
                subs_placed += len(placed)
                wanted |= placed

    # Drop links for torrents that are gone from Real-Debrid, and sidecars whose
    # source is gone (a new pick replaces the subtitle set along with the video).
    removed = 0
    for path in TARGET.rglob("*"):
        if path in wanted:
            continue
        if path.is_symlink() or (path.is_file() and path.suffix == ".srt"):
            path.unlink()
            removed += 1
    for path in sorted(TARGET.rglob("*"), key=lambda p: -len(p.parts)):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()

    print(f"[librarian] {counts['movie']} movies, {counts['series']} series from "
          f"{len(managed)} requested ({len(wanted)} links: +{created} new, "
          f"-{removed} stale)"
          + (f" | dropped {counts['junk']} cam/screener" if counts["junk"] else "")
          + (f", {counts['losers']} worse duplicates" if counts["losers"] else "")
          + (f", {counts['skipped']} unusable" if counts["skipped"] else "")
          + (f" | {subs_placed} subtitle file(s)" if subs_placed else ""))


if __name__ == "__main__":
    main()
