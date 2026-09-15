"""Score a release so the best copy of a film wins.

Two jobs. First, never surface a camera recording when a real source exists — a
CAM is worthless no matter how good its resolution claims to be, and they often
carry burned-in betting ads.

Second, prefer what the PS5 can actually play. Plex's PlayStation profile only
accepts HEVC inside MP4, and torrents are MKV, so an x265 file forces a full
software re-encode (there is no Plex Pass here, so no hardware help), while an
x264 file is just a container swap. A 1080p x264 release therefore beats a 2160p
x265 one *in practice*, despite looking worse on paper.
"""
import re

# A camera recording is never acceptable. Scored as None = do not use at all.
BANNED = ("cam", "camrip", "hdcam", "ts", "telesync", "hdts", "tc", "telecine",
          "scr", "screener", "dvdscr", "r5", "workprint", "hdtc", "predvd")

RESOLUTION = {"1080p": 100, "720p": 40, "2160p": 30, "480p": 5}   # 2160p costs a transcode
CODEC = {"x264": 60, "x265": 0}
SOURCE_BONUS = {"bluray": 25, "remux": 20, "web-dl": 20, "webdl": 20, "webrip": 10, "hdtv": 0}

# Always prefer the original audio. What ruins a release is a DUB into a
# language the title was not made in — not subtitles, which sit on top of the
# original audio and can be switched off.
#
# So "VOSTFR" (French subs, Japanese audio) is fine, while "Castellano"
# (Spanish dub) is not, and for a Turkish series a Turkish track IS the original.
DUB_MARKERS = {
    "dublado": "pt", "dublado.official": "pt", "castellano": "es", "latino": "es",
    "dual-lat": "es", "espanol": "es", "truefrench": "fr", "vff": "fr", "vfq": "fr",
    "german": "de", "deutsch": "de", "hindi": "hi", "hin": "hi", "tamil": "ta",
    "tam": "ta", "telugu": "te", "tel": "te", "kannada": "kn", "kan": "kn",
    "malayalam": "ml", "mal": "ml", "rus": "ru", "russian": "ru", "ita": "it",
    "italian": "it", "dublaj": "tr",
}
# Subtitles leave the original audio alone, but burned-in ones cannot be turned
# off, so they are worth avoiding. Chinese scene tags in particular almost always
# mean hardsubs.
HARDSUB_MARKERS = ("chs", "cht", "中字", "subtitulado", "hardsub", "hc")
SOFTSUB_MARKERS = ("vostfr", "esubs", "msubs", "multisub", "subbed", "eng.subs",
                   "engsubs", "sub.ita")

# How each language shows up in a release name, so we can tell "also has the
# original audio" from "dubbed over it".
LANGUAGE_TOKENS = {
    "en": ("eng", "english"), "ja": ("jpn", "japanese", "jap"),
    "tr": ("tur", "turkish", "turkce"), "fr": ("fre", "french", "vff", "truefrench"),
    "de": ("ger", "german", "deutsch"), "es": ("spa", "spanish", "castellano", "latino"),
    "it": ("ita", "italian"), "ru": ("rus", "russian"), "zh": ("chi", "chinese", "chs", "cht"),
    "ko": ("kor", "korean"), "hi": ("hin", "hindi"), "pt": ("por", "portuguese", "dublado"),
}
# Several audio tracks, so the original is in there somewhere.
MULTI_MARKERS = ("multi", "dual", "dual.audio", "dualaudio")

# Which language a title was actually made in, inferred from its country.
COUNTRY_LANGUAGE = {
    "united states": "en", "united kingdom": "en", "canada": "en",
    "australia": "en", "ireland": "en", "new zealand": "en",
    "japan": "ja", "turkey": "tr", "türkiye": "tr", "france": "fr",
    "germany": "de", "spain": "es", "mexico": "es", "argentina": "es",
    "italy": "it", "russia": "ru", "china": "zh", "hong kong": "zh",
    "taiwan": "zh", "south korea": "ko", "korea": "ko", "india": "hi",
    "brazil": "pt", "portugal": "pt",
}


def origin_language(country):
    """Best guess at a title's original language from its country of origin."""
    if not country:
        return None
    first = str(country).split(",")[0].strip().lower()
    return COUNTRY_LANGUAGE.get(first)


def tokens(name):
    """Split a release name on every separator groups actually use.

    "The.Pitt.S01E01.CHS-ENG.mkv" -> {the, pitt, s01e01, chs, eng, mkv}. Splitting
    only on dots and spaces left "chs-eng" glued together, so short language
    codes were never found as whole words.
    """
    return {tok for tok in re.split(r"[^0-9a-z\u4e00-\u9fff]+", name.lower()) if tok}


def is_banned(name):
    return bool(tokens(name) & set(BANNED))


# What the link between this Mac and Real-Debrid can sustain. Measured at ~64 Mbps
# raw and ~46 Mbps through the mount over Wi-Fi; a transcode needs input at its
# own speed, and the PS5's buffer forgives nothing, so stay well under it.
LINK_MBPS = float(__import__("os").environ.get("LINK_MBPS", "45"))


def est_mbps(size_bytes, runtime_min):
    """Average bitrate a release will demand, from its size and the title's runtime."""
    if not size_bytes or not runtime_min:
        return None
    return size_bytes * 8 / (float(runtime_min) * 60) / 1e6


def score(name, size_bytes=0, origin=None, context="", runtime_min=None):
    """Higher is better. None means the release must never be used.

    `origin` is the title's original language (see origin_language); when known,
    a dub into any other language is heavily penalised.

    `context` is any extra text describing the release — a scraper's own summary,
    or the containing torrent's name. Language markers are looked for there too,
    because a Tamil-dubbed pack can hold a file named plainly
    "The.Pitt.S01E07.1080p.mkv"; only the torrent name gives it away. Quality is
    still judged on `name` alone, so a scraper's blurb cannot inflate it.
    """
    if is_banned(name) or is_banned(context):
        return None

    low = name.lower()
    points = 0

    for label, value in RESOLUTION.items():
        if label in low:
            points += value
            break
    else:
        points += 20                      # unlabelled: assume something middling

    points += CODEC["x264"] if re.search(r"(?i)x264|h\.?264|avc", name) else CODEC["x265"]

    for label, value in SOURCE_BONUS.items():
        if label in low.replace(" ", "-"):
            points += value
            break

    # An MP4 needs no container swap at all — pure Direct Play on the PS5.
    if low.endswith(".mp4"):
        points += 15
    # DTS/TrueHD are outside the PlayStation profile and force an audio transcode.
    if re.search(r"(?i)\b(dts|truehd)\b", name):
        points -= 10
    # Tone-mapping HDR to SDR in software is the single most expensive thing here.
    if re.search(r"(?i)\b(hdr|dovi|dolby.?vision|hdr10)\b", name):
        points -= 15

    full = f"{name} {context}"
    toks = tokens(full)
    flat = full.lower().replace(" ", ".")

    # Short codes must match a whole token: "castellano" contains "tel", which
    # once made a Spanish film look Telugu. Longer markers can match anywhere,
    # since release names glue words together with dots.
    dubs = {lang for marker, lang in DUB_MARKERS.items()
            if lang != origin
            and (marker in toks if len(marker) <= 3 else (marker in toks or marker in flat))}
    if dubs:
        # "ENG.ITA" lists two audio tracks; it is not an Italian dub of an
        # English show. If the original language is named, the original is there.
        origin_present = origin and any(tok in toks or tok in flat
                                        for tok in LANGUAGE_TOKENS.get(origin, ()))
        multi = origin_present or any(m in toks or m in flat for m in MULTI_MARKERS)
        # Each extra dub crammed in is another sign of an aggregator re-encode
        # rather than a clean release: "[Tam + Tel + Kan + Hin + Eng]".
        points -= (30 if multi else 90) + 10 * (len(dubs) - 1)

    # Releases re-uploaded by aggregator sites carry the site in the name. They
    # are usually re-encodes, and the prefix also confuses Plex's matcher
    # ("www.Torrenting.com - Whiplash 2014" became a film called "Www Torrenting").
    if "www." in flat or ".com.-." in flat:
        points -= 15

    if any((m in toks if len(m) <= 3 else (m in toks or m in flat))
           for m in HARDSUB_MARKERS):
        points -= 60          # burned into the picture, cannot be switched off
    elif any(m in toks or m in flat for m in SOFTSUB_MARKERS):
        points -= 5

    # Bigger is not better on a link this size. A 30 GB remux is ~32 Mbps and
    # buffered to death; a 10 GB encode of the same film is ~11 Mbps and played.
    mbps = est_mbps(size_bytes, runtime_min)
    if mbps is None:
        points += min(size_bytes / (1024 ** 3), 20) * 0.5     # no runtime: old nudge
    elif mbps < 1.5:
        points -= 100         # a "1080p" feature at 0.1 GB is a sample or the wrong title
    elif mbps > LINK_MBPS * 0.6:
        points -= 80          # will buffer: no headroom for the PS5 or a transcode
    elif mbps > LINK_MBPS * 0.35:
        points -= 20          # marginal
    else:
        points += 10          # comfortable
    return points
