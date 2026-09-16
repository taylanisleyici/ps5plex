"""A Stremio-style source picker for your Plex Watchlist.

The automatic version fetched 17 torrents for one show without ever showing them,
which is not something that should happen to your Real-Debrid account. So the
default is now manual: this serves a page listing your watchlist, and for each
title the available releases ranked best-first, with the reasons visible. You
click the one you want and only that one is added.

Automatic fetching still exists (AUTO_FETCH=1) but is off unless asked for, and
capped by MAX_PER_TITLE so a long-running series cannot pull a back catalogue.
"""
import html
import json
import os
import pathlib
import re
import struct
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
from guessit import guessit

import poller
from rank import audio_languages, est_mbps, is_banned, score, tokens

PORT = int(os.environ.get("PICKER_PORT", "8081"))
SUBS_DIR = pathlib.Path(os.environ.get("SUBS_DIR", "/state/subs"))
# Stremio's OpenSubtitles addon — no API key, and it is the same source your
# Stremio already uses.
SUBS_API = "https://opensubtitles-v3.strem.io"
# Turkish first, English as the fallback.
PREFERRED_LANGS = ("tur", "eng")

CSS = """
*{box-sizing:border-box}
body{font:16px/1.45 -apple-system,system-ui,sans-serif;margin:0;background:#141414;
color:#e8e8e8;-webkit-text-size-adjust:100%}
header{padding:14px 16px;background:#1e1e1e;border-bottom:1px solid #333;
position:sticky;top:0;z-index:5}
a{color:#e5a00d;text-decoration:none}
.wrap{padding:14px 16px;max-width:900px;margin:0 auto}
h3{margin:18px 0 8px;font-size:15px;color:#aaa;text-transform:uppercase;
letter-spacing:.05em}
.item{padding:12px;margin:8px 0;background:#1c1c1c;border-radius:8px;
display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.grow{flex:1 1 220px;min-width:0}
.name{overflow-wrap:anywhere;word-break:break-word}
.meta{color:#999;font-size:13px;margin-top:3px;overflow-wrap:anywhere}
.best{border-left:3px solid #e5a00d}
.bad{opacity:.4}
.badge{display:inline-block;padding:2px 7px;border-radius:99px;font-size:12px;
font-weight:600;white-space:nowrap}
.cached{background:#1d4620;color:#7fe08a}
.uncached{background:#3a3a3a;color:#aaa}
.junk{background:#4a1f1f;color:#ff9a9a}
form{margin:0;flex:0 0 auto}
button{background:#e5a00d;border:0;color:#111;padding:10px 16px;border-radius:6px;
cursor:pointer;font-weight:600;font-size:15px;min-height:42px}
button.sec{background:#333;color:#ddd}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin:6px 0}
.tabs a{padding:6px 10px;background:#242424;border-radius:6px;font-size:14px}
.tabs a.on{background:#e5a00d;color:#111;font-weight:600}
.filters{gap:8px;align-items:center;padding:10px;background:#1c1c1c;border-radius:8px}
.filters input[type=text],.filters select{background:#2a2a2a;color:#e8e8e8;border:1px solid #444;
border-radius:6px;padding:8px;font-size:15px;min-height:40px}
.filters input[type=text]{flex:1 1 160px}
.filters label{font-size:14px;color:#ccc;display:flex;gap:6px;align-items:center;
padding:6px 8px;background:#242424;border-radius:6px;white-space:nowrap}
.filters input[type=checkbox]{width:18px;height:18px;margin:0}
.item[hidden]{display:none}
@media(max-width:480px){
  .item{padding:10px}
  .grow{flex:1 1 100%}
  form{width:100%}
  button{width:100%}
}
"""

def page(title, body):
    return (f"<html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body>"
            f"<header><a href='/'>ps5plex</a> &middot; {html.escape(title)}</header>"
            f"<div class='wrap'>{body}</div></body></html>")


def gb(n):
    return f"{n / 1024 ** 3:.1f} GB" if n else "?"


def tags(release):
    """(resolution, codec, hdr) read off the release name, for the filter bar."""
    low = release.lower()
    res = next((r for r in ("2160p", "1080p", "720p", "480p") if r in low),
               "2160p" if re.search(r"\b(4k|uhd)\b", low.replace(".", " ")) else "other")
    codec = ("x265" if re.search(r"x265|hevc|h\.?265", low)
             else "x264" if re.search(r"x264|avc|h\.?264", low) else "other")
    hdr = bool(re.search(r"\b(hdr|hdr10|dovi|dolby.?vision)\b", low.replace(".", " ")))
    return res, codec, hdr


# Filters run in the browser: the release list is already on the page, and
# asking the scraper again for every click would cost 5-90 s each time.
FILTER_BAR = """
<div class='tabs filters'>
  <input type='text' id='q' placeholder='search releases' oninput='apply()'>
  <select id='res' onchange='apply()'><option value=''>any resolution</option>
    <option>2160p</option><option>1080p</option><option>720p</option></select>
  <select id='codec' onchange='apply()'><option value=''>any codec</option>
    <option>x265</option><option>x264</option></select>
  <label><input type='checkbox' id='cached' onchange='apply()'> plays now</label>
  <label><input type='checkbox' id='hdr' onchange='apply()'> HDR</label>
  <label><input type='checkbox' id='junk' onchange='apply()'> show cam / ads</label>
  <label><input type='checkbox' id='dub' onchange='apply()'> show dubs</label>
  %s
</div>
<p class='meta'>showing <b id='n'>0</b> of %d releases &middot; <b>%d</b> already cached at Real-Debrid</p>
"""
# Goes after the rows, so apply() finds them when it runs on load.
FILTER_JS = """
<script>
function apply(){
  const v=id=>document.getElementById(id).value,
        on=id=>{const e=document.getElementById(id); return !!(e&&e.checked)};
  const q=v('q').toLowerCase(), res=v('res'), codec=v('codec');
  let n=0;
  document.querySelectorAll('.item[data-res]').forEach(el=>{
    const d=el.dataset;
    const show=(!q||el.textContent.toLowerCase().includes(q))
      &&(!res||d.res===res)&&(!codec||d.codec===codec)
      &&(!on('cached')||d.cached==='1')&&(!on('hdr')||d.hdr==='1')
      &&(on('junk')||d.junk!=='1')&&(on('dub')||d.dub!=='1')&&(!on('pack')||d.pack==='1');
    el.hidden=!show; if(show)n++;
  });
  document.getElementById('n').textContent=n;
}
apply();
</script>
"""


def _streams(kind, imdb, season=None, episode=None):
    if kind == "show":
        url = f"{poller.SCRAPER}/stream/series/{imdb}:{season or 1}:{episode or 1}.json"
    else:
        url = f"{poller.SCRAPER}/stream/movie/{imdb}.json"
    r = requests.get(url, timeout=90, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.json().get("streams", [])


def candidates(kind, imdb, season=None, episode=None, other_episode=None):
    """Every usable release for a title, best first, with why.

    The scraper answers per episode and names only the file, never the torrent,
    so a season pack looks exactly like a single episode. The one reliable tell
    is that a pack's info hash comes back for every episode of the season: ask
    for `other_episode` too and mark hashes that appear in both as packs.
    """
    info = poller.title_info(kind, imdb)
    origin, runtime = info["origin"], info["runtime_min"]
    streams = _streams(kind, imdb, season, episode)
    packs = set()
    if kind == "show" and other_episode:
        try:
            packs = {poller._stream_fields(s)[1] for s in _streams(kind, imdb, season, other_episode)}
        except Exception as e:
            print(f"[picker] could not check for season packs: {e}", flush=True)

    # A sponsored rip gets re-uploaded under a plain name: the same 4.6 GB file
    # appeared eight times as "House.of.the.Dragon.S03E01.mkv" and once as
    # "...Dragon.Money.Studio.mkv". Byte-identical to a banned rip means banned.
    fields = [poller._stream_fields(s) for s in streams]
    tainted = {size for release, _, size, _, context in fields
               if size and (is_banned(release) or is_banned(context))}

    out, seen, seen_file = [], set(), {}
    for release, info_hash, size, cached, context in fields:
        if not (release and info_hash) or info_hash in seen:
            continue
        seen.add(info_hash)
        # The same file is often in the list under a dozen torrents. One row
        # is enough; keep a cached copy over an uncached one.
        key = (release.lower(), size)
        if key in seen_file:
            if cached and not seen_file[key]["cached"]:
                seen_file[key].update(hash=info_hash, cached=True,
                                      rank=None if seen_file[key]["score"] is None
                                      else seen_file[key]["score"] + 400)
            continue
        pts = score(release, size, origin, context, runtime)
        if pts is not None and size in tainted:
            pts, context = None, context + " (same file as a sponsored rip)"
        mbps = est_mbps(size, runtime)
        dubs, multi = audio_languages(release, context, origin)
        # A cached release plays now; an uncached one has to download first, and
        # Real-Debrid can spend minutes just resolving the magnet. That is worth
        # more than a few points of picture quality, so it sorts first.
        rank_pts = None if pts is None else pts + (400 if cached else 0)
        out.append({"release": release, "hash": info_hash, "size": size,
                    "cached": cached, "score": pts, "rank": rank_pts, "mbps": mbps,
                    "pack": info_hash in packs,
                    # the scraper's own flag line, so the audio is visible at a glance
                    "flags": " ".join(re.findall(r"[\U0001F1E6-\U0001F1FF]{2}|🌎", context)),
                    "dub": bool(dubs) and not multi, "dubs": sorted(dubs),
                    "junk": pts is None or is_banned(release) or is_banned(context)})
        seen_file[key] = out[-1]
    out.sort(key=lambda c: (-1e9 if c["rank"] is None else -c["rank"]))
    return out


RDSERVE = os.environ.get("RDSERVE_URL", "http://rdserve:8080")
# How many subtitles to keep per language. Sync depends on which rip a subtitle
# was timed to, and its name only hints at that, so keep several and let the
# player's menu be the second opinion — the way Stremio lists them.
SUBS_PER_LANG = 5


def subtitles(kind, imdb, season=None, episode=None, file_hash=None):
    """Everything OpenSubtitles has for a title, via Stremio's addon.

    `file_hash` is (hash, size) of the actual video file. Given that, the addon
    also asks OpenSubtitles for uploads made against that exact file, which it
    flags with m="h": those are in sync by definition.
    """
    what = (f"series/{imdb}:{season or 1}:{episode or 1}" if kind == "show"
            else f"movie/{imdb}")
    extra = f"/videoHash={file_hash[0]}&videoSize={file_hash[1]}" if file_hash else ""
    r = requests.get(f"{SUBS_API}/subtitles/{what}{extra}.json", timeout=45,
                     headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return [s for s in r.json().get("subtitles", []) if s.get("url")]


# Blu-ray, web and TV rips are different cuts with different timings, so a
# subtitle timed to one family is the wrong one for another.
SOURCE_FAMILY = {"remux": "bluray", "bluray": "bluray", "blu": "bluray", "bdrip": "bluray",
                 "brrip": "bluray", "uhd": "bluray", "web": "web", "webdl": "web",
                 "webrip": "web", "amzn": "web", "nf": "web", "dsnp": "web", "atvp": "web",
                 "hmax": "web", "hdtv": "hdtv", "dcprip": "dcp"}


def family(name):
    toks = tokens(name)
    return next((f for t, f in SOURCE_FAMILY.items() if t in toks), None)


def sub_score(sub, release):
    """How likely a subtitle is to be in sync with the picked release.

    OpenSubtitles records which rip each file was timed to (movieReleaseName).
    A hash match is certain. Otherwise, the closer that name is to ours — same
    source family, same group, same tags — the better the odds. A subtitle
    timed to a cam or telesync is timed to a different film, effectively.
    """
    if sub.get("m") == "h":
        return 1000
    name = f"{sub.get('movieReleaseName', '')} {sub.get('subtitleFileName', '')}"
    pts = 0
    if is_banned(name):
        pts -= 500
    low = name.lower()
    if "forced" in low:
        pts -= 300               # only the foreign-language lines
    if "sdh" in low or "hearing" in low:
        pts -= 5
    mine, theirs = family(release), family(name)
    if mine and theirs:
        pts += 40 if mine == theirs else -40
    pts += 8 * len({t for t in tokens(release) & tokens(name) if len(t) > 1})
    # ponytail: film is 23.976 fps almost without exception; 25 (PAL) and 30/60
    # timings drift within minutes, 24 is usually a DCP or telesync source.
    # Replace with the real frame rate from Plex if a title ever proves otherwise.
    fps = sub.get("fpsMilli") or 0
    if fps == 23976:
        pts += 15
    elif fps in (25000, 29970, 30000, 50000, 59940, 60000):
        pts -= 15
    return pts


def pick_subtitles(subs, release, lang, n=SUBS_PER_LANG):
    """The n best-fitting subtitles in one language, best first.

    One per source rip where possible: five uploads timed to the same WEBRip
    are five copies of the same drift, not five second opinions. Duplicates
    only fill in when there are not enough distinct rips.
    """
    ranked = sorted((s for s in subs if s.get("lang") == lang),
                    key=lambda s: -sub_score(s, release))
    out, seen_rip, seen_id = [], set(), set()
    for distinct_only in (True, False):
        for s in ranked:
            rip = (s.get("movieReleaseName") or s.get("subtitleFileName") or "").lower()
            if s.get("id") in seen_id or (distinct_only and rip in seen_rip):
                continue
            seen_id.add(s.get("id"))
            seen_rip.add(rip)
            out.append(s)
            if len(out) == n:
                return out
    return out


def sub_path(imdb, lang, season=None, episode=None, n=None):
    """Where a downloaded subtitle lives until the librarian places it.

    "<imdb>[.sXXeYY][.<n>].<lang>.srt". The librarian carries the middle part
    into the sidecar's name, so several per language can sit beside one video.
    """
    tag = f".s{int(season):02d}e{int(episode):02d}" if season and episode else ""
    mid = f".{n}" if n is not None else ""
    return SUBS_DIR / f"{imdb}{tag}{mid}.{lang}.srt"


def folder_files(folder):
    """(base URL, [(href, size)]) for every file rdserve lists in a torrent.

    Sizes come from a one-byte ranged GET each, so nothing is downloaded.
    An empty list means rdserve does not have the torrent yet.
    """
    requests.get(f"{RDSERVE}/refresh", timeout=60)     # it lists only what it has seen
    base = f"{RDSERVE}/{urllib.parse.quote(folder)}/"
    r = requests.get(base, timeout=15)
    files = []
    if r.status_code == 200:
        for href in re.findall(r'href="([^"]+)"', r.text):
            h = requests.get(base + href, headers={"Range": "bytes=0-0"}, timeout=30, stream=True)
            total = int((h.headers.get("Content-Range") or "/0").rsplit("/", 1)[-1] or 0) \
                or (int(h.headers.get("Content-Length", 0)) if h.status_code == 200 else 0)
            h.close()
            files.append((href, total))
    return base, files


def episodes_in(filename):
    """Episode numbers a file name carries: {7} for S01E07, {1, 2} for E01-E02."""
    ep = guessit(filename, {"type": "episode"}).get("episode")
    return {int(e) for e in (ep if isinstance(ep, list) else [ep]) if e}


def video_hash(folder, episode=None):
    """OpenSubtitles hash of a picked torrent's video, via rdserve.

    Size plus the first and last 64 KB summed as little-endian uint64s. Two
    ranged GETs against Real-Debrid's CDN, ~128 KB in total. For a show, the
    file whose name carries `episode`; for a movie, the biggest file. None
    when the torrent is not downloaded yet or anything else goes wrong; the
    name-based ranking still runs without it.
    """
    try:
        base, files = folder_files(folder)
        if episode is not None:
            files = [f for f in files if episode in episodes_in(urllib.parse.unquote(f[0]))]
        files = [f for f in files if f[1] >= 131072]
        if not files:
            return None
        href, size = max(files, key=lambda f: f[1])

        def chunk(rng):
            data = requests.get(base + href, headers={"Range": rng}, timeout=60).content[:65536]
            return sum(struct.unpack("<%dQ" % (len(data) // 8), data[:len(data) // 8 * 8]))

        digest = (size + chunk("bytes=0-65535")
                  + chunk(f"bytes={size - 65536}-{size - 1}")) & 0xFFFFFFFFFFFFFFFF
        return "%016x" % digest, size
    except Exception as e:
        print(f"[picker] no file hash for {folder[:50]}: {e}", flush=True)
        return None


def clean_srt(raw):
    """Return the subtitle as UTF-8 bytes, whatever it arrived as.

    OpenSubtitles files come three ways: proper UTF-8, legacy Windows-1254, and
    UTF-8 that someone upstream read as Windows-1252 and re-saved, which turns
    "KURTULUŞ" into "KURTULUÅž". Plex renders the last kind exactly as broken as
    it looks, so undo it here.
    """
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1254", "replace")
    if any(m in text for m in ("Ã", "Å", "Ä")):        # Turkish never uses these
        try:
            text = _unmangle(text)
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass                                        # not that kind of broken
    return text.encode("utf-8")


def _unmangle(text):
    """Reverse a UTF-8 → Windows-1252 misread, one character at a time.

    Windows maps the five bytes cp1252 leaves undefined (0x81, 0x8D, 0x8F, 0x90,
    0x9D) to C1 control characters, which Python's codec refuses. A single one
    of those in a 200 KB file, like the "Á" in a name, left the whole file
    mangled, so fall back to the raw byte value for anything below 256.
    """
    out = bytearray()
    for c in text:
        try:
            out += c.encode("cp1252")
        except UnicodeEncodeError:
            if ord(c) >= 256:
                raise
            out.append(ord(c))
    return out.decode("utf-8")


def save_subtitle(url, imdb, lang, season=None, episode=None, n=None):
    r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    SUBS_DIR.mkdir(parents=True, exist_ok=True)
    path = sub_path(imdb, lang, season, episode, n)
    path.write_bytes(clean_srt(r.content))
    return path


def speech_times(raw):
    """Start second of every cue that is speech, not SDH noise or music."""
    out = []
    for block in re.split(r"\n\s*\n", raw.decode("utf-8", "replace")):
        m = re.search(r"(\d+):(\d+):(\d+)[,.]\d+\s*-->", block)
        if not m:
            continue
        text = re.sub(r"\{[^}]*\}|<[^>]+>", "", " ".join(block.splitlines()[2:])).strip()
        if not text or re.fullmatch(r"[\[\(].*[\]\)]|[♪♫\s]+", text):
            continue
        out.append(int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)))
    return out


def aligned(a, b):
    """Do two files share a timing? Half of b's cues land within a second of an a cue."""
    if not a or not b:
        return False
    a = sorted(a)
    import bisect
    hits = 0
    for t in b:
        i = bisect.bisect_left(a, t)
        if (i < len(a) and abs(a[i] - t) <= 1) or (i and abs(a[i - 1] - t) <= 1):
            hits += 1
    return hits >= len(b) / 2


def order_saved(files):
    """Reorder downloaded subtitles by what they actually contain.

    `files` is [(rank, raw bytes)] best-fit first. Two fixes on top of the fit
    score, both from a real episode: the official-subtitle translation skips
    lines that are burned into the picture (High Valyrian), so a file with the
    same timing that covers a stretch the leader leaves empty is more complete
    and goes first; a file that is a fraction of the others' length is forced/
    partial and goes last. A file whose timing does not match the leader is a
    different cut and is never promoted — being early there means out of sync.
    """
    times = [speech_times(raw) for _, raw in files]
    order = list(range(len(files)))
    lead = 0
    for i in order[1:]:
        if aligned(times[lead], times[i]) and times[lead] \
                and sum(t < min(times[lead]) for t in times[i]) >= 2:
            lead = i
    order.remove(lead)
    order.insert(0, lead)
    most = max((len(t) for t in times), default=0)
    partial = [i for i in order if len(times[i]) < most * 0.4]
    return [i for i in order if i not in partial] + partial


def auto_subtitles(kind, imdb, release, folder, season=None, episodes=()):
    """Save the best-fitting Turkish and English SRTs for a freshly picked title.

    The PS5 plays a sidecar SRT for free, but selecting an embedded image
    subtitle forces a full re-encode of the video, so both languages are always
    put on disk. Several per language, numbered best-first: Plex shows them as
    identical "Türkçe (SRT External)" entries in that order, so if the first is
    out of sync the next one down is the second opinion. For a show this runs
    once per episode the season torrent holds. Runs in the background after the
    pick; the librarian places the files as they land.
    """
    targets = [(season, e) for e in sorted(episodes)] if kind == "show" else [(None, None)]
    label = f"{imdb} S{int(season):02d}" if kind == "show" else imdb
    try:
        stale = f"{imdb}.s{int(season):02d}e*.srt" if kind == "show" else f"{imdb}.*.srt"
        for old in SUBS_DIR.glob(stale):
            old.unlink()                         # timed to the previous pick
    except OSError:
        pass
    saved = 0
    for s_no, ep in targets:
        try:
            subs = subtitles(kind, imdb, s_no, ep, file_hash=video_hash(folder, ep))
        except Exception as e:
            print(f"[picker] subtitles lookup failed for {label} E{ep}: {e}", flush=True)
            continue
        for lang in PREFERRED_LANGS:
            got = []
            for s in pick_subtitles(subs, release, lang):
                try:
                    r = requests.get(s["url"], timeout=60, headers={"User-Agent": "Mozilla/5.0"})
                    r.raise_for_status()
                    got.append(clean_srt(r.content))
                except Exception as e:
                    print(f"[picker] could not fetch a {lang} subtitle for {label}: {e}", flush=True)
            SUBS_DIR.mkdir(parents=True, exist_ok=True)
            for n, i in enumerate(order_saved(list(enumerate(got))), 1):
                sub_path(imdb, lang, s_no, ep, n).write_bytes(got[i])
                saved += 1
    print(f"[picker] {saved} subtitle file(s) saved for {label}", flush=True)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, body, code=200):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, to):
        self.send_response(303)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---- pages ----------------------------------------------------------
    def home(self):
        managed = poller.load_managed()
        rows = []
        for kind, title, key in poller.watchlist():
            imdb = poller.imdb_id(key) or ""
            have = [v for v in managed.values() if v.get("imdb") == imdb]
            state = (f"<span class='meta'>{len(have)} fetched</span>"
                     if have else "<span class='meta'>nothing fetched</span>")
            link = f"/pick?kind={kind}&imdb={imdb}&title={urllib.parse.quote(title)}"
            rows.append(f"<div class='item'><div class='grow'>"
                        f"<div class='name'><b>{html.escape(title)}</b></div>"
                        f"<div class='meta'>{state}</div></div>"
                        f"<form><a href='{link}'><button type='button'>choose source"
                        f"</button></a></form></div>")
        if not rows:
            rows = ["<p class='meta'>Nothing on your Plex Watchlist.</p>"]

        lib = "".join(
            f"<div class='item'><div class='grow'><div class='name'>"
            + html.escape(v["title"])
            + (f" <span class='badge uncached'>S{v['season']:02d}</span>"
               if v.get("season") else "")
            + f"</div><div class='meta'>{html.escape(f[:80])}</div></div>"
            f"<form method='post' action='/remove'>"
            f"<input type='hidden' name='folder' value='{html.escape(f)}'>"
            f"<button class='sec'>remove</button></form></div>"
            for f, v in managed.items())

        return page("watchlist", "<h3>On your watchlist</h3>" + "".join(rows)
                    + "<h3>In your library</h3>" + (lib or "<p class='meta'>Empty.</p>"))

    def pick(self, q):
        kind = q.get("kind", ["movie"])[0]
        imdb = q.get("imdb", [""])[0]
        title = q.get("title", ["?"])[0]
        season = int(q.get("season", ["1"])[0])
        episode = int(q.get("episode", ["1"])[0])
        if not imdb:
            return self._send(page("error", "<p>No IMDb id.</p>"), 400)

        subs_link = (f"/subs?kind={kind}&imdb={imdb}&title={urllib.parse.quote(title)}"
                     f"&season={season}&episode={episode}")
        head = (f"<h3>{html.escape(title)}</h3>"
                f"<p><a href='{subs_link}'><button class='sec'>subtitles</button></a></p>")
        if kind == "show":
            schedule = poller.aired_episodes(imdb)
            seasons = "".join(
                f"<a class='{'on' if s == season else ''}' "
                f"href='/pick?kind=show&imdb={imdb}&title={urllib.parse.quote(title)}"
                f"&season={s}&episode=1'>S{s:02d}</a>" for s in schedule)
            eps = "".join(
                f"<a class='{'on' if e == episode else ''}' "
                f"href='/pick?kind=show&imdb={imdb}&title={urllib.parse.quote(title)}"
                f"&season={season}&episode={e}'>E{e:02d}</a>"
                for e in schedule.get(season, []))
            head += f"<div class='tabs'>{seasons}</div><div class='tabs'>{eps}</div>"

        other = None
        if kind == "show":
            aired = schedule.get(season, [])
            other = next((e for e in reversed(aired) if e != episode), None)
        cands = candidates(kind, imdb, season, episode, other)
        ready = sum(1 for c in cands if c["cached"] and not c["junk"])
        packs = sum(1 for c in cands if c["pack"])
        rows = []
        for c in cands:
            cls = "item bad" if c["junk"] else ("item best" if not rows else "item")
            badge = ("<span class='badge junk'>cam / ads</span>" if c["junk"]
                     else "<span class='badge cached'>&#9889; plays now</span>" if c["cached"]
                     else "<span class='badge uncached'>must download</span>")
            if c["pack"]:
                badge += " <span class='badge cached'>season pack</span>"
            if c["dub"]:
                badge += f" <span class='badge junk'>{'/'.join(c['dubs'])} dub only</span>"
            meta = [gb(c["size"]) + (" per episode" if c["pack"] else "")]
            if c["flags"]:
                meta.append(c["flags"])
            if c["mbps"]:
                meta.append(f"~{c['mbps']:.0f} Mbps")
            if c["score"] is not None:
                meta.append(f"score {c['score']:.0f}")
            res, codec, hdr = tags(c["release"])
            rows.append(
                f"<div class='{cls}' data-res='{res}' data-codec='{codec}' data-hdr='{int(hdr)}'"
                f" data-cached='{int(bool(c['cached']))}' data-junk='{int(c['junk'])}'"
                f" data-pack='{int(c['pack'])}' data-dub='{int(c['dub'])}'>"
                f"<div class='grow'><div class='name'>{html.escape(c['release'][:110])}</div>"
                f"<div class='meta'>{badge} &middot; {' &middot; '.join(meta)}</div></div>"
                f"<form method='post' action='/add'>"
                f"<input type='hidden' name='hash' value='{c['hash']}'>"
                f"<input type='hidden' name='imdb' value='{imdb}'>"
                f"<input type='hidden' name='kind' value='{kind}'>"
                f"<input type='hidden' name='title' value='{html.escape(title)}'>"
                f"<input type='hidden' name='season' value='{season if kind == 'show' else ''}'>"
                f"<button>add</button></form></div>")
        body = ("".join(rows) or "<p class='meta'>No releases found.</p>")
        # Off by default: the packs on offer are often the worst releases of a
        # season, and defaulting to them once hid every clean single episode.
        pack_box = ("" if kind != "show" else
                    f"<label><input type='checkbox' id='pack' onchange='apply()'>"
                    f" full season only ({packs})</label>")
        return self._send(page(title, head + FILTER_BAR % (pack_box, len(cands), ready) + body + FILTER_JS))

    def subs(self, q):
        kind = q.get("kind", ["movie"])[0]
        imdb = q.get("imdb", [""])[0]
        title = q.get("title", ["?"])[0]
        season = q.get("season", [""])[0]
        episode = q.get("episode", [""])[0]
        season = int(season) if season else None
        episode = int(episode) if episode else None

        # Score against the picked release, so the list reads best-fit first.
        managed = poller.load_managed()
        release = next((f for f, v in managed.items() if v.get("imdb") == imdb), "")
        have = {p.name for p in SUBS_DIR.glob(f"{imdb}*.srt")} if SUBS_DIR.exists() else set()
        subs = subtitles(kind, imdb, season, episode)
        rows = []
        langs = list(PREFERRED_LANGS) + sorted({s.get("lang", "?") for s in subs} - set(PREFERRED_LANGS))
        for lang in langs:
            picked = pick_subtitles(subs, release, lang, n=8 if lang in PREFERRED_LANGS else 3)
            if not picked:
                continue
            rows.append(f"<h3>{html.escape(lang)}</h3>")
            for s in picked:
                pts = sub_score(s, release)
                saved = sub_path(imdb, lang, season, episode, f"m{s.get('id', '')}").name in have
                fps = f"{s['fpsMilli'] / 1000:g} fps" if s.get("fpsMilli") else "fps ?"
                rows.append(
                    f"<div class='{'item' if pts > -100 else 'item bad'}'><div class='grow'>"
                    f"<div class='name'>{html.escape((s.get('movieReleaseName') or s.get('subtitleFileName') or '?')[:100])}"
                    + (" <span class='badge cached'>saved</span>" if saved else "")
                    + (" <span class='badge cached'>exact file match</span>" if s.get("m") == "h" else "")
                    + f"</div><div class='meta'>{fps} &middot; fit {pts}"
                    + (" &middot; timed to a cam rip" if is_banned(s.get("movieReleaseName") or "") else "")
                    + f"</div></div>"
                    f"<form method='post' action='/addsub'>"
                    f"<input type='hidden' name='url' value='{html.escape(s['url'])}'>"
                    f"<input type='hidden' name='id' value='{html.escape(str(s.get('id', '')))}'>"
                    f"<input type='hidden' name='imdb' value='{imdb}'>"
                    f"<input type='hidden' name='lang' value='{html.escape(lang)}'>"
                    f"<input type='hidden' name='season' value='{season or ''}'>"
                    f"<input type='hidden' name='episode' value='{episode or ''}'>"
                    f"<button>use</button></form></div>")
        note = ("<p class='meta'>Picking a movie already saves the five best-fitting Turkish and "
                "English files; in the PS5 player they appear in that order, so if the first "
                "is out of sync try the next one. Use this page to add a specific one.</p>")
        return self._send(page(f"subtitles — {title}", note + ("".join(rows) or
                          "<p class='meta'>None found.</p>")))

    # ---- actions --------------------------------------------------------
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        form = urllib.parse.parse_qs(self.rfile.read(length).decode())
        managed = poller.load_managed()
        try:
            return self._post(form, managed)
        except Exception as e:
            # Show what went wrong instead of dropping the connection.
            return self._send(page("could not add", (
                f"<p><b>{html.escape(type(e).__name__)}</b></p>"
                f"<pre>{html.escape(str(e))[:600]}</pre>"
                f"<p><a href='/'>back</a></p>")), 500)

    def _post(self, form, managed):
        if self.path == "/add":
            season = form.get("season", [""])[0]
            season = int(season) if season else None
            # A new pick replaces the old one for the same title (same season
            # for a show). The torrent stays in Real-Debrid; only the library
            # forgets it, and the librarian swaps the link on its next pass.
            for folder in [f for f, v in managed.items()
                           if v.get("imdb") == form["imdb"][0] and v.get("season") == season]:
                managed.pop(folder)
            before = set(managed)
            kind, imdb = form["kind"][0], form["imdb"][0]
            episodes = poller._add_and_record(
                (0, form["hash"][0], form["hash"][0]), form["title"][0], imdb,
                "", kind, managed, season)
            folder = next(iter(set(managed) - before), form["hash"][0])
            if kind == "show" and not episodes:
                # Not downloaded yet, so Real-Debrid has no file list. Assume the
                # pack holds the aired season; extra subtitles are harmless.
                episodes = poller.aired_episodes(imdb).get(season, [])
            threading.Thread(target=auto_subtitles, daemon=True,
                             args=(kind, imdb, folder, folder, season, episodes)).start()
            return self._redirect("/")

        if self.path == "/addsub":
            season = form.get("season", [""])[0]
            episode = form.get("episode", [""])[0]
            save_subtitle(form["url"][0], form["imdb"][0], form["lang"][0],
                          int(season) if season else None,
                          int(episode) if episode else None,
                          n=f"m{form.get('id', [''])[0]}")
            return self._redirect("/")

        if self.path == "/remove":
            folder = form["folder"][0]
            managed.pop(folder, None)
            poller.save_managed(managed)
            # The torrent itself is left in Real-Debrid; only the library forgets it.
            return self._redirect("/")
        self.send_error(404)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/":
                return self._send(self.home())
            if parsed.path == "/pick":
                return self.pick(urllib.parse.parse_qs(parsed.query))
            if parsed.path == "/subs":
                return self.subs(urllib.parse.parse_qs(parsed.query))
        except Exception as e:
            return self._send(page("error", f"<pre>{html.escape(str(e))}</pre>"), 500)
        self.send_error(404)


def housekeeping():
    """Finish torrents Real-Debrid was still converting, and keep the library in
    step with the watchlist."""
    while True:
        try:
            poller.reconcile()
            managed = poller.load_managed()
            items = poller.watchlist()
            poller.prune(managed, {poller.imdb_id(k) for _, _, k in items} - {None})
        except Exception as e:
            print(f"[picker] housekeeping: {type(e).__name__}: {e}", flush=True)
        time.sleep(30)


def main():
    print(f"[picker] open http://localhost:{PORT}/", flush=True)
    threading.Thread(target=housekeeping, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
