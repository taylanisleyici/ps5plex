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
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

import poller
from rank import LINK_MBPS, est_mbps, is_banned, score

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


def candidates(kind, imdb, season=None, episode=None):
    """Every usable release for a title, best first, with why."""
    info = poller.title_info(kind, imdb)
    origin, runtime = info["origin"], info["runtime_min"]
    if kind == "show":
        url = f"{poller.SCRAPER}/stream/series/{imdb}:{season or 1}:{episode or 1}.json"
    else:
        url = f"{poller.SCRAPER}/stream/movie/{imdb}.json"
    r = requests.get(url, timeout=90, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()

    out, seen = [], set()
    for s in r.json().get("streams", []):
        release, info_hash, size, cached, context = poller._stream_fields(s)
        if not (release and info_hash) or info_hash in seen:
            continue
        seen.add(info_hash)
        pts = score(release, size, origin, context, runtime)
        mbps = est_mbps(size, runtime)
        # A cached release plays now; an uncached one has to download first, and
        # Real-Debrid can spend minutes just resolving the magnet. That is worth
        # more than a few points of picture quality, so it sorts first.
        rank_pts = None if pts is None else pts + (400 if cached else 0)
        out.append({"release": release, "hash": info_hash, "size": size,
                    "cached": cached, "score": pts, "rank": rank_pts, "mbps": mbps,
                    "junk": pts is None or is_banned(release) or is_banned(context)})
    out.sort(key=lambda c: (-1e9 if c["rank"] is None else -c["rank"]))
    return out


def subtitles(kind, imdb, season=None, episode=None):
    """Available subtitles, preferred languages first."""
    if kind == "show":
        url = f"{SUBS_API}/subtitles/series/{imdb}:{season or 1}:{episode or 1}.json"
    else:
        url = f"{SUBS_API}/subtitles/movie/{imdb}.json"
    r = requests.get(url, timeout=45, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    subs = [s for s in r.json().get("subtitles", []) if s.get("url")]
    def rank(s):
        lang = s.get("lang", "")
        return (PREFERRED_LANGS.index(lang) if lang in PREFERRED_LANGS else 99, lang)
    return sorted(subs, key=rank)


def sub_path(imdb, lang, season=None, episode=None):
    """Where a downloaded subtitle lives until the librarian places it."""
    tag = f".s{int(season):02d}e{int(episode):02d}" if season and episode else ""
    return SUBS_DIR / f"{imdb}{tag}.{lang}.srt"


def save_subtitle(url, imdb, lang, season=None, episode=None):
    r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    SUBS_DIR.mkdir(parents=True, exist_ok=True)
    path = sub_path(imdb, lang, season, episode)
    path.write_bytes(r.content)
    return path


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

        cands = candidates(kind, imdb, season, episode)
        ready = sum(1 for c in cands if c["cached"] and not c["junk"])
        rows = []
        for c in cands:
            cls = "item bad" if c["junk"] else ("item best" if not rows else "item")
            badge = ("<span class='badge junk'>cam / screener</span>" if c["junk"]
                     else "<span class='badge cached'>&#9889; plays now</span>" if c["cached"]
                     else "<span class='badge uncached'>must download</span>")
            meta = [gb(c["size"])]
            if c["mbps"]:
                heavy = c["mbps"] > LINK_MBPS * 0.6
                meta.append(f"<span class='badge {'junk' if heavy else 'uncached'}'>"
                            f"~{c['mbps']:.0f} Mbps{' — will buffer on this link' if heavy else ''}</span>")
            if c["score"] is not None:
                meta.append(f"score {c['score']:.0f}")
            rows.append(
                f"<div class='{cls}'>"
                f"<div class='grow'><div class='name'>{html.escape(c['release'][:110])}</div>"
                f"<div class='meta'>{badge} &middot; {' &middot; '.join(meta)}</div></div>"
                f"<form method='post' action='/add'>"
                f"<input type='hidden' name='hash' value='{c['hash']}'>"
                f"<input type='hidden' name='imdb' value='{imdb}'>"
                f"<input type='hidden' name='kind' value='{kind}'>"
                f"<input type='hidden' name='title' value='{html.escape(title)}'>"
                f"<input type='hidden' name='season' value='{season if kind == 'show' else ''}'>"
                f"<button>add</button></form></div>")
        summary = (f"<p class='meta'>{len(cands)} releases &middot; "
                   f"<b>{ready}</b> already cached at Real-Debrid (those play "
                   f"immediately and are listed first)</p>")
        return self._send(page(title, head + summary + (
            "".join(rows) or "<p class='meta'>No releases found.</p>")))

    def subs(self, q):
        kind = q.get("kind", ["movie"])[0]
        imdb = q.get("imdb", [""])[0]
        title = q.get("title", ["?"])[0]
        season = q.get("season", [""])[0]
        episode = q.get("episode", [""])[0]
        season = int(season) if season else None
        episode = int(episode) if episode else None

        have = {p.name for p in SUBS_DIR.glob(f"{imdb}*.srt")} if SUBS_DIR.exists() else set()
        rows = []
        for s in subtitles(kind, imdb, season, episode)[:40]:
            lang = s.get("lang", "?")
            target = sub_path(imdb, lang, season, episode).name
            mark = " &middot; <b>saved</b>" if target in have else ""
            rows.append(
                f"<div class='item'><div class='grow'>"
                f"<div class='name'><b>{html.escape(lang)}</b>"
                + ("<span class='badge cached'>saved</span>" if target in have else "")
                + f"</div><div class='meta'>{html.escape(s.get('id','')[:44])}</div></div>"
                f"<form method='post' action='/addsub'>"
                f"<input type='hidden' name='url' value='{html.escape(s['url'])}'>"
                f"<input type='hidden' name='imdb' value='{imdb}'>"
                f"<input type='hidden' name='lang' value='{html.escape(lang)}'>"
                f"<input type='hidden' name='season' value='{season or ''}'>"
                f"<input type='hidden' name='episode' value='{episode or ''}'>"
                f"<button>use</button></form></div>")
        note = ("<p class='meta'>Saved subtitles are placed next to the video file, so "
                "Plex offers them as a selectable track on the PS5.</p>")
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
            poller._add_and_record(
                (0, form["hash"][0], form["hash"][0]), form["title"][0], form["imdb"][0],
                "", form["kind"][0], managed, season)
            return self._redirect("/")

        if self.path == "/addsub":
            season = form.get("season", [""])[0]
            episode = form.get("episode", [""])[0]
            save_subtitle(form["url"][0], form["imdb"][0], form["lang"][0],
                          int(season) if season else None,
                          int(episode) if episode else None)
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
