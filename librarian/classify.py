"""Decide whether a release name is a movie or a TV episode, and parse it.

Release names are parsed with `guessit`, a maintained parser that handles the
thousands of naming conventions scene/P2P groups actually use — including things
no hand-written regex catches, like Spanish "Cap.406" or Russian "1 сезон".

Two guards sit on top of it, both from observed failures on a real library:
  * guessit reads a leading number plus a year as season/episode, turning
    "10 Things I Hate About You (1999)" into S1999E10. Real seasons are small.
  * "Complete Series"/"Complete Collection" packs carry no season marker at all.

Anything still ambiguous — typically anime batches with no season token anywhere
— goes in config/overrides.yml, because no parser can win those from the name.
"""
from guessit import guessit

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov", ".webm"}
SERIES_KEYWORDS = ("complete series", "complete collection", "сезон", "season")
MAX_PLAUSIBLE_SEASON = 100


def _first(v):
    """guessit returns a list when a name spans several seasons/episodes."""
    return v[0] if isinstance(v, list) else v


def classify(name, overrides=None):
    """Return (kind, info) where kind is 'movie' or 'series'.

    `overrides` maps a lowercase substring to 'movie' or 'series' and wins
    outright, for names no parser can resolve.
    """
    lowered = name.lower()
    for fragment, forced in (overrides or {}).items():
        if fragment.lower() in lowered:
            return forced, _parse(name, forced == "series")

    g = guessit(name)
    season = _first(g.get("season"))
    is_episode = g.get("type") == "episode"

    if is_episode and season and season > MAX_PLAUSIBLE_SEASON:
        is_episode = False          # a "season" that big is really a year
    if not is_episode and any(k in lowered for k in SERIES_KEYWORDS):
        is_episode = True           # complete-series pack with no Sxx token

    return ("series" if is_episode else "movie"), _parse(name, is_episode)


def _parse(name, as_series):
    # Force the type: told to expect a movie, guessit stops consuming a leading
    # number as an episode, so "10 Things I Hate About You" keeps its "10".
    g = guessit(name, {"type": "episode" if as_series else "movie"})
    return {
        "title": g.get("title") or name,
        "year": _first(g.get("year")),
        "season": _first(g.get("season")) if as_series else None,
        "episode": _first(g.get("episode")) if as_series else None,
    }
