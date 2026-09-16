"""Score a release so the best copy of a film wins.

Two jobs. First, never surface a camera recording when a real source exists — a
CAM is worthless no matter how good its resolution claims to be, and they often
carry burned-in betting ads.

Second, prefer the best picture. Measured on this server, the PS5 app copies
both H.264 and 2160p HEVC HDR video straight through its DASH remux, so codec and
HDR cost nothing; only DTS/TrueHD audio gets converted, which is trivial. Higher
resolution and a cleaner source are what is left to rank on.
"""
import re

# A camera recording is never acceptable. Scored as None = do not use at all.
BANNED = ("cam", "camrip", "hdcam", "ts", "telesync", "hdts", "tc", "telecine",
          "scr", "screener", "dvdscr", "r5", "workprint", "hdtc", "predvd",
          # casino-sponsored rips: a floating betting banner burned into the picture
          "1xbet", "melbet", "mostbet", "vavada", "betwinner", "casino", "1win", "fonbet")
# Same, for sponsors whose name splits into innocent tokens ("Dragon Money" —
# on a show called House of the Dragon). Matched on the dotted lowercase name.
SPONSORED = ("dragon.money", "dragonmoney", "pin.up.", "leon.bet", "olimp.bet")

RESOLUTION = {"2160p": 100, "1080p": 70, "720p": 30, "480p": 5}
SOURCE_BONUS = {"remux": 30, "bluray": 25, "web-dl": 20, "webdl": 20, "webrip": 10, "hdtv": 0}

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
    "malayalam": "ml", "mal": "ml", "rus": "ru", "russian": "ru", "ru": "ru", "ita": "it",
    "italian": "it", "dublaj": "tr", "ukr": "uk",
    # Russian voice-over studios that tag releases with their name only. A
    # "House.of.the.Dragon.S03E01.1080p.NewComers.mkv" carries no language at all.
    "lostfilm": "ru", "newstudio": "ru", "newcomers": "ru", "ultradox": "ru",
    "hdrezka": "ru", "kerob": "ru", "jaskier": "ru", "baibako": "ru", "alexfilm": "ru",
    "ideafilm": "ru", "coldfilm": "ru", "omskbird": "ru", "kurajbambey": "ru",
}
# Comet lists the audio tracks it knows as flags on the last line of its
# description. A release flagged 🇷🇺 alone is a Russian dub whatever its file
# name says — that is how "House.of.the.Dragon.S03.1080p.Ru.Ultradox" got
# through on the name alone. 🌎 means multi/unknown.
FLAG_LANGUAGE = {
    "🇬🇧": "en", "🇺🇸": "en", "🇷🇺": "ru", "🇺🇦": "uk", "🇮🇳": "hi", "🇮🇹": "it",
    "🇪🇸": "es", "🇲🇽": "es", "🇫🇷": "fr", "🇩🇪": "de", "🇯🇵": "ja", "🇨🇳": "zh",
    "🇰🇷": "ko", "🇹🇷": "tr", "🇧🇷": "pt", "🇵🇹": "pt", "🇨🇿": "cs", "🇵🇱": "pl",
    "🇳🇱": "nl", "🇸🇪": "sv", "🇭🇺": "hu", "🇷🇴": "ro", "🇬🇷": "el", "🇸🇦": "ar",
    "🇦🇪": "ar", "🇹🇭": "th", "🇻🇳": "vi", "🇮🇩": "id",
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
    flat = (name or "").lower().replace(" ", ".").replace("_", ".")
    return bool(tokens(name) & set(BANNED)) or any(s in flat for s in SPONSORED)


def has_group(name):
    """Does the name end in a release-group tag: "...x265-ELiTE.mkv", "...[WD-13].mkv"?

    Scene and P2P releases sign their work. The rips that carry burned-in
    adverts are the ones named like a home recording, "Show.S03E01.mkv".
    """
    stem = re.sub(r"\.(mkv|mp4|avi|m4v|ts|mov|webm)$", "", name.strip(), flags=re.I)
    return bool(re.search(r"-[A-Za-z0-9]{2,}$|\[[^\]]{2,}\]\)?$", stem))


def flags(context):
    """Language codes for the flag emojis in a scraper description."""
    return {FLAG_LANGUAGE[f] for f in re.findall(r"[\U0001F1E6-\U0001F1FF]{2}", context or "")
            if f in FLAG_LANGUAGE}


def audio_languages(name, context, origin):
    """(languages dubbed into, original audio also present) for a release.

    Read from two places: dub markers in the name ("Castellano", "Ru",
    "Dublado") and the scraper's flag line. Short codes must match a whole
    token: "castellano" contains "tel", which once made a Spanish film look
    Telugu. Longer markers can match anywhere, since release names glue words
    together with dots.

    "ENG.ITA" or 🇬🇧/🇮🇹 lists two audio tracks; that is not an Italian dub of an
    English show. If the original language is named, the original is there.
    Flags are only trusted when the original language is known, otherwise a
    plain English release flagged 🇬🇧 would count as a dub.
    """
    full = f"{name} {context}"
    toks = tokens(full)
    flat = full.lower().replace(" ", ".")
    dubs = {lang for marker, lang in DUB_MARKERS.items()
            if lang != origin
            and (marker in toks if len(marker) <= 3 else (marker in toks or marker in flat))}
    flagged = flags(context) if origin else set()
    dubs |= flagged - {origin}
    origin_present = bool(origin) and (
        origin in flagged
        or any(tok in toks or tok in flat for tok in LANGUAGE_TOKENS.get(origin, ())))
    multi = origin_present or "🌎" in (context or "") \
        or any(m in toks or m in flat for m in MULTI_MARKERS)
    return dubs, multi


def est_mbps(size_bytes, runtime_min):
    """Average bitrate a release will demand, from its size and the title's runtime.

    Shown in the picker so you can judge it yourself; it does not affect the
    score beyond catching samples."""
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

    for label, value in SOURCE_BONUS.items():
        if label in low.replace(" ", "-"):
            points += value
            break

    # DTS/TrueHD get converted to AAC on the fly. Cheap, but it keeps the
    # transcoder awake, so an AC3/EAC3 track is slightly nicer.
    if re.search(r"(?i)\b(dts|truehd)\b", name):
        points -= 5
    # HDR10 passes through the copy and the TV shows it. Dolby Vision's extra
    # layer is dropped by the remux, so it is no better than plain HDR10.
    if re.search(r"(?i)\b(hdr|dovi|dolby.?vision|hdr10)\b", name):
        points += 10

    full = f"{name} {context}"
    toks = tokens(full)
    flat = full.lower().replace(" ", ".")

    dubs, multi = audio_languages(name, context, origin)
    if dubs:
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

    # An unsigned release is the tell for the junk that carries adverts.
    if not has_group(name):
        points -= 30

    # Bitrate is picture quality: on this link a 9.7 GB episode beats a 2.3 GB
    # one at the same resolution, and that is the order Stremio shows too.
    mbps = est_mbps(size_bytes, runtime_min)
    if mbps is not None:
        points += min(mbps, 25)
        if mbps < 1.5:
            points -= 100      # a "1080p" feature at 0.1 GB is a sample or the wrong title
    else:
        points += min(size_bytes / (1024 ** 3), 20) * 0.5    # no runtime: size as a proxy
    return points
