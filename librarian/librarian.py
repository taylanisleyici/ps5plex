"""Build a Plex-shaped symlink tree over the flat Real-Debrid mount.

zurg exposes one folder per torrent, named after the release. Plex copes badly
with that: its movie scanner treats every video file in a folder as a separate
movie (so a 120 MB featurette can win over the feature), and its TV scanner wants
Show/Season NN/... rather than a flat pile of episode files.

So we read the mount, classify each release, and lay out symlinks:

    /library/movies/La La Land (2016)/La La Land (2016).mkv
    /library/shows/The Boys/Season 05/The Boys - S05E01.mkv

Symlinks cost nothing and download nothing — they point back into the FUSE mount,
which streams on demand. Plex only ever sees clean, unambiguous names.
"""
import os
import pathlib
import sys

from classify import VIDEO_EXTS, classify, _first
from guessit import guessit
from rank import score

SOURCE = pathlib.Path(os.environ.get("LIBRARIAN_SOURCE", "/media/__all__"))
TARGET = pathlib.Path(os.environ.get("LIBRARIAN_TARGET", "/library"))
OVERRIDES_FILE = pathlib.Path(os.environ.get("LIBRARIAN_OVERRIDES", "/etc/ps5plex/overrides.yml"))


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
    if not SOURCE.exists():
        sys.exit(f"[librarian] source {SOURCE} is missing — is the mount up?")

    wanted, created = set(), 0
    counts = {"movie": 0, "series": 0, "skipped": 0, "junk": 0, "losers": 0}
    movies = {}          # (title, year) -> best candidate so far

    for folder in sorted(SOURCE.iterdir()):
        if not folder.is_dir():
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
            for src, dst in pairs:
                wanted.add(dst)
                created += link(src, dst)
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
            movies[key] = (points, info, files)
        else:
            counts["losers"] += 1

    for points, info, files in movies.values():
        pairs = plan_movie(info, files)
        if not pairs:
            counts["skipped"] += 1
            continue
        counts["movie"] += 1
        for src, dst in pairs:
            wanted.add(dst)
            created += link(src, dst)

    # Drop links for torrents that are gone from Real-Debrid.
    removed = 0
    for path in TARGET.rglob("*"):
        if path.is_symlink() and path not in wanted:
            path.unlink()
            removed += 1
    for path in sorted(TARGET.rglob("*"), key=lambda p: -len(p.parts)):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()

    print(f"[librarian] {counts['movie']} movies, {counts['series']} series "
          f"({len(wanted)} links: +{created} new, -{removed} stale) | "
          f"dropped {counts['junk']} cam/screener, {counts['losers']} lower-quality "
          f"duplicates, {counts['skipped']} unusable")


if __name__ == "__main__":
    main()
