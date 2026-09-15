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

# Releases dubbed or hard-subbed into a language you do not read are useless no
# matter how clean the video is. Without this a Chinese-subbed rip of The Pitt
# outscored the English one purely on file size.
FOREIGN = {
    "chs": 90, "cht": 90, "中字": 90, "castellano": 90, "latino": 90, "lat": 40,
    "subtitulado": 90, "vff": 90, "vostfr": 90, "truefrench": 90, "french": 60,
    "nordic": 50, "hindi": 90, "tamil": 90, "telugu": 90, "korsub": 90,
    "dublado": 90, "ita": 40, "esp": 60, "rus": 60, "multi": 15,
}
# ...unless English is clearly present too.
ENGLISH = ("eng", "english", "englisn", "en")


def tokens(name):
    return set(w.strip("[]()._-").lower() for w in name.replace(".", " ").split())


def is_banned(name):
    return bool(tokens(name) & set(BANNED))


def score(name, size_bytes=0):
    """Higher is better. None means the release must never be used."""
    if is_banned(name):
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

    toks = tokens(name)
    has_english = bool(toks & set(ENGLISH))
    for marker, penalty in FOREIGN.items():
        if marker in toks or marker in low:
            # A dual-language release that includes English is still watchable.
            points -= penalty // 3 if has_english else penalty
            break

    # Mild nudge towards the larger of two otherwise-equal releases.
    points += min(size_bytes / (1024 ** 3), 20) * 0.5
    return points
